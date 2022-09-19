"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import re
import json
from pydantic import ValidationError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError

from .app import app
from .middleware import ray_connection, require_ray_client
from .listener_actions import post_job_status
from .logging import slack_log_decorator
from .templates.models import NewJobForm, convert_pydantic_to_slack_error
from .templates.messages import (
    OnboardingMessage,
    LoginMessage,
    LogoutMessage,
    SuccessfulLogoutMessage,
    JobStatusNoIdMessage,
    NewJobMessage,
    JobSubmitMessage,
    HelpMessage,
    WhatsNextMessage,
    WhoamiMessage,
    InvalidCommandMessage,
)
from .templates.views import new_job_modal
from .web import files_list_simple, get_bot_accessible_files
from .select_options import get_language_options, map_file_options
from ..auth.connector import disconnect_ray_account
from ..ray import RayService
from ..watson import watson_message


# ---------------------------------------------------------
# Set up Slack listeners here.
# ---------------------------------------------------------


@app.event(
    {"type": "message", "subtype": (None, "file_share")}, middleware=[ray_connection]
)
@slack_log_decorator
async def message_event(message, context, say, client):
    # TODO handle message threads (do not respond to threads)
    # If there is no text, show new job button or ignore the message.
    if not message.get("text"):
        if message.get("files"):
            msg = NewJobMessage(context["channel_id"], message["ts"])
            await say(blocks=msg.blocks, text=msg.text)
        return

    response = watson_message(message["text"], context.get("user_id"))
    context["log"].set_watson_log(
        status_code=response.status_code,
        text=message["text"],
        response=response.data,
        headers=dict(response.headers),
        intents=response.data["output"]["intents"],
        entities=response.data["output"]["entities"],
    )
    match response.intent:
        case "General_About_You" | "General_Agent_Capabilities" | "General_Greetings":
            await say(blocks=HelpMessage().blocks, text=HelpMessage().text)
        case "Login":
            await client.chat_postEphemeral(
                channel=context["channel_id"],
                user=context["user_id"],
                blocks=context["login_prompt"].blocks,
                text=context["login_prompt"].text,
            )
        case "Logout":
            if await require_ray_client(context):
                msg = LogoutMessage(context["ray"].client.username)
                await client.chat_postEphemeral(
                    channel=context["channel_id"],
                    user=context["user_id"],
                    blocks=msg.blocks,
                    text=msg.text,
                )
        case "Job_Status":
            tj_number_entity = response.findEntity("tj-number")
            if tj_number_entity:
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        context, context["ray"].client, tj_number_entity.groups[0]
                    )
            else:
                await say(JobStatusNoIdMessage().text)
        case "New_Translation_Job":
            msg = NewJobMessage(context["channel_id"], message["ts"])
            await say(blocks=msg.blocks, text=msg.text)
        case "Jokes":
            # Delegate jokes to IBM Watson Assistant dialog.
            await say(response.reply)
        case _:
            tj_number_entity = response.findEntity("tj-number")
            if tj_number_entity:
                # Show the job status if only a job id is entered.
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        context, context["ray"].client, tj_number_entity.groups[0]
                    )
            elif response.reply:
                # Default to Watson Assistant fallback response if no other matches.
                await say(response.reply)


@app.event("app_home_opened")
@slack_log_decorator
async def home_opened(event, body, say, client):
    # Send an onboarding message if the app home is opened for the first time.
    # TODO also onboard if the user hasn't opened in a long time and the account
    # is not connected yet
    history = await client.conversations_history(channel=event.get("channel"), limit=1)
    if not history.get("messages"):
        message = OnboardingMessage(
            event.get("user"), body["team_id"], body["api_app_id"], event.get("channel")
        )
        await say(blocks=message.blocks, text=message.text)


@app.message_shortcut("new_job", middleware=[ray_connection])
@slack_log_decorator
async def new_job_shortcut(ack, shortcut, context, client):
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        # Include a bit more than the max 100 options due to hidden files.
        files = await files_list_simple(client, count=110)
        # Set files in the message as initial values if the bot has access to them.
        init_files = await get_bot_accessible_files(
            client, (f["id"] for f in shortcut["message"].get("files", []))
        )
        await client.views_open(
            trigger_id=shortcut["trigger_id"],
            view=new_job_modal(
                context["ray"].client.username,
                file_options=files,
                initial_files=init_files,
            ),
        )


@app.command("/ray", middleware=[ray_connection])
@slack_log_decorator
async def ray_command(ack, respond, command, context, client):
    await ack()

    # Strip the text formatting from the command args (not perfect).
    def strip_formatting(text: str):
        if re.match(r"(\*.+\*)|(~.+~)|(_.+_)|(`.+`)", text):
            return text[1:-1]
        return text

    command_args = re.split(
        r"\s+", strip_formatting(command.get("text", "").strip().lower())
    )
    command_args = [strip_formatting(arg) for arg in command_args]
    match command_args:
        case ["account"]:
            if await require_ray_client(context):
                await respond(WhoamiMessage(context["ray"].client.username).text)
        case ["login" | "signin" | "connect"]:
            await respond(
                text=context["login_prompt"].text,
                blocks=context["login_prompt"].blocks,
            )
        case ["logout" | "signout" | "disconnect"]:
            if await require_ray_client(context):
                msg = LogoutMessage(context["ray"].client.username)
                await respond(text=msg.text, blocks=msg.blocks)
        case ["new"]:
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                files = await files_list_simple(client, count=110)
                # Try to get the files from the last 3 messages to set as the
                # default files to translate in the new job modal.
                init_files = []
                try:
                    response = await client.conversations_history(
                        channel=context["channel_id"],
                        limit=3,
                    )
                    for message in response["messages"]:
                        if message.get("files"):
                            init_files = message.get("files")
                            break
                except SlackApiError:
                    pass
                await client.views_open(
                    trigger_id=command["trigger_id"],
                    view=new_job_modal(
                        context["ray"].client.username,
                        file_options=files,
                        initial_files=init_files,
                    ),
                )
        case ["help" | ""]:
            await respond(blocks=HelpMessage().blocks, text=HelpMessage().text)
        case ["whatsnext"] | ["whats", "next"]:
            await respond(
                blocks=WhatsNextMessage().blocks, text=WhatsNextMessage().text
            )
        case [command_text]:
            match = re.fullmatch(r"tj\d+", command_text, re.IGNORECASE)
            if match:
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(context, context["ray"].client, command_text)
            else:
                await respond(text=InvalidCommandMessage().text)
        case _:
            await respond(text=InvalidCommandMessage().text)


@app.block_action("new_job", middleware=[ray_connection])
@slack_log_decorator
async def new_job_action(ack, payload, context, client, body):
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        files = await files_list_simple(client, count=110)
        # Get files from the source message to prefill the modal.
        init_files = []
        try:
            value = json.loads(payload["value"])
            response = await client.conversations_history(
                channel=value["channel_id"],
                latest=value["ts"],
                inclusive=True,
                limit=1,
            )
            message = response["messages"][0]
            # Assume the files are accessible if we are able to get the message
            init_files = message.get("files", [])
        except (SlackApiError, json.JSONDecodeError, KeyError):
            pass  # The payload value is malformed or no access to the files.
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=new_job_modal(
                context["ray"].client.username,
                file_options=files,
                initial_files=init_files,
            ),
        )


@app.block_action("disconnect")
async def disconnect_account_action(ack, action, body, context, respond):
    await ack()
    disconnect_ray_account(context["user_id"], context["team_id"], body["api_app_id"])
    # action["value"] should contain the DeltaRAY account username.
    msg = SuccessfulLogoutMessage(context["user_id"], action.get("value"))
    await respond(text=msg.text, blocks=msg.blocks, replace_original=True)


@app.block_action("delete_ephemeral_message")
async def delete_ephemeral_message(ack, respond):
    await ack()
    await respond(delete_original=True)


@app.block_action(re.compile(r"link(_\d+)?|login"))
async def link(ack):
    """Simple link button action. No additional actions required."""
    await ack()


@app.view("new_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_new_job(ack, view, context, client):
    if await require_ray_client(context, prompt_login=False):
        try:
            form = NewJobForm.parse_slack(view["state"]["values"])
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")
        # The response is already returned at this point, can do long tasks here.
        message = JobSubmitMessage(form)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=message.text,
            blocks=message.blocks,
        )

        # Process files and submit job.
        responses = await RayService.get_service(context["ray"].client).submit_job(
            client, form
        )
        for response in responses:
            context["log"].add_api_log(
                status_code=response.response.status_code,
                url=str(response.response.url),
                payload=None,  # TODO: log payload without file
                response=response.response.content.decode() or None,
                headers=dict(response.response.headers.items()),
                version="v3",
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


@app.options("language_options")
async def language_options(ack, payload):
    options = await get_language_options(payload.get("value"))
    await ack(options=options)


@app.options("file_options")
async def file_options(ack, payload, client):
    """This select options endpoint is used as a backup in case there are
    no files available for the new job files input.
    """
    # Include a bit more than the max 100 options due to filters.
    files = await files_list_simple(client, count=120)
    if filter := payload.get("value"):
        files = [f for f in files if filter.lower().strip() in f["title"].lower()]
    await ack(options=map_file_options(files[:100]))


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

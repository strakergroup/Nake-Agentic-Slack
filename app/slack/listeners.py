"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import re
import json
import time

from pydantic import ValidationError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError
from buglog import notify_exception

from .app import app
from .middleware import ray_connection, require_ray_client
from .listener_actions import (
    post_job_status,
    post_job_details,
    post_job_summary,
    post_job_list,
    submit_job,
    approve_pending_client,
)
from .logging import slack_log_decorator
from .templates.models import NewJobForm, convert_pydantic_to_slack_error
from .templates.messages import (
    LoginMessage,
    LogoutMessage,
    QuoteMessage,
    SuccessfulLogoutMessage,
    JobStatusNoIdMessage,
    NewJobMessage,
    JobSubmitMessage,
    HelpMessage,
    WhatsNextMessage,
    ConnectionInfoMessage,
    InvalidCommandMessage,
    ClientApprovedMessage,
    ClientAlreadyApprovedMessage,
)
from .templates.views import new_job_modal, home_view
from .web import files_list_simple, get_bot_accessible_files
from .select_options import get_language_options, map_file_options
from ..auth.connector import disconnect_ray_account
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
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
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


@app.event("app_home_opened", middleware=[ray_connection])
@slack_log_decorator
async def home_opened(event, context, body, say, client):
    # publish view to home tab
    await client.views_publish(
        user_id=event.get("user"),
        view=home_view(context, context["team_id"], context['ray'].client.slack_app_id),
    )


@app.message_shortcut("new_job", middleware=[ray_connection])
@slack_log_decorator
async def new_job_shortcut(ack, shortcut, context, client, body):
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
async def ray_command(ack, respond, say, command, context):
    await ack()

    # Strip the text formatting from the command args (not perfect).
    def strip_formatting(text: str):
        if re.match(r"(\*.+\*)|(~.+~)|(_.+_)|(`.+`)", text):
            return text[1:-1]
        return text

    command_formatted = strip_formatting(command.get("text", "").strip())
    command_args = re.split(r"\s+", command_formatted.lower())
    command_args = [strip_formatting(arg) for arg in command_args]
    match command_args:
        case ["info" | "account"]:
            msg = ConnectionInfoMessage(
                context["ray"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                app_id=command["api_app_id"],
                channel_id=context["channel_id"],
            )
            await respond(text=msg.text, blocks=msg.blocks)
        case ["login" | "signin" | "connect"]:
            await respond(
                text=context["login_prompt"].text,
                blocks=context["login_prompt"].blocks,
            )
        case ["logout" | "signout" | "disconnect"]:
            if await require_ray_client(context):
                msg = LogoutMessage(context["ray"].client.username)
                await respond(text=msg.text, blocks=msg.blocks)
        case ["job", reference, *reference_other]:
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                # Try searching job by TJ number if the format is correct.
                if not reference_other and re.fullmatch(
                    r"tj\d+", reference, re.IGNORECASE
                ):
                    await post_job_status(context, context["ray"].client, reference)
                # Otherwise, search job by client reference.
                else:
                    client_reference = command_formatted.removeprefix("job").strip()
                    await post_job_list(
                        context,
                        context["ray"].client,
                        preset="CLIENT_REFERENCE",
                        client_ref=client_reference,
                    )
        case ["jobs"] | ["my", "jobs"]:
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                await post_job_summary(context, context["ray"].client)
        case ["quote"]:
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                # quote is like new job except it doesn't open the modal.
                await ack()
                msg = QuoteMessage(context["channel_id"], time.time())
                await say(text=msg.text, blocks=msg.blocks)
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


@app.block_action("show_job_details", middleware=[ray_connection])
@slack_log_decorator
async def show_job_details(ack, action, payload, context):
    """Get job info. Triggered from the "View More Info" in the job list"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        try:
            job_info = json.loads(payload["value"])
            job_id, status = job_info["id"], job_info["status"]
        except (KeyError, json.JSONDecodeError):
            pass
        else:
            await post_job_details(context, context["ray"].client, job_id, status)


@app.action("quote", middleware=[ray_connection])
@slack_log_decorator
async def quote(ack, payload, context, say):
    """Get quote. Triggered from the Home View New Job button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        msg = QuoteMessage(payload, time.time())
        await context.client.chat_postEphemeral(
            channel=payload["value"],
            user=context.user_id,
            text=msg.text,
            blocks=msg.blocks,
        )


# create a block action to get daily summary
@app.action("daily_summary", middleware=[ray_connection])
@slack_log_decorator
async def daily_summary(ack, payload, context):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(context, context["ray"].client, payload['value'])


@app.block_action("job_list", middleware=[ray_connection])
@slack_log_decorator
async def job_list_action(ack, payload, context):
    """Paginated job list. Triggered from the job summary dropdown."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        if "selected_option" in payload:
            preset = payload["selected_option"].get("value")
            await post_job_list(context, context["ray"].client, preset=preset)


@app.block_action(re.compile(r"job_list_paginated(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def job_list_paginated_action(ack, payload, context):
    """Paginated job list. Triggered from the job list "Show more" and
    "Show previous" buttons.
    """
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        try:
            settings = json.loads(payload["value"])
            preset = settings["preset"]
            client_ref = settings["client_reference"]
            page, page_size = settings["page"], settings["page_size"]
        except (KeyError, json.JSONDecodeError):
            pass
        else:
            await post_job_list(
                context,
                context["ray"].client,
                preset=preset,
                client_ref=client_ref,
                page=page,
                page_size=page_size,
                replace_original=True,
            )


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


@app.block_action("approve_pending_client", middleware=[ray_connection])
@slack_log_decorator
async def approve_pending_client_action(ack, action, context, say, client):
    await ack()
    if await require_ray_client(context):
        try:
            # action["value"] should contain the new client details.
            pending_client_details = json.loads(action["value"])
            client_id = pending_client_details["id"]
            client_username = pending_client_details["username"]
        except Exception as e:
            notify_exception(e)
        else:
            approved_groups = await approve_pending_client(
                context["ray"].client,
                pending_client_id=client_id,
                pending_client_username=client_username,
            )
            if approved_groups:
                await say(ClientApprovedMessage(client_username).text)
            else:
                await client.chat_postEphemeral(
                    channel=context["channel_id"],
                    user=context["user_id"],
                    text=ClientAlreadyApprovedMessage(client_username).text,
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
        try:
            responses = await submit_job(context, context["ray"].client, form)
        except Exception as e:
            notify_exception(e)
            await client.chat_postMessage(
                channel=context["user_id"],
                text="There was an error submitting your translation request, please try again.",  # noqa: B950
            )
        else:
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

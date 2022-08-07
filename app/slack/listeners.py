"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""
from typing import Callable
import re
import json
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from .app import app
from .middleware import load_ray_client
from .templates.messages import (
    OnboardingMessage,
    JobStatusMessage,
    NewJobMessage,
    HelpMessage,
    WhoamiMessage,
    InvalidCommandMessage,
)
from .templates.views import new_job_modal, new_job_files_modal
from .select_options import get_language_options, map_file_options
from ..watson import watson_message
from ..ray.methods import get_job
from random import randrange

# logging.basicConfig(level=logging.INFO)


# ---------------------------------------------------------
# Set up Slack listeners here.
# ---------------------------------------------------------


@app.event(
    {"type": "message", "subtype": (None, "file_share")}, middleware=[load_ray_client]
)
async def message_event(message, context, say, client):
    if "text" not in message:
        return

    async def require_ray_client(callback: Callable[[None], None]):
        if context["ray_client"]:
            await callback()
        else:
            await client.chat_postEphemeral(
                channel=context["channel_id"],
                user=context["user_id"],
                blocks=context["login_prompt"]["blocks"],
                text=context["login_prompt"]["text"],
            )

    response = watson_message(message["text"], context.get("user_id"))
    match response.intent:
        case "General_About_You" | "General_Agent_Capabilities" | "General_Greetings":
            await say(blocks=HelpMessage().blocks, text=HelpMessage().text)
        case "Login":
            await client.chat_postEphemeral(
                channel=context["channel_id"],
                user=context["user_id"],
                blocks=context["login_prompt"]["blocks"],
                text=context["login_prompt"]["text"],
            )
        case "Job_Status":

            async def action():
                await say("Job status ...")

            await require_ray_client(action)
        case "New_Translation_Job":
            message = NewJobMessage(context["channel_id"], message["ts"])
            await say(blocks=message.blocks, text=message.text)
        case _:
            await say(response.reply)


@app.event("app_home_opened")
async def home_opened(event, body, say, client):
    # Send an onboarding message if the app home is opened for the first time.
    # TODO also onboard if the user hasn't opened in a long time and the account is not connected
    history = await client.conversations_history(channel=event.get("channel"), limit=1)
    if not history.get("messages"):
        message = OnboardingMessage(
            event.get("user"), body["team_id"], body["api_app_id"], event.get("channel")
        )
        await say(blocks=message.blocks, text=message.text)


@app.global_shortcut("new_job_global", middleware=[load_ray_client])
async def new_job_global(ack, shortcut, context, client):
    await ack()
    if context["ray_client"]:
        await client.views_open(
            trigger_id=shortcut["trigger_id"],
            view=new_job_modal(context["ray_client"]["username"]),
        )
    else:
        # Prompt login if accounts are not connected yet.
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"]["blocks"],
            text=context["login_prompt"]["text"],
        )


@app.message_shortcut("new_job", middleware=[load_ray_client])
async def new_job(ack, shortcut, context, respond, client):
    await ack()
    if context["ray_client"]:
        await client.views_open(
            trigger_id=shortcut["trigger_id"],
            view=new_job_modal(
                context["ray_client"]["username"], shortcut["message"].get("files")
            ),
        )
    else:
        # Prompt login if accounts are not connected yet.
        await respond(
            blocks=context["login_prompt"]["blocks"],
            text=context["login_prompt"]["text"],
        )


@app.command("/ray", middleware=[load_ray_client])
async def ray_command(ack, say, respond, command, context, client):
    await ack()
    if context["ray_client"]:
        # TODO trim, remove extra whitespace
        match command.get("text", "").lower().split(" "):
            case ["whoami"]:
                await respond(WhoamiMessage(context["ray_client"]["username"]).text)
            case ["login" | "signin" | "connect"]:
                await respond(
                    blocks=context["login_prompt"]["blocks"],
                    text=context["login_prompt"]["text"],
                )
            case ["logout" | "signoff"]:
                await respond("Logout prompt")
            case ["new"]:
                await client.views_open(
                    trigger_id=command["trigger_id"],
                    # TODO: get latest files
                    view=new_job_modal(context["ray_client"]["username"]),
                )
            case ["help" | ""]:
                await respond(blocks=HelpMessage().blocks, text=HelpMessage().text)
            case [command_text]:
                # TODO strip text of markdown
                match = re.fullmatch(r"tj\d+", command_text, re.IGNORECASE)
                if match:
                    job = await get_job(
                        context['ray_client']['access_token'],
                        command_text,
                    )
                    if job is not None:
                        message = JobStatusMessage(job, context["ray_client"]["id"])
                        await say(blocks=message.blocks, text=message.text)
                    else:
                        await respond(
                            f"Cannot find the job: `{command_text.upper()}`"
                        )
                else:
                    await respond(text=InvalidCommandMessage().text)
            case _:
                await respond(text=InvalidCommandMessage().text)
    else:
        # Prompt login if accounts are not connected yet.
        await respond(
            blocks=context["login_prompt"]["blocks"],
            text=context["login_prompt"]["text"],
        )


@app.action("new_job", middleware=[load_ray_client])
async def new_job_action(ack, payload, context, client, respond, body):
    await ack()
    if context["ray_client"]:
        # Get files from the source message to prefill the modal.
        files = []
        try:
            value = json.loads(payload["value"])
            response = await client.conversations_history(
                channel=value["channel_id"],
                latest=value["ts"],
                inclusive=True,
                limit=1,
            )
            message = response["messages"][0]
            files = message.get("files", [])
        except Exception:
            pass  # THe payload value is malformed
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=new_job_modal(context["ray_client"]["username"], files),
        )
    else:
        await respond(
            blocks=context["login_prompt"]["blocks"],
            text=context["login_prompt"]["text"],
        )


@app.action("login")
async def login(ack):
    # No need to do anything here, user opened a link.
    await ack()


@app.action("link")
async def link(ack):
    """Simple link button action. No additional actions required."""
    await ack()


@app.view("new_job", middleware=[load_ray_client])
async def handle_new_job(ack, view, context, body, client):
    if context["ray_client"]:
        # TODO input validation, e.g. target date
        files = []
        if "private_metadata" in view:
            try:
                metadata = json.loads(view["private_metadata"])
                files = metadata.get("files", [])
            except Exception:
                # Ignore private_metadata if the format is invalid.
                pass
        # Go to the next form to select the files to translate.
        await ack(response_action="push", view=new_job_files_modal(files))
    else:
        await ack(response_action="clear")
        # Prompt login if accounts are not connected yet.
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"]["blocks"],
            text=context["login_prompt"]["text"],
        )


@app.view("new_job_files", middleware=[load_ray_client])
async def handle_new_job_files(ack, view, context, client):
    print(view["state"]["values"])
    await ack(response_action="clear")
    # await client.chat_postMessage(
    #     channel=context['user_id'],
    #     text=':tada: Your translation job has been submitted. You will be notified when the job is created.'
    # )
    await client.chat_postMessage(
        channel=context["user_id"],
        text=f"New job submitted with ID: `TJ{randrange(800_000, 1_200_000)}`",
    )


@app.options("language_options")
async def language_options(ack, payload):
    options = await get_language_options(payload.get("value"))
    await ack(options=options)


@app.options("file_options")
async def file_options(ack, payload, context, client):
    channel_id = None
    view = payload.get("view")
    # TODO Handle selected conversation change (state?).
    if view and "conversation" in view["state"]["values"]:
        selected_channel = view["state"]["values"]["conversation"][
            "select_conversation"
        ]["selected_conversation"]
        # If the selected conversation is a DM, get the real conversation id (instead of the user id).
        if selected_channel == context["bot_user_id"]:
            channel = await client.conversations_open(
                users=context["user_id"], prevent_creation=True
            )
            channel_id = channel["channel"]["id"]
        elif selected_channel[0] == "U":
            # TODO handle this (user tokens?)
            channel_id = None
        else:
            # TODO this wont work for private channels, mpim, use user token instead
            channel_id = selected_channel

    response = await client.files_list(
        channel=channel_id,
        count=100,
        show_files_hidden_by_limit=False,
        # TODO user= user filter?
    )
    files = response.get("files", [])
    await ack(options=map_file_options(files))


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

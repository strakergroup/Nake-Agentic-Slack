"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import re
import json

from pydantic import ValidationError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError
from ray_sdk import RayAPIResponseError
from buglog import notify_exception, notify_message

from .app import app
from .middleware import ray_connection, require_ray_client
from .listener_actions import (
    respond_to_message,
    get_groups,
    post_job_status,
    post_job_details,
    post_job_summary,
    post_job_list,
    show_quote_form_modal,
    submit_job,
    approve_pending_client,
    post_report_insights,
    post_batch_list,
    post_file_list,
)
from .logging import slack_log_decorator
from .templates.models import NewJobForm, convert_pydantic_to_slack_error
from .templates.messages import (
    LoginMessage,
    LogoutMessage,
    OnboardingMessage,
    QuoteMessage,
    SuccessfulLogoutMessage,
    JobSubmitMessage,
    HelpMessage,
    WhatsNextMessage,
    ConnectionInfoMessage,
    InvalidCommandMessage,
    ClientApprovedMessage,
    ClientAlreadyApprovedMessage,
    JobDelayMessage,
)
from .templates.views import home_view
from .web import files_list_simple, get_bot_accessible_files
from .select_options import get_language_options, map_file_options
from ..auth.connector import (
    disconnect_ray_account,
    disconnect_ray_super_group_and_users,
)


# ---------------------------------------------------------
# Set up Slack listeners here.
# ---------------------------------------------------------


@app.event(
    {"type": "message", "subtype": (None, "message_replied", "file_share")},
    middleware=[ray_connection],
)
@slack_log_decorator
async def message_event(context, message):
    # https://api.slack.com/events/message
    # Respond to messages without threads in 1-on-1 DMs with the bot only,
    # not channel or group conversations (see the "app_mention" event).
    if message.get("channel_type") == "im" or context["channel_id"][0] in ("D", "U"):
        await respond_to_message(context, message, use_thread=False)
    else:
        notify_message(
            "Slack App message event received from channel",
            extra={
                "detail": "This event should not be received from a conversation "
                "other than a DM with the bot, unsubscribe from message:groups, "
                "message:channels, and message:mpim"
            },
        )


@app.event("app_mention", middleware=[ray_connection])
@slack_log_decorator
async def app_mention_event(context, event):
    # https://api.slack.com/events/app_mention
    # Respond to messages with threads in channel and group chats if mentioned.
    # Remove user mentions from text before processing.
    event["text"] = re.sub(r"<@\w+>", "", event.get("text", "")).strip()
    await respond_to_message(context, event, use_thread=True)


@app.event("app_home_opened", middleware=[ray_connection])
@slack_log_decorator
async def home_opened(event, context, body, say, client):
    # https://api.slack.com/events/app_home_opened
    # Send an onboarding message if the app home is opened for the first time.
    history = await client.conversations_history(channel=event.get("channel"), limit=1)
    if not history.get("messages"):
        message = OnboardingMessage(
            context["user_id"],
            context["team_id"],
            context.get("enterprise_id"),
            event.get("channel"),
        )
        await say(blocks=message.blocks, text=message.text)
    # Publish view to home tab.
    await client.views_publish(
        user_id=event.get("user"),
        view=home_view(context, body["api_app_id"], context["ray"]),
    )


@app.event("app_uninstalled")
@slack_log_decorator
async def app_uninstalled(context):
    # https://api.slack.com/events/app_uninstalled
    # Disconnect the Super Group and all users linked to the Slack workspace
    # when the app is uninstalled.
    # RAY-59799: This is a requirement of the Slack app directory submission.
    disconnect_ray_super_group_and_users(
        context["team_id"], context.get("enterprise_id")
    )


@app.message_shortcut("new_job", middleware=[ray_connection])
@slack_log_decorator
async def new_job_shortcut(ack, shortcut, context, client):
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        # Set files in the message as initial values if the bot has access to them.
        init_files = await get_bot_accessible_files(
            client, (f["id"] for f in shortcut["message"].get("files", []))
        )
        await show_quote_form_modal(
            context,
            shortcut["trigger_id"],
            context["ray"].client,
            initial_files=init_files,
        )


@app.command(re.compile(r"\/\w*(ray|straker|lc)\w*"), middleware=[ray_connection])
@slack_log_decorator
async def ray_command(ack, respond, say, command, context, client):
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
                enterprise_id=context.get("enterprise_id"),
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
        case ["new"]:
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                await show_quote_form_modal(
                    context,
                    command["trigger_id"],
                    context["ray"].client,
                    check_last_messages=4,
                )
        case ["quote"]:
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                # quote is like new job except it doesn't open the modal.
                await ack()
                msg = QuoteMessage()
                await respond(text=msg.text, blocks=msg.blocks)
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
async def quote(ack, context, client):
    """Get quote. Triggered from the Home View New Job button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        msg = QuoteMessage()
        await client.chat_postMessage(
            channel=context["user_id"],
            text=msg.text,
            blocks=msg.blocks,
        )


@app.action("daily_summary", middleware=[ray_connection])
@slack_log_decorator
async def daily_summary(ack, context):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(context, context["ray"].client)


@app.block_action("all_summary", middleware=[ray_connection])
@slack_log_decorator
async def all_summary(ack, context):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(
            context=context, ray_client=context["ray"].client, all_jobs=True
        )


@app.action("report_insights", middleware=[ray_connection])
@slack_log_decorator
async def handle_report_insights_action(ack, context):
    """Get Report and Insights. Triggered from the Home Report Insights button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_report_insights(context, context["ray"].client)


@app.block_action("job_list", middleware=[ray_connection])
@slack_log_decorator
async def job_list_action(ack, payload, context):
    """Paginated job list. Triggered from the job summary dropdown."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        if "selected_option" in payload:
            preset = payload["selected_option"].get("value")
            await post_job_list(context, context["ray"].client, preset=preset)
        else:
            preset = payload.get("value")
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
            # The payload value does not exist, is malformed, or no access to the files.
            pass
        await show_quote_form_modal(
            context,
            body["trigger_id"],
            context["ray"].client,
            initial_files=init_files,
            # Check message history for initial files if not in payload.
            check_last_messages=4,
        )


@app.block_action("delay_info")
@slack_log_decorator
async def get_delay_info(ack, respond):
    await ack()
    await respond(JobDelayMessage().text, JobDelayMessage().blocks)


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
async def disconnect_account_action(ack, action, context, respond):
    await ack()
    disconnect_ray_account(
        context["user_id"], context["team_id"], context.get("enterprise_id")
    )
    # action["value"] should contain the LanguageCloud account username.
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
            if isinstance(e, RayAPIResponseError):
                try:
                    notify_exception(e, extra={"response": e.response.json()})
                except Exception:
                    notify_exception(e, extra={"response": e.response.content.decode()})
            else:
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


@app.options("group_options", middleware=[ray_connection])
async def group_options(ack, context):
    if await require_ray_client(context):
        options = await get_groups(context["ray"].client)
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


@app.block_action(re.compile(r"batch_list(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def batch_list_action(ack, payload, context):
    """Paginated batch file list. Triggered from the Show In Progress Files button."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        settings = json.loads(payload["value"])
        job_id = settings["id"]
        page = settings["page"]
        page_size = settings["page_size"]
        replace_original = settings["replace_original"]
        await post_batch_list(
            context,
            context["ray"].client,
            job_id=job_id,
            page=page,
            page_size=page_size,
            replace_original=replace_original,
        )


@app.block_action(re.compile(r"file_list(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def file_list_action(ack, payload, context):
    """Paginated file list. Triggered from the Show Files button."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        settings = json.loads(payload["value"])
        job_id = settings["id"]
        page = settings["page"]
        page_size = settings["page_size"]
        replace_original = settings["replace_original"]
        await post_file_list(
            context,
            context["ray"].client,
            job_id=job_id,
            page=page,
            page_size=page_size,
            replace_original=replace_original,
        )


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import asyncio
import re
import json

from pydantic import ValidationError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError
from ray_sdk import RayAPIResponseError
from buglog import notify_exception, notify_message
from datetime import datetime, timedelta
from .app import app
from .middleware import ray_connection, require_ray_client
from .listener_actions import (
    respond_to_message,
    auto_translate_message,
    get_groups,
    post_job_status,
    post_job_details,
    post_job_summary,
    post_job_list,
    show_quote_form_modal,
    show_job_search_modal,
    submit_job,
    approve_pending_client,
    post_report_insights,
    post_batch_list,
    post_file_list,
    show_sso_form_modal,
)
from .logging import slack_log_decorator
from .templates.models import (
    convert_pydantic_to_slack_error,
    NewJobForm,
    JobSearchForm,
    SsoLoginForm,
    AutoTranslationSettingsForm,
)
from .templates.messages import (
    LoginMessage,
    LogoutMessage,
    OnboardingMessage,
    QuoteMessage,
    WelcomeBackMessage,
    SuccessfulLoginMessage,
    SuccessfulLogoutMessage,
    JobSubmitMessage,
    HelpMessage,
    WhatsNextMessage,
    ConnectionInfoMessage,
    SsoConnectionInfoMessage,
    InvalidCommandMessage,
    ClientApprovedMessage,
    ClientAlreadyApprovedMessage,
    JobDelayMessage,
)
from .templates.views import home_view, settings_auto_translate_view
from .web import files_list_simple, get_bot_accessible_files
from .select_options import get_language_options, get_file_options_cached
from ..auth.connector import (
    connect_ray_account,
    disconnect_ray_account,
    disconnect_ray_super_group_and_users,
    connect_ray_account_sso,
    get_ray_connection,
)
from ..ray.events.parse import get_ray_event_message
from ..ray.settings import (
    get_auto_translate_settings_conversations,
    get_auto_translate_settings_langs,
    update_auto_translate_settings,
)
from slack_bolt.context.async_context import AsyncBoltContext

# ---------------------------------------------------------
# Set up Slack listeners here.
# ---------------------------------------------------------


@app.event(
    {"type": "message", "subtype": (None, "message_replied", "file_share")},
    middleware=[ray_connection],
)
@slack_log_decorator
async def message_event(client, context, message):
    # https://api.slack.com/events/message
    # Respond to messages without threads in 1-on-1 DMs with the bot only,
    # not channel or group conversations (see the "app_mention" event).
    if message.get("channel_type") == "im" or context["channel_id"][0] in ("D", "U"):
        await respond_to_message(client, context, message, use_thread=False)
    elif message.get("text") and f"<@{context['bot_user_id']}>" not in message["text"]:
        # Do not auto-translate if the bot is mentioned (should default to normal response).
        if await require_ray_client(context, prompt_login=False):
            await auto_translate_message(context, context["ray"].client, message)
    else:
        # Do nothing if the Slack app is not mentioned in group chats and
        # auto-translate is disabled.
        pass


@app.event("app_mention", middleware=[ray_connection])
@slack_log_decorator
async def app_mention_event(client, context, event):
    # https://api.slack.com/events/app_mention
    # Respond to messages with threads in channel and group chats if mentioned.
    # Remove user mentions from text before processing.
    event["text"] = re.sub(r"<@\w+>", "", event.get("text", "")).strip()
    await respond_to_message(client, context, event, use_thread=True)


@app.event("app_home_opened", middleware=[ray_connection])
@slack_log_decorator
async def home_opened(event, action, context, body, say, client):
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
    # Send a welcome message if the app home has been idle for 24 hours
    else:
        history_last_24_hours = await client.conversations_history(
            channel=event.get("channel"),
            oldest=int((datetime.now() - timedelta(hours=24)).timestamp()),
            latest=int(datetime.now().timestamp()),
        )
        if not history_last_24_hours.get("messages"):
            message = WelcomeBackMessage(context["user_id"])
            await say(blocks=message.blocks, text=message.text)
        else:
            # There had been some activity in the last 24 hours
            pass
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
        asyncio.create_task(
            files_list_simple(client, channel_id=context["channel_id"], count=120)
        )
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


@app.block_action("login_sso", middleware=[ray_connection])
@slack_log_decorator
async def login_sso_action(ack, context, body, respond):
    if context["ray"].client is None:
        await ack()
        await show_sso_form_modal(context, body["trigger_id"])
    else:
        await ack()
        if context["ray"].client.sso:
            msg = SsoConnectionInfoMessage(
                context["ray"],
            )
        else:
            msg = ConnectionInfoMessage(
                context["ray"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.get("enterprise_id"),
                channel_id=context["channel_id"],
            )
        await respond(text=msg.text, blocks=msg.blocks)


@app.view("login_sso", middleware=[ray_connection])
@slack_log_decorator
async def handle_login_sso(ack, context: AsyncBoltContext, respond, client, view):
    try:
        if "channel_id" not in context:
            context["channel_id"] = context["user_id"]
        if context["ray"] is not None:
            if context["ray"].client is None:
                try:
                    form = SsoLoginForm.parse_slack(view["state"]["values"])
                except ValidationError as e:
                    errors = convert_pydantic_to_slack_error(e)
                    await ack(response_action="errors", errors=errors)
                    return
                await ack(response_action="clear")
                ray_user_id = connect_ray_account_sso(
                    context["user_id"],
                    context["team_id"],
                    form.email,
                    form.firstName,
                    form.lastName,
                    context["channel_id"],
                    context.get("enterprise_id"),
                )
                context["ray"] = await get_ray_connection(
                    context["user_id"],
                    context["team_id"],
                    context.get("enterprise_id"),
                )
                # Show connection success message
                sso_msg = SsoConnectionInfoMessage(
                    context["ray"],
                )
                await client.chat_postMessage(
                    channel=context["channel_id"],
                    text=sso_msg.text,
                    blocks=sso_msg.blocks,
                    replace_original=True,
                )
                data = {
                    "client_id": ray_user_id,
                    "username": form.email,
                    "user_id": context["user_id"],
                    "team_id": context["team_id"],
                    "channel_id": context["channel_id"],
                    "enterprise_id": context.get("enterprise_id"),
                }
                msg = get_ray_event_message("ray:slack:account_connected", data)
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=msg.text,
                    blocks=msg.blocks,
                )
        else:
            await ack(response_action="clear")
            await respond(
                text="Your organisation requires a Super Group to connect your account to Slack."
            )
    except Exception as e:
        notify_exception(e)
        await ack()
        await respond(
            text="There was an error connecting your account, please try again."
        )


@app.block_action("job_search", middleware=[ray_connection])
@slack_log_decorator
async def job_search_action(ack, payload, context, client, body):
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        await show_job_search_modal(
            context,
            body["trigger_id"],
            context["ray"].client,
        )


@app.command(re.compile(r"\/\w*(ray|straker|lc)\w*"), middleware=[ray_connection])
@slack_log_decorator
# Process slash commands.
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

    # Use match to handle different command arguments.
    match command_args:
        case ["info" | "account"]:
            # Get connection info and respond with message.
            msg = ConnectionInfoMessage(
                context["ray"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.get("enterprise_id"),
                channel_id=context["channel_id"],
            )
            await respond(text=msg.text, blocks=msg.blocks)

        case ["login" | "signin" | "connect"]:
            # Respond with login prompt.
            await respond(
                text=context["login_prompt"].text,
                blocks=context["login_prompt"].blocks,
            )

        case ["logout" | "signout" | "disconnect"]:
            # Logout and respond with message.
            if await require_ray_client(context):
                msg = LogoutMessage(context["ray"].client)
                await respond(text=msg.text, blocks=msg.blocks)

        case ["job", reference, *reference_other]:
            # Get job status or list of jobs.
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
            # Get summary of jobs.
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                await post_job_summary(context, context["ray"].client)

        case ["new"]:
            # Show quote form modal.
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                asyncio.create_task(
                    files_list_simple(
                        client, channel_id=context["channel_id"], count=120
                    )
                )
                await show_quote_form_modal(
                    context,
                    command["trigger_id"],
                    context["ray"].client,
                    check_last_messages=4,
                )

        case ["quote"]:
            # Show quote message.
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                # quote is like new job except it doesn't open the modal.
                await ack()
                msg = QuoteMessage()
                await respond(text=msg.text, blocks=msg.blocks)

        case ["help" | ""]:
            # Show help message.
            await respond(
                blocks=HelpMessage(context).blocks, text=HelpMessage(context).text
            )

        case ["whatsnext"] | ["whats", "next"]:
            # Show what's next message.
            await respond(
                blocks=WhatsNextMessage().blocks, text=WhatsNextMessage().text
            )

        case [command_text]:
            # Get job status by TJ number.
            match = re.fullmatch(r"tj\d+", command_text, re.IGNORECASE)
            if match:
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(context, context["ray"].client, command_text)
            else:
                await respond(text=InvalidCommandMessage().text)

        case _:
            # Invalid command.
            await respond(text=InvalidCommandMessage().text)


@app.block_action("settings_auto_translate", middleware=[ray_connection])
@slack_log_decorator
async def show_auto_translate_settings(ack, context, body, client):
    await ack()
    if await require_ray_client(context):
        channels = get_auto_translate_settings_conversations(context["ray"].client)
        languages = get_auto_translate_settings_langs(context["ray"].client)
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=settings_auto_translate_view(channels, languages),
        )


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
        asyncio.create_task(
            files_list_simple(client, channel_id=context["channel_id"], count=120)
        )
        await show_quote_form_modal(
            context,
            body["trigger_id"],
            context["ray"].client,
            initial_files=init_files,
            # Check message history for initial files if not in payload.
            check_last_messages=4,
        )


# The "Account Info" button short cut
@app.block_action("account_info", middleware=[ray_connection])
@slack_log_decorator
async def get_account_info(ack, context, respond):
    await ack()
    msg = ConnectionInfoMessage(
        context["ray"],
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.get("enterprise_id"),
        channel_id=context["channel_id"],
    )
    await respond(text=msg.text, blocks=msg.blocks)


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


@app.block_action("login")
async def login_account_action(ack, action, context, respond):
    await ack()
    try:
        # Use language cloud API to send success message
        pass
        # result = await connect_ray_account(
        #     context["user_id"],
        #     context["team_id"],
        #     context.get("enterprise_id"),
        #     channel_id=context["channel_id"],
        # )
        # if result == "success":
        #     msg = SuccessfulLoginMessage(context["user_id"], action.get("value"))
        #     await respond(text=msg.text, blocks=msg.blocks, replace_original=True)
        # else :
        #     await respond(
        #         text="Login required on language cloud website. Please try again."
        #     )
    except Exception as e:
        notify_exception(e)
        await respond(
            text="There was an error connecting your account, please try again."
        )


@app.block_action("disconnect")
async def disconnect_account_action(ack, action, context, respond):
    await ack()
    # Get connection info before disconnecting.
    context["ray"] = await get_ray_connection(
        context["user_id"], context["team_id"], context.get("enterprise_id")
    )
    disconnect_ray_account(
        context["user_id"], context["team_id"], context.get("enterprise_id")
    )
    # action["value"] should contain the LanguageCloud account username.
    msg = SuccessfulLogoutMessage(
        context["user_id"], context["ray"].client.sso, action.get("value")
    )
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


@app.view("job_search", middleware=[ray_connection])
@slack_log_decorator
async def handle_job_search(ack, view, context, client):
    if await require_ray_client(context, prompt_login=False):
        try:
            form = JobSearchForm.parse_slack(view["state"]["values"])
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")
        # The response is already returned at this point, can do long tasks here.
        reference = form.reference.strip()
        # Try searching job by TJ number if the format is correct.
        if re.fullmatch(r"tj\d+", reference, re.IGNORECASE):
            await post_job_status(context, context["ray"].client, reference)
        elif re.fullmatch(r"\d+", reference, re.IGNORECASE):
            await post_job_status(context, context["ray"].client, "TJ" + reference)
        else:
            client.chat_postMessage(
                channel=context["user_id"],
                text="TJ Number is in incorrect format. E.g. TJ123456 or 123456",
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


@app.view("settings_auto_translate", middleware=[ray_connection])
@slack_log_decorator
async def view_update_auto_translate_settings(ack, view, context, client):
    if await require_ray_client(context, prompt_login=False):
        try:
            form = AutoTranslationSettingsForm.parse_slack(view["state"]["values"])
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")

        try:
            update_auto_translate_settings(
                context["ray"].client,
                conversations=form.conversations,
                languages=form.languages,
            )
        except Exception as e:
            notify_exception(e)
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


@app.options(re.compile(r"file_options_.+"))
async def file_options(ack, payload, client):
    """This select options endpoint is used as a backup in case there are
    no files available for the new job files input.
    """
    channel_id = payload["action_id"].split("_")[2]
    # Include a bit more than the max 100 options due to filters
    # refresh cache this should not be awaited since this can take time. Seems to cause issue with timeout
    task = asyncio.create_task(
        files_list_simple(client, channel_id=channel_id, count=120)
    )
    # only respond with cached files since time can cause timeout unless files empty
    files = await get_file_options_cached(channel_id)
    if not files:
        files = await task
    print(files)
    if filter := payload.get("value"):
        files = [
            f for f in files if filter.lower().strip() in f["text"]["text"].lower()
        ]
    await ack(options=files[:100])


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

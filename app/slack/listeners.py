"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import asyncio
import os
from pathlib import Path
import re
import json
from datetime import datetime, timedelta
from ..database import engines

from pydantic import ValidationError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError
from ray_sdk import RayAPIResponseError
from buglog import notify_exception, notify_message

from app.ray.utils import download_from_file_server, upload_to_file_server
from app.translate import _
from app.wb_tasks.tasks import get_task
from ..redis import redis_conn

from .app import app
from .middleware import ray_connection, require_ray_client, require_mt_tokens
from .listener_actions import (
    document_machine_translate,
    respond_to_message,
    auto_translate_message,
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
    cancel_job_process,
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
    DocumentMTJobMessage,
    LoginMessage,
    LogoutMessage,
    OnboardingMessage,
    QuoteMessage,
    WelcomeBackMessage,
    SuccessfulLoginMessage,
    SuccessfulLogoutMessage,
    SrtTranslateMessage,
    JobSubmitMessage,
    HelpMessage,
    ConnectionInfoMessage,
    SsoConnectionInfoMessage,
    InvalidCommandMessage,
    ClientApprovedMessage,
    ClientAlreadyApprovedMessage,
    JobDelayMessage,
    AutoTranslateSettingsChangedMessage,
    AutoTranslateSettingsDisabledMessage,
)
from .templates.views import (
    home_view,
    translation_settings_view,
    translation_settings_view_error,
    job_search_modal,
    sso_form_modal,
    cancel_job_modal,
)
from .web import download_file, files_list_simple, get_bot_accessible_files
from .select_options import get_language_options, get_file_options_cached
from .utils import is_channel_im
from ..auth.connector import (
    connect_ray_account,
    disconnect_ray_account,
    disconnect_ray_super_group_and_users,
    connect_ray_account_sso,
    get_bot_token,
    get_ray_connection,
)
from ..ray.events.parse import get_ray_event_message
from ..ray.settings import (
    get_auto_translate_settings_and_langs,
    update_auto_translate_group_settings,
    disable_auto_translate_group_settings,
)
from slack_bolt.context.async_context import AsyncBoltContext
from ..config import config, domains

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
    # use threads in channels or group conversations (see the "app_mention" event).
    if not context["is_bot"]:
        if message.get("channel_type") == "im" or is_channel_im(context["channel_id"]):
            await respond_to_message(client, context, message, use_thread=False)
        elif (
            message.get("text")
            and f"<@{context['bot_user_id']}>" not in message["text"]
        ):
            # Do not auto-translate if the bot is mentioned (should default to normal response).
            await auto_translate_message(client, context, message)
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
    if not context["is_bot"]:
        if event["text"] or event.get("files", []):
            await respond_to_message(client, context, event, use_thread=True)
        else:
            ...  # TODO Show auto-translate settings modal


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
        user_id=context["user_id"],
        view=await home_view(context, body["api_app_id"], context.get("ray")),
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
            client,
            context,
            shortcut["trigger_id"],
            context["ray"].client,
            initial_files=init_files,
        )


# @app.block_action("login_sso", middleware=[ray_connection])
# @slack_log_decorator
# async def login_sso_action(ack, context, body, respond, client):
#     if context["ray"].client is None:
#         await ack()
#         await client.views_open(
#             trigger_id=body["trigger_id"],
#             view=sso_form_modal(),
#         )
#     else:
#         await ack()
#         if context["ray"].client.sso:
#             msg = SsoConnectionInfoMessage(
#                 context["ray"],
#             )
#         else:
#             msg = ConnectionInfoMessage(
#                 context["ray"],
#                 user_id=context["user_id"],
#                 team_id=context["team_id"],
#                 enterprise_id=context.get("enterprise_id"),
#                 channel_id=context["channel_id"],
#             )
#         await respond(text=msg.text, blocks=msg.blocks)


@app.action("show_srt_translate_form", middleware=[ray_connection])
@slack_log_decorator
async def show_srt_translate_form(ack, context, action, body, client):
    await ack()
    if await require_ray_client(context):
        task_uuid = action["value"]
        # SrtTranslateMessage normal message no modal just message
        msg = SrtTranslateMessage(task_uuid)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=msg.text,
            blocks=msg.blocks,
        )


# document_mt_job
@app.action("document_mt_job", middleware=[ray_connection])
@slack_log_decorator
async def document_mt_job_action(ack, context, action, body, client):
    await ack()
    if await require_ray_client(context):
        output_file = action["value"]
        # Perform the necessary actions to document the MT job
        msg = DocumentMTJobMessage(output_file)
        # Add your code here
        await client.chat_postMessage(
            channel=context["user_id"],
            text=msg.text,
            blocks=msg.blocks,
        )


@app.action("document_mt_submit", middleware=[ray_connection])
@slack_log_decorator
async def document_mt_submit_action(ack, action, context, body, say, client):
    await ack()
    if await require_ray_client(context):
        slack_file_id = action["value"]
        # get uuid from output_file
        if await require_mt_tokens(context, 1):
            # get selected language from redis keyed on output_file
            # selected from get_auto_translate_language_options
            selected_language = await redis_conn.get(f"output_file_{slack_file_id}")
            if selected_language:
                input_file = await download_file(
                    client=client, file_id=slack_file_id, http=None
                )
                input_file_id = upload_to_file_server(input_file)
                await document_machine_translate(
                    client, context, input_file_id, selected_language
                )
                await say(
                    _(
                        "The file is being translated. You will be notified when it is ready."
                    )
                )
            else:
                await say(_("Please select a language to translate to."))


@app.block_action("download_transcribed_file", middleware=[ray_connection])
@slack_log_decorator
async def download_transcribed_file(ack, action, context, client):
    await ack()
    if await require_ray_client(context):
        task_uuid = action["value"]
        task_result = await get_task(task_uuid, context["ray"].client.id)
        file_id = task_result["file_id"]
        file = download_from_file_server(file_id)
        await client.files_upload_v2(
            channel=context["channel_id"],
            file=file["file"],
            title=file["file_name"],
        )


@app.action("srt_translate", middleware=[ray_connection])
@slack_log_decorator
async def srt_translate_action(ack, action, context, body, say, client):
    await ack()
    if await require_ray_client(context):
        task_uuid = action["value"]
        # get uuid from output_file
        task_result = await get_task(task_uuid, context["ray"].client.id)
        if await require_mt_tokens(context, task_result["tokens"]):
            # get selected language from redis keyed on output_file
            # selected from get_auto_translate_language_options
            selected_language = await redis_conn.get(f"output_file_{task_uuid}")
            if selected_language:
                await document_machine_translate(
                    client, context, task_result["file_id"], selected_language
                )
                await say(
                    _(
                        "The file is being translated. You will be notified when it is ready."
                    )
                )
            else:
                await say(_("Please select a language to translate to."))


@app.block_action("login_sso", middleware=[ray_connection])
@slack_log_decorator
async def login_sso_action(ack, context: AsyncBoltContext, respond, client, view):

    try:
        if "channel_id" not in context:
            context["channel_id"] = context["user_id"]
        if context["ray"] is not None:
            if context["ray"].client is None:
                # The API endpoint to get user info
                info_response_json = await client.users_info(user=context["user_id"])
                if info_response_json["ok"]:
                    user_info = info_response_json["user"]
                    ray_user_id = connect_ray_account_sso(
                        context["user_id"],
                        context["team_id"],
                        user_info["profile"]["email"],
                        user_info["profile"]["first_name"],
                        user_info["profile"]["last_name"],
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
                    await ack(response_action="clear")
                    if context.response_url:
                        await respond(text=sso_msg.text, blocks=sso_msg.blocks)
                    else:
                        await client.chat_postMessage(
                            channel=context["channel_id"],
                            text=sso_msg.text,
                            blocks=sso_msg.blocks,
                        )

                    data = {
                        "client_id": ray_user_id,
                        "username": user_info["profile"]["email"],
                        "user_id": context["user_id"],
                        "team_id": context["team_id"],
                        "channel_id": context["channel_id"],
                        "enterprise_id": context.get("enterprise_id"),
                    }
                    msg = get_ray_event_message("ray:slack:account_connected", data)
                    await ack(response_action="clear")
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=msg.text,
                        blocks=msg.blocks,
                    )
            else:
                await ack(response_action="clear")
                if context["ray"].client.sso:
                    msg = SsoConnectionInfoMessage(
                        context["ray"],
                    )
                # need else block if triggered from old message
                else:
                    msg = ConnectionInfoMessage(
                        context["ray"],
                        user_id=context["user_id"],
                        team_id=context["team_id"],
                        enterprise_id=context.get("enterprise_id"),
                        channel_id=context["channel_id"],
                    )
                await respond(text=msg.text, blocks=msg.blocks)
        else:
            await ack(response_action="clear")
            await respond(
                text="Your organisation requires a Super Group to connect your account to Slack."
            )
    except SlackApiError as sae:
        if sae.response["error"] == "missing_scope":
            await ack(response_action="clear")
            await respond(
                text="This app requires the 'user_read' scope to access user information. "
                "Please grant the necessary permissions and try again. You can reinstall the app "
                f"from this URL: {domains.slack_ray_translator}slack/install"
            )
        else:
            notify_exception(sae)
            await ack()
            await respond(
                text="There was an error retrieving user information. Please try again."
            )
    except Exception as e:
        notify_exception(e)
        await ack()
        await respond(
            text="There was an error connecting your account, please try again."
        )


@app.block_action("job_search", middleware=[ray_connection])
@slack_log_decorator
async def job_search_action(ack, context, client, body):
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=job_search_modal(
                context["ray"].client.username,
            ),
        )


@app.command(re.compile(r"\/\w*(ray|straker|lc)\w*"), middleware=[ray_connection])
@slack_log_decorator
# Process slash commands.
async def ray_command(ack, respond, command, context, client):
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

        case ["translate"]:
            settings, auto_translate_langs = get_auto_translate_settings_and_langs(
                context, context.channel_id
            )
            await client.views_open(
                trigger_id=command["trigger_id"],
                view=translation_settings_view(
                    [context.channel_id],
                    auto_translate_langs,
                    settings.display_format if settings else "thread",
                    team_id=context.team_id,
                ),
            )

        case ["job", reference, *reference_other]:
            # Get job status or list of jobs.
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                # Try searching job by TJ number if the format is correct.
                if not reference_other and re.fullmatch(
                    r"tj\d+", reference, re.IGNORECASE
                ):
                    await post_job_status(
                        client, context, context["ray"].client, reference
                    )
                # Otherwise, search job by client reference.
                else:
                    client_reference = command_formatted.removeprefix("job").strip()
                    await post_job_list(
                        client,
                        context,
                        context["ray"].client,
                        preset="CLIENT_REFERENCE",
                        client_ref=client_reference,
                    )

        case ["jobs"] | ["my", "jobs"]:
            # Get summary of jobs.
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                await post_job_summary(client, context, context["ray"].client)

        case ["new"]:
            # Show quote form modal.
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                asyncio.create_task(
                    files_list_simple(
                        client, channel_id=context["channel_id"], count=120
                    )
                )
                await show_quote_form_modal(
                    client,
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

        case [command_text]:
            # Get job status by TJ number.
            match = re.fullmatch(r"tj\d+", command_text, re.IGNORECASE)
            if match:
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        client, context, context["ray"].client, command_text
                    )
            else:
                await respond(text=InvalidCommandMessage().text)

        case _:
            # Invalid command.
            await respond(text=InvalidCommandMessage().text)


@app.block_action("settings_auto_translate", middleware=[ray_connection])
@slack_log_decorator
async def show_auto_translate_settings(ack, context, payload, body, client):
    await ack()
    channel_info = json.loads(payload["value"])
    channel_id = channel_info.get("channel_id")
    team_id = channel_info.get("team_id")
    token = client.token
    with engines["ray_integration_readonly"].connect() as conn:
        token = get_bot_token(conn, team_id)
        if token:
            client.token = token
    # TODO Could have no channel_id if triggered from home tab.
    settings, auto_translate_langs = get_auto_translate_settings_and_langs(
        context, channel_id
    )
    if channel_id:
        error_msg = _("You do not have permission to edit this channel!!")
        try:
            # Check if the channel is public or private.
            conver_info = await client.conversations_info(channel=channel_id)
            # Check if the user is a member of the channel.
            response = await client.conversations_members(channel=channel_id)
            if (
                context["user_id"] in response["members"]
                or not conver_info["channel"]["is_private"]
            ):
                await client.views_open(
                    trigger_id=body["trigger_id"],
                    view=translation_settings_view(
                        [channel_id] if channel_id else None,
                        auto_translate_langs,
                        settings.display_format if settings else "thread",
                        team_id=team_id,
                    ),
                )
            else:
                await client.views_open(
                    trigger_id=body["trigger_id"],
                    view=translation_settings_view_error(error_msg),
                )
        except SlackApiError as e:
            if e.response["error"] == "missing_scope":
                notify_exception(e)
                error_msg = _("Please reinstall the app")
            elif e.response["error"] == "channel_not_found":
                error_msg = _("The bot is not integrated in this channel!!")
            await client.views_open(
                trigger_id=body["trigger_id"],
                view=translation_settings_view_error(error_msg),
            )
    else:
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=translation_settings_view(
                [channel_id] if channel_id else None,
                auto_translate_langs,
                settings.display_format if settings else "thread",
                team_id=team_id,
            ),
        )


@app.block_action("settings_auto_translate_disable", middleware=[ray_connection])
async def disable_auto_translate_settings(ack, context, payload, body, client):
    try:
        channel_info = json.loads(payload["value"])
        channel_id = channel_info.get("channel_id")
        if not channel_id:
            notify_message("Channel ID not found in payload", extra=payload)
            return
        disable_auto_translate_group_settings(context, channel_id)
        await ack()
        await client.views_publish(
            user_id=context["user_id"],
            view=await home_view(context, body["api_app_id"], context.get("ray")),
        )

        async def join_channel(channel_id: str):
            try:
                await client.conversations_join(channel=channel_id)
            except SlackApiError:
                pass  # Cannot join private channel, or cannot find channel.
            except Exception as e:
                notify_exception(e)

        async def notify_channel(channel_id: str):
            try:
                msg = AutoTranslateSettingsDisabledMessage(channel_id)
                await client.chat_postMessage(channel=channel_id, text=msg.text)
            except SlackApiError:
                pass  # Must be in channel to post. TODO check other events, e.g. app_mention
            except Exception as e:
                notify_exception(e)

        await asyncio.gather(
            *[join_channel(channel_id)],
            return_exceptions=True,
        )
        await asyncio.gather(
            *[notify_channel(channel_id)],
            return_exceptions=True,
        )
    except Exception as e:
        notify_exception(e)


@app.block_action("show_job_details", middleware=[ray_connection])
@slack_log_decorator
async def show_job_details(ack, action, payload, context, client):
    """Get job info. Triggered from the "View More Info" in the job list"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        try:
            job_info = json.loads(payload["value"])
            job_id, status = job_info["id"], job_info["status"]
        except (KeyError, json.JSONDecodeError):
            pass
        else:
            await post_job_details(
                client, context, context["ray"].client, job_id, status
            )


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
async def daily_summary(ack, context, client):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(client, context, context["ray"].client)


@app.block_action("all_summary", middleware=[ray_connection])
@slack_log_decorator
async def all_summary(ack, context, client):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(
            client, context=context, ray_client=context["ray"].client, all_jobs=True
        )


@app.action("report_insights", middleware=[ray_connection])
@slack_log_decorator
async def handle_report_insights_action(ack, context, client):
    """Get Report and Insights. Triggered from the Home Report Insights button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_report_insights(client, context, context["ray"].client)


@app.block_action("job_list", middleware=[ray_connection])
@slack_log_decorator
async def job_list_action(ack, payload, context, client):
    """Paginated job list. Triggered from the job summary dropdown."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        if "selected_option" in payload:
            preset = payload["selected_option"].get("value")
            await post_job_list(client, context, context["ray"].client, preset=preset)
        else:
            preset = payload.get("value")
            await post_job_list(client, context, context["ray"].client, preset=preset)


@app.block_action(re.compile(r"job_list_paginated(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def job_list_paginated_action(ack, payload, context, client):
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
                client,
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
            client,
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


# The "Connect" button short cut in Help Message
@app.block_action("connect_info", middleware=[ray_connection])
@slack_log_decorator
async def get_connect_info(ack, context, respond):
    await ack()
    msg = LoginMessage(
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.get("enterprise_id"),
        channel_id=context.get("channel_id", context["user_id"]),
        ray_client=context["ray"].client if context["ray"] is not None else None,
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


@app.block_action("disconnect", middleware=[ray_connection])
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
        # message = JobSubmitMessage(form)
        # await client.chat_postMessage(
        #     channel=context["user_id"],
        #     text=message.text,
        #     blocks=message.blocks,
        # )

        # Process files and submit job.
        try:
            responses = await submit_job(client, context["ray"].client, form)
            result = responses[0].response.json()["Message"]
            if "job_id" in result:
                message = JobSubmitMessage(form)
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=message.text,
                    blocks=message.blocks,
                )
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
        reference = form.reference.strip().replace(" ", "")
        # Try searching job by TJ number if the format is correct.
        if re.fullmatch(r"TJ\d+(,\s?TJ\d+)*", reference, re.IGNORECASE):
            await post_job_status(client, context, context["ray"].client, reference)
        elif re.fullmatch(r"\d+(,\s?\d+)*", reference, re.IGNORECASE):
            await post_job_status(
                client, context, context["ray"].client, "TJ" + reference
            )
        else:
            await client.chat_postMessage(
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
async def view_update_auto_translate_settings(ack, view, context, body, client):
    try:
        team_id = view["private_metadata"]
        with engines["ray_integration_readonly"].connect() as conn:
            token = get_bot_token(conn, team_id)
            if token:
                client.token = token
        form = AutoTranslationSettingsForm.parse_slack(view["state"]["values"])
        for c in form.channels:
            await client.conversations_info(channel=c)
    except SlackApiError as e:
        if e.response["error"] == "channel_not_found":
            error_msg = _(
                "Please /invite @Straker to the private channels in order to enable channel translation."
            )
            await ack(
                response_action="errors",
                errors={"channels": error_msg},
            )
        if e.response["error"] == "missing_scope":
            await ack(
                response_action="errors",
                errors={"channels": "Please reinstall the app"},
            )
        return
    except ValidationError as e:
        errors = convert_pydantic_to_slack_error(e)
        await ack(response_action="errors", errors=errors)
        return
    await ack(response_action="clear")
    try:
        update_auto_translate_group_settings(
            context,
            channels=form.channels,
            languages=form.languages,
            display_format=form.display_format,
        )
        await client.views_publish(
            user_id=context["user_id"],
            view=await home_view(context, body["api_app_id"], context.get("ray")),
        )

        # Try to join channel automatically after updating settings.
        async def join_channel(channel_id: str):
            try:
                await client.conversations_join(channel=channel_id)
            except SlackApiError:
                pass  # Cannot join private channel, or cannot find channel.
            except Exception as e:
                notify_exception(e)

        async def notify_channel(channel_id: str):
            try:
                msg = AutoTranslateSettingsChangedMessage(
                    channel_id, form.languages, form.display_format
                )
                await client.chat_postMessage(channel=channel_id, text=msg.text)
            except SlackApiError:
                pass  # Must be in channel to post. TODO check other events, e.g. app_mention
            except Exception as e:
                notify_exception(e)

        await asyncio.gather(
            *[join_channel(channel_id) for channel_id in form.channels],
            return_exceptions=True,
        )
        await asyncio.gather(
            *[notify_channel(channel_id) for channel_id in form.channels],
            return_exceptions=True,
        )
    except Exception as e:
        notify_exception(e)


@app.action("language_mt_options")
async def language_mt_options_selected(ack, body):
    # redis store the selected options keyed by ouputn file
    await ack()
    file_id = body["actions"][0]["block_id"]
    selected_language = body["actions"][0]["selected_option"]["value"]
    await redis_conn.set(f"output_file_{file_id}", selected_language)


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
    if filter := payload.get("value"):
        files = [
            f for f in files if filter.lower().strip() in f["text"]["text"].lower()
        ]
    await ack(options=files[:100])


@app.block_action(re.compile(r"batch_list(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def batch_list_action(ack, payload, context, client):
    """Paginated batch file list. Triggered from the Show In Progress Files button."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        settings = json.loads(payload["value"])
        job_id = settings["id"]
        page = settings["page"]
        page_size = settings["page_size"]
        replace_original = settings["replace_original"]
        await post_batch_list(
            client,
            context,
            context["ray"].client,
            job_id=job_id,
            page=page,
            page_size=page_size,
            replace_original=replace_original,
        )


@app.block_action(re.compile(r"file_list(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def file_list_action(ack, payload, context, client):
    """Paginated file list. Triggered from the Show Files button."""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        settings = json.loads(payload["value"])
        job_id = settings["id"]
        page = settings["page"]
        page_size = settings["page_size"]
        replace_original = settings["replace_original"]
        await post_file_list(
            client,
            context,
            context["ray"].client,
            job_id=job_id,
            page=page,
            page_size=page_size,
            replace_original=replace_original,
        )


@app.block_action("cancel_job", middleware=[ray_connection])
@slack_log_decorator
async def cancel_job_action(ack, payload, context, client, body):
    await ack()
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        if "value" in payload:
            job_info = json.loads(payload["value"])
            if job_info.get("job_action") == "list":
                job_id = job_info["job_id"].split("TJ")[1]
                await cancel_job_process(
                    client, context, context["ray"].client, job_id=job_id
                )
            elif job_info.get("job_action") == "submit":
                await cancel_job_process(
                    client, context, context["ray"].client, job_uuid=job_info["job_id"]
                )
            else:
                await client.views_open(
                    trigger_id=body["trigger_id"],
                    view=cancel_job_modal(context["ray"].client.username),
                )
        else:
            await client.views_open(
                trigger_id=body["trigger_id"],
                view=cancel_job_modal(context["ray"].client.username),
            )


@app.view("cancel_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_cancel_job(ack, view, context, client):
    """Get job info. Triggered from the "View More Info" in the job list"""
    await ack()
    if await require_ray_client(context, prompt_login=False):
        try:
            form = JobSearchForm.parse_slack(view["state"]["values"])
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")
        reference = form.reference.strip().lower()
        # Try searching job by TJ number if the format is correct.
        if re.fullmatch(r"tj\d+", reference, re.IGNORECASE):
            job_id = reference.split("tj")[1]
            await cancel_job_process(client, context, context["ray"].client, job_id)
        elif re.fullmatch(r"\d+", reference, re.IGNORECASE):
            await cancel_job_process(client, context, context["ray"].client, reference)
        else:
            await client.chat_postMessage(
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


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

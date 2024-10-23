"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import asyncio
import os
import re
import json
from datetime import datetime, timedelta

from app.api.verify import (
    download_verify_file,
    get_client_evaluation_job,
    submit_evaluation_job,
)
from ..database import engines

from pydantic import ValidationError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_sdk.errors import SlackApiError
from ray_sdk import RayAPIResponseError
from buglog import notify_exception, notify_message

from app.ray.utils import (
    download_from_file_server,
    is_ibm_enterprise,
    supported_file_types,
    upload_to_file_server,
)
from app.translate import _
from app.wb_tasks.tasks import get_task
from ..redis import redis_conn

from .app import app
from .middleware import ray_connection, require_ray_client, require_mt_tokens
from .listener_actions import (
    document_machine_translate,
    get_mt_translation,
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
    ai_translate_help,
    verify_help,
    post_batch_list,
    post_file_list,
    cancel_job_process,
)
from .logging import slack_log_decorator
from .templates.models import (
    convert_pydantic_to_slack_error,
    NewJobForm,
    EvaluateJobForm,
    JobSearchForm,
    AutoTranslationSettingsForm,
)
from .templates.messages import (
    DocumentMTJobMessage,
    JobCreationMessage,
    LoginMessage,
    LogoutMessage,
    OnboardingMessage,
    QuoteMessage,
    WelcomeBackMessage,
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
    evaluate_job_modal,
    home_view,
    translation_settings_view,
    translation_settings_view_error,
    job_search_modal,
    cancel_job_modal,
    verify_job_modal,
)
from .web import (
    download_file,
    files_list_simple,
    get_bot_accessible_files,
    get_mt_ts_cached,
)
from .select_options import (
    _get_languages_cached,
    get_language_options,
    get_file_options_cached,
)
from .utils import is_channel_im
from ..auth.connector import (
    RayContext,
    disconnect_ray_account,
    disconnect_ray_super_group_and_users,
    connect_ray_account_sso,
    get_all_tokens_for_enterprise,
    get_bot_token,
    get_group_quote_settings,
    get_ray_connection,
    resolve_channels_to_team,
    is_slack_team_admin,
)
from ..ray.events.parse import get_ray_event_message
from ..ray.settings import (
    get_auto_translate_settings_and_langs,
    update_auto_translate_group_settings,
    disable_auto_translate_group_settings,
    update_channel_id,
)

from ..config import domains

from typing import Dict, Any, Optional
from slack_sdk.web.async_client import AsyncWebClient
from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncSay, AsyncRespond

# ---------------------------------------------------------
# Set up Slack listeners here.
# ---------------------------------------------------------


@app.event(
    {"type": "message", "subtype": (None, "message_replied", "file_share")},
    middleware=[ray_connection],
)
@slack_log_decorator
async def message_event(
    client: AsyncWebClient,
    context: RayContext,
    message: Dict[str, Any],
    body: Dict[str, Any],
):
    # https://api.slack.com/events/message
    # Respond to messages without threads in 1-on-1 DMs with the bot only,
    # use threads in channels or group conversations (see the "app_mention" event).
    if not context.is_bot:
        if message.get("channel_type") == "im" or is_channel_im(context["channel_id"]):
            with engines["ray_integration_readonly"].connect() as conn:
                # extract team id from body
                body_team_id = body.get("event", {}).get("team")
                if body_team_id:
                    token = get_bot_token(
                        conn=conn,
                        team_id=body_team_id,
                        enterprise_id=context.enterprise_id,
                    )
                    if token:
                        if token != client.token:
                            print("token different")
                            client.token = token
                        else:
                            print("token same")
                    else:
                        print("no token")
                else:
                    print("no event in body")
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
async def app_mention_event(
    client: AsyncWebClient, context: RayContext, event: Dict[str, Any]
):
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
async def home_opened(
    event: Dict[str, Any],
    action: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    say: AsyncSay,
    client: AsyncWebClient,
):
    # https://api.slack.com/events/app_home_opened
    # Send an onboarding message if the app home is opened for the first time.
    # TODO: put try catch around this
    try:
        history = await client.conversations_history(
            channel=event.get("channel"), limit=1
        )
        is_ibm = is_ibm_enterprise(enterprise_id=context.enterprise_id)
        if not history.get("messages"):
            message = OnboardingMessage(
                context["user_id"],
                context["team_id"],
                context.enterprise_id,
                event.get("channel"),
                not is_ibm,
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
                message = WelcomeBackMessage(context["user_id"], context["ray"])
                await say(blocks=message.blocks, text=message.text)
            else:
                # There had been some activity in the last 24 hours
                pass
    except SlackApiError as e:
        notify_exception(e)
    # Publish view to home tab.
    await client.views_publish(
        user_id=context["user_id"],
        view=await home_view(context, body["api_app_id"], context.get("ray")),
    )


@app.action(re.compile(r"home_load_(next|previous)"), middleware=[ray_connection])
@slack_log_decorator
async def home_load(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    # submit from next button on transation settings view
    home_info = json.loads(action["value"])
    page = int(home_info.get("page", 1))
    await client.views_publish(
        user_id=context["user_id"],
        view=await home_view(context, body["api_app_id"], context.get("ray"), page),
    )


@app.event("app_uninstalled")
@slack_log_decorator
async def app_uninstalled(context: RayContext):
    # https://api.slack.com/events/app_uninstalled
    # Disconnect the Super Group and all users linked to the Slack workspace
    # when the app is uninstalled.
    # RAY-59799: This is a requirement of the Slack app directory submission.
    disconnect_ray_super_group_and_users(context["team_id"], context.enterprise_id)


@app.event("channel_id_changed")
@slack_log_decorator
async def channel_id_changed(event: Dict[str, Any]):
    update_channel_id(event.get("old_channel_id"), event.get("new_channel_id"))


@app.message_shortcut("new_job", middleware=[ray_connection])
@slack_log_decorator
async def new_job_shortcut(
    ack: AsyncAck,
    shortcut: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
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
# async def login_sso_action(ack: AsyncAck, context: RayContext, body: Dict[str, Any], respond: AsyncRespond, client: AsyncWebClient):
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
#                 enterprise_id=context.enterprise_id,
#                 channel_id=context["channel_id"],
#             )
#         await respond(text=msg.text, blocks=msg.blocks)


@app.action("show_srt_translate_form", middleware=[ray_connection])
@slack_log_decorator
async def show_srt_translate_form(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
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
async def document_mt_job_action(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    if await require_ray_client(context):
        output_file = action["value"]
        file_info = await client.files_info(file=output_file)
        file_path, file_extension = os.path.splitext(file_info["file"]["name"])
        is_valid_file_type = supported_file_types(file_extension)
        # Perform the necessary actions to document the MT job
        if is_valid_file_type:
            msg = DocumentMTJobMessage(output_file)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=msg.text,
                blocks=msg.blocks,
            )
        else:
            msg = _(
                "This file type is currently not supported. Please check the <https://help.strakertranslations.com/hc/en-us/articles/35943216049945-AI-Translate-for-Documents-in-Straker-Translate-App-for-Slack|help docs>"
            )
            # Add your code here
            await client.chat_postMessage(
                channel=context["user_id"],
                text=msg,
            )


@app.action("document_mt_submit", middleware=[ray_connection])
@slack_log_decorator
async def document_mt_submit_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    say: AsyncSay,
    client: AsyncWebClient,
):
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
                    context, input_file_id, selected_language
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
async def download_transcribed_file(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
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
            filename=file["file_name"],
        )


@app.block_action("download_ai_translation_action", middleware=[ray_connection])
@slack_log_decorator
async def download_ai_translation_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    if await require_ray_client(context):
        file_uuid = action["value"]
        file = await download_verify_file(context.ray.client, file_uuid)
        await client.files_upload_v2(
            channel=context["channel_id"],
            file=file["file"],
            title=file["file_name"],
            filename=file["file_name"],
        )


@app.shortcut("shortcut_translate", middleware=[ray_connection])
@slack_log_decorator
async def handle_translate_shortcut(
    ack: AsyncAck,
    body: Dict[str, Any],
    client: AsyncWebClient,
    context: RayContext,
):
    await ack()
    mt_tl = context.get("locale", "en")
    mt_text = body["message"]["text"]

    user_info = await context.client.users_info(
        user=context["user_id"], include_locale=True
    )

    # Set user language for AI Translate based on user locale and timezone for Romanian(ro-RO), Polish(pl-PL) and Dutch[Belgium](nl-NL)
    # Romanian Timezone - (UTC+02:00) Athens, Bucharest.
    # Polish Timezone - (UTC+01:00) Sarajevo, Skopje, Warsaw, Zagreb.
    # Dutch(Belgium) Timezone - (UTC+01:00) Brussels, Copenhagen, Madrid, Paris
    if user_info["user"]["locale"] == "fr-FR":
        if user_info["user"]["tz"] == "Europe/Athens":
            mt_tl = "ro-RO"
        elif user_info["user"]["tz"] == "Europe/Warsaw":
            mt_tl = "pl-PL"
        elif user_info["user"]["tz"] == "Europe/Brussels":
            mt_tl = "nl-NL"

    await get_mt_translation(
        client,
        context,
        source_lang="",
        target_lang=mt_tl,
        sentence=mt_text,
    )


@app.action("srt_translate", middleware=[ray_connection])
@slack_log_decorator
async def srt_translate_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    say: AsyncSay,
    client: AsyncWebClient,
):
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
                    context, task_result["file_id"], selected_language
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
async def login_sso_action(
    ack: AsyncAck,
    context: RayContext,
    respond: AsyncRespond,
    client: AsyncWebClient,
    view: Optional[Dict[str, Any]],
):

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
                        context.enterprise_id,
                    )
                    context["ray"] = await get_ray_connection(
                        context["user_id"],
                        context["team_id"],
                        context.enterprise_id,
                    )
                    # Show connection success message
                    sso_msg = SsoConnectionInfoMessage(
                        context["ray"],
                        is_ibm=(is_ibm_enterprise(context.enterprise_id)),
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
                        "enterprise_id": context.enterprise_id,
                    }
                    msg = await get_ray_event_message(
                        "ray:slack:account_connected", data, None, context["ray"]
                    )
                    await ack(response_action="clear")
                    if msg:
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
                        is_ibm=(is_ibm_enterprise(context.enterprise_id)),
                    )
                # need else block if triggered from old message
                else:
                    msg = ConnectionInfoMessage(
                        context["ray"],
                        user_id=context["user_id"],
                        team_id=context["team_id"],
                        enterprise_id=context.enterprise_id,
                        channel_id=context["channel_id"],
                        is_ibm=is_ibm_enterprise(context.enterprise_id),
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
                f"from this URL: {domains.slack_ray_translator}/slack/install"
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
async def job_search_action(
    ack: AsyncAck,
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
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
async def ray_command(
    ack: AsyncAck,
    respond: AsyncRespond,
    command: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
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
                enterprise_id=context.enterprise_id,
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
            # Check if the user has a connected account.
            # Open the channel translation settings modal
            # If translation_settings_enabled is True.
            # Else display link to help docs.
            if await require_ray_client(context):
                is_straker_admin = (
                    context["ray"]
                    and context["ray"].client
                    and await is_slack_team_admin(
                        context["ray"].client.id, context.get("enterprise_id")
                    )
                )
                translation_settings_enabled = not is_ibm_enterprise(
                    context.get("enterprise_id")
                ) or (context["ray"] and context["ray"].client and is_straker_admin)

                if translation_settings_enabled:
                    settings, auto_translate_langs = (
                        get_auto_translate_settings_and_langs(
                            context, context.channel_id
                        )
                    )
                    await client.views_open(
                        trigger_id=command["trigger_id"],
                        view=translation_settings_view(
                            [context.channel_id],
                            auto_translate_langs,
                            settings.display_format if settings else "thread",
                        ),
                    )
                else:
                    url_doc ="https://help.strakertranslations.com/hc/en-us/articles/32480860047001-Enabling-Channel-Translation"
                    text_help = "help docs"
                    text = _(
                        f"Please check the <{url_doc}|{text_help}>."
                    )
                    await client.chat_postMessage(
                        channel=context["channel_id"],
                        text=text,
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
async def show_auto_translate_settings(
    ack: AsyncAck,
    context: RayContext,
    payload: Dict[str, Any],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    channel_info = json.loads(payload["value"])
    channel_id = channel_info.get("channel_id")
    settings, auto_translate_langs = get_auto_translate_settings_and_langs(
        context, channel_id
    )

    if channel_id:
        error_msg = _("You do not have permission to edit this channel!!")
        old_token = client.token
        try:
            # check if we have a token that can get channel info for the channel
            channel_info = await resolve_channels_to_team(
                [channel_id], client, context.enterprise_id
            )
            client.token = channel_info[0]["bot_token"]
            channel_info = await client.conversations_info(channel=channel_id)
            # reassign token to the original token since it is required for the original trigger_id
            client.token = old_token
            await client.views_open(
                trigger_id=body["trigger_id"],
                view=translation_settings_view(
                    [channel_id] if channel_id else None,
                    auto_translate_langs,
                    settings.display_format if settings else "thread",
                ),
            )
        except SlackApiError as e:
            client.token = old_token
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
            ),
        )


@app.block_action("settings_auto_translate_disable", middleware=[ray_connection])
async def disable_auto_translate_settings(
    ack: AsyncAck,
    context: RayContext,
    payload: Dict[str, Any],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    try:
        await ack()
        channel_info = json.loads(payload["value"])
        channel_id = channel_info.get("channel_id")
        team_channel = await resolve_channels_to_team(
            [channel_id], client, context.enterprise_id
        )
        client.token = team_channel[0]["bot_token"]
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
                msg = AutoTranslateSettingsDisabledMessage(
                    context["user_id"], channel_id
                )
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
async def show_job_details(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
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
async def quote(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
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
async def daily_summary(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(client, context, context["ray"].client)


@app.block_action("all_summary", middleware=[ray_connection])
@slack_log_decorator
async def all_summary(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_job_summary(
            client, context=context, ray_client=context["ray"].client, all_jobs=True
        )


@app.action("report_insights", middleware=[ray_connection])
@slack_log_decorator
async def handle_report_insights_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    """Get Report and Insights. Triggered from the Home Report Insights button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await post_report_insights(client, context, context["ray"].client)


@app.action("ai_translate_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_ai_translate_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    """Get ai translate help link. Triggered from the Home AI Translate help button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        await ai_translate_help(client, context, context["ray"].client)

@app.action("verify_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_verify_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    """Get verify help link. Triggered from the Home Verify help button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.QUALITY_EVALUATION):
        await verify_help(client, context, context["ray"].client)


@app.block_action("job_list", middleware=[ray_connection])
@slack_log_decorator
async def job_list_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
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
async def job_list_paginated_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
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
async def new_job_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
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
async def get_account_info(ack: AsyncAck, context: RayContext, respond: AsyncRespond):
    await ack()
    msg = ConnectionInfoMessage(
        context["ray"],
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.enterprise_id,
        channel_id=context["channel_id"],
    )
    await respond(text=msg.text, blocks=msg.blocks, replace_original=False)


# The "Connect" button short cut in Help Message
@app.block_action("connect_info", middleware=[ray_connection])
@slack_log_decorator
async def get_connect_info(ack: AsyncAck, context: RayContext, respond: AsyncRespond):
    await ack()
    msg = LoginMessage(
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.enterprise_id,
        channel_id=context.get("channel_id", context["user_id"]),
        ray_client=context["ray"].client if context["ray"] is not None else None,
    )
    await respond(text=msg.text, blocks=msg.blocks, replace_original=False)


@app.block_action("delay_info", middleware=[ray_connection])
@slack_log_decorator
async def get_delay_info(ack: AsyncAck, respond: AsyncRespond):
    await ack()
    await respond(JobDelayMessage().text, JobDelayMessage().blocks)


@app.block_action("approve_pending_client", middleware=[ray_connection])
@slack_log_decorator
async def approve_pending_client_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    say: AsyncSay,
    client: AsyncWebClient,
):
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
async def login_account_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    await ack()
    try:
        # Use language cloud API to send success message
        pass
        # result = await connect_ray_account(
        #     context["user_id"],
        #     context["team_id"],
        #     context.enterprise_id,
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
async def disconnect_account_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    await ack()
    disconnect_ray_account(
        context["user_id"], context["team_id"], context.enterprise_id
    )
    # action["value"] should contain the LanguageCloud account username.
    msg = SuccessfulLogoutMessage(
        context.user_id, context.ray.client.sso, action.get("value")
    )
    await respond(text=msg.text, blocks=msg.blocks, replace_original=True)


@app.block_action("delete_ephemeral_message")
async def delete_ephemeral_message(ack: AsyncAck, respond: AsyncRespond):
    await ack()
    await respond(delete_original=True)


@app.block_action(re.compile(r"link(_\d+)?|login"))
async def link(ack: AsyncAck):
    """Simple link button action. No additional actions required."""
    await ack()


@app.view("new_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_new_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    if await require_ray_client(context, prompt_login=False):
        try:
            form_data = view["state"]["values"] if view else {}
            form = NewJobForm.parse_slack(form_data)
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
            group_id = form.group_id or context["ray"].client.user_group_id
            if "job_id" in result:
                if not is_ibm_enterprise(
                    context.enterprise_id
                ) or get_group_quote_settings(group_id):
                    message = JobSubmitMessage(form)
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=message.text,
                        blocks=message.blocks,
                    )
                else:
                    message = JobCreationMessage(result["job_id"], False)
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
async def handle_job_search(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    if await require_ray_client(context, prompt_login=False):
        try:
            form_data = view.get("state", {}).get("values") if view else {}
            form = JobSearchForm.parse_slack(form_data)
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
                text=_("TJ Number is in incorrect format. E.g. TJ123456 or 123456"),
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
async def view_update_auto_translate_settings(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    try:
        form_data = view.get("state", {}).get("values") if view else {}
        form = AutoTranslationSettingsForm.parse_slack(form_data)
        team_channels = await resolve_channels_to_team(
            form.channels, client, context.enterprise_id
        )
    except SlackApiError as e:
        if e.response["error"] == "channel_not_found":
            error_msg = _(
                "Please /invite @Straker Translate to the private channels in order to enable channel translation."
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
            channels=team_channels,
            languages=form.languages,
            display_format=form.display_format,
        )
        await client.views_publish(
            user_id=context["user_id"],
            view=await home_view(context, body["api_app_id"], context.get("ray")),
        )

        # Try to join channel automatically after updating settings.
        async def join_channel(channel_id: str, bot_token: str):
            try:
                client.token = bot_token
                await client.conversations_join(channel=channel_id)
            except SlackApiError:
                pass  # Cannot join private channel, or cannot find channel.
            except Exception as e:
                notify_exception(e)

        async def notify_channel(channel_id: str, bot_token: str):
            try:
                msg = AutoTranslateSettingsChangedMessage(
                    context["user_id"], channel_id, form.languages, form.display_format
                )
                client.token = bot_token
                await client.chat_postMessage(channel=channel_id, text=msg.text)
            except Exception as e:
                notify_exception(e)
                if context.enterprise_id:
                    all_tokens = get_all_tokens_for_enterprise(context.enterprise_id)
                    for token in all_tokens:
                        client.token = token.bot_token
                        try:
                            await client.chat_postMessage(
                                channel=channel_id, text=msg.text
                            )
                            break
                        except Exception as e:
                            pass

        for channel in team_channels:
            await join_channel(channel["channel_id"], channel["bot_token"])
            await notify_channel(channel["channel_id"], channel["bot_token"])

    except Exception as e:
        notify_exception(e)


@app.action("language_mt_options", middleware=[ray_connection])
async def language_mt_options_selected(ack: AsyncAck, body: Dict[str, Any]):
    # redis store the selected options keyed by ouputn file
    await ack()
    file_id = body["actions"][0]["block_id"]
    selected_language = body["actions"][0]["selected_option"]["value"]
    await redis_conn.set(f"output_file_{file_id}", selected_language)


@app.options("language_options", middleware=[ray_connection])
async def language_options(ack: AsyncAck, payload: Dict[str, Any]):
    options = await get_language_options(payload.get("value"))
    await ack(options=options)


@app.options("language_options_uuid", middleware=[ray_connection])
async def language_options_uuid(ack: AsyncAck, payload: Dict[str, Any]):
    # returns the language options where the value is the uuid
    options = await get_language_options(payload.get("value"), "uuid")
    await ack(options=options)


@app.options("group_options", middleware=[ray_connection])
async def group_options(ack: AsyncAck, context: RayContext):
    if await require_ray_client(context):
        options = await get_groups(context["ray"].client)
        await ack(options=options)


@app.options(re.compile(r"file_options_.+"))
async def file_options(ack: AsyncAck, payload: Dict[str, Any], client: AsyncWebClient):
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
async def batch_list_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
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
async def file_list_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
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
async def cancel_job_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
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
async def handle_cancel_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    """Get job info. Triggered from the "View More Info" in the job list"""
    await ack()
    if await require_ray_client(context, prompt_login=False):
        try:
            form_state = view["state"]["values"] if view else {}
            form = JobSearchForm.parse_slack(form_state)
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
                text=_("TJ Number is in incorrect format. E.g. TJ123456 or 123456"),
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


@app.event({"type": "message", "subtype": "message_deleted"})
@slack_log_decorator
async def message_deleted_event(
    message: Dict[str, Any],
    client: AsyncWebClient,
    body: Dict[str, Any],
    context: RayContext,
):
    if message.get("subtype") == "message_deleted":
        deleted_ts = body["event"]["deleted_ts"]
        timestamp = await get_mt_ts_cached(deleted_ts)
        if timestamp and context.channel_id:
            await client.chat_delete(ts=timestamp, channel=context.channel_id)


@app.event(
    {"type": "message", "subtype": "message_changed"},
    middleware=[ray_connection],
)
@slack_log_decorator
async def message_changed_event(
    client: AsyncWebClient,
    body: Dict[str, Any],
    context: RayContext,
    message: Dict[str, Any],
):
    if message.get("subtype") == "message_changed":
        if message.get("message", {}).get("subtype") == "tombstone":
            deleted_ts = body["event"]["previous_message"]["ts"]
            timestamp = await get_mt_ts_cached(deleted_ts)
            if timestamp and context.channel_id:
                await client.chat_delete(ts=timestamp, channel=context.channel_id)
        else:
            is_edit = True
            if (
                message["message"].get("text")
                and f"<@{context['bot_user_id']}>" not in message["message"]["text"]
            ):
                # Do not auto-translate if the bot is mentioned (should default to normal response).
                await auto_translate_message(
                    client, context, message["message"], is_edit
                )


@app.view("evaluate_job", middleware=[ray_connection])
@slack_log_decorator
async def evaluate_job_submit(
    view: Optional[Dict[str, Any]],
    client: AsyncWebClient,
    ack: AsyncAck,
    context: RayContext,
):
    """Evaluate job. Triggered from the Evaluate form view."""
    await ack()
    try:
        if view:
            file_id = view["private_metadata"]
            form_data = view["state"]["values"]
            form = EvaluateJobForm.parse_slack(form_data)
            # call verify api to submit a file for evaluation
    except ValidationError as e:
        errors = convert_pydantic_to_slack_error(e)
        print(errors)
        await ack(response_action="errors", errors=errors)
        return
    await ack(response_action="clear")
    if await require_ray_client(context, prompt_login=True):
        input_file = await download_file(client=client, file_id=file_id, http=None)
        response = await submit_evaluation_job(
            context.ray.client, input_file, form.target_langs_uuid, form.reference
        )
        if response:
            # TODO use form to match spec
            msg = _(
                "You've successfully submitted your document for quality evaluation. Your documents will be AI Translated and you will be given a score."
            )
            await client.chat_postMessage(channel=context.user_id, text=msg)


@app.action("evaluate_job", middleware=[ray_connection])
@slack_log_decorator
async def evaluate_job_action(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    ack: AsyncAck,
    context: RayContext,
):
    """Evaluate job. Triggered from the Evaluate Job button."""
    await ack()
    file_id = action["value"] if "value" in action else ""
    file_info = await client.files_info(file=file_id)
    file_path, file_extension = os.path.splitext(file_info["file"]["name"])
    is_valid_file_type = supported_file_types(file_extension)
    if is_valid_file_type:
        await client.views_open(
            trigger_id=body["trigger_id"],
            view=evaluate_job_modal(file_id),
        )
    else:
        msg = _(
            "This file type is currently not supported. Please check the help docs."
        )
        # Add your code here
        await client.chat_postMessage(
            channel=context["user_id"],
            text=msg,
        )


@app.action("verify_job_modal_open", middleware=[ray_connection])
@slack_log_decorator
async def verify_job_modal_open_action(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
    ack: AsyncAck,
):
    """Open modal for human verification. Triggered from the Send for human verification button."""
    await ack()
    job_uuid = action["value"]
    job = await get_client_evaluation_job(context.ray.client, job_uuid)
    all_langs = await _get_languages_cached()
    await client.views_open(
        trigger_id=body["trigger_id"], view=verify_job_modal(job["data"], all_langs)
    )


@app.view("verify_job")
async def handle_verify_job_submission(ack, body, client):
    await ack()

    # Extract the private metadata (job UUID)
    job_uuid = body["view"]["private_metadata"]

    # Extract the selected checkbox values
    # Example: Send a message with the selected values
    user_id = body["user"]["id"]
    msg = _(
            "This feature is yet to be implemented. Coming Soon!"
        )
    await client.chat_postMessage(
        channel=user_id,
        text=msg,
    )


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

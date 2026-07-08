"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.
"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Optional, cast

import httpx
from pydantic import ValidationError
from ray_sdk import RayAPIResponseError
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncRespond, AsyncSay
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.api.language_cloud import detect_language
from app.api.verify import (
    VerifyAPIError,
    download_verify_file,
    get_client_evaluation_job,
    get_evaluation_job_quote,
    get_job_pricing,
    proceed_evaluation_job,
    proceed_quality_evaluation,
)
from app.constants import (
    EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
    EVALUATE_SERVICE_AI_TRANSLATION,
    EVALUATE_SERVICE_QUALITY_EVALUATION,
    HUMAN_EVALUATION_WORKFLOW_UUID,
)
from app.models import SlackGroupSettingsTranslation
from app.ray.submissions import (
    check_and_record_submission_async,
)
from app.ray.utils import (
    download_from_file_server_async,
    is_ibm_enterprise,
    upload_to_file_server,
)
from app.saq_jobs import (
    enqueue_document_mt_quote_preflight,
    enqueue_evaluation_submission,
)
from app.slack.buglog_notifier import notify_exception, notify_message
from app.slack.utils import calculate_total_estimated_days
from app.transcriber_tasks.tasks import get_asr_task
from app.translate import _

from ..auth.connector import (
    RayContext,
    connect_ray_account_sso,
    disconnect_ray_account,
    disconnect_ray_super_group_and_users,
    get_all_tokens_for_enterprise,
    get_bot_token_async,
    get_group_quote_settings,
    get_ray_connection,
    get_token_for_team,
    is_slack_team_admin,
    resolve_channels_to_team,
)
from ..config import domains
from ..ray.settings import (
    delete_channel_id,
    disable_auto_translate_group_settings,
    get_auto_translate_settings_and_langs,
    update_auto_translate_group_settings,
    update_channel_id,
)
from ..redis import is_duplicate_event, redis_conn
from .app import app
from .bot_translation import mark_channel_source_deleted
from .document_mt_quote_actions import (
    accept_document_mt_quote,
    cancel_document_mt_quote,
)
from .document_mt_quotes import new_document_mt_quote_id
from .evaluation_combined_quotes import (
    COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID,
    PRE_QE_QUOTE_DISPLAY,
    WORST_CASE_QE_QUALITY_TIER,
    ai_translation_target_file_uuids,
    combined_human_job_quote_message,
    job_file_uuids,
    job_target_language_uuids,
    qe_additional_cost,
    unsubmitted_human_translation_targets,
)
from .evaluation_quotes import (
    AI_TERMINAL_STAGES,
    QE_TERMINAL_STAGES,
    STAGE_ACCEPTED_AI,
    STAGE_ACCEPTED_QE,
    STAGE_AWAITING_AI,
    STAGE_AWAITING_QE,
    STAGE_PROCESSING_AI,
    STAGE_PROCESSING_QE,
    get_evaluate_quote_session,
    save_evaluate_quote_session,
    update_evaluate_quote_slack_message,
    update_evaluate_quote_stage,
)
from .file_submissions import (
    slack_file_submission_payload,
    slack_file_submission_payload_from_option,
)
from .language_validation import get_conflicting_target_language_labels
from .listener_actions import (
    VERIFY_JOB_SUBMISSION_LOCK_TTL_SECONDS,
    ai_translate_help,
    approve_pending_client,
    auto_translate_message,
    cancel_job_process,
    document_machine_translate,
    document_mt_selected_languages,
    get_accessible_slack_files,
    get_groups,
    get_mt_translation,
    is_slack_file_not_found,
    is_srt_file,
    maybe_show_thread_media_embed_option,
    notify_missing_slack_files,
    post_batch_list,
    post_file_list,
    post_job_details,
    post_job_list,
    post_job_status,
    post_job_summary,
    resolve_media_thread_ts,
    respond_to_message,
    send_translation_success_message,
    submit_existing_srt_embed_task,
    submit_job,
    submit_verification_job,
    update_human_job_quote_message,
    verify_help,
    verify_job_submission_lock_key,
)
from .logging import slack_log_decorator
from .middleware import (
    populate_ray_connection,
    ray_connection,
    require_mt_tokens,
    require_ray_client,
)
from .modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from .pdf_evaluate_quotes import (
    PDF_EVALUATE_QUOTE_ACTION_ID,
    get_pdf_evaluate_quote_session,
    update_pdf_evaluate_quote_message,
    update_pdf_evaluate_quote_session,
)
from .pdf_evaluate_quotes import (
    STAGE_ACCEPTED as PDF_PREQUOTE_STAGE_ACCEPTED,
)
from .pdf_evaluate_quotes import (
    STAGE_AWAITING_ACCEPT as PDF_PREQUOTE_STAGE_AWAITING_ACCEPT,
)
from .pdf_evaluate_quotes import (
    STAGE_PROCESSING_ACCEPT as PDF_PREQUOTE_STAGE_PROCESSING_ACCEPT,
)
from .select_options import (
    get_file_options_cached,
    get_language_options,
)
from .templates.messages import (
    AutoTranslateSettingsChangedMessage,
    AutoTranslateSettingsDisabledMessage,
    ClientAlreadyApprovedMessage,
    ClientApprovedMessage,
    ConnectionInfoMessage,
    HelpMessage,
    HumanJobMessage,
    InfoMessage,
    InvalidCommandMessage,
    JobCreationMessage,
    JobDelayMessage,
    JobSubmitMessage,
    LoginMessage,
    LogoutMessage,
    NewJobMessage,
    OnboardingMessage,
    QuoteMessage,
    SlackMessage,
    SsoConnectionInfoMessage,
    SuccessfulLoginMessage,
    SuccessfulLogoutMessage,
    WelcomeBackMessage,
)
from .templates.models import (
    EVALUATE_JOB_REFERENCE_FALLBACK,
    AutoTranslationSettingsForm,
    EvaluateJobForm,
    JobSearchForm,
    NewJobForm,
    convert_pydantic_to_slack_error,
)
from .templates.views import (
    cancel_job_modal,
    document_mt_job_modal,
    home_view,
    human_job_modal,
    job_search_modal,
    srt_translate_modal,
    translation_settings_view,
    verify_job_modal,
    verify_quote_summary_modal,
)
from .utils import (
    extract_language_codes_from_form,
    is_channel_im,
)
from .web import (
    download_file,
    files_list_simple,
    get_mt_ts_cached,
    upload_file_to_slack_memory_efficient,
)

# ---------------------------------------------------------
# Set up Slack listeners here.
# ---------------------------------------------------------


@app.event(
    {
        "type": "message",
        "subtype": (None, "message_replied", "file_share", "bot_message"),
    },
    middleware=[ray_connection],
)
@slack_log_decorator
async def message_event(
    client: AsyncWebClient,
    context: RayContext,
    message: Dict[str, Any],
    body: Dict[str, Any],
):
    # Check for duplicate events
    if context.enterprise_id and await is_duplicate_event(
        context.enterprise_id, "message", message.get("ts")
    ):
        return

    # https://api.slack.com/events/message
    # Respond to messages without threads in 1-on-1 DMs with the bot only,
    # use threads in channels or group conversations (see the "app_mention" event).
    channel_id = context.get("channel_id")
    is_direct_message = message.get("channel_type") == "im" or is_channel_im(channel_id)
    bot_user_id = context.get("bot_user_id")
    text = message.get("text")

    if context.is_bot and is_direct_message:
        return

    if (
        not context.is_bot
        and message.get("thread_ts")
        and message.get("files")
        and any(is_srt_file(file) for file in message.get("files", []))
    ):
        handled = await maybe_show_thread_media_embed_option(client, context, message)
        if handled:
            return
    elif not context.is_bot and is_direct_message:
        # extract team id from body
        body_team_id = body.get("event", {}).get("team")
        if body_team_id:
            token = await get_bot_token_async(
                team_id=body_team_id,
                enterprise_id=context.enterprise_id,
            )
            if token:
                if token != client.token:
                    client.token = token
        await respond_to_message(client, context, message, use_thread=False)
    elif (
        text
        and (not bot_user_id or f"<@{bot_user_id}>" not in text)
        and message.get("user") != bot_user_id
    ):
        # Do not auto-translate if this app is mentioned or posted the message.
        await auto_translate_message(client, context, message)
    else:
        # Do nothing if the Slack app is not mentioned in group chats and
        # auto-translate is disabled.
        pass


# chhanel deletion
@app.event("channel_deleted")
@slack_log_decorator
async def channel_deleted_event(
    client: AsyncWebClient, context: RayContext, event: Dict[str, Any]
):
    await delete_channel_id(event.get("channel"))


@app.event("app_mention", middleware=[ray_connection])
@slack_log_decorator
async def app_mention_event(
    client: AsyncWebClient, context: RayContext, event: Dict[str, Any]
):
    # Check for duplicate events
    if context.enterprise_id and await is_duplicate_event(
        context.enterprise_id, "app_mention", str(event.get("ts"))
    ):
        return

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
    ack: AsyncAck,
):
    # https://api.slack.com/events/app_home_opened
    # Send an onboarding message if the app home is opened for the first time.
    # TODO: put try catch around this
    await ack()
    try:
        channel = event.get("channel")
        if isinstance(channel, str):
            history = await client.conversations_history(channel=channel, limit=1)
            is_ibm = is_ibm_enterprise(enterprise_id=context.enterprise_id)
            if not history.get("messages"):
                message: SlackMessage = OnboardingMessage(
                    context["user_id"],
                    context["team_id"],
                    context.enterprise_id,
                    channel,
                    not is_ibm,
                )
                await say(blocks=message.blocks, text=message.text)
            # Send a welcome message if the app home has been idle for 24 hours
            else:
                history_last_24_hours = await client.conversations_history(
                    channel=channel,
                    oldest=str((datetime.now() - timedelta(hours=24)).timestamp()),
                    latest=str(datetime.now().timestamp()),
                )
                if not history_last_24_hours.get("messages"):
                    message = WelcomeBackMessage(
                        context["user_id"], context["ray"], context.enterprise_id
                    )
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
    ack: AsyncAck,
):
    await ack()
    # submit from next button on transation settings view
    assert action is not None
    home_info = json.loads(action["value"]) if isinstance(action["value"], str) else {}
    page = int(home_info.get("page", 1))
    team_id = home_info.get("team_id", "")
    if team_id:
        token = await get_token_for_team(team_id)
        if token:
            client.token = token
        context["team_id"] = team_id
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
    old_channel_id = event.get("old_channel_id")
    new_channel_id = event.get("new_channel_id")

    if old_channel_id and new_channel_id:
        await update_channel_id(old_channel_id, new_channel_id)


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
        # check if the message has files
        if shortcut and shortcut["message"] and shortcut["message"].get("files"):
            new_job_msg = NewJobMessage(
                context["channel_id"],
                shortcut["message"]["ts"],
                shortcut["message"].get("files", []),
                context.ray.super_group[0].enable_verify_in_slack
                if context.ray and context.ray.super_group
                else False,
                is_ibm_enterprise(context.enterprise_id),
            )
            await context.say(
                text=new_job_msg.text,
                blocks=new_job_msg.blocks,
                thread_ts=shortcut["message"]["ts"],
            )


@app.action("show_srt_translate_form")
@slack_log_decorator
async def show_srt_translate_form(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    assert action is not None
    task_uuid = action["value"]
    channel_id = context.get("channel_id") or context["user_id"]
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client, view_id, srt_translate_modal(task_uuid, channel_id)
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


@app.action("document_mt_job")
@slack_log_decorator
async def document_mt_job_action(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    assert action is not None
    action_data = json.loads(action.get("value", ""))
    files = action_data.get("files", [])
    channel_id = action_data.get("channel_id")
    if not files:
        await populate_ray_connection(context)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("No files found in the message. Please upload files to translate."),
        )
        return

    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, allow_org_billing=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client, view_id, document_mt_job_modal(channel_id, files)
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


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
    if await require_ray_client(context, allow_org_billing=True):
        assert action is not None
        slack_file_ids = json.loads(action["value"])
        selected_language = await redis_conn.get(f"output_file_{action['value']}")
        selected_languages = document_mt_selected_languages(selected_language)
        # get uuid from output_file
        if await require_mt_tokens(context, 1):
            # get selected language from redis keyed on output_file
            # selected from get_auto_translate_language_options
            for slack_file_id in slack_file_ids:
                if selected_languages:
                    try:
                        input_file = await download_file(
                            client=client, file_id=slack_file_id, http=None
                        )
                    except SlackApiError as e:
                        if is_slack_file_not_found(e):
                            await notify_missing_slack_files(
                                client,
                                context["user_id"],
                                [{"id": slack_file_id, "title": slack_file_id}],
                            )
                            continue
                        raise
                    input_file_id = await upload_to_file_server(input_file)
                    # Dedupe check and record in DB
                    submitted_languages: list[str] = []
                    submission_ids: dict[str, int] = {}
                    for selected_language_code in selected_languages:
                        is_dup, _record = await check_and_record_submission_async(
                            path=input_file,
                            file_name=os.path.basename(input_file),
                            file_id=input_file_id,
                            user_id=context["user_id"],
                            team_id=context["team_id"],
                            channel_id=context.get("channel_id", context["user_id"]),
                            target_language=selected_language_code,
                        )
                        if is_dup:
                            await say(
                                _(
                                    f"This file has already been submitted for {selected_language_code}. Skipping duplicate."
                                )
                            )
                            continue
                        submitted_languages.append(selected_language_code)
                        submission_ids[selected_language_code] = _record.id

                    if submitted_languages:
                        await document_machine_translate(
                            context,
                            input_file_id,
                            None,
                            submitted_languages,
                            submission_ids,
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
        assert context["ray"] is not None
        assert context["ray"].client is not None
        assert action is not None
        task_uuid = action["value"]
        task_result = await get_asr_task(task_uuid)
        assert task_result is not None
        file_id = task_result.file_id
        assert file_id is not None, "ASR task has no file_id"
        file = await download_from_file_server_async(file_id)

        try:
            # Upload file to Slack using memory-efficient method
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file["file"],
                channel_id=context["channel_id"],
                title=file["file_name"],
                filename=file["file_name"],
            )
        finally:
            # Clean up the temporary file
            if os.path.exists(file["file"]):
                os.unlink(file["file"])


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
        assert action is not None
        file_uuid = action["value"]
        assert context["ray"] is not None
        assert context["ray"].client is not None
        file = await download_verify_file(context["ray"].client, file_uuid)

        try:
            # Upload file to Slack using memory-efficient method
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file["file"],
                channel_id=context["channel_id"],
                title=file["file_name"],
                filename=file["file_name"],
            )
        finally:
            # Clean up the temporary file
            if os.path.exists(file["file"]):
                os.unlink(file["file"])


@app.block_action("download_ai_translations_action", middleware=[ray_connection])
@slack_log_decorator
async def download_ai_translations_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    await ack()
    if not await require_ray_client(context):
        return

    assert action is not None
    assert context["ray"] is not None
    assert context["ray"].client is not None

    job_uuid = action["value"]
    job = await get_client_evaluation_job(context["ray"].client, job_uuid)
    target_file_uuids = ai_translation_target_file_uuids(job["data"])
    channel_id = context.get("channel_id") or body.get("channel", {}).get("id")
    if not channel_id:
        raise AssertionError("No channel to upload AI translations to")
    thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get(
        "ts"
    )

    if not target_file_uuids:
        await client.chat_postMessage(
            channel=channel_id,
            text=_("No AI translation files are available to download yet."),
            thread_ts=thread_ts,
        )
        return

    await client.chat_postMessage(
        channel=channel_id,
        text=_("Uploading your AI translation files to this thread."),
        thread_ts=thread_ts,
    )

    for file_uuid in target_file_uuids:
        file = await download_verify_file(context["ray"].client, file_uuid)
        try:
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file["file"],
                channel_id=channel_id,
                title=file["file_name"],
                filename=file["file_name"],
                thread_ts=thread_ts,
            )
        finally:
            if os.path.exists(file["file"]):
                os.unlink(file["file"])


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
    mt_text = body["message"].get("text", "").strip()

    # Check if the message has any text content to translate
    if not mt_text:
        error_msg = _(
            "The selected message doesn't contain any text to translate. "
            "Please select a message with text content."
        )
        if context.response_url and context.respond:
            await context.respond(text=error_msg)
        else:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=error_msg,
            )
        return

    # Truncate text to 5000 chars for detect_language API limit
    text_for_detection = mt_text[:5000] if len(mt_text) > 5000 else mt_text
    source_lang = await detect_language(context, text_for_detection)
    await get_mt_translation(
        client,
        context,
        source_lang=source_lang.language,
        target_lang=mt_tl,
        sentence=mt_text,
        usage_type="shortcut_translate",
    )


@app.view("srt_translate", middleware=[ray_connection])
@slack_log_decorator
async def srt_translate_action(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Handle SRT translation modal submission."""
    try:
        await ack(response_action="clear")
        if await require_ray_client(context):
            assert context["ray"] is not None
            assert context["ray"].client is not None
            assert view is not None

            # Get task_uuid and channel_id from private_metadata
            private_metadata = view.get("private_metadata", "")
            if not private_metadata:
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=_("An error occurred: task UUID not found."),
                )
                return

            # Parse private_metadata: format is "task_uuid|channel_id" or just "task_uuid" for backwards compatibility
            metadata_parts = private_metadata.split("|")
            task_uuid = metadata_parts[0]
            channel_id = metadata_parts[1] if len(metadata_parts) > 1 else None

            # Set channel_id in context (same pattern as document_mt_job)
            context["channel_id"] = channel_id or context["user_id"]

            # Get selected languages from the form
            form_data = view.get("state", {}).get("values", {})
            selected_languages = extract_language_codes_from_form(form_data)

            if not selected_languages:
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=_("Please select at least one language to translate to."),
                )
                return

            # Get file_id from task result
            task_result = await get_asr_task(task_uuid)
            assert task_result is not None
            file_id = task_result.file_id
            assert file_id is not None, f"Task {task_uuid} has no file_id"

            # Create translation job for each selected language
            for selected_language in selected_languages:
                await document_machine_translate(
                    context,
                    file_id,
                    None,
                    cast(str, selected_language),
                    0,  # submission_id - not available in this context
                )

            # Send success message
            await send_translation_success_message(
                client, context["user_id"], selected_languages
            )
    except Exception as exc:
        notify_exception(exc, "Error in srt_translate_action")
        # Respond to user with error, if possible
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "An error occurred while processing your translation request. Please try again or contact support."
            ),
        )


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
                    await connect_ray_account_sso(
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

                    msg: SlackMessage = SuccessfulLoginMessage(
                        context["user_id"],
                        user_info["profile"]["email"],
                        context["ray"],
                        context.enterprise_id,
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
                # check if respond is available
                if context.response_url:
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


@app.block_action("job_search")
@slack_log_decorator
async def job_search_action(
    ack: AsyncAck,
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    await ack()
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, variation=LoginMessage.NEW_JOB):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await safe_views_update(
            client,
            view_id,
            job_search_modal(context["ray"].client.username),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


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

    assert command is not None
    command_formatted = strip_formatting(command.get("text", "").strip())
    command_args = re.split(r"\s+", command_formatted.lower())
    command_args = [strip_formatting(arg) for arg in command_args]

    # Use match to handle different command arguments.
    match command_args:
        case ["info" | "account"]:
            # Get connection info and respond with message.
            msg: SlackMessage = ConnectionInfoMessage(
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
                assert context["ray"] is not None
                assert context["ray"].client is not None
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
                    assert context.channel_id is not None
                    view_id = await open_loading_modal(client, command["trigger_id"])
                    try:
                        settings = await get_auto_translate_settings_and_langs(
                            context, context.channel_id
                        )
                        auto_translate_langs = [
                            setting["target_lang"] for setting in settings
                        ]
                        await safe_views_update(
                            client,
                            view_id,
                            translation_settings_view(
                                [context.channel_id],
                                auto_translate_langs,
                                cast(
                                    SlackGroupSettingsTranslation.DisplayFormatType,
                                    settings[0].get("display_format", "thread")
                                    if settings
                                    else "thread",
                                ),
                            ),
                        )
                    except Exception as e:
                        notify_exception(e)
                        await safe_views_update(client, view_id, request_error_modal())
                else:
                    url_doc = "https://help.straker.ai/en/docs/how-to-use-channel-translations"
                    text_help = "help docs"
                    text = _(f"Please check the <{url_doc}|{text_help}>.")
                    await client.chat_postMessage(
                        channel=context["channel_id"],
                        text=text,
                    )

        case ["job", reference, *reference_other]:
            # Get job status or list of jobs.
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                assert context["ray"] is not None
                assert context["ray"].client is not None
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
                assert context["ray"] is not None
                assert context["ray"].client is not None
                await post_job_summary(client, context, context["ray"].client)
        case ["quote"] | ["new"]:
            # Show quote message.
            if await require_ray_client(
                context, variation=LoginMessage.QUALITY_EVALUATION
            ):
                # quote is like new job except it doesn't open the modal.
                await ack()
                quote_msg = QuoteMessage()
                await respond(text=quote_msg.text, blocks=quote_msg.blocks)

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
                    assert context["ray"] is not None
                    assert context["ray"].client is not None
                    await post_job_status(
                        client, context, context["ray"].client, command_text
                    )
            else:
                await respond(text=InvalidCommandMessage().text)

        case _:
            # Invalid command.
            await respond(text=InvalidCommandMessage().text)


@app.block_action("settings_auto_translate")
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
    team_id = channel_info.get("team_id", "")
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        settings = await get_auto_translate_settings_and_langs(
            context, channel_id, team_id
        )
        auto_translate_langs = [setting["target_lang"] for setting in settings]
        await safe_views_update(
            client,
            view_id,
            translation_settings_view(
                [channel_id] if channel_id else None,
                auto_translate_langs,
                cast(
                    SlackGroupSettingsTranslation.DisplayFormatType,
                    settings[0].get("display_format", "thread")
                    if settings
                    else "thread",
                ),
                team_id,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


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
        team_id = channel_info.get("team_id", "")
        await disable_auto_translate_group_settings(context, channel_id)
        context["team_id"] = team_id
        team_channel = await resolve_channels_to_team(
            channel_id, client, context.enterprise_id, team_id
        )
        if isinstance(team_channel["bot_token"], str):
            client.token = str(team_channel["bot_token"])
        else:
            notify_message("Bot token not found in team channel", extra=team_channel)
            return
        await client.views_publish(
            user_id=context["user_id"],
            view=await home_view(context, body["api_app_id"], context.get("ray")),
        )

        if not channel_id:
            notify_message("Channel ID not found in payload", extra=payload)
            return
        await ack()
        if team_id:
            context["team_id"] = team_id

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
        assert context["ray"] is not None
        assert context["ray"].client is not None
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
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await post_job_summary(client, context, context["ray"].client)


@app.block_action("all_summary", middleware=[ray_connection])
@slack_log_decorator
async def all_summary(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await post_job_summary(
            client, context=context, ray_client=context["ray"].client, all_jobs=True
        )


@app.action("ai_translate_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_ai_translate_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    """Get ai translate help link. Triggered from the Home AI Translate help button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await ai_translate_help(client, context, context["ray"].client)


@app.action("verify_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_verify_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    """Get verify help link. Triggered from the Home Verify help button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.QUALITY_EVALUATION):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await verify_help(client, context, context["ray"].client)


@app.action("human_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_human_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    """Get verify help link. Triggered from the Home Verify help button"""
    await ack()
    if await require_ray_client(context, variation=LoginMessage.HUMAN_TRANSLATION):
        msg = HumanJobMessage()
        if context.response_url and context.respond:
            await context.respond(
                text=msg.text,
                blocks=msg.blocks,
                replace_original=False,
            )
        else:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=msg.text,
                blocks=msg.blocks,
            )


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
        assert context["ray"] is not None
        assert context["ray"].client is not None
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
        assert context["ray"] is not None
        assert context["ray"].client is not None
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


# The "Account Info" button short cut
@app.block_action("account_info", middleware=[ray_connection])
@slack_log_decorator
async def get_account_info(ack: AsyncAck, context: RayContext, respond: AsyncRespond):
    await ack()
    if await require_ray_client(context):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        msg = InfoMessage(
            ray_client=context["ray"].client,
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
            is_ibm=is_ibm_enterprise(context.enterprise_id),
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
        assert context["ray"] is not None
        assert context["ray"].client is not None
        try:
            assert action is not None
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
@slack_log_decorator
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
    assert context["ray"] is not None
    assert context["ray"].client is not None
    if action is not None:
        username = action.get("value") if isinstance(action.get("value"), str) else None
        msg: SlackMessage = SuccessfulLogoutMessage(
            context.user_id, context["ray"].client.sso, username
        )
    else:
        msg = SuccessfulLogoutMessage(context.user_id, context["ray"].client.sso)
    await respond(text=msg.text, blocks=msg.blocks, replace_original=True)


@app.block_action("delete_ephemeral_message")
@slack_log_decorator
async def delete_ephemeral_message(ack: AsyncAck, respond: AsyncRespond):
    await ack()
    await respond(delete_original=True)


@app.block_action(re.compile(r"link(_\d+)?|login"))
@slack_log_decorator
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
            assert context["ray"] is not None
            assert context["ray"].client is not None
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
                    job_msg = JobCreationMessage(result["job_id"], False)
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=job_msg.text,
                        blocks=job_msg.blocks,
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
        assert view is not None
        assert context.team_id is not None
        # get team_id from private_metadata
        team_id = view.get("private_metadata", "")
        form_data = view.get("state", {}).get("values") if view else {}
        form = AutoTranslationSettingsForm.parse_slack(form_data)
        team_channels: list[dict[str, bool | str | None]] = []
        await ack(response_action="clear")
        for channel in form.channels:
            try:
                team_channels.append(
                    await resolve_channels_to_team(
                        channel, client, context.enterprise_id, context.team_id
                    )
                )
            except SlackApiError as e:
                if e.response["error"] == "channel_not_found":
                    error_msg = _(
                        "Channel not found when creating channel translation setting. Please /invite @Straker to the channel <#{channel}> and recreate the setting."
                    )
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=error_msg,
                    )

                if e.response["error"] == "missing_scope":
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=_("Reinstall the app"),
                    )
                return
    except ValidationError as e:
        errors = convert_pydantic_to_slack_error(e)
        await ack(response_action="errors", errors=errors)
        return
    try:
        if not form.languages:
            for channel_list in team_channels:
                if channel_list:  # Check if the list is not empty
                    channel_id = channel_list["channel_id"]
                    if isinstance(channel_id, str):
                        await disable_auto_translate_group_settings(context, channel_id)
        else:
            # Filter team_channels to include channel info for storage
            filtered_channels = [
                {
                    "channel_id": str(channel["channel_id"]),
                    "team_id": str(channel["team_id"]),
                    "name": channel.get("name"),
                    "is_private": channel.get("is_private", False),
                }
                for channel in team_channels
                if channel.get("channel_id") and channel.get("team_id")
            ]
            await update_auto_translate_group_settings(
                context,
                channels=filtered_channels,
                languages=form.languages,
                display_format=form.display_format,
            )
        team_token = await get_token_for_team(team_id) if team_id else None
        if team_token:
            client.token = team_token
        context["team_id"] = team_id
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
                client.token = bot_token
                if form.languages:
                    msg = AutoTranslateSettingsChangedMessage(
                        context["user_id"],
                        channel_id,
                        form.languages,
                        form.display_format,
                    )
                    await client.chat_postMessage(channel=channel_id, text=msg.text)
                else:
                    disabled_msg = AutoTranslateSettingsDisabledMessage(
                        context["user_id"], channel_id
                    )
                    await client.chat_postMessage(
                        channel=channel_id, text=disabled_msg.text
                    )
            except Exception as e:
                notify_exception(e)
                if context.enterprise_id:
                    all_tokens = await get_all_tokens_for_enterprise(
                        context.enterprise_id
                    )
                    if all_tokens:
                        for token in all_tokens:
                            client.token = token["bot_token"]
                            try:
                                await client.chat_postMessage(
                                    channel=channel_id, text=msg.text
                                )
                                break
                            except Exception as e:
                                pass

        for channel_list in team_channels:
            bot_token = channel_list["bot_token"]
            channel_id = channel_list["channel_id"]
            if isinstance(channel_id, str) and isinstance(bot_token, str):
                await join_channel(channel_id, bot_token)
                await notify_channel(channel_id, bot_token)

    except Exception as e:
        print(e)
        notify_exception(e)


@app.action("language_mt_options", middleware=[ray_connection])
async def language_mt_options_selected(ack: AsyncAck, body: Dict[str, Any]):
    # redis store the selected options keyed by output file
    await ack()
    file_id = body["actions"][0]["block_id"]
    # Handle both single select (selected_option) and multi select (selected_options)
    if "selected_options" in body["actions"][0]:
        # Multi-select: store as JSON array
        selected_languages = [
            option["value"] for option in body["actions"][0]["selected_options"]
        ]
        await redis_conn.set(f"output_file_{file_id}", json.dumps(selected_languages))
    elif "selected_option" in body["actions"][0]:
        # Single select: store as string for backward compatibility
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


@app.options("source_language_option_uuid", middleware=[ray_connection])
async def source_language_option_uuid(ack: AsyncAck, payload: Dict[str, Any]):
    options = await get_language_options(payload.get("value"), "uuid", source_only=True)
    await ack(options=options)


@app.options("group_options", middleware=[ray_connection])
async def group_options(ack: AsyncAck, context: RayContext):
    if await require_ray_client(context):
        assert context["ray"] is not None
        assert context["ray"].client is not None
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


@app.block_action("cancel_job")
@slack_log_decorator
async def cancel_job_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    await ack()
    # Non-modal cancel paths do not need trigger_id; load Ray first.
    if "value" in payload:
        job_info = json.loads(payload["value"])
        if job_info.get("job_action") in {"list", "submit"}:
            await populate_ray_connection(context)
            if not await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                return
            assert context["ray"] is not None
            assert context["ray"].client is not None
            if job_info.get("job_action") == "list":
                job_id = job_info["job_id"].split("TJ")[1]
                await cancel_job_process(
                    client, context, context["ray"].client, job_id=job_id
                )
            else:
                await cancel_job_process(
                    client, context, context["ray"].client, job_uuid=job_info["job_id"]
                )
            return

    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, variation=LoginMessage.NEW_JOB):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await safe_views_update(
            client, view_id, cancel_job_modal(context["ray"].client.username)
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


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
        assert context["ray"] is not None
        assert context["ray"].client is not None
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
        # Tombstone the source so an in-flight translation callback skips delivery.
        await mark_channel_source_deleted(deleted_ts)
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
    # Check for duplicate events
    if (
        context.enterprise_id
        and message.get("ts")
        and await is_duplicate_event(
            context.enterprise_id, "message", str(message.get("ts"))
        )
    ):
        return

    if message.get("subtype") == "message_changed":
        if message.get("message", {}).get("subtype") == "tombstone":
            deleted_ts = body["event"]["previous_message"]["ts"]
            # Tombstone the source so an in-flight translation callback skips delivery.
            await mark_channel_source_deleted(deleted_ts)
            timestamp = await get_mt_ts_cached(deleted_ts)
            if timestamp and context.channel_id:
                await client.chat_delete(ts=timestamp, channel=context.channel_id)
        else:
            is_edit = True
            if (
                message["message"].get("text")
                and f"<@{context['bot_user_id']}>" not in message["message"]["text"]
            ):
                # compare text of old message and new message
                old_message = body["event"]["previous_message"]
                if old_message["text"] != message["message"]["text"]:
                    await auto_translate_message(
                        client, context, message["message"], is_edit
                    )


async def _publish_pdf_evaluate_convert(
    ray_client,
    input_files: list[str],
    file_titles: list[str],
    target_langs_uuid: list[str],
    reference: str,
    channel_id: str,
    source_lang_uuid: str = "",
    workflow_uuid: str | None = None,
    job_notes: str = "",
    workflow_version: float = 3.0,
    docconverter_version: str = "m48",
    preaccepted_ai_translation_quote: bool = False,
    prequote_message_ts: str | None = None,
) -> None:
    """Upload files to GridFS and publish to the PDF conversion stream.

    The int-slack-verify-consumer will convert PDFs to DOCX and submit
    the evaluate job on behalf of the user.
    """
    file_ids = []
    for file_path in input_files:
        file_id = await upload_to_file_server(file_path)
        file_ids.append(file_id)

    payload = {
        "client_uuid": ray_client.id,
        "file_ids": file_ids,
        "file_names": file_titles,
        "target_languages_uuid": target_langs_uuid,
        "source_language_uuid": source_lang_uuid,
        "reference": reference,
        "workflow_uuid": workflow_uuid or "",
        "job_notes": job_notes,
        "workflow_version": workflow_version,
        "docconverter_version": docconverter_version,
        "channel_id": channel_id,
        "app_source": "slack",
        "confirmation_required": True,
        "preaccepted_ai_translation_quote": preaccepted_ai_translation_quote,
    }
    if prequote_message_ts:
        payload["prequote_message_ts"] = prequote_message_ts

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/slack:evaluate:pdf:convert",
            json={
                "data": payload,
                "source": "Straker Translate for Slack",
            },
        )


@app.view("evaluate_job", middleware=[ray_connection])
@app.view("evaluate_job_human", middleware=[ray_connection])
@slack_log_decorator
async def evaluate_job_submit(
    view: Optional[Dict[str, Any]],
    client: AsyncWebClient,
    ack: AsyncAck,
    context: RayContext,
):
    """Evaluate job. Triggered from the Evaluate form view."""
    if not view:
        await ack(response_action="clear")
        return
    channel_id = view.get("private_metadata")
    if not channel_id:
        await ack(response_action="clear")
        return
    if view.get("callback_id") == "evaluate_job" and is_ibm_enterprise(
        context.enterprise_id
    ):
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "Quality Evaluation is now available through the Human Translation quote flow, not as a standalone Slack submission."
            ),
        )
        return

    form_data = view["state"]["values"]
    try:
        form = EvaluateJobForm.parse_human_job_form(form_data, view["callback_id"])
    except ValidationError as e:
        errors = convert_pydantic_to_slack_error(e)
        field_to_block = {"target_langs_uuid": "target_langs"}
        errors = {field_to_block.get(k, k): v for k, v in errors.items()}
        await ack(response_action="errors", errors=errors)
        return

    conflicting_target_labels = await get_conflicting_target_language_labels(
        form.source_lang_uuid, form.target_langs_uuid
    )
    if conflicting_target_labels:
        languages = ", ".join(conflicting_target_labels)
        await ack(
            response_action="errors",
            errors={
                "target_langs": _(
                    "The source language cannot be the same language or regional variant as a target language. Please remove: {languages}."
                )
            },
        )
        return

    await ack(response_action="clear")
    if await require_ray_client(context, prompt_login=True):
        if view["callback_id"] == "evaluate_job":
            msg = _(
                "Analyzing your content. You will receive an AI Translation quote shortly."
            )
        elif form.workflow_options:
            msg = _(
                "Your request is being processed. You will receive a summary to review before you finalise the order."
            )
        else:
            msg = _(
                "You've successfully submitted your document(s) for quality evaluation."
            )
        try:
            file_payloads = [
                slack_file_submission_payload(
                    file_id=file.id,
                    title=file.title,
                    size=file.size,
                )
                for file in form.files
            ]
            accessible_files, missing_files = await get_accessible_slack_files(
                client, file_payloads
            )
            if missing_files:
                await notify_missing_slack_files(
                    client, context["user_id"], missing_files
                )
            accessible_file_ids = {
                str(file["id"]) for file in accessible_files if file.get("id")
            }
            file_payloads = [
                file for file in file_payloads if str(file["id"]) in accessible_file_ids
            ]
            if not file_payloads:
                return

            verify_reference = (
                EVALUATE_JOB_REFERENCE_FALLBACK
                if view["callback_id"] == "evaluate_job_human"
                else form.reference
            )
            await client.chat_postMessage(channel=channel_id, text=msg)
            await enqueue_evaluation_submission(
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=channel_id,
                files=file_payloads,
                target_langs_uuid=form.target_langs_uuid,
                reference=verify_reference,
                source_lang_uuid=form.source_lang_uuid,
                workflow_uuid=form.workflow_options,
                job_notes=form.job_notes or "",
            )
        except VerifyAPIError as e:
            await client.chat_postMessage(
                channel=channel_id,
                text=_(
                    "There was an error processing your request. You do not have permission to perform this action. Please contact your team administrator."
                ),
            )
        except Exception as e:
            notify_exception(e)
            if form.workflow_options:
                error_msg = _(
                    "There was an error submitting your human translation request, please try again."
                )
            else:
                error_msg = _(
                    "There was an error submitting your quality evaluation request, please try again."
                )
            await client.chat_postMessage(
                channel=channel_id,
                text=error_msg,
            )


@app.action("evaluate_job")
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
    action_data = json.loads(action.get("value", ""))
    job_type = action_data.get("job_type", "evaluate")
    login_variation = (
        LoginMessage.HUMAN_TRANSLATION
        if job_type == "human"
        else LoginMessage.QUALITY_EVALUATION
    )
    files = action_data.get("files", [])
    channel_id = action_data.get("channel_id")
    if not files:
        await populate_ray_connection(context)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "No files found in the message. Please try reupload files to translate."
            ),
        )
        return

    # Open before Ray middleware I/O — same pattern as RAY-72999 quote modals.
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, variation=login_variation):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        if job_type != "human" and is_ibm_enterprise(context.enterprise_id):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Quality Evaluation"),
                    _(
                        "Quality Evaluation is now available through the Human Translation quote flow, not as a standalone Slack submission."
                    ),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            human_job_modal(
                channel_id,
                files,
                is_ibm_enterprise(context.enterprise_id),
                job_type,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


@app.action("verify_job_modal_open")
@app.action("quote_summary_modal_open")
@slack_log_decorator
async def verify_job_modal_open_action(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
    ack: AsyncAck,
):
    """Open modal for human translation. Triggered from the Send for human translation button."""
    await ack()
    job_uuid = action["value"]
    message_ts = body.get("message", {}).get("ts")
    quote_channel_id = (
        body.get("channel", {}).get("id")
        or body.get("message", {}).get("channel")
        or context.get("channel_id")
    )
    # Open before Redis/Ray I/O — RAY-72999 loading-modal pattern.
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        lock_key = verify_job_submission_lock_key(job_uuid)
        if await redis_conn.get(lock_key):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Request in progress"),
                    _(
                        "A request is already in progress. Please try again in a few seconds."
                    ),
                ),
            )
            return
        if not await require_ray_client(context, prompt_login=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        assert context["ray"] is not None
        assert context["ray"].client is not None
        job = await get_client_evaluation_job(context["ray"].client, job_uuid)
        langs = [lang["uuid"] for lang in job["data"]["target_languages"]]
        session = await get_evaluate_quote_session(job_uuid)
        quote_snapshot = (session or {}).get("quote_snapshot") or {}
        is_combined_qe_human_quote = bool(
            quote_snapshot.get("auto_submit_human_job")
            and action["action_id"] == "quote_summary_modal_open"
        )
        qe_token_cost = int(quote_snapshot.get("token_cost") or 0)
        costs = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            [file["file_uuid"] for file in job["data"]["source_files"]],
            langs,
            assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER
            if is_combined_qe_human_quote
            else None,
        )
        final_view = (
            verify_quote_summary_modal(
                job["data"],
                costs["data"],
                message_ts,
                quote_channel_id,
                additional_costs=qe_additional_cost(qe_token_cost, costs["data"])
                if is_combined_qe_human_quote
                else None,
                metadata={
                    "combined_qe_human_quote": True,
                    "qe_token_cost": qe_token_cost,
                }
                if is_combined_qe_human_quote
                else None,
                show_quality_discount=not is_combined_qe_human_quote,
                show_savings=not is_combined_qe_human_quote,
                embed_additional_costs_in_line_price=is_combined_qe_human_quote,
            )
            if action["action_id"] == "quote_summary_modal_open"
            else verify_job_modal(
                job["data"], costs["data"], message_ts, quote_channel_id
            )
        )
        await safe_views_update(client, view_id, final_view)
    except VerifyAPIError:
        await safe_views_update(
            client,
            view_id,
            status_modal(
                _("Unauthorized"),
                _(
                    "You do not have permission to access this verification job. Please contact your team administrator."
                ),
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def _resolve_evaluate_quote_message_ts(
    body: Dict[str, Any], job_uuid: str
) -> str | None:
    message_ts = body.get("message", {}).get("ts")
    if message_ts:
        return message_ts
    session = await get_evaluate_quote_session(job_uuid)
    if session:
        stored_ts = session.get("message_ts")
        if isinstance(stored_ts, str):
            return stored_ts
    return None


async def _accept_evaluation_service_quote(
    *,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
    lock_key_prefix: str,
    service: str,
    service_label: str,
    accept_action_id: str,
    terminal_stages: frozenset[str],
    awaiting_stage: str,
    processing_stage: str,
    accepted_stage: str,
    include_pdf_fee: bool,
    accepted_message: str,
    insufficient_balance_message: str,
    generic_error_message: str,
    proceed: Callable[..., Awaitable[None]],
) -> None:
    """Accept a staged evaluate quote, updating the original Slack message in place."""
    job_uuid = action["value"]
    channel_id = context["channel_id"]
    message_ts = await _resolve_evaluate_quote_message_ts(body, job_uuid)
    context_enterprise_id = (
        context.get("enterprise_id") if hasattr(context, "get") else None
    ) or getattr(context, "enterprise_id", None)
    is_ibm = is_ibm_enterprise(context_enterprise_id)

    session = await get_evaluate_quote_session(job_uuid)
    if session and session.get("stage") in terminal_stages:
        return

    lock_key = f"{lock_key_prefix}_{job_uuid}"
    lock_acquired = await redis_conn.set(lock_key, "1", ex=300, nx=True)
    if not lock_acquired:
        if message_ts:
            quote_snapshot = (session or {}).get("quote_snapshot") or {}
            await update_evaluate_quote_slack_message(
                client,
                channel_id=channel_id,
                message_ts=message_ts,
                service_label=quote_snapshot.get("service_label", service_label),
                token_cost=int(quote_snapshot.get("token_cost", 0)),
                job_uuid=job_uuid,
                accept_action_id=accept_action_id,
                pdf_page_count=(session or {}).get("pdf_page_count"),
                pdf_tokens=quote_snapshot.get("pdf_tokens"),
                actions=False,
                status_message=_(
                    "A request is already in progress. Please wait a moment."
                ),
                is_ibm=is_ibm,
            )
        return

    base_token_cost = 0
    pdf_page_count: int | None = None
    pdf_tokens: int | None = None

    async def _refresh_quote_message(
        *,
        actions: bool,
        status_message: str | None,
    ) -> None:
        if not message_ts:
            return
        await update_evaluate_quote_slack_message(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            service_label=service_label,
            token_cost=base_token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
            pdf_tokens=pdf_tokens,
            actions=actions,
            status_message=status_message,
            is_ibm=is_ibm,
        )

    try:
        assert context["ray"] is not None
        assert context["ray"].client is not None

        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [service],
        )
        services_costs = quote.get("services_costs") or {}
        base_token_cost = int(services_costs.get(service, quote.get("token", 0)))
        total_token_cost = base_token_cost

        if include_pdf_fee:
            job = await get_client_evaluation_job(context["ray"].client, job_uuid)
            extra_info = job["data"].get("extra_info") or {}
            pdf_page_count = extra_info.get("pdf_page_count")
            if pdf_page_count:
                pdf_tokens = (
                    int(pdf_page_count) * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
                )
                total_token_cost += pdf_tokens

        await update_evaluate_quote_stage(job_uuid, processing_stage)
        await _refresh_quote_message(
            actions=False,
            status_message=_("Accepting quote..."),
        )

        await proceed(context["ray"].client, job_uuid, total_token_cost)

        await update_evaluate_quote_stage(job_uuid, accepted_stage)
        await _refresh_quote_message(
            actions=False,
            status_message=accepted_message,
        )
    except VerifyAPIError as e:
        await update_evaluate_quote_stage(job_uuid, awaiting_stage)
        if e.status_code == 402:
            await _refresh_quote_message(
                actions=False,
                status_message=insufficient_balance_message,
            )
        else:
            await _refresh_quote_message(
                actions=True,
                status_message=generic_error_message,
            )
        await redis_conn.delete(lock_key)
    except Exception as e:
        notify_exception(e)
        await update_evaluate_quote_stage(job_uuid, awaiting_stage)
        await _refresh_quote_message(
            actions=True,
            status_message=_(
                "There was an error processing your request. Please try again."
            ),
        )
        await redis_conn.delete(lock_key)


@app.action(PDF_EVALUATE_QUOTE_ACTION_ID, middleware=[ray_connection])
@slack_log_decorator
async def evaluation_pdf_prequote_accept_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept a pre-job PDF evaluate quote and start real processing."""
    await ack()
    quote_id = action["value"]
    session = await get_pdf_evaluate_quote_session(quote_id)
    if not session:
        return
    if session.get("stage") in {
        PDF_PREQUOTE_STAGE_PROCESSING_ACCEPT,
        PDF_PREQUOTE_STAGE_ACCEPTED,
    }:
        return

    lock_key = f"evaluate_pdf_prequote_accept_{quote_id}"
    lock_acquired = await redis_conn.set(lock_key, "1", ex=300, nx=True)
    if not lock_acquired:
        return

    channel_id = str(session.get("channel_id") or context.get("channel_id") or "")
    message_ts = session.get("message_ts") or body.get("message", {}).get("ts")
    ai_token_estimate = int(session.get("ai_token_estimate") or 0)
    pdf_page_count = int(session.get("pdf_page_count") or 0)
    context_enterprise_id = (
        context.get("enterprise_id") if hasattr(context, "get") else None
    ) or getattr(context, "enterprise_id", None)
    is_ibm = is_ibm_enterprise(session.get("enterprise_id") or context_enterprise_id)

    try:
        await update_pdf_evaluate_quote_session(
            quote_id,
            stage=PDF_PREQUOTE_STAGE_PROCESSING_ACCEPT,
        )
        if channel_id and message_ts:
            await update_pdf_evaluate_quote_message(
                client,
                channel_id=channel_id,
                message_ts=message_ts,
                quote_id=quote_id,
                ai_token_estimate=ai_token_estimate,
                pdf_page_count=pdf_page_count,
                actions=False,
                status_message=_(
                    "Quote accepted. Converting your PDF and preparing the AI Translation job..."
                ),
                is_ibm=is_ibm,
            )

        await enqueue_evaluation_submission(
            user_id=str(session["user_id"]),
            team_id=str(session["team_id"]),
            enterprise_id=session.get("enterprise_id"),
            channel_id=channel_id,
            files=session["files"],
            target_langs_uuid=session["target_langs_uuid"],
            reference=str(session["reference"]),
            source_lang_uuid=str(session["source_lang_uuid"]),
            workflow_uuid=session.get("workflow_uuid"),
            job_notes=str(session.get("job_notes") or ""),
            preaccepted_ai_translation_quote=True,
            prequote_message_ts=str(message_ts) if message_ts else None,
        )
        await update_pdf_evaluate_quote_session(
            quote_id,
            stage=PDF_PREQUOTE_STAGE_ACCEPTED,
        )
    except Exception as e:
        notify_exception(e)
        await update_pdf_evaluate_quote_session(
            quote_id,
            stage=PDF_PREQUOTE_STAGE_AWAITING_ACCEPT,
        )
        if channel_id and message_ts:
            await update_pdf_evaluate_quote_message(
                client,
                channel_id=channel_id,
                message_ts=message_ts,
                quote_id=quote_id,
                ai_token_estimate=ai_token_estimate,
                pdf_page_count=pdf_page_count,
                actions=True,
                status_message=_(
                    "There was an error accepting your quote. Please try again."
                ),
                is_ibm=is_ibm,
            )
        await redis_conn.delete(lock_key)


@app.action("evaluation_ai_quote_accept", middleware=[ray_connection])
@slack_log_decorator
async def evaluation_ai_quote_accept_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept the AI Translation quote for a staged evaluate submission."""
    await ack()

    async def proceed(ray_client, job_uuid: str, token_cost: int) -> None:
        await proceed_evaluation_job(
            ray_client,
            job_uuid,
            token_cost=token_cost,
            skip_quality_evaluation=True,
        )

    await _accept_evaluation_service_quote(
        client=client,
        body=body,
        action=action,
        context=context,
        lock_key_prefix="evaluate_quote_accept",
        service=EVALUATE_SERVICE_AI_TRANSLATION,
        service_label=_("AI Translation"),
        accept_action_id="evaluation_ai_quote_accept",
        terminal_stages=AI_TERMINAL_STAGES,
        awaiting_stage=STAGE_AWAITING_AI,
        processing_stage=STAGE_PROCESSING_AI,
        accepted_stage=STAGE_ACCEPTED_AI,
        include_pdf_fee=True,
        accepted_message=_(
            "Your AI Translation quote has been accepted. Processing will begin shortly."
        ),
        insufficient_balance_message=_(
            "Insufficient AI token balance to accept this quote."
        ),
        generic_error_message=_(
            "There was an error accepting your quote. Please try again or contact your administrator."
        ),
        proceed=proceed,
    )


@app.action(COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID, middleware=[ray_connection])
@slack_log_decorator
async def evaluation_qe_human_quote_accept_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept the combined Quality Evaluation + Human Translation quote."""
    await ack()

    job_uuid = action["value"]
    channel_id = context["channel_id"]
    message_ts = await _resolve_evaluate_quote_message_ts(body, job_uuid)
    session = await get_evaluate_quote_session(job_uuid)
    if session and session.get("stage") in QE_TERMINAL_STAGES:
        return

    lock_key = f"evaluate_qe_human_quote_accept_{job_uuid}"
    lock_acquired = await redis_conn.set(lock_key, "1", ex=300, nx=True)
    if not lock_acquired:
        return

    qe_token_cost = 0
    job_data: dict[str, Any] | None = None
    costs: list[dict[str, Any]] = []

    async def _refresh_combined_quote(
        *,
        actions: bool,
        status_message: str | None,
    ) -> None:
        if not message_ts or job_data is None:
            return
        message = combined_human_job_quote_message(
            job_data,
            costs,
            qe_token_cost=qe_token_cost,
            actions=actions,
            status_message=status_message,
            allow_adjust=actions,
            download_translations_job_uuid=job_data["uuid"],
            **PRE_QE_QUOTE_DISPLAY,
        )
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=message.text,
            blocks=message.blocks,
        )

    try:
        assert context["ray"] is not None
        assert context["ray"].client is not None

        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [EVALUATE_SERVICE_QUALITY_EVALUATION],
        )
        services_costs = quote.get("services_costs") or {}
        qe_token_cost = int(
            services_costs.get(
                EVALUATE_SERVICE_QUALITY_EVALUATION, quote.get("token", 0)
            )
        )
        job = await get_client_evaluation_job(context["ray"].client, job_uuid)
        job_data = job["data"]
        pricing = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            job_file_uuids(job_data),
            job_target_language_uuids(job_data),
            assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER,
        )
        costs = pricing["data"]

        await update_evaluate_quote_stage(job_uuid, STAGE_PROCESSING_QE)
        await _refresh_combined_quote(
            actions=False,
            status_message=_("Accepting quote..."),
        )

        await proceed_quality_evaluation(
            context["ray"].client,
            job_uuid,
            token_cost=qe_token_cost,
            human_translation_file_and_languages=unsubmitted_human_translation_targets(
                job_data
            ),
        )

        await update_evaluate_quote_stage(job_uuid, STAGE_ACCEPTED_QE)
        await _refresh_combined_quote(
            actions=False,
            status_message=_(
                "Quote accepted! Submitting for human translation and "
                "calculating your final discount with Arbitr..."
            ),
        )
    except VerifyAPIError as e:
        await update_evaluate_quote_stage(job_uuid, STAGE_AWAITING_QE)
        if e.status_code == 402:
            await _refresh_combined_quote(
                actions=False,
                status_message=_("Insufficient AI token balance to accept this quote."),
            )
        else:
            await _refresh_combined_quote(
                actions=True,
                status_message=_(
                    "There was an error accepting your quote. Please try again or contact your administrator."
                ),
            )
        await redis_conn.delete(lock_key)
    except Exception as e:
        notify_exception(e)
        await update_evaluate_quote_stage(job_uuid, STAGE_AWAITING_QE)
        await _refresh_combined_quote(
            actions=True,
            status_message=_(
                "There was an error processing your request. Please try again."
            ),
        )
        await redis_conn.delete(lock_key)


@app.action("quote_accept_all", middleware=[ray_connection])
@slack_log_decorator
async def quote_accept_all_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept all available language/file combinations for verification."""
    await ack()
    timestamp = body.get("message", {}).get("ts")
    job_uuid = action["value"]
    channel_id = context["channel_id"]
    lock_key = verify_job_submission_lock_key(job_uuid)

    lock_acquired = await redis_conn.set(
        lock_key,
        "1",
        ex=VERIFY_JOB_SUBMISSION_LOCK_TTL_SECONDS,
        nx=True,
    )
    if not lock_acquired:
        return

    try:
        assert context["ray"] is not None
        assert context["ray"].client is not None
        job = await get_client_evaluation_job(context["ray"].client, job_uuid)
    except VerifyAPIError as e:
        await redis_conn.delete(lock_key)
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "You do not have permission to access this verification job. You do not have permission to perform this action. Please contact your team administrator."
            ),
        )
        return
    except Exception as e:
        notify_exception(e)
        await redis_conn.delete(lock_key)
        await client.chat_postMessage(
            channel=channel_id,
            text=_("There was an error processing your request. Please try again."),
        )
        return

    # Get all available language/file combinations that are not in progress
    selected_languages = []
    for source_file in job["data"]["source_files"]:
        # Skip if already has a human job status
        for target_file in source_file.get("target_files", []):
            if not target_file.get("human_job_status"):
                selected_languages.append(
                    f"{source_file['file_uuid']}:{target_file['language_uuid']}"
                )
            if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
                target_file["human_job_status"] = "Submitted"

    if timestamp and job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
        costs = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            [file["file_uuid"] for file in job["data"]["source_files"]],
            [lang["uuid"] for lang in job["data"]["target_languages"]],
        )
        await update_human_job_quote_message(
            client,
            channel_id=channel_id,
            message_ts=timestamp,
            job=job["data"],
            costs=costs["data"],
            actions=False,
            status_message=_("Submitting quote..."),
        )

    await submit_verification_job(
        client=client,
        context=context,
        job_uuid=job_uuid,
        selected_languages=selected_languages,
        user_id=body["user"]["id"],
        timestamp=timestamp,
        job=job,
        channel_id=channel_id,
    )


@app.view("verify_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_verify_job_submission(
    ack: AsyncAck, body: Dict[str, Any], client: AsyncWebClient, context: RayContext
):
    await ack(response_action="clear")

    # Extract the private metadata (job UUID and message timestamp)
    private_metadata = json.loads(body["view"]["private_metadata"])
    job_uuid = private_metadata.get("job_uuid")
    message_ts = private_metadata.get("timestamp", None)
    quote_channel_id = private_metadata.get("channel_id") or context.get("channel_id")
    # lock so that if submission is in progress, it will not be submitted again
    lock_key = verify_job_submission_lock_key(job_uuid)
    lock_acquired = await redis_conn.set(
        lock_key,
        "1",
        ex=VERIFY_JOB_SUBMISSION_LOCK_TTL_SECONDS,
        nx=True,
    )
    if not lock_acquired:
        await client.chat_postMessage(
            channel=body["user"]["id"],
            text=_(
                "This request is no longer available. Please resubmit your documents in the message pane below"
            ),
        )
        return
    assert context["ray"] is not None
    assert context["ray"].client is not None

    job = await get_client_evaluation_job(context["ray"].client, job_uuid)
    target_languages = job["data"]["target_languages"]

    # Extract the selected checkbox values from input blocks
    selected_languages = []
    for source_file in job["data"]["source_files"]:
        for lang in target_languages:
            block_id = (
                f"verification_checkbox_{lang['uuid']}_{source_file['file_uuid']}"
            )
            if block_id in body["view"]["state"]["values"]:
                selected_options = body["view"]["state"]["values"][block_id][
                    "verification_checkbox_action"
                ]["selected_options"]
                selected_languages.extend(
                    [
                        ":".join(option["value"].split(":")[:2])
                        for option in selected_options
                    ]
                )
                if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
                    for target_lang_option in selected_languages:
                        parts = target_lang_option.rsplit(":", 1)
                        file_uuid, lang_uuid = parts[0], parts[1]
                        # Only mark if this selection is for the current source file
                        if file_uuid == source_file["file_uuid"]:
                            for target_file in source_file["target_files"]:
                                if target_file["language_uuid"] == lang_uuid:
                                    target_file["human_job_status"] = "Submitted"
                                    break

    if private_metadata.get("combined_qe_human_quote"):
        if not selected_languages:
            await redis_conn.delete(lock_key)
            await client.chat_postMessage(
                channel=body["user"]["id"],
                text=_("Please select at least one file and target language."),
            )
            return

        session = await get_evaluate_quote_session(job_uuid)
        if session and session.get("stage") in QE_TERMINAL_STAGES:
            await redis_conn.delete(lock_key)
            return

        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [EVALUATE_SERVICE_QUALITY_EVALUATION],
        )
        services_costs = quote.get("services_costs") or {}
        qe_token_cost = int(
            services_costs.get(
                EVALUATE_SERVICE_QUALITY_EVALUATION, quote.get("token", 0)
            )
        )
        costs = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            job_file_uuids(job["data"]),
            job_target_language_uuids(job["data"]),
            assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER,
        )
        quote_snapshot = dict((session or {}).get("quote_snapshot") or {})
        quote_snapshot.update(
            {
                "service": EVALUATE_SERVICE_QUALITY_EVALUATION,
                "token_cost": qe_token_cost,
                "service_label": _("Quality Evaluation + Human Translation"),
                "accept_action_id": COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID,
                "assumed_quality_tier": WORST_CASE_QE_QUALITY_TIER,
                "auto_submit_human_job": True,
                "selected_languages": selected_languages,
            }
        )
        await save_evaluate_quote_session(
            job_uuid,
            channel_id=quote_channel_id or context["channel_id"],
            user_id=body["user"]["id"],
            team_id=context["team_id"],
            stage=STAGE_PROCESSING_QE,
            quote_snapshot=quote_snapshot,
            message_ts=message_ts,
        )
        if message_ts and quote_channel_id:
            message = combined_human_job_quote_message(
                job["data"],
                costs["data"],
                qe_token_cost=qe_token_cost,
                actions=False,
                status_message=_("Accepting quote..."),
                allow_adjust=False,
                download_translations_job_uuid=job_uuid,
                **PRE_QE_QUOTE_DISPLAY,
            )
            await client.chat_update(
                channel=quote_channel_id,
                ts=message_ts,
                text=message.text,
                blocks=message.blocks,
            )

        try:
            await proceed_quality_evaluation(
                context["ray"].client,
                job_uuid,
                token_cost=qe_token_cost,
                human_translation_file_and_languages=selected_languages,
            )
        except Exception:
            await update_evaluate_quote_stage(job_uuid, STAGE_AWAITING_QE)
            await redis_conn.delete(lock_key)
            raise

        await update_evaluate_quote_stage(job_uuid, STAGE_ACCEPTED_QE)
        if message_ts and quote_channel_id:
            message = combined_human_job_quote_message(
                job["data"],
                costs["data"],
                qe_token_cost=qe_token_cost,
                actions=False,
                status_message=_(
                    "Quote accepted! Submitting for human translation and "
                    "calculating your final discount with Arbitr..."
                ),
                allow_adjust=False,
                download_translations_job_uuid=job_uuid,
                **PRE_QE_QUOTE_DISPLAY,
            )
            await client.chat_update(
                channel=quote_channel_id,
                ts=message_ts,
                text=message.text,
                blocks=message.blocks,
            )
        return

    if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
        for source_file in job["data"]["source_files"]:
            for target_file in source_file["target_files"]:
                if target_file.get("human_job_status") != "Submitted":
                    target_file["human_job_status"] = "Cancelled"

    # update origial message ts to remove buttons
    # fetch original message
    await submit_verification_job(
        client=client,
        context=context,
        job_uuid=job_uuid,
        selected_languages=selected_languages,
        user_id=body["user"]["id"],
        timestamp=message_ts,
        job=job,
        channel_id=quote_channel_id,
    )


@app.block_action("verification_checkbox_action", middleware=[ray_connection])
@slack_log_decorator
async def handle_checkbox_action(ack, body, client, action):
    await ack()

    try:
        view_id = body["view"]["id"]
        action_ts = action.get("action_ts", "0")
        # Check if this is the latest action for this view
        latest_action_key = f"latest_action_{view_id}"
        latest_action_ts = await redis_conn.get(latest_action_key)

        if latest_action_ts and float(latest_action_ts) > float(action_ts):
            # A more recent action is already being processed, skip this one
            return

        # Set this as the latest action
        await redis_conn.set(latest_action_key, action_ts, ex=2)  # 2 second expiry

        # Use Redis lock to ensure only one views_update happens at a time
        lock_key = f"view_update_lock_{view_id}"
        lock_acquired = await redis_conn.set(
            lock_key, action_ts, ex=2, nx=True
        )  # 2 second lock, only if not exists

        if not lock_acquired:
            # Another update is in progress, wait for it to complete
            # Wait up to 5 seconds for the lock to be released
            for _ in range(20):  # 20 * 0.1 = 2 seconds
                await asyncio.sleep(0.1)
                if await redis_conn.get(lock_key) is None:
                    break
            else:
                return

            # Re-check if this is still the latest action after lock acquisition
            latest_action_ts = await redis_conn.get(latest_action_key)
            if latest_action_ts and float(latest_action_ts) > float(action_ts):
                # A more recent action is already being processed, skip this one
                return
            else:
                # We are still the latest action, try to acquire the lock
                lock_acquired = await redis_conn.set(
                    lock_key, action_ts, ex=2, nx=True
                )  # 2 second lock, only if not exists

                if not lock_acquired:
                    # Still can't acquire lock, give up
                    return

        try:
            # Parse all selected options from the state values
            selected_options = []
            state_values = body["view"]["state"]["values"]
            # Iterate through all block IDs that contain verification_checkbox_action
            for block_id, block_data in state_values.items():
                if "verification_checkbox_action" in block_data:
                    checkbox_data = block_data["verification_checkbox_action"]
                    if checkbox_data.get("type") == "checkboxes":
                        selected_options.extend(
                            checkbox_data.get("selected_options", [])
                        )

            # Calculate total cost from selected options. The optional fifth value
            # is a displayed per-target extra such as the distributed QE fee.
            total_cost = 0.0
            for option in selected_options:
                match = re.search(r"USD\$([\d.]+)", option["text"]["text"])
                if match:
                    total_cost += float(match.group(1))
                parts = option["value"].split(":")
                if len(parts) > 4:
                    total_cost += float(parts[4])
            total_savings = sum(
                float(option["value"].split(":")[3])
                for option in selected_options
                if len(option["value"].split(":")) > 3
            )
            total_savings = sum(
                float(option["value"].split(":")[3])
                for option in selected_options
                if len(option["value"].split(":")) > 3
            )

            private_metadata_raw = body["view"].get("private_metadata") or "{}"
            try:
                private_metadata = json.loads(private_metadata_raw)
            except json.JSONDecodeError:
                private_metadata = {}
            if not isinstance(private_metadata, dict):
                private_metadata = {}
            if private_metadata.get("combined_qe_human_quote"):
                from app.slack.evaluation_combined_quotes import qe_total_usd

                qe_token_cost = int(private_metadata.get("qe_token_cost") or 0)
                total_savings = max(
                    total_savings - qe_total_usd(qe_token_cost),
                    0.0,
                )

            # Calculate total estimated time from selected options (Verify: global max)
            total_estimated_days = calculate_total_estimated_days(
                [float(option["value"].split(":")[2]) for option in selected_options]
            )

            # Calculate completion date
            completion_date = datetime.now() + timedelta(days=total_estimated_days)
            formatted_date = completion_date.strftime("%d %B %Y")

            # Update the view
            view = body["view"]
            blocks = view["blocks"]

            # Find and update the total cost block
            for block in blocks:
                if block.get("block_id") == "total_cost_block":
                    existing_text = block["text"]["text"]
                    localized_prefix = existing_text.split("USD")[0]
                    total_text = f"{localized_prefix}USD ${total_cost:.2f}"
                    if total_savings > 0:
                        total_text += f" (saved ${total_savings:.2f})"
                    block["text"]["text"] = total_text
                    break

            # Find and update the total estimated time block
            for block in blocks:
                if block.get("block_id") == "total_estimated_time_block":
                    existing_text = block["text"]["text"]
                    prefix, _ = existing_text.split(":", 1)
                    block["text"]["text"] = f"{prefix}: {formatted_date}"
                    break

            await client.views_update(
                view_id=view["id"],
                view={
                    "type": "modal",
                    "title": view["title"],
                    "blocks": blocks,
                    "close": view["close"],
                    "submit": view["submit"],
                    "private_metadata": view["private_metadata"],
                    "callback_id": view["callback_id"],
                },
            )
        except Exception as e:
            notify_exception(e)
        finally:
            # Always release the lock when done
            await redis_conn.delete(lock_key)

    except Exception as e:
        notify_exception(e)
        raise e


@app.action("document_mt_quote_accept", middleware=[ray_connection])
@slack_log_decorator
async def document_mt_quote_accept_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept a cached document MT quote and enqueue the actual translation."""
    await ack()
    await accept_document_mt_quote(
        client=client,
        body=body,
        action=action,
        context=context,
    )


@app.action("document_mt_quote_cancel", middleware=[ray_connection])
@slack_log_decorator
async def document_mt_quote_cancel_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Cancel a cached document MT quote."""
    await ack()
    await cancel_document_mt_quote(
        client=client,
        body=body,
        action=action,
        context=context,
    )


@app.view("document_mt_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_document_mt_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    """Handle document machine translation job submission."""
    if await require_ray_client(context, prompt_login=False, allow_org_billing=True):
        acked = False
        try:
            form_data = view["state"]["values"] if view else {}
            selected_source_language = (
                form_data.get("source_lang", {})
                .get("language_mt_options", {})
                .get("selected_option", {})
                .get("value")
            )
            selected_languages = (
                form_data.get("target_langs", {})
                .get("language_mt_options", {})
                .get("selected_options", [])
            )

            if not selected_source_language:
                await ack(
                    response_action="errors",
                    errors={
                        "source_lang": _(
                            "Please select a source language for translation."
                        )
                    },
                )
                return

            if any(
                str(lang.get("value", "")) == selected_source_language
                for lang in selected_languages
            ):
                await ack(
                    response_action="errors",
                    errors={
                        "target_langs": _(
                            "The source language cannot be the same as a target language. Please choose a different target language."
                        )
                    },
                )
                return

            await ack(response_action="clear")
            acked = True

            if not selected_languages:
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=_(
                        "Please select at least one target language for translation."
                    ),
                )
                return

            # Get the file IDs from the form state
            files = (
                form_data.get("files", {}).get("files", {}).get("selected_options", [])
            )

            if not files:
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=_("Please select at least one file to translate."),
                )
                return

            channel_id = (
                view["private_metadata"]
                if view and "private_metadata" in view
                else None
            )
            context["channel_id"] = channel_id or context["user_id"]
            file_payloads = [
                slack_file_submission_payload_from_option(file) for file in files
            ]
            accessible_files, missing_files = await get_accessible_slack_files(
                client, file_payloads
            )
            if missing_files:
                await notify_missing_slack_files(
                    client, context["user_id"], missing_files
                )
            accessible_file_ids = {
                str(file["id"]) for file in accessible_files if file.get("id")
            }
            file_payloads = [
                file for file in file_payloads if str(file["id"]) in accessible_file_ids
            ]
            if not file_payloads:
                return

            selected_file_titles = [str(file["title"]) for file in file_payloads]
            quote_id = new_document_mt_quote_id()
            await enqueue_document_mt_quote_preflight(
                quote_id=quote_id,
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=context["channel_id"],
                files=file_payloads,
                source_language=selected_source_language,
                target_languages=[str(lang["value"]) for lang in selected_languages],
            )
            await client.chat_postMessage(
                channel=context["channel_id"],
                text=_(
                    f"Preparing an AI Translate quote for document(s) *({', '.join(selected_file_titles)})*. Please review the quote before translation starts."
                ),
            )

        except Exception as e:
            if not acked:
                try:
                    await ack(response_action="clear")
                except Exception:
                    pass
            notify_exception(e)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "There was an error submitting your translation request, please try again."
                ),
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


@app.action("video_transcribe_only", middleware=[ray_connection])
@slack_log_decorator
async def handle_video_transcribe_only(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Handle transcribe-only button - starts transcription directly without modal."""
    await ack()
    if not await require_ray_client(context):
        return

    try:
        assert action is not None
        assert context["ray"] is not None
        assert context["ray"].client is not None

        action_data = json.loads(action.get("value", "{}"))
        channel_id = (
            action_data.get("channel_id")
            or context.get("channel_id")
            or context["user_id"]
        )

        files = action_data["files"]
        thread_ts = resolve_media_thread_ts(action_data, body)

        # Check for duplicate transcription-only submissions
        from ..ray.submissions import (
            check_and_record_transcription_only_submission_async,
        )

        # Process each file
        files_to_process = []
        duplicate_files = []
        for file_data in files:
            (
                is_dup,
                submission_record,
            ) = await check_and_record_transcription_only_submission_async(
                slack_file_id=file_data["file_id"],
                file_name=file_data["file_name"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                channel_id=channel_id,
            )

            if is_dup:
                duplicate_files.append(file_data["file_name"])
            else:
                files_to_process.append(
                    {
                        **file_data,
                        "submission_id": submission_record.id,
                    }
                )

        if not files_to_process:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription to prevent duplicate submissions."
                ),
            )
            return

        # Create ASR task for each file
        from ..models import ASRTask, TranscriptionTaskData
        from ..transcriber_tasks.tasks import create_asr_task

        for file_info in files_to_process:
            # Download file from Slack to get URL
            slack_file_info = await client.files_info(file=file_info["file_id"])
            slack_file_data: dict[str, Any] = slack_file_info.get("file", {})
            download_url = slack_file_data.get(
                "url_private_download"
            ) or slack_file_data.get("url_private")

            if not download_url:
                continue

            task_data = TranscriptionTaskData(
                client_id=context["ray"].client.id,
                file_name=file_info["file_name"],
                download_url=download_url,
                app_token=client.token or "",
                out_stream_name=f"{domains.stream_proxy}/events/transcription:slack:media:results",
                service="azure",
                model="whisper-1",
                embed_subtitles=False,
                sandbox=False,
            )

            # Build extra_data with submission_id
            extra_data_dict = {
                "slack_user_id": context["user_id"],
                "slack_team_id": context["team_id"],
                "slack_enterprise_id": context.enterprise_id,
                "slack_channel_id": channel_id,
                "slack_thread_ts": thread_ts,
                "submission_id": file_info["submission_id"],
            }

            asr_task = ASRTask(
                member_uuid=context["ray"].client.id,
                event_name="sup-subtitle-ai:media:asr",
                app_source="slack",
                service="azure",
                model="whisper-1",
                extra_data=extra_data_dict,
                task_data=task_data,
            )

            await create_asr_task(asr_task)

        # Notify user
        file_count = len(files_to_process)
        count = file_count
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                ":stopwatch: Please wait a moment while we transcribe your {count} file(s)."
            ),
            thread_ts=thread_ts,
        )

        # Notify about duplicate files if some were skipped
        if duplicate_files:
            files = ", ".join(duplicate_files)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Skipped duplicate files already being processed: {files}"),
            )

    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


@app.action("video_transcribe_translate")
@slack_log_decorator
async def handle_video_transcribe_translate(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Show the transcribe & translate modal for language selection."""
    await ack()
    assert action is not None
    from .templates.views import video_transcribe_translate_modal

    action_data = json.loads(action.get("value", "{}"))
    thread_ts = resolve_media_thread_ts(action_data, body)
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            video_transcribe_translate_modal(
                channel_id=action_data.get("channel_id", context.get("channel_id", "")),
                files=action_data["files"],
                thread_ts=thread_ts,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


@app.action("video_embed_subtitles")
@slack_log_decorator
async def handle_video_embed_subtitles(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Handle subtitle embedding from either the modal flow or a thread-uploaded SRT."""
    await ack()
    assert action is not None
    from .listener_actions import is_audio_only_file
    from .templates.views import video_embed_subtitles_modal

    action_data = json.loads(action.get("value", "{}"))
    thread_ts = resolve_media_thread_ts(action_data, body)
    if action_data.get("subtitle_file"):
        await populate_ray_connection(context)
        if await require_ray_client(context):
            await submit_existing_srt_embed_task(
                client, context, action_data, thread_ts
            )
        return

    all_files = action_data["files"]
    video_files = [f for f in all_files if not is_audio_only_file(f)]
    if not video_files:
        await populate_ray_connection(context)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "Subtitle embedding is only available for video files (MP4, MPEG, WEBM). "
                "Audio files (MP3, WAV, M4A) cannot have subtitles embedded."
            ),
        )
        return

    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            video_embed_subtitles_modal(
                channel_id=action_data.get("channel_id", context.get("channel_id", "")),
                files=video_files,
                thread_ts=thread_ts,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


@app.view("video_transcribe_translate_submit", middleware=[ray_connection])
@slack_log_decorator
async def handle_video_transcribe_translate_submit(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    """Handle transcribe & translate form submission."""
    await ack(response_action="clear")

    if not await require_ray_client(context, prompt_login=True):
        return

    try:
        assert view is not None
        assert context["ray"] is not None
        assert context["ray"].client is not None

        # Parse form data
        metadata = json.loads(view["private_metadata"])
        form_values = view["state"]["values"]

        # Get target languages
        lang_selection = form_values.get("target_languages", {}).get(
            "language_mt_options", {}
        )
        selected_options = lang_selection.get("selected_options", [])

        if not selected_options:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one target language for translation."),
            )
            return

        # Build target languages list (use language code for translation)
        target_language_codes = [opt["value"] for opt in selected_options]
        target_language_names = [opt["text"]["text"] for opt in selected_options]

        channel_id = (
            metadata.get("channel_id")
            or context.get("channel_id")
            or context["user_id"]
        )

        # Get selected files from form (user may have deselected some)
        file_selection = form_values.get("selected_file", {}).get("file_display", {})
        selected_file_options = file_selection.get("selected_options", [])
        selected_file_ids = {opt["value"] for opt in selected_file_options}

        # Filter to only include selected files
        all_files = metadata["files"]
        files = [f for f in all_files if f["file_id"] in selected_file_ids]

        if not files:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one file to process."),
            )
            return

        thread_ts = metadata.get("thread_ts")

        # Check for duplicate submissions per file and target language
        # Create ASR task for transcriber with translation info in extra_data
        from ..models import ASRTask, TranscriptionTaskData
        from ..ray.submissions import check_and_record_transcription_submission_async
        from ..transcriber_tasks.tasks import create_asr_task

        files_processed = 0
        all_duplicate_languages = []

        for file_info in files:
            duplicate_languages = []
            valid_languages = []
            submission_ids = []  # Store submission IDs for completion updates
            for lang_code, lang_name in zip(
                target_language_codes, target_language_names, strict=True
            ):
                (
                    is_dup,
                    submission_record,
                ) = await check_and_record_transcription_submission_async(
                    slack_file_id=file_info["file_id"],
                    file_name=file_info["file_name"],
                    user_id=context["user_id"],
                    team_id=context["team_id"],
                    channel_id=channel_id,
                    target_language=lang_code,
                )
                if is_dup:
                    duplicate_languages.append(lang_name)
                else:
                    valid_languages.append({"code": lang_code, "name": lang_name})
                    submission_ids.append(submission_record.id)

            # If all languages are duplicates for this file, skip it
            if not valid_languages:
                all_duplicate_languages.extend(duplicate_languages)
                continue

            # Download file from Slack to get URL
            slack_file_info = await client.files_info(file=file_info["file_id"])
            slack_file_data: dict[str, Any] = slack_file_info.get("file", {})
            download_url = slack_file_data.get(
                "url_private_download"
            ) or slack_file_data.get("url_private")

            if not download_url:
                continue

            # Only include valid (non-duplicate) languages
            valid_language_codes = [lang["code"] for lang in valid_languages]
            valid_language_names_list = [lang["name"] for lang in valid_languages]

            task_data = TranscriptionTaskData(
                client_id=context["ray"].client.id,
                file_name=file_info["file_name"],
                download_url=download_url,
                app_token=client.token or "",
                out_stream_name=f"{domains.stream_proxy}/events/transcription:slack:media:results",
                service="azure",
                model="whisper-1",
                embed_subtitles=False,
                sandbox=False,
            )

            # Build extra_data with submission_ids
            extra_data_dict = {
                "slack_user_id": context["user_id"],
                "slack_team_id": context["team_id"],
                "slack_enterprise_id": context.enterprise_id,
                "slack_channel_id": channel_id,
                "slack_thread_ts": thread_ts,
                # Include translation info for post-transcription processing
                "pipeline_type": "transcribe_translate",
                "target_languages": valid_language_codes,
                "target_language_names": valid_language_names_list,
                "submission_ids": submission_ids,
            }

            asr_task = ASRTask(
                member_uuid=context["ray"].client.id,
                event_name="sup-subtitle-ai:media:asr",
                app_source="slack",
                service="azure",
                model="whisper-1",
                extra_data=extra_data_dict,
                task_data=task_data,
            )

            await create_asr_task(asr_task)
            files_processed += 1

            # Track duplicate languages for this file
            if duplicate_languages:
                all_duplicate_languages.extend(duplicate_languages)

        if files_processed == 0:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription & translation(s) to prevent duplicate submissions."
                ),
            )
            return

        # Notify user
        count = files_processed
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                ":stopwatch: Please wait a moment while we transcribe & AI translate your {count} file(s)."
            ),
            thread_ts=thread_ts,
        )

        # Notify about duplicate languages if some were skipped
        if all_duplicate_languages:
            langs = ", ".join(list(set(all_duplicate_languages)))
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Some translations were skipped as duplicates: {langs}"),
            )

    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


@app.view("video_embed_subtitles_submit", middleware=[ray_connection])
@slack_log_decorator
async def handle_video_embed_subtitles_submit(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    """Handle embed subtitles form submission."""
    await ack(response_action="clear")

    if not await require_ray_client(context, prompt_login=True):
        return

    try:
        assert view is not None
        assert context["ray"] is not None
        assert context["ray"].client is not None

        # Parse form data
        metadata = json.loads(view["private_metadata"])
        form_values = view["state"]["values"]

        # Get target languages
        lang_selection = form_values.get("target_languages", {}).get(
            "language_mt_options", {}
        )
        selected_options = lang_selection.get("selected_options", [])

        if not selected_options:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one target language for translation."),
            )
            return

        # Build target languages list (use language code for translation)
        target_language_codes = [opt["value"] for opt in selected_options]
        target_language_names = [opt["text"]["text"] for opt in selected_options]

        channel_id = (
            metadata.get("channel_id")
            or context.get("channel_id")
            or context["user_id"]
        )

        # Get selected files from form (user may have deselected some)
        file_selection = form_values.get("selected_file", {}).get("file_display", {})
        selected_file_options = file_selection.get("selected_options", [])
        selected_file_ids = {opt["value"] for opt in selected_file_options}

        # Filter to only include selected files
        all_files = metadata["files"]
        files = [f for f in all_files if f["file_id"] in selected_file_ids]

        if not files:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one file to process."),
            )
            return

        thread_ts = metadata.get("thread_ts")

        # Check for duplicate submissions per file and target language
        # Create ASR task for transcriber with translation and embedding info in extra_data
        from ..models import ASRTask, TranscriptionTaskData
        from ..ray.submissions import check_and_record_transcription_submission_async
        from ..transcriber_tasks.tasks import create_asr_task

        files_processed = 0
        all_duplicate_languages = []

        for file_info in files:
            duplicate_languages = []
            valid_languages = []
            submission_ids = []  # Store submission IDs for completion updates
            for lang_code, lang_name in zip(
                target_language_codes, target_language_names, strict=True
            ):
                (
                    is_dup,
                    submission_record,
                ) = await check_and_record_transcription_submission_async(
                    slack_file_id=file_info["file_id"],
                    file_name=file_info["file_name"],
                    user_id=context["user_id"],
                    team_id=context["team_id"],
                    channel_id=channel_id,
                    target_language=lang_code,
                )
                if is_dup:
                    duplicate_languages.append(lang_name)
                else:
                    valid_languages.append({"code": lang_code, "name": lang_name})
                    submission_ids.append(submission_record.id)

            # If all languages are duplicates for this file, skip it
            if not valid_languages:
                all_duplicate_languages.extend(duplicate_languages)
                continue

            # Download file from Slack to get URL
            slack_file_info = await client.files_info(file=file_info["file_id"])
            slack_file_data: dict[str, Any] = slack_file_info.get("file", {})
            download_url = slack_file_data.get(
                "url_private_download"
            ) or slack_file_data.get("url_private")

            if not download_url:
                continue

            # Only include valid (non-duplicate) languages
            valid_language_codes = [lang["code"] for lang in valid_languages]
            valid_language_names_list = [lang["name"] for lang in valid_languages]

            task_data = TranscriptionTaskData(
                client_id=context["ray"].client.id,
                file_name=file_info["file_name"],
                download_url=download_url,
                app_token=client.token or "",
                out_stream_name=f"{domains.stream_proxy}/events/transcription:slack:media:results",
                service="azure",
                model="whisper-1",
                embed_subtitles=True,
                sandbox=False,
            )

            # Build extra_data with submission_ids and embedding info
            extra_data_dict = {
                "slack_user_id": context["user_id"],
                "slack_team_id": context["team_id"],
                "slack_enterprise_id": context.enterprise_id,
                "slack_channel_id": channel_id,
                "slack_thread_ts": thread_ts,
                # Include translation and embedding info for post-transcription processing
                "pipeline_type": "transcribe_translate_embed",
                "target_languages": valid_language_codes,
                "target_language_names": valid_language_names_list,
                # Store original video info for embedding
                "original_video_file_id": file_info["file_id"],
                "original_video_download_url": download_url,
                "original_video_file_name": file_info["file_name"],
                "submission_ids": submission_ids,
            }

            asr_task = ASRTask(
                member_uuid=context["ray"].client.id,
                event_name="sup-subtitle-ai:media:asr",
                app_source="slack",
                service="azure",
                model="whisper-1",
                extra_data=extra_data_dict,
                task_data=task_data,
            )

            await create_asr_task(asr_task)
            files_processed += 1

            # Track duplicate languages for this file
            if duplicate_languages:
                all_duplicate_languages.extend(duplicate_languages)

        if files_processed == 0:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription & embedding(s) to prevent duplicate submissions."
                ),
            )
            return

        # Notify user
        count = files_processed
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                ":stopwatch: Please wait a moment while we transcribe, AI-translate, and embed subtitles into your {count} file(s)."
            ),
            thread_ts=thread_ts,
        )

        # Notify about duplicate languages if some were skipped
        if all_duplicate_languages:
            langs = ", ".join(list(set(all_duplicate_languages)))
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Some embeddings were skipped as duplicates: {langs}"),
            )

    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


@app.event(re.compile(r".+"))
@slack_log_decorator
async def catch_all_event_callbacks(body: Dict[str, Any]):
    """Ack any unexpected event types so Slack receives HTTP 200 responses."""
    return


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

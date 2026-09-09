"""This module registers listeners to handle events, interactions,
commands, etc. from the Slack API.

Only the Slack Bolt route registrations live here. Each route is a thin
wrapper that (optionally) acknowledges the request and delegates all business
logic to a focused handler module under ``app/slack/handlers/``.
"""

import re
from typing import Any, Dict, Optional

from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncRespond, AsyncSay
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext

from .app import app
from .document_mt_quote_actions import (
    accept_document_mt_quote,
    cancel_document_mt_quote,
)
from .document_mt_quote_adjustment import DOCUMENT_MT_QUOTE_ADJUST_ACTION_ID
from .evaluation_ai_adjustment import (
    AI_QUOTE_ADJUST_ACTION_ID,
    AI_QUOTE_ADJUST_CALLBACK_ID,
    AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID,
)
from .evaluation_combined_quotes import COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID
from .evaluation_pdf_quote_actions import accept_pdf_evaluate_quote
from .evaluation_quote_actions import (
    accept_ai_translation_quote,
    accept_combined_qe_human_quote,
)
from .handlers import (
    auth,
    auto_translate,
    commands,
    document_mt,
    downloads,
    evaluate,
    evaluate_verify,
    home,
    jobs,
    lifecycle,
    media,
    media_submissions,
    messages,
    options,
    shortcuts,
)
from .handlers import help as help_handlers
from .logging import slack_log_decorator
from .media_quote_adjustment import MEDIA_TRANSLATION_QUOTE_ADJUST_ACTION_ID
from .middleware import ray_connection
from .pdf_evaluate_quotes import (
    PDF_EVALUATE_QUOTE_ACTION_ID,
    PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID,
)

# ---------------------------------------------------------
# Set up Slack listeners here. Keep these wrappers thin —
# business logic belongs in app/slack/handlers/.
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
    await messages.handle_message_event(
        client=client, context=context, message=message, body=body
    )


# channel deletion
@app.event("channel_deleted")
@slack_log_decorator
async def channel_deleted_event(
    client: AsyncWebClient, context: RayContext, event: Dict[str, Any]
):
    await messages.handle_channel_deleted(event)


@app.event("app_mention", middleware=[ray_connection])
@slack_log_decorator
async def app_mention_event(
    client: AsyncWebClient, context: RayContext, event: Dict[str, Any]
):
    await messages.handle_app_mention(client, context, event)


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
    await ack()
    await home.handle_home_opened(
        event=event, context=context, body=body, say=say, client=client
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
    await home.handle_home_load(
        action=action, context=context, client=client, body=body
    )


@app.event("app_uninstalled")
@slack_log_decorator
async def app_uninstalled(context: RayContext):
    await lifecycle.handle_app_uninstalled(context)


@app.event("channel_id_changed")
@slack_log_decorator
async def channel_id_changed(event: Dict[str, Any]):
    await lifecycle.handle_channel_id_changed(event)


@app.message_shortcut("new_job", middleware=[ray_connection])
@slack_log_decorator
async def new_job_shortcut(
    ack: AsyncAck,
    shortcut: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await shortcuts.handle_new_job_shortcut(
        shortcut=shortcut, context=context, client=client
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
    await shortcuts.handle_show_srt_translate_form(
        context=context, action=action, body=body, client=client
    )


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
    await document_mt.handle_document_mt_job_action(
        context=context, action=action, body=body, client=client
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
    await document_mt.handle_document_mt_submit(
        action=action, context=context, body=body, say=say, client=client
    )


@app.block_action("download_transcribed_file", middleware=[ray_connection])
@slack_log_decorator
async def download_transcribed_file(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await downloads.handle_download_transcribed_file(
        action=action, context=context, client=client
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
    await downloads.handle_download_ai_translation(
        action=action, context=context, client=client
    )


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
    await downloads.handle_download_ai_translations(
        action=action, context=context, client=client, body=body
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
    await shortcuts.handle_translate_shortcut(body=body, client=client, context=context)


@app.view("srt_translate", middleware=[ray_connection])
@slack_log_decorator
async def srt_translate_action(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await shortcuts.handle_srt_translate(
        ack=ack, view=view, context=context, body=body, client=client
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
    await jobs.handle_job_search_action(context=context, client=client, body=body)


@app.command(re.compile(r"\/\w*(ray|straker|lc)\w*"))
@slack_log_decorator
async def ray_command(
    ack: AsyncAck,
    respond: AsyncRespond,
    command: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await commands.handle_ray_command(
        ack=ack, respond=respond, command=command, context=context, client=client
    )


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
    await auto_translate.handle_show_auto_translate_settings(
        context=context, payload=payload, body=body, client=client
    )


@app.block_action("settings_auto_translate_disable", middleware=[ray_connection])
async def disable_auto_translate_settings(
    ack: AsyncAck,
    context: RayContext,
    payload: Dict[str, Any],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await auto_translate.handle_disable_auto_translate_settings(
        ack=ack, context=context, payload=payload, body=body, client=client
    )


@app.block_action("show_job_details", middleware=[ray_connection])
@slack_log_decorator
async def show_job_details(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await jobs.handle_show_job_details(
        action=action, payload=payload, context=context, client=client
    )


@app.action("quote", middleware=[ray_connection])
@slack_log_decorator
async def quote(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
    await ack()
    await jobs.handle_quote(context=context, client=client)


@app.action("daily_summary", middleware=[ray_connection])
@slack_log_decorator
async def daily_summary(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
    await ack()
    await jobs.handle_daily_summary(context, client)


@app.block_action("all_summary", middleware=[ray_connection])
@slack_log_decorator
async def all_summary(ack: AsyncAck, context: RayContext, client: AsyncWebClient):
    await ack()
    await jobs.handle_all_summary(context, client)


@app.action("ai_translate_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_ai_translate_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    await ack()
    await help_handlers.handle_ai_translate_help(context, client)


@app.action("verify_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_verify_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    await ack()
    await help_handlers.handle_verify_help(context, client)


@app.action("human_help", middleware=[ray_connection])
@slack_log_decorator
async def handle_human_help_action(
    ack: AsyncAck, context: RayContext, client: AsyncWebClient
):
    await ack()
    await help_handlers.handle_human_help(context, client)


@app.block_action("job_list", middleware=[ray_connection])
@slack_log_decorator
async def job_list_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await jobs.handle_job_list(payload, context, client)


@app.block_action(re.compile(r"job_list_paginated(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def job_list_paginated_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await jobs.handle_job_list_paginated(payload, context, client)


# The "Account Info" button short cut
@app.block_action("account_info", middleware=[ray_connection])
@slack_log_decorator
async def get_account_info(ack: AsyncAck, context: RayContext, respond: AsyncRespond):
    await ack()
    await jobs.handle_account_info(context, respond)


# The "Connect" button short cut in Help Message
@app.block_action("connect_info", middleware=[ray_connection])
@slack_log_decorator
async def get_connect_info(ack: AsyncAck, context: RayContext, respond: AsyncRespond):
    await ack()
    await jobs.handle_connect_info(context, respond)


@app.block_action("delay_info", middleware=[ray_connection])
@slack_log_decorator
async def get_delay_info(ack: AsyncAck, respond: AsyncRespond):
    await ack()
    await jobs.handle_delay_info(respond)


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
    await auth.handle_approve_pending_client(
        action=action, context=context, say=say, client=client
    )


@app.block_action("login")
async def login_account_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    await ack()
    await auth.handle_login_account(action=action, context=context, respond=respond)


@app.block_action("disconnect", middleware=[ray_connection])
@slack_log_decorator
async def disconnect_account_action(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    await ack()
    await auth.handle_disconnect_account(
        action=action, context=context, respond=respond
    )


@app.block_action("delete_ephemeral_message")
@slack_log_decorator
async def delete_ephemeral_message(ack: AsyncAck, respond: AsyncRespond):
    await ack()
    await auth.handle_delete_ephemeral_message(respond)


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
    await jobs.handle_new_job(ack=ack, view=view, context=context, client=client)


@app.view("job_search", middleware=[ray_connection])
@slack_log_decorator
async def handle_job_search(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await jobs.handle_job_search(ack=ack, view=view, context=context, client=client)


@app.view("settings_auto_translate", middleware=[ray_connection])
@slack_log_decorator
async def view_update_auto_translate_settings(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await auto_translate.handle_update_auto_translate_settings(
        ack=ack, view=view, context=context, body=body, client=client
    )


@app.action("language_mt_options", middleware=[ray_connection])
async def language_mt_options_selected(ack: AsyncAck, body: Dict[str, Any]):
    await ack()
    await options.handle_language_mt_options_selected(body)


@app.options("language_options", middleware=[ray_connection])
async def language_options(ack: AsyncAck, payload: Dict[str, Any]):
    await options.handle_language_options(ack, payload)


@app.options("language_options_uuid", middleware=[ray_connection])
async def language_options_uuid(ack: AsyncAck, payload: Dict[str, Any]):
    await options.handle_language_options_uuid(ack, payload)


@app.options("source_language_option_uuid", middleware=[ray_connection])
async def source_language_option_uuid(ack: AsyncAck, payload: Dict[str, Any]):
    await options.handle_source_language_option_uuid(ack, payload)


@app.options("group_options", middleware=[ray_connection])
async def group_options(ack: AsyncAck, context: RayContext):
    await options.handle_group_options(ack, context)


@app.options(re.compile(r"file_options_.+"))
async def file_options(ack: AsyncAck, payload: Dict[str, Any], client: AsyncWebClient):
    await options.handle_file_options(ack, payload, client)


@app.block_action(re.compile(r"batch_list(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def batch_list_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await jobs.handle_batch_list(payload, context, client)


@app.block_action(re.compile(r"file_list(_\d+)?"), middleware=[ray_connection])
@slack_log_decorator
async def file_list_action(
    ack: AsyncAck,
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()
    await jobs.handle_file_list(payload, context, client)


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
    await jobs.handle_cancel_job_action(
        payload=payload, context=context, client=client, body=body
    )


@app.view("cancel_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_cancel_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await jobs.handle_cancel_job(ack=ack, view=view, context=context, client=client)


@app.event({"type": "message", "subtype": "message_deleted"})
@slack_log_decorator
async def message_deleted_event(
    message: Dict[str, Any],
    client: AsyncWebClient,
    body: Dict[str, Any],
    context: RayContext,
):
    await messages.handle_message_deleted(
        message=message, client=client, body=body, context=context
    )


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
    await messages.handle_message_changed(
        client=client, body=body, context=context, message=message
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
    await evaluate.handle_evaluate_job_submit(
        view=view, client=client, ack=ack, context=context
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
    await ack()
    await evaluate.handle_evaluate_job_action(
        client=client, body=body, action=action, context=context
    )


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
    await ack()
    await evaluate.handle_verify_job_modal_open(
        client=client, body=body, action=action, context=context
    )


@app.action(AI_QUOTE_ADJUST_ACTION_ID)
@app.action(PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID)
@app.action(DOCUMENT_MT_QUOTE_ADJUST_ACTION_ID)
@app.action(MEDIA_TRANSLATION_QUOTE_ADJUST_ACTION_ID)
@slack_log_decorator
async def evaluation_ai_quote_adjust_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    await ack()
    await evaluate.handle_ai_quote_adjust(
        client=client, body=body, action=action, context=context
    )


@app.block_action(AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID)
@slack_log_decorator
async def evaluation_ai_quote_adjust_selection_action(
    ack: AsyncAck,
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    await evaluate.handle_ai_quote_adjust_selection(body=body, client=client)


@app.view(AI_QUOTE_ADJUST_CALLBACK_ID, middleware=[ray_connection])
@slack_log_decorator
async def evaluation_ai_quote_adjust_submit(
    ack: AsyncAck,
    body: Dict[str, Any],
    client: AsyncWebClient,
    context: RayContext,
):
    await evaluate.handle_ai_quote_adjust_submit(
        ack=ack, body=body, client=client, context=context
    )


@app.action(PDF_EVALUATE_QUOTE_ACTION_ID, middleware=[ray_connection])
@slack_log_decorator
async def evaluation_pdf_prequote_accept_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept a PDF evaluate quote from its original Slack message."""
    await ack()
    await accept_pdf_evaluate_quote(
        client,
        body,
        str(action["value"]),
        context,
    )


@app.action("evaluation_ai_quote_accept", middleware=[ray_connection])
@slack_log_decorator
async def evaluation_ai_quote_accept_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept the AI Translation quote from its original Slack message."""
    await ack()
    await accept_ai_translation_quote(
        client=client, body=body, action=action, context=context
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
    await accept_combined_qe_human_quote(
        client=client, body=body, action=action, context=context
    )


@app.action("quote_accept_all", middleware=[ray_connection])
@slack_log_decorator
async def quote_accept_all_action(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    await ack()
    await evaluate_verify.handle_quote_accept_all(
        client=client, body=body, action=action, context=context
    )


@app.view("verify_job", middleware=[ray_connection])
@slack_log_decorator
async def handle_verify_job_submission(
    ack: AsyncAck, body: Dict[str, Any], client: AsyncWebClient, context: RayContext
):
    await ack(response_action="clear")
    await evaluate_verify.handle_verify_job_submission(
        body=body, client=client, context=context
    )


@app.block_action("verification_checkbox_action", middleware=[ray_connection])
@slack_log_decorator
async def handle_checkbox_action(ack, body, client, action):
    await ack()
    await evaluate_verify.handle_verification_checkbox(body, client, action)


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
    await document_mt.handle_document_mt_job(
        ack=ack, view=view, context=context, client=client
    )


@app.action("media_quote_accept", middleware=[ray_connection])
@slack_log_decorator
async def handle_media_quote_accept(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    await ack()
    await media.handle_media_quote_accept(
        client=client, body=body, action=action, context=context
    )


@app.action("media_quote_cancel", middleware=[ray_connection])
@slack_log_decorator
async def handle_media_quote_cancel(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    await ack()
    await media.handle_media_quote_cancel(
        client=client, body=body, action=action, context=context
    )


@app.action("media_translation_quote_accept", middleware=[ray_connection])
@slack_log_decorator
async def handle_media_translation_quote_accept(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    await ack()
    await media.handle_media_translation_quote_accept(
        client=client, body=body, action=action, context=context
    )


@app.action("media_translation_quote_cancel", middleware=[ray_connection])
@slack_log_decorator
async def handle_media_translation_quote_cancel(
    ack: AsyncAck,
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    await ack()
    await media.handle_media_translation_quote_cancel(
        client=client, body=body, action=action, context=context
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
    await ack()
    await media.handle_video_transcribe_only(
        context=context, action=action, body=body, client=client
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
    await ack()
    await media.handle_video_transcribe_translate(
        context=context, action=action, body=body, client=client
    )


@app.action("video_embed_subtitles")
@slack_log_decorator
async def handle_video_embed_subtitles(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    await media.handle_video_embed_subtitles(
        context=context, action=action, body=body, client=client
    )


@app.action("video_configure_media")
@slack_log_decorator
async def handle_video_configure_media(
    ack: AsyncAck,
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    await media.handle_video_configure_media(
        context=context, action=action, body=body, client=client
    )


@app.action("video_configure_workflow_type")
@slack_log_decorator
async def handle_video_configure_workflow_type(
    ack: AsyncAck,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    await ack()
    assert action is not None
    await media.handle_video_configure_workflow_type(
        client=client, body=body, action=action
    )


@app.view("video_transcribe_translate_submit", middleware=[ray_connection])
@slack_log_decorator
async def handle_video_transcribe_translate_submit(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack(response_action="clear")
    await media_submissions.handle_video_transcribe_translate_submit(
        view=view, context=context, client=client
    )


@app.view("video_embed_subtitles_submit", middleware=[ray_connection])
@slack_log_decorator
async def handle_video_embed_subtitles_submit(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack(response_action="clear")
    await media_submissions.handle_video_embed_subtitles_submit(
        view=view, context=context, client=client
    )


@app.view("video_configure_media_submit", middleware=[ray_connection])
@slack_log_decorator
async def handle_video_configure_media_submit(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack(response_action="clear")
    await media_submissions.handle_video_configure_media_submit(
        view=view, context=context, client=client
    )


@app.event(re.compile(r".+"))
@slack_log_decorator
async def catch_all_event_callbacks(body: Dict[str, Any]):
    """Ack any unexpected event types so Slack receives HTTP 200 responses."""
    return


# FastAPI will use this to handle Slack API requests.
slack_handler = AsyncSlackRequestHandler(app)

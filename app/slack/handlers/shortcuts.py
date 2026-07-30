"""Handlers for Slack shortcuts and the SRT translate form."""

from typing import Any, Dict, Optional, cast

from slack_bolt.kwargs_injection.async_args import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient

from app.api.language_cloud import detect_language
from app.auth.connector import RayContext
from app.ray.utils import is_ibm_enterprise
from app.slack.buglog_notifier import notify_exception
from app.slack.listener_actions import (
    document_machine_translate,
    get_mt_translation,
    send_translation_success_message,
)
from app.slack.middleware import (
    populate_ray_connection,
    require_ray_client,
)
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from app.slack.templates.messages import LoginMessage, NewJobMessage
from app.slack.templates.views import srt_translate_modal
from app.slack.utils import extract_language_codes_from_form
from app.transcriber_tasks.tasks import get_asr_task
from app.translate import _


async def handle_new_job_shortcut(
    shortcut: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
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


async def handle_show_srt_translate_form(
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
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


async def handle_translate_shortcut(
    body: Dict[str, Any],
    client: AsyncWebClient,
    context: RayContext,
):
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


async def handle_srt_translate(
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

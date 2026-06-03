import hashlib
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.models import MtTranslationExtraData
from app.api.verify import get_evaluation_job, get_job_pricing
from app.auth.connector import (
    build_spend_idempotency_key,
    duration_to_subtitling_tokens,
    duration_to_tokens,
    get_ray_client,
    get_ray_connection,
    log_embedding_by_client_id,
    log_inline_mt_usage_by_client_id,
    log_transcribe_by_client_id,
)
from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.database import async_engines
from app.media.embed_spend import (
    embedding_source_language as _embedding_source_language,
)
from app.media.embed_spend import (
    embedding_target_language_codes as _embedding_target_language_codes,
)
from app.media.embed_spend import (
    is_embed_only_pipeline as _is_embed_only_pipeline,
)
from app.models import TranscriptionTask, TranscriptionTaskInfo
from app.mt.logs import log_google_api_usage
from app.ray.settings import get_auto_translate_language_name
from app.ray.submissions import SubmissionStatus, updated_submission_status
from app.ray.utils import (
    download_from_file_server_async,
    is_ibm_enterprise,
    set_user_language,
)
from app.slack.buglog_notifier import notify_exception, notify_message
from app.slack.select_options import _get_languages_cached
from app.transcriber_tasks.tasks import get_transcription_task
from app.translate import _

from ..auth.connector import (
    SlackUser,
    get_client_access_tokens,
    get_client_type,
    get_demo_link,
    get_group_admin_slack_users,
    get_job_group_quote_settings,
    get_slack_user,
    is_verify_job,
    validate_api_callback_signature,
)
from ..dependencies import RayEvent, RayEventAuth, get_ray_event_auth
from ..ray.events.logging import (
    post_channel_translation_notification,
    post_notification,
    post_notification_ephemeral,
)
from ..ray.events.models import (
    Balance,
    ClientApprovedEvent,
    ClientGroup,
    ClientSignupEvent,
    JobQuoteAcceptedEvent,
    JobQuoteCancelledEvent,
    JobQuoteCreatedEvent,
    JobStatusChangedEvent,
    JobTranscribedEvent,
    MtErrorResponseSchema,
    MtSuccessResponseSchema,
    SlackAccountConnectedEvent,
)
from ..redis import redis_conn
from ..saq_jobs.dispatch import (
    enqueue_mt_success_upload,
    enqueue_transcription_upload,
    enqueue_verify_complete_upload,
)
from ..slack.templates.messages import (
    AutoTranslationMessage,
    ClientApprovedEventMessage,
    ClientSignupEventAdminMessage,
    ClientSignupEventMessage,
    DocComplexityErrorMessage,
    DocInvalidPdfErrorMessage,
    DocMtMessage,
    DocParseErrorMessage,
    EvaluateErrorMessage,
    EvaluateSuccessMessage,
    HumanJobQuoteMessage,
    JobCancelledEventMessage,
    JobCompletedEventMessage,
    JobCreationMessage,
    JobQuoteAcceptedEventMessage,
    JobQuoteCancelledEventMessage,
    JobQuotedEventMessage,
    JobStatusChangedEventMessage,
    JobTranscribedEventMessage,
    MachineTranslationMessage,
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
    SlackMessage,
    SuccessfulLoginMessage,
    VerifyCompleteMessage,
)
from ..slack.web import upload_file_to_slack_memory_efficient

router = APIRouter()

logger = logging.getLogger(__name__)

CALLBACK_ERROR_DETAIL_MAX_LENGTH = 500
RAY_EVENT_DEDUPE_TTL_SECONDS = 7 * 24 * 60 * 60


def _safe_callback_error_detail(payload_error: Any) -> str:
    error_detail = " ".join(str(payload_error or "").split())
    if not error_detail:
        return _("Unknown error")
    return error_detail[:CALLBACK_ERROR_DETAIL_MAX_LENGTH]


def _format_callback_error(stage: str, payload_error: Any) -> str:
    error_detail = _safe_callback_error_detail(payload_error)
    if stage == "transcription":
        return _("Transcription failed: {error_detail}")
    if stage == "translation":
        return _("Translation failed: {error_detail}")
    if stage == "embedding":
        return _("Embedding failed: {error_detail}")
    raise ValueError(f"Unsupported callback error stage: {stage}")


def _order_translations_by_target_language_order(
    translations: dict[str, Any],
    target_language_order: list[str] | None,
) -> dict[str, Any]:
    """Order translations by caller-requested target order, keeping leftovers."""
    if not target_language_order:
        return translations

    ordered_translations: dict[str, Any] = {}
    for target_language in target_language_order:
        if (
            target_language in translations
            and target_language not in ordered_translations
        ):
            ordered_translations[target_language] = translations[target_language]

    for target_language, translated_text in translations.items():
        if target_language not in ordered_translations:
            ordered_translations[target_language] = translated_text

    return ordered_translations


async def _claim_evaluate_complete_notification(event: RayEvent) -> bool:
    """Atomically claim a user-facing evaluate-complete notification."""
    client_id = event.data.get("client_id")
    job_uuid = event.data.get("job_uuid")
    if not client_id or not job_uuid:
        return True

    key = f"ray_event:{event.event}:{client_id}:{job_uuid}"
    try:
        was_set = await redis_conn.set(
            key,
            "1",
            ex=RAY_EVENT_DEDUPE_TTL_SECONDS,
            nx=True,
        )
    except Exception:
        logger.warning(
            "Failed to claim evaluate-complete notification idempotency key",
            exc_info=True,
        )
        return True

    if isinstance(was_set, bool):
        return was_set
    if was_set is None:
        return False
    if isinstance(was_set, str):
        return was_set.upper() == "OK"
    return bool(was_set)


def _resolve_event_thread_ts(
    extra_data: dict[str, Any] | None, event_data: dict[str, Any]
) -> str | None:
    """Resolve the best thread timestamp from task extra_data and the raw event payload."""
    return (
        (extra_data.get("slack_thread_ts") if extra_data else None)
        or event_data.get("thread_ts")
        or event_data.get("message_ts")
    )


async def _update_tokens_consumed(
    task_uuid: str,
    additional_tokens: int,
) -> int:
    """Update tokens_consumed in database and return total.

    Args:
        task_uuid: Task UUID to update
        additional_tokens: Tokens to add to current total

    Returns:
        Total tokens consumed after update
    """
    try:
        # Reload task_info to get current tokens_consumed
        task_info = await get_transcription_task(task_uuid)
        if not task_info:
            return additional_tokens

        total_tokens = task_info.tokens_consumed + additional_tokens

        # Update tokens_consumed in database
        async with AsyncSession(async_engines["sitecommons"]) as session:
            await session.execute(
                update(TranscriptionTask)
                .where(TranscriptionTask.task_uuid == task_uuid)
                .values(tokens_consumed=total_tokens)
            )
            await session.commit()

        return total_tokens
    except Exception as e:
        notify_exception(e, "Failed to update tokens_consumed")
        logger.error(f"Error updating tokens_consumed: {e}")
        return additional_tokens


async def _show_tokens_message(
    client: AsyncWebClient,
    task_uuid: str,
    channel_id: str,
    thread_ts: str | None,
    is_ibm: bool = False,
) -> None:
    """Show token consumption message at the end of pipeline completion.

    Args:
        client: Slack web client
        task_uuid: Task UUID to look up tokens
        channel_id: Channel ID to post message
        thread_ts: Thread timestamp for threaded messages
        is_ibm: Whether the user is IBM enterprise (skip token message if True)
    """
    # Don't show token messages for IBM enterprise users
    if is_ibm:
        return

    try:
        task_info = await get_transcription_task(task_uuid)
        if task_info and task_info.tokens_consumed > 0:
            await client.chat_postMessage(
                channel=channel_id,
                text=_(f"You have used {task_info.tokens_consumed} AI tokens."),
                thread_ts=thread_ts,
            )
    except Exception as e:
        notify_exception(e, "Failed to show tokens message")
        logger.error(f"Error showing tokens message: {e}")


async def _get_language_name(lang_code: str) -> str:
    """Get the full language name from a language code.

    Args:
        lang_code: 2-letter language code (e.g., "en", "id", "ga")

    Returns:
        Full language name (e.g., "English", "Indonesian", "Georgian") or the code if not found
    """
    try:
        languages = await _get_languages_cached()
        for lang in languages:
            if lang.get("code") == lang_code.lower():
                return lang.get("name", lang_code)
        return lang_code  # Fallback to code if not found
    except Exception:
        return lang_code  # Fallback to code on error


async def _spend_transcription_credits(
    task_info: TranscriptionTaskInfo,
    auth: Any,
) -> int:
    """Spend credits for transcription stage.

    Args:
        task_info: Transcription task information
        auth: Authentication context with slack_user

    Returns:
        Amount of credits spent (0 if already charged or no credits spent)
    """
    try:
        # Check if credits have already been spent for transcription
        extra_data = task_info.extra_data or {}
        charged_stages = extra_data.get("_charged_stages", [])
        if "transcription" in charged_stages:
            return 0  # Already charged

        if not auth.slack_user or not auth.slack_user.ray_user_group_id:
            return 0

        # Charge for transcription based on duration
        if not task_info.duration_ms:
            return 0

        amount = duration_to_tokens(task_info.duration_ms)

        if amount > 0:
            # Charge through the LanguageCloud API so the gateway writes the
            # self-describing credit_transaction_usage row (source language +
            # idempotency) atomically with the debit (RAY-80000 §3.2). This
            # replaces the direct credit-ledger write, which left no usage row.
            _tokens, transaction_uuid = await log_transcribe_by_client_id(
                client_id=auth.slack_user.ray_client_id,
                duration_ms=task_info.duration_ms,
                file_name=task_info.file_name or "",
                source_language=task_info.detected_language,
                idempotency_key=build_spend_idempotency_key(
                    app_source="slack",
                    submission_id=task_info.task_uuid,
                    service="transcription",
                    unit_type="milliseconds",
                ),
            )

            # Mark transcription as charged and store transaction UUID
            charged_stages.append("transcription")
            extra_data["_charged_stages"] = charged_stages
            async with AsyncSession(async_engines["sitecommons"]) as session:
                await session.execute(
                    update(TranscriptionTask)
                    .where(TranscriptionTask.task_uuid == task_info.task_uuid)
                    .values(
                        extra_data=extra_data,
                        credit_transaction_uuid=transaction_uuid,
                    )
                )
                await session.commit()

            return amount

        return 0

    except Exception as e:
        notify_exception(e, "Failed to spend credits for transcription stage")
        logger.error(f"Error spending credits for transcription: {e}")
        return 0


async def _spend_translation_credits(
    task_info: TranscriptionTaskInfo,
    auth: Any,
) -> int:
    """Mark translation stage as charged.

    Note: Actual credit spending and API usage logging is handled by
    int-slack-verify-consumer during SRT translation (async_spend_mt_token ->
    /mt/transaction, in subtitle_translation.py). This function only marks the
    stage as "charged" in extra_data to prevent duplicate processing.

    Args:
        task_info: Transcription task information
        auth: Authentication context with slack_user

    Returns:
        0 (credits are spent in the consumer, not here)
    """
    try:
        # Check if translation has already been marked as charged
        extra_data = task_info.extra_data or {}
        charged_stages = extra_data.get("_charged_stages", [])
        if "translation" in charged_stages:
            return 0  # Already marked

        if not auth.slack_user or not auth.slack_user.ray_user_group_id:
            return 0

        # Check if translation data exists
        if not task_info.source_text_length or not task_info.num_target_languages:
            return 0

        # Mark translation as charged in the database
        # Note: Actual credit spending happens in int-slack-verify-consumer via
        # async_spend_mt_token, which also logs to google_api_log for billing reports
        charged_stages.append("translation")
        extra_data["_charged_stages"] = charged_stages
        async with AsyncSession(async_engines["sitecommons"]) as session:
            await session.execute(
                update(TranscriptionTask)
                .where(TranscriptionTask.task_uuid == task_info.task_uuid)
                .values(extra_data=extra_data)
            )
            await session.commit()

        logger.info(
            f"Marked translation as charged for task {task_info.task_uuid} "
            f"(credits spent by cloud-verify-consumer)"
        )
        return 0

    except Exception as e:
        notify_exception(e, "Failed to mark translation stage as charged")
        logger.error(f"Error marking translation as charged: {e}")
        return 0


async def _spend_embedding_credits(
    task_info: TranscriptionTaskInfo,
    auth: Any,
) -> int:
    """Spend credits for embedding stage.

    Charges tokens based on duration and number of target languages.
    Subtitling cost: $0.60 per minute = 30 tokens per minute (at $0.02 per token).

    For embedding pipelines, this also ensures transcription and translation
    are charged first if they haven't been charged yet.

    Args:
        task_info: Transcription task information
        auth: Authentication context with slack_user

    Returns:
        Amount of tokens spent (0 if already charged or no credits spent)
    """
    try:
        # Reload task_info to get latest charged_stages (in case translation event already charged)
        reloaded_task_info = await get_transcription_task(task_info.task_uuid)
        if reloaded_task_info:
            task_info = reloaded_task_info

        # Check if credits have already been spent for embedding
        extra_data = task_info.extra_data or {}
        charged_stages = extra_data.get("_charged_stages", [])
        if "embedding" in charged_stages:
            return 0  # Already charged

        if not auth.slack_user or not auth.slack_user.ray_user_group_id:
            return 0

        # Charge for embedding based on duration and number of target languages
        if not task_info.duration_ms:
            return 0

        # Store duration_ms before reloads to preserve type
        duration_ms = task_info.duration_ms

        # Full modal embed: prior stages charge on their own callbacks. Thread
        # embed-only (pipeline_type embed) must not catch up transcribe/translate here.
        if not _is_embed_only_pipeline(task_info):
            if "transcription" not in charged_stages:
                await _spend_transcription_credits(task_info, auth)
                reloaded_task_info = await get_transcription_task(task_info.task_uuid)
                if not reloaded_task_info:
                    return 0
                task_info = reloaded_task_info
                extra_data = task_info.extra_data or {}
                charged_stages = extra_data.get("_charged_stages", [])

            if (
                "translation" not in charged_stages
                and task_info.source_text_length
                and task_info.num_target_languages
            ):
                await _spend_translation_credits(task_info, auth)
                reloaded_task_info = await get_transcription_task(task_info.task_uuid)
                if not reloaded_task_info:
                    return 0
                task_info = reloaded_task_info
                extra_data = task_info.extra_data or {}
                charged_stages = extra_data.get("_charged_stages", [])

        # Charge for embedding
        # Default to 1 target language if not specified (for transcribe_embed pipelines)
        num_target_languages = task_info.num_target_languages or 1

        # Calculate tokens: subtitling tokens per duration * number of target languages
        tokens_per_language = duration_to_subtitling_tokens(duration_ms)
        amount = tokens_per_language * num_target_languages

        if amount > 0:
            # Stable per-task key so a redelivered embedding result replays to a
            # single debit; service differs from transcription so the two stages
            # of the same task stay distinct charges (RAY-80000 §3.5).
            embedding_idempotency_key = build_spend_idempotency_key(
                app_source="slack",
                submission_id=task_info.task_uuid,
                service="media_embedding",
                unit_type="milliseconds",
            )
            # Charge through the LanguageCloud API so the gateway writes the
            # self-describing credit_transaction_usage row (idempotency; languages
            # are Not applicable for embedding) atomically with the debit
            # (RAY-80000 §3.5). This replaces the direct credit-ledger write, which
            # left no usage row.
            target_languages = _embedding_target_language_codes(task_info)
            await log_embedding_by_client_id(
                client_id=auth.slack_user.ray_client_id,
                duration_ms=duration_ms,
                num_target_languages=num_target_languages,
                target_languages=target_languages or None,
                source_language=_embedding_source_language(task_info),
                file_name=task_info.file_name,
                idempotency_key=embedding_idempotency_key,
            )

            # Mark embedding as charged in the database
            charged_stages.append("embedding")
            extra_data["_charged_stages"] = charged_stages
            async with AsyncSession(async_engines["sitecommons"]) as session:
                await session.execute(
                    update(TranscriptionTask)
                    .where(TranscriptionTask.task_uuid == task_info.task_uuid)
                    .values(extra_data=extra_data)
                )
                await session.commit()

            return amount

        return 0

    except Exception as e:
        notify_exception(e, "Failed to spend credits for embedding stage")
        logger.error(f"Error spending credits for embedding: {e}")
        return 0


async def _mark_stage_processed(
    task_uuid: str, stage: str, extra_data_to_merge: dict | None = None
) -> None:
    """Mark a processing stage as processed in the database.

    Args:
        task_uuid: The task UUID
        stage: The stage to mark as processed
        extra_data_to_merge: Optional dict to merge into extra_data (preserves other fields)
    """
    try:
        async with AsyncSession(async_engines["sitecommons"]) as session:
            task = await session.get(TranscriptionTask, task_uuid)
            if task:
                extra_data = (task.extra_data or {}).copy()
                # Merge any provided extra_data (preserves fields like _processed_file_id)
                if extra_data_to_merge:
                    extra_data.update(extra_data_to_merge)
                processed_stages = extra_data.get("_processed_stages", [])
                if stage not in processed_stages:
                    processed_stages.append(stage)
                    extra_data["_processed_stages"] = processed_stages
                    await session.execute(
                        update(TranscriptionTask)
                        .where(TranscriptionTask.task_uuid == task_uuid)
                        .values(extra_data=extra_data)
                    )
                    await session.commit()
                else:
                    # Still update extra_data even if stage already processed (to preserve merged fields)
                    if extra_data_to_merge:
                        await session.execute(
                            update(TranscriptionTask)
                            .where(TranscriptionTask.task_uuid == task_uuid)
                            .values(extra_data=extra_data)
                        )
                        await session.commit()
    except Exception as e:
        notify_exception(e, f"Failed to mark stage {stage} as processed")


async def _update_submission_status(extra_data: dict) -> None:
    """Update submission status to completed."""
    try:
        submission_id = extra_data.get("submission_id") if extra_data else None
        submission_ids = extra_data.get("submission_ids") if extra_data else None

        if submission_id is not None:
            updated_submission_status(
                submission_id=int(submission_id),
                processing_status=SubmissionStatus.COMPLETED,
            )
        elif submission_ids:
            for sid in submission_ids:
                if sid is not None:
                    updated_submission_status(
                        submission_id=int(sid),
                        processing_status=SubmissionStatus.COMPLETED,
                    )
    except Exception as e:
        notify_exception(e, "Failed to update submission status")


async def _handle_transcription_complete(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    task_info: TranscriptionTaskInfo,
    is_ibm: bool,
    channel_id: str,
    thread_ts: str | None,
    event: RayEvent,
    auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)],
    auth_slack_user: Any,
) -> None:
    """Handle transcription completion - show message and upload SRT file."""
    transcribed_message = JobTranscribedEventMessage(
        source_file_name=task_info.file_name or "",
        is_ibm_enterprise=is_ibm,
    )
    response = await post_notification(
        client,
        event,
        auth_slack_user,
        transcribed_message,
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    effective_thread_ts = thread_ts
    if (
        not effective_thread_ts
        and isinstance(response, AsyncSlackResponse)
        and isinstance(response.data, dict)
    ):
        effective_thread_ts = response.data.get("ts")

    if result_file_id and result_file_name:
        upload_channel_id: str | None = channel_id or (
            auth_slack_user.channel_id if auth_slack_user else None
        )

        if upload_channel_id and auth.slack_user is not None:
            await enqueue_transcription_upload(
                file_id=result_file_id,
                file_name=result_file_name,
                task_uuid=task_info.task_uuid,
                pipeline_type=task_info.pipeline_type,
                client_id=auth.slack_user.ray_client_id,
                channel_id=upload_channel_id,
                thread_ts=effective_thread_ts,
                follow_up_message=_(
                    "Download the AI translations provided above, make your edits, "
                    "and reupload the edited files back to the same thread."
                ),
            )


async def _handle_translation_complete(
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str | None,
    task_info: Any,
    auth: Any,
) -> None:
    """Handle translation completion - upload translated files."""
    translated_file_ids = task_info.translated_file_ids or {}

    status_response = await client.chat_postMessage(
        channel=channel_id,
        text=_("Your file is AI translated and can be downloaded below."),
        thread_ts=thread_ts,
    )
    effective_thread_ts = thread_ts or status_response.get("ts")

    # Upload each translated file
    # Use original file_name from database (the uploaded file) as base for naming
    original_file_name = task_info.file_name or "transcription.srt"
    original_path = Path(original_file_name)
    original_stem = original_path.stem

    for target_lang, file_id in translated_file_ids.items():
        try:
            # Download file from server
            output_file = await download_from_file_server_async(file_id)
            file_path = output_file.get("file")

            # Get full language name from code (e.g., "ja" -> "Japanese")
            lang_name = get_auto_translate_language_name(target_lang)

            # Construct filename using language name
            # Pattern: "filename.srt" -> "filename_Japanese.srt"
            title = f"{original_stem}_{lang_name}.srt"

            if not file_path:
                continue

            # Rename the temp file to have the correct filename so Slack displays it properly
            temp_dir = os.path.dirname(file_path)
            renamed_file_path = os.path.join(temp_dir, title)
            if file_path != renamed_file_path:
                os.rename(file_path, renamed_file_path)
                file_path = renamed_file_path

            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file_path,
                channel_id=channel_id,
                title=title,
                filename=title,
                thread_ts=effective_thread_ts,
            )

            # Clean up temp file
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)

        except Exception as e:
            notify_exception(e, "Error handling translation complete")
            logger.error(f"Error handling translation complete: {e}")

    # Show token message at the end for transcribe_translate pipeline
    if task_info.pipeline_type == "transcribe_translate":
        is_ibm = (
            is_ibm_enterprise(auth.slack_user.enterprise_id)
            if auth.slack_user
            else False
        )
        await _show_tokens_message(
            client,
            task_info.task_uuid,
            channel_id,
            effective_thread_ts,
            is_ibm=is_ibm,
        )


async def _handle_transcribe_embed_pipeline(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    task_info: TranscriptionTaskInfo,
    channel_id: str,
    thread_ts: str | None,
    auth: Any = None,
) -> None:
    """Handle transcription + translation + embed pipeline result."""
    if not result_file_id:
        return

    effective_thread_ts = thread_ts
    try:
        if not effective_thread_ts:
            anchor_response = await client.chat_postMessage(
                channel=channel_id,
                text=_("Your embedded media file is ready. Uploading now..."),
            )
            effective_thread_ts = anchor_response.get("ts")

        output_file = await download_from_file_server_async(result_file_id)
        file_path = output_file.get("file")
        if file_path and os.path.exists(file_path):
            # Upload file - this only returns after files_completeUploadExternal succeeds
            # which means Slack has processed and made the file available
            # Use the original filename, not the temp file path
            output_filename = result_file_name or task_info.file_name
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file_path,
                channel_id=channel_id,
                thread_ts=effective_thread_ts,
                title=output_filename,
                filename=output_filename,
                initial_comment=_(
                    "Your video with embedded subtitles is ready! "
                    "Please download the media file(s) to view the embedded subtitles."
                ),
            )
            os.unlink(file_path)

            # Show token message at the end for transcribe_translate_embed pipeline
            if task_info.pipeline_type == "transcribe_translate_embed":
                is_ibm = (
                    is_ibm_enterprise(auth.slack_user.enterprise_id)
                    if auth and auth.slack_user
                    else False
                )
                await _show_tokens_message(
                    client,
                    task_info.task_uuid,
                    channel_id,
                    effective_thread_ts,
                    is_ibm=is_ibm,
                )

    except Exception as e:
        notify_exception(e, "Error handling embedded video")
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "An error occurred while processing your embedded video. Please try again."
            ),
            thread_ts=effective_thread_ts,
        )


@router.post("/ray/events")
async def ray_events(
    event: RayEvent, auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)]
):
    """Receives and responds to an event from the RAY platform."""
    client = None
    user_info = None
    message: Optional[SlackMessage] = None
    if auth.slack_user:
        client = AsyncWebClient(token=auth.slack_user.bot_token)
        try:
            if auth.slack_user.user_id != auth.slack_user.ray_client_id:
                user_info = await client.users_info(
                    user=auth.slack_user.user_id, include_locale=True
                )
                set_user_language(user_info)
        except Exception as e:
            pass

        # Handle different event types directly
        is_ibm = (
            is_ibm_enterprise(auth.slack_user.enterprise_id)
            if auth.slack_user
            else False
        )

        if event.event == "ray:slack:account_connected":
            try:
                event_data = SlackAccountConnectedEvent.model_validate(event.data)
                # Get ray connection for the user
                ray_connection = await get_ray_connection(
                    event_data.user_id, event_data.team_id, event_data.enterprise_id
                )
                if ray_connection is None:
                    raise ValueError(
                        "Could not get ray connection for successful login"
                    )
                assert ray_connection is not None
                login_message: SuccessfulLoginMessage = SuccessfulLoginMessage(
                    event_data.user_id,
                    event_data.username,
                    ray_connection,
                    event_data.enterprise_id,
                )
                # Send login message to the same conversation where it was prompted
                await post_notification_ephemeral(
                    client,
                    auth.slack_user.channel_id,
                    event,
                    auth.slack_user,
                    login_message,
                )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:client:signup":
            try:
                signup_event = ClientSignupEvent.model_validate(event.data)
                signup_message: ClientSignupEventMessage = ClientSignupEventMessage(
                    signup_event
                )
                # Send important messages regardless of subscribed status
                if auth.slack_user.is_subscribed or True:  # Always send signup messages
                    if auth.demo_slack_users:
                        for slack_user_id in auth.demo_slack_users:
                            new_slack_user = replace(
                                auth.slack_user, user_id=slack_user_id
                            )
                            try:
                                await post_notification(
                                    client, event, new_slack_user, signup_message
                                )
                            except Exception as e:
                                notify_exception(
                                    e,
                                    "Failed to send notification to send demo message",
                                )
                    else:
                        await post_notification(
                            client, event, auth.slack_user, signup_message
                        )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:client:approved":
            try:
                approved_event = ClientApprovedEvent.model_validate(event.data)
                group_names = [group.label for group in approved_event.groups]
                approved_message: ClientApprovedEventMessage = (
                    ClientApprovedEventMessage(group_names)
                )
                # Send important messages regardless of subscribed status
                if (
                    auth.slack_user.is_subscribed or True
                ):  # Always send approved messages
                    if auth.demo_slack_users:
                        for slack_user_id in auth.demo_slack_users:
                            new_slack_user = replace(
                                auth.slack_user, user_id=slack_user_id
                            )
                            try:
                                await post_notification(
                                    client, event, new_slack_user, approved_message
                                )
                            except Exception as e:
                                notify_exception(
                                    e,
                                    "Failed to send notification to send demo message",
                                )
                    else:
                        await post_notification(
                            client, event, auth.slack_user, approved_message
                        )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:status_changed":
            try:
                status_event = JobStatusChangedEvent.model_validate(event.data)
                status_event.status = status_event.status.strip().upper()
                status_event.previous_status = (
                    status_event.previous_status.strip().upper()
                    if status_event.previous_status
                    else None
                )
                # Do not send notification if quote is accepted or cancelled,
                # send those notifications instead.
                if status_event.status in (
                    "LEAD",
                    "IN_PROGRESS",
                    "VALIDATION",
                    "REFUNDED",
                ):
                    message: SlackMessage = JobStatusChangedEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                        status=status_event.status,
                        is_ibm=is_ibm,
                    )
                elif status_event.status == "COMPLETED":
                    message: SlackMessage = JobCompletedEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                        target_languages=[lang.label for lang in status_event.tl],
                        is_ibm=is_ibm,
                    )
                elif status_event.status == "CANCELLED":
                    message: SlackMessage = JobCancelledEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                    )

                # Send message if we have one and user is subscribed
                if message is not None and auth.slack_user.is_subscribed:
                    if isinstance(message, JobCompletedEventMessage):
                        if not is_verify_job(event.data["uuid"]):
                            await post_notification(
                                client, event, auth.slack_user, message
                            )
                    else:
                        await post_notification(client, event, auth.slack_user, message)
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:quote_created":
            try:
                quote_event = JobQuoteCreatedEvent.model_validate(event.data)
                quote_message: JobQuotedEventMessage = JobQuotedEventMessage(
                    quote_event, is_ibm
                )
                # Send message if user is subscribed
                if auth.slack_user.is_subscribed:
                    await post_notification(
                        client, event, auth.slack_user, quote_message
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:quote_accepted":
            try:
                quote_accepted_event = JobQuoteAcceptedEvent.model_validate(event.data)
                accepted_message: JobQuoteAcceptedEventMessage = (
                    JobQuoteAcceptedEventMessage(quote_accepted_event, is_ibm)
                )
                # Send message if user is subscribed
                if auth.slack_user.is_subscribed:
                    await post_notification(
                        client, event, auth.slack_user, accepted_message
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:quote_cancelled":
            try:
                quote_cancelled_event = JobQuoteCancelledEvent.model_validate(
                    event.data
                )
                cancelled_message: JobQuoteCancelledEventMessage = (
                    JobQuoteCancelledEventMessage(
                        client_id=quote_cancelled_event.client_id,
                        job_uuid=quote_cancelled_event.uuid,
                        job_id=quote_cancelled_event.id,
                        is_ibm=is_ibm,
                    )
                )
                # Send message if user is subscribed
                if auth.slack_user.is_subscribed:
                    await post_notification(
                        client, event, auth.slack_user, cancelled_message
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "transcription:slack:media:transcription:results":
            # Handle transcription completion events
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get task info from database
                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Handle errors
                extra_data = task_info.extra_data or {}
                if transcribed_event.error or event.data.get("error"):
                    thread_ts = _resolve_event_thread_ts(extra_data, event.data)
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=_format_callback_error("transcription", error_msg),
                        thread_ts=thread_ts,
                    )
                    return

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = _resolve_event_thread_ts(extra_data, event.data)

                # Track processed stages for reference
                processed_stages = extra_data.get("_processed_stages", [])
                if "transcription" not in processed_stages:
                    # Mark as processed FIRST to prevent race condition
                    await _mark_stage_processed(
                        transcribed_event.task_uuid, "transcription"
                    )
                    await _handle_transcription_complete(
                        client,
                        task_info.result_file_id,
                        task_info.result_file_name,
                        task_info,
                        is_ibm,
                        str(channel_id),
                        thread_ts,
                        event,
                        auth,
                        auth.slack_user,
                    )
                    # Spend credits for transcription
                    transcription_tokens = await _spend_transcription_credits(
                        task_info, auth
                    )
                    # Track total tokens (message will be shown after file upload completes)
                    if transcription_tokens > 0:
                        await _update_tokens_consumed(
                            task_info.task_uuid, transcription_tokens
                        )

            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "transcription:slack:media:translation:results":
            # Handle translation completion events
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get task info from database
                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Handle errors
                extra_data = task_info.extra_data or {}
                if transcribed_event.error or event.data.get("error"):
                    thread_ts = _resolve_event_thread_ts(extra_data, event.data)
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=_format_callback_error("translation", error_msg),
                        thread_ts=thread_ts,
                    )
                    return

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = _resolve_event_thread_ts(extra_data, event.data)

                # Track processed stages for reference
                processed_stages = extra_data.get("_processed_stages", [])
                if (
                    "translation" not in processed_stages
                    and task_info.translated_file_ids
                ):
                    # Mark as processed FIRST to prevent race condition
                    await _mark_stage_processed(
                        transcribed_event.task_uuid, "translation"
                    )
                    await _handle_translation_complete(
                        client,
                        str(channel_id),
                        thread_ts,
                        task_info,
                        auth,
                    )
                    # Spend credits for translation
                    translation_tokens = await _spend_translation_credits(
                        task_info, auth
                    )
                    # Track total tokens (message will be shown after file upload completes)
                    if translation_tokens > 0:
                        await _update_tokens_consumed(
                            transcribed_event.task_uuid, translation_tokens
                        )

            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "transcription:slack:media:embedding:results":
            # Handle embedding completion events
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get task info from database
                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Handle errors
                extra_data = task_info.extra_data or {}
                if transcribed_event.error or event.data.get("error"):
                    thread_ts = _resolve_event_thread_ts(extra_data, event.data)
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=_format_callback_error("embedding", error_msg),
                        thread_ts=thread_ts,
                    )
                    return

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = _resolve_event_thread_ts(extra_data, event.data)

                # Track processed stages for reference
                processed_stages = extra_data.get("_processed_stages", [])
                if "embedding" not in processed_stages:
                    # Mark as processed FIRST to prevent race condition
                    await _mark_stage_processed(
                        transcribed_event.task_uuid, "embedding"
                    )
                    await _handle_transcribe_embed_pipeline(
                        client,
                        task_info.result_file_id,
                        task_info.result_file_name,
                        task_info,
                        str(channel_id),
                        thread_ts,
                        auth,
                    )
                    # Spend credits for embedding
                    embedding_tokens = await _spend_embedding_credits(task_info, auth)
                    # Track total tokens (message will be shown after file upload completes)
                    if embedding_tokens > 0:
                        await _update_tokens_consumed(
                            transcribed_event.task_uuid, embedding_tokens
                        )
                    await _update_submission_status(extra_data)

            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "verify:slack:document:translated":
            # Handle MT success/error
            try:
                document_translated_data = MtErrorResponseSchema.model_validate(
                    event.data
                )
                # Update submission status to FAILED if submission_id is present
                if document_translated_data.submission_id:
                    updated_submission_status(
                        submission_id=document_translated_data.submission_id,
                        processing_status=SubmissionStatus.FAILED,
                    )
                document_message: Optional[SlackMessage] = None
                if document_translated_data.error_type == "insufficient_balance":
                    # Send message to user that they need to purchase tokens
                    client_type = await get_client_type(
                        auth.slack_user.ray_client_id,
                        auth.slack_user.ray_user_group_id,
                    )
                    balance = Balance.model_validate(
                        document_translated_data.error_data
                    )

                    if client_type in ["Admin", "Owner"] and not is_ibm_enterprise(
                        auth.slack_user.enterprise_id
                    ):
                        document_message: SlackMessage = RequiresMtTokenMessage(
                            balance.balance, balance.required
                        )
                    else:
                        document_message: SlackMessage = RequiresMtTokenAdminMessage(
                            balance.balance, balance.required
                        )
                elif document_translated_data.error_type == "conversion_error":
                    document_message: SlackMessage = DocParseErrorMessage(
                        document_translated_data.error_data.get("ext", ""),
                        document_translated_data.error_data.get("file_expected", ""),
                    )
                elif document_translated_data.error_type == "file_complexity_error":
                    document_message: SlackMessage = DocComplexityErrorMessage(
                        document_translated_data.error_data.get("ext", ""),
                    )
                elif document_translated_data.error_type == "invalid_pdf":
                    document_message: SlackMessage = DocInvalidPdfErrorMessage(
                        document_translated_data.error_data.get("message", ""),
                    )
                else:
                    document_message: SlackMessage = DocMtMessage()
                if document_message is not None:
                    await post_notification_ephemeral(
                        client,
                        document_translated_data.channel_id
                        or auth.slack_user.channel_id,
                        event,
                        auth.slack_user,
                        document_message,
                    )
            except ValidationError:
                success_data = MtSuccessResponseSchema.model_validate(event.data)
                await enqueue_mt_success_upload(success_data)

        elif event.event == "verify:slack:evaluate:complete":
            if not await _claim_evaluate_complete_notification(event):
                logger.info(
                    "Skipping duplicate evaluate-complete notification for job %s",
                    event.data.get("job_uuid"),
                )
                return {"message": "Duplicate evaluate-complete event skipped"}

            if event.data.get("error"):
                try:
                    error_data = MtErrorResponseSchema.model_validate(event.data)
                    if error_data.error_type == "insufficient_balance":
                        # Send message to user that they need to purchase tokens
                        client_type = await get_client_type(
                            auth.slack_user.ray_client_id,
                            auth.slack_user.ray_user_group_id,
                        )
                        balance = Balance.model_validate(error_data.error_data)

                        if client_type in ["Admin", "Owner"] and not is_ibm_enterprise(
                            auth.slack_user.enterprise_id
                        ):
                            message: SlackMessage = RequiresMtTokenMessage(
                                balance.balance, balance.required
                            )
                        else:
                            message: SlackMessage = RequiresMtTokenAdminMessage(
                                balance.balance, balance.required
                            )
                    elif error_data.error_type == "conversion_error":
                        message: SlackMessage = DocParseErrorMessage(
                            error_data.error_data.get("ext", ""),
                            error_data.error_data.get("file_expected", ""),
                        )
                    elif error_data.error_type == "file_complexity_error":
                        message: SlackMessage = DocComplexityErrorMessage(
                            error_data.error_data.get("ext", ""),
                        )
                    elif error_data.error_type == "invalid_pdf":
                        message: SlackMessage = DocInvalidPdfErrorMessage(
                            error_data.error_data.get("message", ""),
                        )
                    else:
                        # For "other" or any other error type, use generic error message
                        message: SlackMessage = EvaluateErrorMessage()
                except ValidationError:
                    # If validation fails, fall back to generic error message
                    message: SlackMessage = EvaluateErrorMessage()
            else:
                try:
                    job = await get_evaluation_job(
                        auth.slack_user, event.data["job_uuid"]
                    )
                    if job["data"].get("human_job_in_progress", False):
                        raise ValueError(f"Invalid RAY event type: {event.event}")
                    all_langs = await _get_languages_cached()
                    if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
                        ray_client = await get_ray_client(
                            auth.slack_user.user_id, auth.slack_user.team_id
                        )
                        if ray_client is None:
                            raise ValueError("Could not get ray client for job pricing")
                        costs = await get_job_pricing(
                            ray_client,
                            job["data"]["uuid"],
                            [file["file_uuid"] for file in job["data"]["source_files"]],
                            [lang["uuid"] for lang in job["data"]["target_languages"]],
                        )
                        message: SlackMessage = HumanJobQuoteMessage(
                            job["data"], costs["data"]
                        )
                    else:
                        message: SlackMessage = EvaluateSuccessMessage(
                            job["data"], is_ibm, event.data["tokens"]
                        )
                except Exception as e:
                    raise HTTPException(
                        422,
                        {
                            "message": f"Error processing evaluation complete event: {str(e)}",
                        },
                    ) from e

            # Send message if we have one
            if message is not None:
                await post_notification(
                    client,
                    event,
                    auth.slack_user,
                    message,
                )
        elif event.event == "verify:human_verification:completed":
            try:
                all_langs = await _get_languages_cached()
                lang_label = ""
                for lang in all_langs:
                    if lang["uuid"] == event.data["lang_uuid"]:
                        lang_label = lang["name"]
                        break
                verify_message: VerifyCompleteMessage = VerifyCompleteMessage(
                    event.data["job_title"], lang_label
                )
                # Send message and enqueue durable file upload (RAY-79638)
                response = await post_notification(
                    client,
                    event,
                    auth.slack_user,
                    verify_message,
                )
                upload_channel_id = (
                    response.data["channel"] if isinstance(response.data, dict) else ""
                )
                if upload_channel_id and auth.slack_user is not None:
                    await enqueue_verify_complete_upload(
                        grid_file_id=event.data["grid_file_id"],
                        client_id=auth.slack_user.ray_client_id,
                        channel_id=upload_channel_id,
                    )
            except Exception as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing human verification complete event: {str(e)}",
                    },
                ) from e

        elif event.event == "slack:direct:mt:result":
            try:
                mt_result_extra_data = MtTranslationExtraData.model_validate(
                    event.data["extra_data"]
                )

                # Parse translations from the new service-based response format
                translations = event.data.get("translations", {})

                if (
                    mt_result_extra_data.usage_type == "direct_machine_translation"
                    or mt_result_extra_data.usage_type == "shortcut_translate"
                ):
                    # For direct translation, we need to get the first target language
                    # and combine all translations into a single string
                    first_target_lang = None
                    for (
                        lang_glossary_map
                    ) in mt_result_extra_data.service_language_mapping.values():
                        if lang_glossary_map:
                            first_target_lang = next(iter(lang_glossary_map.keys()))
                            break

                    # Combine all translations into a single string
                    # The translations dict should be {lang: [text1, text2, ...]}
                    combined_translations = " ".join(
                        [
                            text
                            for lang_translations in translations.values()
                            for text in (
                                lang_translations
                                if isinstance(lang_translations, list)
                                else [lang_translations]
                            )
                        ]
                    )
                    assert mt_result_extra_data.source_text
                    mt_result_message: MachineTranslationMessage = (
                        MachineTranslationMessage(
                            first_target_lang or "unknown",
                            mt_result_extra_data.source_language,
                            mt_result_extra_data.source_text,
                            combined_translations,
                        )
                    )
                    # Send message with response method configuration
                    await post_notification(
                        client,
                        event,
                        auth.slack_user,
                        mt_result_message,
                        channel_id=mt_result_extra_data.channel_id,
                        thread_ts=mt_result_extra_data.thread_ts,
                        is_edit=mt_result_extra_data.is_edit,
                        response_url=mt_result_extra_data.response_url,
                    )
                elif mt_result_extra_data.usage_type == "channel_translation":
                    # For channel translation, pass the translations dict directly
                    # The AutoTranslationMessage expects {lang: [text1, text2, ...]} format
                    translations = _order_translations_by_target_language_order(
                        translations,
                        mt_result_extra_data.target_language_order,
                    )
                    assert mt_result_extra_data.source_text
                    auto_translation_message: AutoTranslationMessage = (
                        AutoTranslationMessage(
                            mt_result_extra_data.source_text,
                            mt_result_extra_data.source_language,
                            translations=translations,
                        )
                    )
                    try:
                        await post_channel_translation_notification(
                            client,
                            event,
                            auth.slack_user,
                            auto_translation_message,
                            channel_id=mt_result_extra_data.channel_id,
                            thread_ts=mt_result_extra_data.thread_ts,
                            is_edit=mt_result_extra_data.is_edit,
                            display_format=mt_result_extra_data.display_format,
                            message_ts=mt_result_extra_data.message_ts,
                        )
                    except SlackApiError as e:
                        notify_exception(
                            e, "Failed to post channel translation notification"
                        )
                        raise HTTPException(
                            422,
                            {
                                "message": f"Failed to post channel translation notification: {e.response.get('error', 'unknown error')}",
                            },
                        ) from e
                else:
                    raise HTTPException(
                        422,
                        {
                            "message": f"Invalid usage type: {mt_result_extra_data.usage_type}",
                        },
                    )
                assert auth.slack_user.ray_user_group_id is not None
                # Resolve a friendly channel name first so it can be persisted on
                # the usage row as well as the Google API usage log.
                channel_name = None
                if mt_result_extra_data.channel_id:
                    if not mt_result_extra_data.channel_id.startswith("C"):
                        channel_name = "direct message"
                    elif mt_result_extra_data.usage_type == "shortcut_translate":
                        channel_name = "shortcut translation"
                    else:
                        try:
                            channel_info = await client.conversations_info(
                                channel=mt_result_extra_data.channel_id
                            )
                            channel = channel_info.get("channel")
                            channel_name = (
                                channel["name"]
                                if channel and "name" in channel
                                else None
                            )
                        except Exception as e:
                            notify_exception(e, "Failed to get channel info")

                # One billed entry per (service, language) pair so the gateway's
                # ceil(text_length * len(target_languages) * 0.1) reproduces the
                # prior calculate_cost(text_length * total_languages) amount.
                billed_target_languages = [
                    lang
                    for lang_glossary_map in (
                        mt_result_extra_data.service_language_mapping.values()
                    )
                    for lang in lang_glossary_map
                ]
                # MT services contributing to this debit, recorded as context.
                engine = (
                    ",".join(sorted(mt_result_extra_data.service_language_mapping))
                    or None
                )
                # Per-message key so a redelivered MT result replays to a single
                # debit. The submission id combines the message ts with a
                # fingerprint of the translated text so a genuine *edit* (same ts,
                # new content) is still charged, while a pure redelivery (same ts,
                # same content) dedupes. Omitted when no message ts is available so
                # the charge stays back-compatible (RAY-80000 §3.4).
                inline_idempotency_key = None
                if mt_result_extra_data.message_ts:
                    content_fingerprint = hashlib.sha256(
                        (mt_result_extra_data.source_text or "").encode("utf-8")
                    ).hexdigest()[:16]
                    inline_idempotency_key = build_spend_idempotency_key(
                        app_source="slack",
                        submission_id=(
                            f"{mt_result_extra_data.message_ts}:{content_fingerprint}"
                        ),
                        service=mt_result_extra_data.usage_type,
                        unit_type="characters",
                    )
                # Channel/shortcut MT is billed against the group, so the usage
                # report cannot resolve the poster from client_uuid. Send the
                # Slack user identity so the usage row carries it (RAY-80000).
                slack_profile = user_info["user"]["profile"] if user_info else {}
                user_email = slack_profile.get("email") or None
                user_name = slack_profile.get("real_name") or None

                # Charge through the LanguageCloud API so the gateway writes the
                # self-describing credit_transaction_usage row (languages, engine,
                # idempotency) atomically with the debit (RAY-80000 §3.4). This
                # replaces the direct credit-ledger write, which left no usage row.
                transaction_uuid = await log_inline_mt_usage_by_client_id(
                    client_id=auth.slack_user.ray_client_id,
                    text_length=mt_result_extra_data.text_length,
                    target_languages=billed_target_languages,
                    usage_type=mt_result_extra_data.usage_type,
                    source_language=mt_result_extra_data.source_language,
                    engine=engine,
                    channel_name=channel_name,
                    idempotency_key=inline_idempotency_key,
                    email=user_email,
                    client_name=user_name,
                )

                # Log Google API usage
                # Convert translations from dict[lang, list[str]] to dict[lang, str]
                translations_for_log = {
                    lang: " ".join(texts) if isinstance(texts, list) else texts
                    for lang, texts in translations.items()
                }

                await log_google_api_usage(
                    user_uuid=auth.slack_user.ray_client_id,
                    group_uuid=auth.slack_user.ray_user_group_id,
                    organization_uuid=mt_result_extra_data.organization_uuid,
                    input_text=mt_result_extra_data.source_text
                    or "[Source text not available]",
                    source_lang=mt_result_extra_data.source_language,
                    translations=translations_for_log,
                    transaction_uuid=transaction_uuid,
                    app_name="slack",
                    usage_type=mt_result_extra_data.usage_type,
                    text_length=mt_result_extra_data.text_length,
                    channel_name=channel_name,
                    email=user_email,
                )
            except Exception as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing MT result event: {str(e)}",
                    },
                ) from e

        else:
            raise HTTPException(
                400, f"The event type is invalid: {event.event}"
            ) from None

    # Send notifications to group admins when a new client signs up.
    if (
        event.event == "ray:client:signup"
        and auth.slack_user is not None
        and client is not None
    ):
        try:
            signup_event = ClientSignupEvent.model_validate(event.data)
            admins: dict[str, tuple[SlackUser, list[ClientGroup]]] = {}
            for group in signup_event.groups:
                admin_slack_users = await get_group_admin_slack_users(group.uuid)
                for admin in admin_slack_users:
                    if admin.ray_client_id not in admins:
                        admins[admin.ray_client_id] = (admin, [])
                    admins[admin.ray_client_id][1].append(group)

            for user, groups in admins.values():
                admin_message = ClientSignupEventAdminMessage(
                    event=signup_event,
                    groups=groups,
                )
                assert client is not None
                await post_notification(client, event, user, admin_message)
        except ValidationError:
            # If validation fails, skip admin notifications
            pass

    return {"message": "success", "data": {"event": event.event}}


class RayCallback(BaseModel):
    """The expected body format for the RAY callback endpoint."""

    event_types: list[str]
    job: list[dict[str, Any]]


@router.post("/ray/callback")
async def api_job_callback(
    request: Request,
    client_id: str,
    body: RayCallback,
    x_straker_signature: Annotated[str, Header()],
):
    """Callback endpoint for API jobs."""
    # Check if the callback can be linked to a Slack user.
    slack_user = await get_slack_user(client_id)
    demo_slack_users = await get_demo_link(client_id)
    if slack_user is None:
        notify_message("Slack user not found in callback endpoint", severity="WARNING")
        raise HTTPException(401)
    client = AsyncWebClient(token=slack_user.bot_token)
    user_info = await client.users_info(user=slack_user.user_id, include_locale=True)
    set_user_language(user_info)
    # Validate X-Straker-Signature.
    raw_body = await request.body()
    access_tokens = await get_client_access_tokens(slack_user.ray_client_id)
    is_header_valid = any(
        validate_api_callback_signature(raw_body, token, x_straker_signature)
        for token in access_tokens
    )
    if not is_header_valid:
        notify_message("Callback X-Straker-Signature is invalid", severity="WARNING")
        raise HTTPException(401)

    # Handle job creation and job completed callbacks.
    if "JOB_NUMBER" in body.event_types:
        try:
            job_data = body.job[0]
            is_auto_quote = True
            if is_ibm_enterprise(slack_user.enterprise_id):
                is_auto_quote = False
                is_auto_quote = await get_job_group_quote_settings(
                    job_data["tj_number"][2:]
                )
            if is_auto_quote:
                message = JobCreationMessage(job_data["tj_number"], True)
        except (KeyError, IndexError):
            raise HTTPException(422, "The callback payload format is invalid") from None
        if demo_slack_users:
            for slack_user_id in demo_slack_users:
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text=message.text,
                    blocks=message.blocks,
                )
        else:
            if is_auto_quote:
                await client.chat_postMessage(
                    channel=slack_user.user_id,
                    text=message.text,
                    blocks=message.blocks,
                )
        return {
            "message": "success",
            "detail": "Slack user notified of event: JOB_NUMBER",
        }
    else:
        return {
            "message": "success",
            "detail": f"Unhandled callback event: {body.event_types}",
        }

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Sequence

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.connector import (
    build_spend_idempotency_key,
    duration_to_subtitling_tokens,
    duration_to_tokens,
    log_embedding_by_client_id,
    log_transcribe_by_client_id,
)
from app.database import async_engines
from app.media.embed_spend import (
    embedding_source_language,
    embedding_target_language_codes,
    is_embed_only_pipeline,
)
from app.media.media_workflow import (
    MediaEmbedRole,
    MediaWorkflowCommand,
    MediaWorkflowEvent,
    MediaWorkflowStage,
    MediaWorkflowTransitionError,
    MediaWorkflowType,
    advance_media_workflow,
    media_workflow_session_from_quote,
)
from app.models import TranscriptionTask, TranscriptionTaskInfo
from app.ray.events.logging import post_notification
from app.ray.utils import download_from_file_server_async, is_ibm_enterprise
from app.saq_jobs.dispatch import enqueue_transcription_upload
from app.slack.buglog_notifier import notify_exception
from app.slack.media_quote_actions import (
    auto_accept_media_translation_quote_if_needed,
    post_media_quote_message,
)
from app.slack.media_quote_adjustment import media_translation_quote_from_session
from app.slack.media_quotes import (
    ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
    ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_DONE,
    STAGE_TRANSCRIBING,
    get_media_quote_session,
    media_translation_tokens,
    update_media_quote_session,
)
from app.slack.select_options import _get_languages_cached
from app.slack.templates.messages import (
    JobTranscribedEventMessage,
    MediaEmbeddingPartialMessage,
    MediaTranslationPartialMessage,
)
from app.slack.web import upload_file_to_slack_memory_efficient
from app.transcriber_tasks.tasks import get_transcription_task
from app.translate import _

from ...dependencies import RayEvent
from ..submissions import SubmissionStatus, updated_submission_status

logger = logging.getLogger(__name__)


def _task_extra_data(task_info: Any) -> dict[str, Any]:
    extra = getattr(task_info, "extra_data", None)
    model_dump = getattr(extra, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return dict(extra) if isinstance(extra, dict) else {}


async def _advance_configure_after_translation(
    client: AsyncWebClient,
    extra: dict[str, Any],
    channel_id: str,
    thread_ts: str | None,
) -> None:
    quote_id = extra.get("media_quote_id")
    if not quote_id:
        return
    session = await get_media_quote_session(str(quote_id))
    workflow = media_workflow_session_from_quote(session) if session else None
    if workflow is None or session is None:
        return
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    try:
        decision = advance_media_workflow(
            workflow, MediaWorkflowEvent.TRANSLATION_COMPLETED
        )
    except MediaWorkflowTransitionError as exc:
        notify_exception(exc)
        return
    session["channel_id"] = session.get("channel_id") or channel_id
    session["thread_ts"] = session.get("thread_ts") or thread_ts
    await execute_media_workflow_decision(
        client=client, session=session, decision=decision
    )


async def _advance_configure_after_embed(
    client: AsyncWebClient,
    extra: dict[str, Any],
    channel_id: str,
    thread_ts: str | None,
) -> None:
    quote_id = extra.get("media_quote_id")
    if not quote_id:
        return
    session = await get_media_quote_session(str(quote_id))
    workflow = media_workflow_session_from_quote(session) if session else None
    if workflow is None or session is None:
        return
    role = extra.get("embed_role")
    if role == MediaEmbedRole.SOURCE:
        event = MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    elif role == MediaEmbedRole.TRANSLATED:
        event = MediaWorkflowEvent.TRANSLATED_EMBED_COMPLETED
    elif workflow.stage is MediaWorkflowStage.EMBEDDING_SOURCE:
        event = MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    elif workflow.stage is MediaWorkflowStage.EMBEDDING_TRANSLATED:
        event = MediaWorkflowEvent.TRANSLATED_EMBED_COMPLETED
    elif workflow.stage is MediaWorkflowStage.AWAITING_TRANSLATION_ACCEPT:
        event = MediaWorkflowEvent.SOURCE_EMBED_COMPLETED
    else:
        return
    try:
        decision = advance_media_workflow(workflow, event)
    except MediaWorkflowTransitionError as exc:
        notify_exception(exc)
        return
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    session["channel_id"] = session.get("channel_id") or channel_id
    session["thread_ts"] = session.get("thread_ts") or thread_ts
    await execute_media_workflow_decision(
        client=client, session=session, decision=decision
    )


def resolve_event_thread_ts(
    extra_data: dict[str, Any] | None, event_data: dict[str, Any]
) -> str | None:
    """Resolve the best thread timestamp from task extra_data and the raw event payload."""
    return (
        (extra_data.get("slack_thread_ts") if extra_data else None)
        or event_data.get("thread_ts")
        or event_data.get("message_ts")
    )


async def update_tokens_consumed(
    task_uuid: str,
    additional_tokens: int,
) -> int:
    """Update tokens_consumed in database and return total."""
    try:
        task_info = await get_transcription_task(task_uuid)
        if not task_info:
            return additional_tokens

        total_tokens = task_info.tokens_consumed + additional_tokens

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


async def show_tokens_message(
    client: AsyncWebClient,
    task_uuid: str,
    channel_id: str,
    thread_ts: str | None,
    is_ibm: bool = False,
) -> None:
    """Show token consumption message at the end of pipeline completion."""
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


async def get_language_name(lang_code: str) -> str:
    """Get the full language name from a language code."""
    try:
        languages = await _get_languages_cached()
        for lang in languages:
            if lang.get("code") == lang_code.lower():
                return lang.get("name", lang_code)
        return lang_code
    except Exception:
        return lang_code


async def get_language_name_by_uuid(lang_uuid: str) -> str:
    """Get the full language name from a language UUID."""
    try:
        languages = await _get_languages_cached()
        for lang in languages:
            if lang.get("uuid") == lang_uuid:
                return lang.get("name", "")
        return ""
    except Exception:
        return ""


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


async def resolve_language_labels(languages: Sequence[str] | None) -> list[str]:
    """Resolve target language identifiers to display names.

    sup-subtitle-ai-cons reports `failed_languages` as language codes, but the
    catalogue is also keyed by UUID, so accept either and never show the user a
    raw identifier when a name is available.
    """
    labels: list[str] = []
    for language in languages or []:
        if not language:
            continue
        label = ""
        if _UUID_RE.match(language):
            label = await get_language_name_by_uuid(language)
        if not label or label == language:
            label = get_auto_translate_language_name(language)
        if not label or label == language:
            label = await get_language_name(language)
        labels.append(label or language)
    return labels


async def spend_transcription_credits(
    task_info: TranscriptionTaskInfo,
    auth: Any,
) -> int:
    """Spend credits for transcription stage."""
    try:
        extra_data = task_info.extra_data or {}
        charged_stages = extra_data.get("_charged_stages", [])
        if "transcription" in charged_stages:
            return 0

        if not auth.slack_user or not auth.slack_user.ray_user_group_id:
            return 0

        if not task_info.duration_ms:
            return 0

        amount = duration_to_tokens(task_info.duration_ms)

        if amount > 0:
            poster_email = (
                extra_data.get("requester_email") or extra_data.get("email") or ""
            ).strip() or None
            poster_name = (
                extra_data.get("client_name") or extra_data.get("slack_user_name") or ""
            ).strip() or None
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
                submission_group_uuid=task_info.task_uuid,
                group_uuid=auth.slack_user.ray_user_group_id,
                email=poster_email,
                client_name=poster_name,
            )

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


async def spend_translation_credits(
    task_info: TranscriptionTaskInfo,
    auth: Any,
) -> int:
    """Mark translation stage as charged; the consumer performs the real MT debit."""
    try:
        extra_data = task_info.extra_data or {}
        charged_stages = extra_data.get("_charged_stages", [])
        if "translation" in charged_stages:
            return 0

        if not auth.slack_user or not auth.slack_user.ray_user_group_id:
            return 0

        if not task_info.source_text_length or not task_info.num_target_languages:
            return 0

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
        logger.error(f"Error marking translation stage as charged: {e}")
        return 0


async def spend_embedding_credits(
    task_info: TranscriptionTaskInfo,
    auth: Any,
) -> int:
    """Spend credits for embedding stage."""
    try:
        reloaded_task_info = await get_transcription_task(task_info.task_uuid)
        if reloaded_task_info:
            task_info = reloaded_task_info

        extra_data = task_info.extra_data or {}
        charged_stages = extra_data.get("_charged_stages", [])
        if "embedding" in charged_stages:
            return 0

        if not auth.slack_user or not auth.slack_user.ray_user_group_id:
            return 0

        if not task_info.duration_ms:
            return 0

        duration_ms = task_info.duration_ms

        if not is_embed_only_pipeline(task_info):
            if "transcription" not in charged_stages:
                await spend_transcription_credits(task_info, auth)
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
                await spend_translation_credits(task_info, auth)
                reloaded_task_info = await get_transcription_task(task_info.task_uuid)
                if not reloaded_task_info:
                    return 0
                task_info = reloaded_task_info
                extra_data = task_info.extra_data or {}
                charged_stages = extra_data.get("_charged_stages", [])

        num_target_languages = task_info.num_target_languages or 1
        tokens_per_language = duration_to_subtitling_tokens(duration_ms)
        amount = tokens_per_language * num_target_languages

        if amount > 0:
            embedding_idempotency_key = build_spend_idempotency_key(
                app_source="slack",
                submission_id=task_info.task_uuid,
                service="media_embedding",
                unit_type="milliseconds",
            )
            target_languages = embedding_target_language_codes(task_info)
            poster_email = (
                extra_data.get("requester_email") or extra_data.get("email") or ""
            ).strip() or None
            poster_name = (
                extra_data.get("client_name") or extra_data.get("slack_user_name") or ""
            ).strip() or None
            await log_embedding_by_client_id(
                client_id=auth.slack_user.ray_client_id,
                duration_ms=duration_ms,
                num_target_languages=num_target_languages,
                target_languages=target_languages or None,
                source_language=embedding_source_language(task_info),
                file_name=task_info.file_name,
                idempotency_key=embedding_idempotency_key,
                submission_group_uuid=task_info.task_uuid,
                group_uuid=auth.slack_user.ray_user_group_id,
                email=poster_email,
                client_name=poster_name,
            )

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


async def mark_stage_processed(
    task_uuid: str, stage: str, extra_data_to_merge: dict | None = None
) -> None:
    """Mark a processing stage as processed in the database."""
    try:
        async with AsyncSession(async_engines["sitecommons"]) as session:
            task = await session.get(TranscriptionTask, task_uuid)
            if task:
                extra_data = (task.extra_data or {}).copy()
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
                elif extra_data_to_merge:
                    await session.execute(
                        update(TranscriptionTask)
                        .where(TranscriptionTask.task_uuid == task_uuid)
                        .values(extra_data=extra_data)
                    )
                    await session.commit()
    except Exception as e:
        notify_exception(e, f"Failed to mark stage {stage} as processed")


def _iter_media_submission_ids(extra_data: dict | None) -> list[int]:
    """Collect unique submission ids from ``submission_id`` and/or ``submission_ids``."""
    if not extra_data:
        return []
    ids: list[int] = []
    seen: set[int] = set()

    def _add(raw: Any) -> None:
        if raw is None:
            return
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            return
        if sid in seen:
            return
        seen.add(sid)
        ids.append(sid)

    _add(extra_data.get("submission_id"))
    submission_ids = extra_data.get("submission_ids")
    if isinstance(submission_ids, dict):
        for value in submission_ids.values():
            _add(value)
    elif isinstance(submission_ids, (list, tuple, set)):
        for value in submission_ids:
            _add(value)
    return ids


async def update_submission_status(
    extra_data: dict | None,
    *,
    processing_status: SubmissionStatus = SubmissionStatus.COMPLETED,
) -> None:
    """Update media submission row(s) to the given status (default completed)."""
    try:
        for sid in _iter_media_submission_ids(extra_data):
            updated_submission_status(
                submission_id=sid,
                processing_status=processing_status,
            )
    except Exception as e:
        notify_exception(e, "Failed to update submission status")


async def fail_media_submissions(extra_data: dict | None) -> None:
    """Mark media submission row(s) failed so 24h dedupe allows retry."""
    await update_submission_status(
        extra_data, processing_status=SubmissionStatus.FAILED
    )


def is_configure_source_embed_job(extra_data: dict | None) -> bool:
    extra = extra_data or {}
    return (
        bool(extra.get("workflow_type"))
        and extra.get("embed_role") == MediaEmbedRole.SOURCE
    )


def configure_source_embed_continues_translation(extra_data: dict | None) -> bool:
    extra = extra_data or {}
    return (
        is_configure_source_embed_job(extra)
        and extra.get("workflow_type") == MediaWorkflowType.TRANSCRIBE_TRANSLATE
    )


async def maybe_post_media_translation_quote(
    client: AsyncWebClient,
    task_info: TranscriptionTaskInfo,
    channel_id: str,
    thread_ts: str | None,
) -> bool:
    """Post Quote2 when a media quote session expects AI translation after ASR.

    Returns True if Quote2 was posted (caller should not treat the job as fully done).
    """
    extra_data = task_info.extra_data or {}
    quote_id = extra_data.get("media_quote_id")
    if not quote_id:
        return False

    session = await get_media_quote_session(str(quote_id))
    if session is None:
        return False

    workflow = media_workflow_session_from_quote(session)
    if workflow is not None:
        from app.slack.media_workflow_actions import execute_media_workflow_decision

        decision = advance_media_workflow(
            workflow, MediaWorkflowEvent.TRANSCRIPTION_COMPLETED
        )
        session["channel_id"] = session.get("channel_id") or channel_id
        session["thread_ts"] = session.get("thread_ts") or thread_ts
        await execute_media_workflow_decision(
            client=client,
            session=session,
            decision=decision,
            source_text_length=int(task_info.source_text_length or 0),
            task_uuid=task_info.task_uuid,
            duration_ms=task_info.duration_ms or session.get("duration_ms"),
        )
        return MediaWorkflowCommand.MARK_DONE not in decision.commands

    pipeline_kind = session.get("pipeline_kind") or extra_data.get("pipeline_kind")
    if pipeline_kind not in (
        PIPELINE_TRANSCRIBE_TRANSLATE,
        PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    ):
        if session.get("stage") == STAGE_TRANSCRIBING:
            await update_media_quote_session(str(quote_id), {"stage": STAGE_DONE})
        return False

    target_languages = (
        session.get("target_languages") or extra_data.get("target_languages") or []
    )
    source_text_length = int(task_info.source_text_length or 0)
    translation_tokens = media_translation_tokens(
        source_text_length, len(target_languages) or 1
    )
    line_items = [
        {
            "label": _("AI Translation"),
            "tokens": translation_tokens,
        }
    ]
    quote_seed = {
        **session,
        "source_text_length": source_text_length,
        "target_languages": target_languages,
    }
    updated = await update_media_quote_session(
        str(quote_id),
        {
            "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
            "task_uuid": task_info.task_uuid,
            "source_text_length": source_text_length,
            "line_items": line_items,
            "total_tokens": translation_tokens,
            "target_languages": target_languages,
            "duration_ms": task_info.duration_ms or session.get("duration_ms"),
            "quote": media_translation_quote_from_session(quote_seed),
        },
    )
    if updated is None:
        return False

    if await auto_accept_media_translation_quote_if_needed(client, updated):
        return True

    await post_media_quote_message(
        client,
        updated,
        accept_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
        cancel_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
    )
    return True


async def mark_media_quote_done(extra_data: dict[str, Any] | None) -> None:
    """Mark the media quote session complete when a terminal stage finishes."""
    if not extra_data:
        return
    quote_id = extra_data.get("media_quote_id")
    if quote_id:
        await update_media_quote_session(str(quote_id), {"stage": STAGE_DONE})


async def handle_transcription_complete(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    task_info: TranscriptionTaskInfo,
    is_ibm: bool,
    channel_id: str,
    thread_ts: str | None,
    event: RayEvent,
    auth: Any,
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
            # Transcription-only (and pre-Quote2) uploads the source SRT only —
            # do not post AI-translation / reupload copy here.
            extra_data = task_info.extra_data or {}
            srt_review_quote_id = None
            if extra_data.get("workflow_type") and extra_data.get("review_gate"):
                quote_id = extra_data.get("media_quote_id")
                if quote_id:
                    srt_review_quote_id = str(quote_id)
                    await update_media_quote_session(
                        srt_review_quote_id, {"defer_source_review": True}
                    )
            await enqueue_transcription_upload(
                file_id=result_file_id,
                file_name=result_file_name,
                task_uuid=task_info.task_uuid,
                pipeline_type=task_info.pipeline_type,
                client_id=auth.slack_user.ray_client_id,
                channel_id=upload_channel_id,
                thread_ts=effective_thread_ts,
                team_id=extra_data.get("slack_team_id") or extra_data.get("team_id"),
                slack_user_id=extra_data.get("slack_user_id"),
                enterprise_id=extra_data.get("slack_enterprise_id")
                or extra_data.get("enterprise_id"),
                srt_review_quote_id=srt_review_quote_id,
            )


async def handle_translation_complete(
    client: AsyncWebClient,
    channel_id: str,
    thread_ts: str | None,
    task_info: Any,
    auth: Any,
    failed_languages: Sequence[str] | None = None,
) -> int:
    """Upload translated files to Slack.

    `failed_languages` holds target languages that sup-subtitle-ai-cons requested
    but could not deliver; when set alongside delivered files the user is told
    which languages are missing instead of only seeing a plain success message.

    Returns the number of files successfully uploaded (0 means delivery failed).
    """
    translated_file_ids = task_info.translated_file_ids or {}

    # Anchor a thread before upload when callers have no thread_ts, but do not
    # claim success until at least one file is delivered (empty SRTs fail Slack
    # upload with length <= 1).
    if thread_ts:
        effective_thread_ts = thread_ts
    else:
        anchor_response = await client.chat_postMessage(
            channel=channel_id,
            text=_("Preparing your AI translation…"),
        )
        effective_thread_ts = anchor_response.get("ts")

    original_file_name = task_info.file_name or "transcription.srt"
    original_path = Path(original_file_name)
    original_stem = original_path.stem
    uploaded_count = 0

    for target_lang, file_id in translated_file_ids.items():
        try:
            output_file = await download_from_file_server_async(file_id)
            file_path = output_file.get("file")
            lang_name = get_auto_translate_language_name(target_lang)
            title = f"{original_stem}_{lang_name}.srt"

            if not file_path:
                continue

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
            uploaded_count += 1

            if file_path and os.path.exists(file_path):
                os.unlink(file_path)

        except Exception as e:
            notify_exception(e, "Error handling translation complete")
            logger.error(f"Error handling translation complete: {e}")

    if uploaded_count:
        failed_language_names = await resolve_language_labels(failed_languages)
        if failed_language_names:
            partial_message = MediaTranslationPartialMessage(failed_language_names)
            await client.chat_postMessage(
                channel=channel_id,
                text=partial_message.text,
                thread_ts=effective_thread_ts,
            )
        else:
            await client.chat_postMessage(
                channel=channel_id,
                text=_("Your file is AI translated and can be downloaded above."),
                thread_ts=effective_thread_ts,
            )
        extra = _task_extra_data(task_info)
        if extra.get("workflow_type"):
            await _advance_configure_after_translation(
                client, extra, channel_id, effective_thread_ts
            )
        else:
            await client.chat_postMessage(
                channel=channel_id,
                text=_(
                    "Download the AI-translated subtitle files (SRT) provided above, "
                    "make your edits, and reupload the edited subtitle files back "
                    "to the same thread."
                ),
                thread_ts=effective_thread_ts,
            )
    elif translated_file_ids:
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "AI translation finished, but the translated file could not be "
                "delivered to Slack. Please try again or contact support."
            ),
            thread_ts=effective_thread_ts,
        )

    # Show token message at the end for translate pipelines
    if (
        task_info.pipeline_type in ("transcribe_translate", "translate_only")
        and uploaded_count
    ):
        is_ibm = (
            is_ibm_enterprise(auth.slack_user.enterprise_id)
            if auth.slack_user
            else False
        )
        await show_tokens_message(
            client,
            task_info.task_uuid,
            channel_id,
            effective_thread_ts,
            is_ibm=is_ibm,
        )
    return uploaded_count


def get_auto_translate_language_name(target_lang: str) -> str:
    from app.ray.settings import get_auto_translate_language_name as get_name

    return get_name(target_lang)


async def handle_transcribe_embed_pipeline(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    task_info: TranscriptionTaskInfo,
    channel_id: str,
    thread_ts: str | None,
    auth: Any = None,
    failed_languages: Sequence[str] | None = None,
) -> bool:
    """Upload embedded media result to Slack.

    `failed_languages` holds subtitle languages that were requested but not
    embedded; the delivered video is still posted and the user is told which
    languages are missing.

    Returns True when the file was delivered; False on missing file or upload error.
    """
    if not result_file_id:
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "Embedding finished, but no output file was available. "
                "Please try again or contact support."
            ),
            thread_ts=thread_ts,
        )
        return False

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
        if not file_path or not os.path.exists(file_path):
            await client.chat_postMessage(
                channel=channel_id,
                text=_(
                    "An error occurred while processing your embedded video. "
                    "Please try again."
                ),
                thread_ts=effective_thread_ts,
            )
            return False

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

        failed_language_names = await resolve_language_labels(failed_languages)
        if failed_language_names:
            partial_message = MediaEmbeddingPartialMessage(failed_language_names)
            await client.chat_postMessage(
                channel=channel_id,
                text=partial_message.text,
                thread_ts=effective_thread_ts,
            )

        if task_info.pipeline_type in (
            "transcribe_translate_embed",
            "translate_embed",
        ):
            is_ibm = (
                is_ibm_enterprise(auth.slack_user.enterprise_id)
                if auth and auth.slack_user
                else False
            )
            await show_tokens_message(
                client,
                task_info.task_uuid,
                channel_id,
                effective_thread_ts,
                is_ibm=is_ibm,
            )
        extra = _task_extra_data(task_info)
        if extra.get("workflow_type"):
            await _advance_configure_after_embed(
                client, extra, channel_id, effective_thread_ts
            )
        return True

    except Exception as e:
        notify_exception(e, "Error handling embedded video")
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "An error occurred while processing your embedded video. Please try again."
            ),
            thread_ts=effective_thread_ts,
        )
        return False

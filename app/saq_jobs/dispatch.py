"""Typed enqueue helpers for the SAQ task registry (RAY-79638).

This module owns *all* queue-shaping logic — idempotency keys, retry/timeout
defaults, payload construction — so callers in routers and event handlers
never need to know the SAQ wire format. Each helper is a thin, well-typed
wrapper around :func:`app.saq_jobs.queue.enqueue` that maps a domain-level
intent (e.g. "deliver an MT success file to Slack") to a registered task
function in :mod:`app.saq_jobs.tasks`.

Keeping this layer here means:

* Routers stay focused on HTTP / event parsing and call a single function
  per durable side-effect.
* Idempotency keys live next to the task they protect, so future task
  changes can update both atomically.
* Tests can patch one module (`app.saq_jobs.dispatch`) to assert enqueue
  behaviour, without each caller-side helper inventing its own conventions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import config as app_config
from app.ray.events.models import MtSuccessResponseSchema
from app.saq_jobs.queue import enqueue

FileSubmissionPayload = dict[str, str | int | None]


def _large_file_submission_threshold_bytes() -> int:
    return app_config.saq_large_file_submission_threshold_mb * 1024 * 1024


def _submission_queue_name(files: list[FileSubmissionPayload]) -> str:
    """Route known-small files to higher concurrency; unknown/large files stay limited."""
    raw_file_sizes = [file.get("size") for file in files]
    if not raw_file_sizes or any(not isinstance(size, int) for size in raw_file_sizes):
        return app_config.saq_file_submission_queue_name
    file_sizes = [size for size in raw_file_sizes if isinstance(size, int)]
    if max(file_sizes) >= _large_file_submission_threshold_bytes():
        return app_config.saq_file_submission_queue_name
    return app_config.saq_small_file_submission_queue_name


def _submission_timeout_seconds(files: list[FileSubmissionPayload]) -> int:
    """Use shorter SAQ attempts for known-small submissions."""
    if _submission_queue_name(files) == app_config.saq_small_file_submission_queue_name:
        return app_config.saq_small_file_upload_timeout_seconds
    return app_config.saq_file_upload_timeout_seconds


def _mt_success_idempotency_key(success_data: MtSuccessResponseSchema) -> str:
    """Stable SAQ key for MT success uploads.

    Includes ``target_language`` so a single MT pipeline producing multiple
    languages still enqueues one job per language. Falls back to a literal
    ``no-task`` segment when ``task_uuid`` is missing so the key is never
    ambiguous.
    """
    return (
        "slack_upload_mt_result:"
        f"{success_data.task_uuid or 'no-task'}:"
        f"{success_data.file_id}:"
        f"{success_data.channel_id}:"
        f"{success_data.target_language}"
    )


def _stable_hash(value: Any) -> str:
    """Short deterministic hash for idempotency-key payload segments."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


async def enqueue_document_mt_submission(
    *,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    files: list[FileSubmissionPayload],
    source_language: str | None,
    target_languages: list[str],
    quote_id: str | None = None,
) -> None:
    """Enqueue durable document MT submission processing.

    The task owns Slack download, file-server upload, duplicate submission
    tracking, and MT event publication. The key prevents duplicate modal
    submissions for the same Slack files/languages from running concurrently.
    """
    key = "process_document_mt_submission:" + _stable_hash(
        {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": channel_id,
            "files": files,
            "source_language": source_language,
            "target_languages": target_languages,
            "quote_id": quote_id,
        }
    )
    await enqueue(
        "process_document_mt_submission",
        queue_name=_submission_queue_name(files),
        key=key,
        retries=app_config.saq_file_upload_retries,
        timeout=_submission_timeout_seconds(files),
        retry_delay=2.0,
        retry_backoff=True,
        user_id=user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id,
        files=files,
        source_language=source_language,
        target_languages=target_languages,
        quote_id=quote_id,
    )


async def enqueue_document_mt_quote_preflight(
    *,
    quote_id: str,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    files: list[FileSubmissionPayload],
    source_language: str | None,
    target_languages: list[str],
) -> None:
    """Enqueue durable document MT quote preflight processing."""
    key = "process_document_mt_quote_preflight:" + _stable_hash(
        {
            "quote_id": quote_id,
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": channel_id,
            "files": files,
            "source_language": source_language,
            "target_languages": target_languages,
        }
    )
    await enqueue(
        "process_document_mt_quote_preflight",
        queue_name=_submission_queue_name(files),
        key=key,
        retries=app_config.saq_file_upload_retries,
        timeout=_submission_timeout_seconds(files),
        retry_delay=2.0,
        retry_backoff=True,
        quote_id=quote_id,
        user_id=user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id,
        files=files,
        source_language=source_language,
        target_languages=target_languages,
    )


async def enqueue_evaluation_submission(
    *,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    files: list[FileSubmissionPayload],
    target_langs_uuid: list[str],
    reference: str,
    source_lang_uuid: str,
    workflow_uuid: str | None,
    job_notes: str,
    preaccepted_ai_translation_quote: bool = False,
    prequote_message_ts: str | None = None,
    ai_translation_filename_and_languages: list[str] | None = None,
    quote_id: str | None = None,
) -> None:
    """Enqueue durable quality-evaluation / human-translation submission processing."""
    key = "process_evaluation_submission:" + _stable_hash(
        {
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": channel_id,
            "files": files,
            "target_langs_uuid": target_langs_uuid,
            "reference": reference,
            "source_lang_uuid": source_lang_uuid,
            "workflow_uuid": workflow_uuid,
            "job_notes": job_notes,
            "preaccepted_ai_translation_quote": preaccepted_ai_translation_quote,
            "prequote_message_ts": prequote_message_ts,
            "ai_translation_filename_and_languages": (
                ai_translation_filename_and_languages
            ),
        }
    )
    await enqueue(
        "process_evaluation_submission",
        queue_name=_submission_queue_name(files),
        key=key,
        retries=app_config.saq_file_upload_retries,
        timeout=_submission_timeout_seconds(files),
        retry_delay=2.0,
        retry_backoff=True,
        user_id=user_id,
        team_id=team_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id,
        files=files,
        target_langs_uuid=target_langs_uuid,
        reference=reference,
        source_lang_uuid=source_lang_uuid,
        workflow_uuid=workflow_uuid,
        job_notes=job_notes,
        preaccepted_ai_translation_quote=preaccepted_ai_translation_quote,
        prequote_message_ts=prequote_message_ts,
        ai_translation_filename_and_languages=ai_translation_filename_and_languages,
        quote_id=quote_id,
    )


async def enqueue_mt_success_upload(
    success_data: MtSuccessResponseSchema,
) -> None:
    """Enqueue the durable MT success upload job (RAY-79638).

    Replaces the in-process ``asyncio.create_task`` previously used to run
    ``_handle_mt_success_background`` from ``app/routers/ray.py``. The job
    is persisted in Redis so it survives container restarts and is retried
    by SAQ on failure (e.g. the ``file_update_failed`` Slack error reported
    in production).
    """
    await enqueue(
        "slack_upload_mt_result",
        queue_name=app_config.saq_file_delivery_queue_name,
        key=_mt_success_idempotency_key(success_data),
        retries=app_config.saq_file_upload_retries,
        timeout=app_config.saq_file_upload_timeout_seconds,
        retry_delay=2.0,
        retry_backoff=True,
        success_data=success_data.model_dump(),
    )


async def enqueue_transcription_upload(
    *,
    file_id: str,
    file_name: str,
    task_uuid: str,
    pipeline_type: str | None,
    client_id: str,
    channel_id: str,
    thread_ts: str | None,
    follow_up_message: str | None = None,
) -> None:
    """Enqueue the durable transcription file upload job (RAY-79638)."""
    key = (
        "slack_upload_transcription:"
        f"{task_uuid}:{file_id}:{channel_id}:{thread_ts or 'no-thread'}"
    )
    await enqueue(
        "slack_upload_transcription",
        queue_name=app_config.saq_file_delivery_queue_name,
        key=key,
        retries=app_config.saq_file_upload_retries,
        timeout=app_config.saq_file_upload_timeout_seconds,
        retry_delay=2.0,
        retry_backoff=True,
        file_id=file_id,
        file_name=file_name,
        task_uuid=task_uuid,
        pipeline_type=pipeline_type,
        client_id=client_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        follow_up_message=follow_up_message,
    )


async def enqueue_verify_complete_upload(
    *,
    grid_file_id: str,
    client_id: str,
    channel_id: str,
) -> None:
    """Enqueue the durable verify-complete file upload job (RAY-79638)."""
    key = f"slack_upload_verify_complete:{grid_file_id}:{channel_id}"
    await enqueue(
        "slack_upload_verify_complete",
        queue_name=app_config.saq_file_delivery_queue_name,
        key=key,
        retries=app_config.saq_file_upload_retries,
        timeout=app_config.saq_file_upload_timeout_seconds,
        retry_delay=2.0,
        retry_backoff=True,
        grid_file_id=grid_file_id,
        client_id=client_id,
        channel_id=channel_id,
    )


async def enqueue_log_notification(
    *,
    event: str,
    event_data: dict[str, Any],
    user_id: str,
    channel_id: str,
    ray_client_id: str,
    message: str,
) -> None:
    """Enqueue a durable ``persist_log_notification`` SAQ job (RAY-79638).

    No idempotency key: notification logs are append-only and high volume,
    so a duplicate row is preferable to dropping a log line during a Redis
    blip. SAQ retry settings still cover transient DB outages.
    """
    await enqueue(
        "persist_log_notification",
        queue_name=app_config.saq_background_queue_name,
        retries=app_config.saq_logging_retries,
        timeout=app_config.saq_logging_timeout_seconds,
        retry_delay=1.0,
        retry_backoff=True,
        event=event,
        event_data=event_data,
        user_id=user_id,
        channel_id=channel_id,
        ray_client_id=ray_client_id,
        message=message,
    )


async def enqueue_inline_mt_billing(
    *,
    idempotency_key: str,
    billing: dict[str, Any],
    usage_log: dict[str, Any],
) -> None:
    """Enqueue durable inline/channel/shortcut MT billing (RAY-80258).

    Decouples the LanguageCloud ``/mt/inline-usage`` charge from the
    ``slack:direct:mt:result`` callback so a transient ``ConnectTimeout``
    no longer returns a 422 to ``redis-slack-consumer`` after the Slack
    notification has been posted. ``charge_inline_mt_usage`` retries the
    charge with the gateway idempotency key carried in ``billing`` and then
    writes the Google API usage row.

    The SAQ ``key`` reuses ``idempotency_key`` so a redelivered stream entry
    (same message + content) collapses to a single in-flight job, matching the
    gateway-side dedup and preventing a double charge on replay.
    """
    await enqueue(
        "charge_inline_mt_usage",
        queue_name=app_config.saq_background_queue_name,
        key=f"charge_inline_mt_usage:{idempotency_key}",
        retries=app_config.saq_logging_retries,
        timeout=app_config.saq_logging_timeout_seconds,
        retry_delay=2.0,
        retry_backoff=True,
        billing=billing,
        usage_log=usage_log,
    )


async def enqueue_document_mt_charge(
    *,
    client_id: str,
    task_uuid: str,
    idempotency_key: str,
    charge: dict[str, Any],
) -> None:
    """Enqueue durable document-MT billing after Slack delivery (RAY-80417).

    The charge (document MT + optional combined PDF conversion fee) is deferred
    until ``slack_upload_mt_result`` confirms the translated file reached the
    user, so a failed delivery is never billed. The SAQ ``key`` reuses the
    per-target MT ``idempotency_key`` so a redelivered upload job collapses to one
    in-flight billing task; the gateway key is the final double-charge backstop.
    """
    await enqueue(
        "charge_document_mt",
        queue_name=app_config.saq_background_queue_name,
        key=f"charge_document_mt:{idempotency_key}",
        retries=app_config.saq_logging_retries,
        timeout=app_config.saq_logging_timeout_seconds,
        retry_delay=2.0,
        retry_backoff=True,
        client_id=client_id,
        charge=charge,
        task_uuid=task_uuid,
    )

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

from typing import Any

from app.config import config as app_config
from app.ray.events.models import MtSuccessResponseSchema
from app.saq_jobs.queue import enqueue


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


async def enqueue_mt_ts_edit(*, send_ts: str, reply_ts: str) -> None:
    """Enqueue a durable ``persist_mt_ts_edit`` SAQ job (RAY-79638).

    Keyed on ``send_ts`` because the Slack thread pivot timestamp is the
    natural unique identifier — a duplicate enqueue with the same parent
    timestamp is always the same logical operation.
    """
    await enqueue(
        "persist_mt_ts_edit",
        key=f"persist_mt_ts_edit:{send_ts}",
        retries=app_config.saq_logging_retries,
        timeout=app_config.saq_logging_timeout_seconds,
        retry_delay=1.0,
        retry_backoff=True,
        send_ts=send_ts,
        reply_ts=reply_ts,
    )

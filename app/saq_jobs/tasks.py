"""Durable SAQ task functions registered with the worker (RAY-79638).

Tasks accept JSON-serialisable kwargs only — never tokens. Slack bot tokens
are looked up from the database inside the task using the ``ray_client_id``
so a queued job remains valid even after a token rotation. Each task is
written to be idempotent: re-running it after a partial failure is safe.

Scope rationale
---------------
``asyncio.create_task`` work that is migrated here:

* The three file-upload background handlers in ``app/routers/ray.py`` (the
  failing path from the production traceback that motivated RAY-79638).
* The ``log_notification`` DB inserts that side-effect from
  ``app/ray/events/logging.py`` (clean kwarg signature, durable benefit).
* The ``set_mt_ts_edit`` Redis cache writes from
  ``app/ray/events/logging.py`` (cheap, durable benefit).

``asyncio.create_task`` sites deliberately left in place:

* In-process cache primers
  (``files_list_simple`` cache warming and ``_update_global_cache`` in
  ``app/slack/select_options.py``). These are intentionally optimistic
  cache pre-fetches that benefit the *next* request; queueing them adds
  latency without giving any durability that matters (a dropped cache
  warm just means the next request reloads).
* ``slack_log_decorator`` ``log_slack`` write — the payload is a
  ``ray_logger.slack.SlackAppLog`` object that is not JSON-serialisable
  through SAQ; the existing ``log_slack`` helper already wraps the call
  in try/except.
* ``RayService.api_ondemand_process`` — needs the request-scoped authed
  ``RayClient``; reconstituting that auth in the worker would require
  storing user tokens in Redis, violating the "no tokens in queue
  payloads" rule that protects against token leakage in the durable
  store.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from saq.types import Context

from app.auth.connector import get_slack_user
from app.ray.events.models import MtSuccessResponseSchema
from app.ray.submissions import SubmissionStatus, updated_submission_status
from app.ray.utils import delete_from_file_server, download_from_file_server_async
from app.slack.buglog_notifier import notify_exception
from app.slack_job import update_slack_job
from app.translate import _

logger = logging.getLogger(__name__)


def _safe_unlink(path: str | None) -> None:
    """Remove a temp file silently. Used in finally blocks across tasks."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:
        logger.warning("Failed to remove temp file", extra={"path_present": True})


# --------------------------------------------------------------------------- #
# Slack file upload tasks
# --------------------------------------------------------------------------- #


async def slack_upload_mt_result(
    ctx: Context,
    *,
    success_data: dict[str, Any],
) -> dict[str, Any]:
    """Durable handler for ``verify:slack:document:translated`` MT success.

    Replaces ``_handle_mt_success_background`` in ``app/routers/ray.py``.
    The MT pipeline produced a translated document; download it from the
    file server and upload it to Slack as the configured user.

    Idempotency:
        Driven by the SAQ ``key`` set by the caller (task_uuid + file_id +
        channel_id). Inside the task the file-server download is the
        external side-effect that may already be deleted on a retry; this
        is treated as success.

    Args:
        ctx: SAQ task context (job, queue, attempts).
        success_data: JSON dump of ``MtSuccessResponseSchema``.

    Returns:
        Status dict with ``status`` and ``task_uuid`` for observability.
    """
    # Local imports keep app startup decoupled from heavy modules.
    from slack_sdk.web.async_client import AsyncWebClient

    from app.slack.web import upload_file_to_slack_memory_efficient

    data = MtSuccessResponseSchema.model_validate(success_data)
    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1

    log_extra = {
        "task_uuid": data.task_uuid,
        "file_id": data.file_id,
        "channel_id": data.channel_id,
        "target_language": data.target_language,
        "attempt": attempt,
    }
    logger.info("MT success upload starting", extra=log_extra)

    if data.submission_id:
        try:
            updated_submission_status(
                submission_id=data.submission_id,
                processing_status=SubmissionStatus.COMPLETED,
            )
        except Exception as e:
            notify_exception(e, "Failed to mark MT submission completed")

    try:
        await update_slack_job(
            task_uuid=data.task_uuid,
            status="slack_uploading",
        )
    except Exception as e:
        notify_exception(e, "Failed to mark slack_job slack_uploading")

    slack_user = await get_slack_user(data.client_id)
    if slack_user is None:
        logger.error(
            "Slack user disappeared before MT upload could complete",
            extra=log_extra,
        )
        await update_slack_job(
            task_uuid=data.task_uuid,
            status="failed_delivery",
        )
        return {"status": "no_slack_user", "task_uuid": data.task_uuid}

    file_path: str | None = None
    try:
        client = AsyncWebClient(token=slack_user.bot_token)
        output_file = await download_from_file_server_async(data.file_id)
        file_path = output_file.get("file")
        if not file_path:
            raise RuntimeError(
                f"File-server download returned no path for file_id={data.file_id}"
            )
        title = output_file.get("file_name")
        # Resolve language name lazily to avoid pulling the cache module at import time.
        from app.routers.ray import _get_language_name  # local: avoids import cycle

        language_name = await _get_language_name(data.target_language)
        initial_comment = _(
            f"Your file is AI translated to *{language_name}* and can be downloaded below."
        )
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=file_path,
            channel_id=data.channel_id,
            title=title,
            filename=title,
            initial_comment=initial_comment,
        )
        await update_slack_job(
            task_uuid=data.task_uuid,
            status="delivered",
        )
        try:
            await delete_from_file_server(data.file_id)
        except Exception as e:
            # File-server cleanup failure is not fatal; the upload already succeeded.
            notify_exception(
                e, "Failed to delete file from file server after MT upload"
            )
        logger.info("MT success upload delivered", extra=log_extra)
        return {"status": "delivered", "task_uuid": data.task_uuid}
    except Exception:
        logger.exception("MT success upload failed; SAQ will retry", extra=log_extra)
        if job is not None and not job.retryable:
            try:
                await update_slack_job(
                    task_uuid=data.task_uuid,
                    status="failed_delivery",
                )
            except Exception as e:
                notify_exception(e, "Failed to mark slack_job failed_delivery")
            notify_exception(
                Exception("Background MT success file handling failed"),
                "Background MT success file handling failed (final attempt)",
            )
        raise
    finally:
        _safe_unlink(file_path)


async def slack_upload_transcription(
    ctx: Context,
    *,
    file_id: str,
    file_name: str,
    task_uuid: str,
    pipeline_type: str | None,
    client_id: str,
    channel_id: str,
    thread_ts: str | None = None,
    follow_up_message: str | None = None,
) -> dict[str, Any]:
    """Durable handler for transcription file uploads.

    Replaces ``_handle_transcribe_success_background`` in
    ``app/routers/ray.py``. Downloads the SRT from the file server, renames
    the temp file so Slack preserves the extension, uploads it, then posts
    the optional follow-up message.

    Args:
        ctx: SAQ task context.
        file_id: File-server file ID.
        file_name: Display filename (used as title and on-disk name).
        task_uuid: Transcription task UUID for correlation/logging.
        pipeline_type: Pipeline that produced the file.
        client_id: Slack user's RAY client ID; used to look up the bot token.
        channel_id: Target Slack channel ID.
        thread_ts: Optional thread to reply in.
        follow_up_message: Optional message to post after the upload completes.

    Returns:
        Status dict for observability.
    """
    from slack_sdk.web.async_client import AsyncWebClient

    from app.slack.web import upload_file_to_slack_memory_efficient

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "task_uuid": task_uuid,
        "file_id": file_id,
        "channel_id": channel_id,
        "pipeline_type": pipeline_type,
        "attempt": attempt,
    }
    logger.info("Transcription upload starting", extra=log_extra)

    slack_user = await get_slack_user(client_id)
    if slack_user is None:
        logger.error(
            "Slack user disappeared before transcription upload",
            extra=log_extra,
        )
        return {"status": "no_slack_user", "task_uuid": task_uuid}

    file_path: str | None = None
    try:
        client = AsyncWebClient(token=slack_user.bot_token)
        output_file = await download_from_file_server_async(file_id)
        file_path = output_file.get("file")
        if not file_path:
            logger.error("Transcription file download empty", extra=log_extra)
            notify_exception(
                Exception(f"Failed to download file {file_id} from file server"),
                "Transcription background task failed",
            )
            return {"status": "download_failed", "task_uuid": task_uuid}

        # Rename so Slack preserves the original filename + extension.
        temp_dir = os.path.dirname(file_path)
        renamed_file_path = os.path.join(temp_dir, file_name)
        if file_path != renamed_file_path:
            os.rename(file_path, renamed_file_path)
            file_path = renamed_file_path

        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=file_path,
            channel_id=channel_id,
            title=file_name,
            filename=file_name,
            thread_ts=thread_ts,
        )
        if follow_up_message:
            await client.chat_postMessage(
                channel=channel_id,
                text=follow_up_message,
                thread_ts=thread_ts,
            )
        logger.info("Transcription upload delivered", extra=log_extra)
        return {"status": "delivered", "task_uuid": task_uuid}
    except Exception:
        logger.exception("Transcription upload failed; SAQ will retry", extra=log_extra)
        if job is not None and not job.retryable:
            notify_exception(
                Exception("Background transcription file handling failed"),
                "Background transcription file handling failed (final attempt)",
            )
        raise
    finally:
        _safe_unlink(file_path)


async def slack_upload_verify_complete(
    ctx: Context,
    *,
    grid_file_id: str,
    client_id: str,
    channel_id: str,
) -> dict[str, Any]:
    """Durable handler for verify-complete file uploads.

    Replaces ``_handle_verify_complete_background`` in ``app/routers/ray.py``.
    """
    from slack_sdk.web.async_client import AsyncWebClient

    from app.slack.web import upload_file_to_slack_memory_efficient

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "grid_file_id": grid_file_id,
        "channel_id": channel_id,
        "attempt": attempt,
    }
    logger.info("Verify-complete upload starting", extra=log_extra)

    slack_user = await get_slack_user(client_id)
    if slack_user is None:
        logger.error(
            "Slack user disappeared before verify-complete upload",
            extra=log_extra,
        )
        return {"status": "no_slack_user"}

    file_path: str | None = None
    try:
        client = AsyncWebClient(token=slack_user.bot_token)
        output_file = await download_from_file_server_async(grid_file_id)
        file_path = output_file.get("file")
        if not file_path:
            raise RuntimeError(
                f"File-server download returned no path for grid_file_id={grid_file_id}"
            )
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=file_path,
            channel_id=channel_id,
            title=output_file.get("file_name"),
            filename=output_file.get("file_name"),
        )
        logger.info("Verify-complete upload delivered", extra=log_extra)
        return {"status": "delivered"}
    except Exception:
        logger.exception(
            "Verify-complete upload failed; SAQ will retry", extra=log_extra
        )
        if job is not None and not job.retryable:
            notify_exception(
                Exception("Background verify complete file handling failed"),
                "Background verify complete file handling failed (final attempt)",
            )
        raise
    finally:
        _safe_unlink(file_path)


# --------------------------------------------------------------------------- #
# Logging / persistence tasks
# --------------------------------------------------------------------------- #


async def persist_log_notification(
    ctx: Context,
    *,
    event: str,
    event_data: dict[str, Any],
    user_id: str,
    channel_id: str,
    ray_client_id: str,
    message: str,
) -> dict[str, Any]:
    """Durable wrapper around ``app.ray.events.logging.log_notification``."""
    from app.ray.events.logging import log_notification

    await log_notification(
        event=event,
        event_data=event_data,
        user_id=user_id,
        channel_id=channel_id,
        ray_client_id=ray_client_id,
        message=message,
    )
    return {"status": "logged", "event": event}


async def persist_mt_ts_edit(
    ctx: Context,
    *,
    send_ts: str,
    reply_ts: str,
) -> dict[str, Any]:
    """Durable wrapper around ``app.slack.web.set_mt_ts_edit`` (Redis cache)."""
    from app.slack.web import set_mt_ts_edit

    await set_mt_ts_edit(send_ts=send_ts, reply_ts=reply_ts)
    return {"status": "cached", "send_ts": send_ts}


# --------------------------------------------------------------------------- #
# Task registry
# --------------------------------------------------------------------------- #

#: Public task name -> callable map. Imported by the worker module to register
#: tasks. Keep names stable; jobs persisted in Redis reference these names.
#: The matching :data:`app.saq_jobs._task_names.TaskName` literal is asserted
#: in sync with this list by ``tests/saq_jobs/test_task_registry.py``.
TASK_FUNCTIONS = [
    slack_upload_mt_result,
    slack_upload_transcription,
    slack_upload_verify_complete,
    persist_log_notification,
    persist_mt_ts_edit,
]

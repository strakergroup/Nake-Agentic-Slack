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
from typing import Any, cast

from saq.types import Context
from slack_bolt.context.async_context import AsyncBoltContext

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
# Slack submission processing tasks
# --------------------------------------------------------------------------- #


async def process_document_mt_submission(
    ctx: Context,
    *,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    files: list[dict[str, Any]],
    source_language: str | None,
    target_languages: list[str],
) -> dict[str, Any]:
    """Durable document MT submission processing.

    Downloads Slack files into unique temp paths, uploads valid files to the
    file server, records duplicate-submission state, and publishes the MT job
    request. Slack/RAY credentials are re-fetched inside the worker.
    """
    from slack_sdk.web.async_client import AsyncWebClient

    from app.auth.connector import (
        RayConnection,
        get_bot_token_async,
        get_ray_connection,
        get_verify_trial_status,
    )
    from app.config import config
    from app.ray.submissions import check_and_record_submission_async
    from app.ray.utils import upload_to_file_server, validate_file
    from app.slack.listener_actions import document_machine_translate
    from app.slack.web import download_file

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "user_id": user_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "attempt": attempt,
        "file_count": len(files),
    }
    ray_connection = await get_ray_connection(user_id, team_id, enterprise_id)
    if ray_connection is None or ray_connection.client is None:
        logger.error("Document MT submission has no RAY client", extra=log_extra)
        return {"status": "no_ray_client"}

    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token:
        logger.error("Document MT submission has no Slack bot token", extra=log_extra)
        return {"status": "no_bot_token"}

    ray_client = ray_connection.client
    if ray_client.is_trial is None:
        ray_client.is_trial, ray_client.trial_remaining = await get_verify_trial_status(
            ray_client.id_token
        )
    max_pdf_size_bytes = (
        config.document_mt_pdf_max_size_bytes if ray_client.is_trial else None
    )

    client = AsyncWebClient(token=bot_token)
    context = {
        "user_id": user_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "ray": RayConnection(ray_connection.super_group, ray_client),
    }
    downloaded_files: list[str] = []
    files_uploaded: list[str] = []
    duplicate_submissions: list[str] = []
    validation_errors: list[str] = []

    try:
        for file_data in files:
            slack_file_id = file_data["id"]
            file_title = file_data.get("title") or slack_file_id
            input_file = await download_file(
                client=client, file_id=slack_file_id, http=None
            )
            downloaded_files.append(input_file)

            is_valid_file_type, is_valid_content, error_message = validate_file(
                input_file,
                max_pdf_size_bytes=max_pdf_size_bytes,
            )
            if not is_valid_file_type:
                validation_errors.append(
                    _(
                        f"The file ({file_title}) file type is currently not supported. Please check the <https://help.strakertranslations.com/hc/en-us/articles/35943216049945-AI-Translate-for-Documents-in-Straker-Translate-App-for-Slack|help docs>"
                    )
                )
                continue
            if not is_valid_content:
                validation_errors.append(error_message or _("Invalid file content."))
                continue

            input_file_id = await upload_to_file_server(input_file)
            submitted_languages: list[str] = []
            submission_ids: dict[str, int] = {}
            for target_language in target_languages:
                is_dup, record = await check_and_record_submission_async(
                    path=input_file,
                    file_name=os.path.basename(input_file),
                    file_id=input_file_id,
                    user_id=user_id,
                    team_id=team_id,
                    channel_id=channel_id,
                    source_language=source_language or "",
                    target_language=target_language,
                )
                if is_dup:
                    duplicate_submissions.append(
                        f"{file_title} ({source_language or 'auto'} -> {target_language})"
                    )
                    continue

                submitted_languages.append(target_language)
                submission_ids[target_language] = record.id

            if submitted_languages:
                await document_machine_translate(
                    cast(AsyncBoltContext, context),
                    input_file_id,
                    source_language,
                    submitted_languages,
                    submission_ids,
                )
                files_uploaded.append(file_title)

        if duplicate_submissions:
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    f"Please allow the system to complete the ongoing translation(s) *({', '.join(duplicate_submissions)})* to prevent duplicate submissions."
                ),
            )
        for message in validation_errors:
            await client.chat_postMessage(channel=user_id, text=message)

        return {
            "status": "processed",
            "uploaded_count": len(files_uploaded),
            "duplicate_count": len(duplicate_submissions),
            "validation_error_count": len(validation_errors),
        }
    except Exception:
        logger.exception(
            "Document MT submission failed; SAQ will retry", extra=log_extra
        )
        if job is not None and not job.retryable:
            notify_exception(
                Exception("Queued document MT submission failed"),
                "Queued document MT submission failed (final attempt)",
            )
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    "There was an error submitting your translation request, please try again."
                ),
            )
        raise
    finally:
        for path in downloaded_files:
            _safe_unlink(path)
            parent_dir = os.path.dirname(path)
            try:
                if (
                    parent_dir
                    and os.path.exists(parent_dir)
                    and not os.listdir(parent_dir)
                ):
                    os.rmdir(parent_dir)
            except OSError:
                pass


async def process_evaluation_submission(
    ctx: Context,
    *,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    files: list[dict[str, Any]],
    target_langs_uuid: list[str],
    reference: str,
    source_lang_uuid: str,
    workflow_uuid: str | None,
    job_notes: str,
) -> dict[str, Any]:
    """Durable quality-evaluation / human-translation submission processing."""
    from slack_sdk.web.async_client import AsyncWebClient

    from app.api.verify import VerifyAPIError, submit_evaluation_job
    from app.auth.connector import get_bot_token_async, get_ray_client
    from app.ray.utils import validate_file
    from app.slack.listeners import _publish_pdf_evaluate_convert
    from app.slack.web import download_file

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "user_id": user_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "attempt": attempt,
        "file_count": len(files),
    }
    ray_client = await get_ray_client(user_id, team_id, enterprise_id)
    if ray_client is None:
        logger.error("Evaluation submission has no RAY client", extra=log_extra)
        return {"status": "no_ray_client"}

    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token:
        logger.error("Evaluation submission has no Slack bot token", extra=log_extra)
        return {"status": "no_bot_token"}

    client = AsyncWebClient(token=bot_token)
    downloaded_files: list[str] = []
    input_files: list[str] = []
    file_titles: list[str] = []

    try:
        for file_data in files:
            input_file = await download_file(
                client=client, file_id=file_data["id"], http=None
            )
            downloaded_files.append(input_file)
            is_valid, is_valid_content, error_message = validate_file(input_file)
            if not is_valid or not is_valid_content:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=error_message,
                )
                continue
            input_files.append(input_file)
            file_titles.append(file_data["title"])

        if not input_files:
            return {"status": "no_valid_files"}

        has_pdf = any(title.lower().endswith(".pdf") for title in file_titles)
        if has_pdf:
            await _publish_pdf_evaluate_convert(
                ray_client=ray_client,
                input_files=input_files,
                file_titles=file_titles,
                target_langs_uuid=target_langs_uuid,
                reference=reference,
                channel_id=channel_id,
                source_lang_uuid=source_lang_uuid,
                workflow_uuid=workflow_uuid,
                job_notes=job_notes,
            )
        else:
            await submit_evaluation_job(
                ray_client,
                input_files,
                target_langs_uuid,
                reference,
                source_language_uuid=source_lang_uuid,
                workflow_uuid=workflow_uuid,
                job_notes=job_notes,
            )
        return {"status": "submitted", "file_count": len(input_files)}
    except VerifyAPIError:
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "There was an error processing your request. You do not have permission to perform this action. Please contact your team administrator."
            ),
        )
        return {"status": "permission_denied"}
    except Exception:
        logger.exception(
            "Evaluation submission failed; SAQ will retry", extra=log_extra
        )
        if job is not None and not job.retryable:
            notify_exception(
                Exception("Queued evaluation submission failed"),
                "Queued evaluation submission failed (final attempt)",
            )
            error_msg = (
                "There was an error submitting your human translation request, please try again."
                if workflow_uuid
                else "There was an error submitting your quality evaluation request, please try again."
            )
            await client.chat_postMessage(channel=channel_id, text=_(error_msg))
        raise
    finally:
        for path in downloaded_files:
            _safe_unlink(path)
            parent_dir = os.path.dirname(path)
            try:
                if (
                    parent_dir
                    and os.path.exists(parent_dir)
                    and not os.listdir(parent_dir)
                ):
                    os.rmdir(parent_dir)
            except OSError:
                pass


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

FILE_DELIVERY_TASK_FUNCTIONS = [
    slack_upload_mt_result,
    slack_upload_transcription,
    slack_upload_verify_complete,
]

FILE_SUBMISSION_TASK_FUNCTIONS = [
    process_document_mt_submission,
    process_evaluation_submission,
]

BACKGROUND_TASK_FUNCTIONS = [
    persist_log_notification,
    persist_mt_ts_edit,
]

#: Public task name -> callable map. Imported by the worker module to register
#: tasks. Keep names stable; jobs persisted in Redis reference these names.
#: The matching :data:`app.saq_jobs._task_names.TaskName` literal is asserted
#: in sync with this list by ``tests/saq_jobs/test_task_registry.py``.
TASK_FUNCTIONS = [
    *FILE_DELIVERY_TASK_FUNCTIONS,
    *FILE_SUBMISSION_TASK_FUNCTIONS,
    *BACKGROUND_TASK_FUNCTIONS,
]

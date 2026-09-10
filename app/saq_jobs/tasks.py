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

import httpx
from saq.types import Context
from slack_bolt.context.async_context import AsyncBoltContext

from app.auth.connector import resolve_slack_delivery_user
from app.ray.events.models import MtSuccessResponseSchema
from app.ray.submissions import SubmissionStatus, updated_submission_status
from app.ray.utils import delete_from_file_server, download_from_file_server_async
from app.slack.buglog_notifier import notify_exception
from app.slack_job import update_slack_job, update_slack_job_transaction_uuid
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


def _is_slack_user_id(user_id: str | None) -> bool:
    """True for Slack member ids, not Verify org/member UUIDs.

    Standard workspaces issue ``U…`` ids; Enterprise Grid issues ``W…``.
    """
    if not user_id:
        return False
    return user_id.startswith(("U", "W")) and user_id.isalnum()


def _alert_gateway_billing_failure(
    exc: BaseException,
    label: str,
    *,
    job: Any,
    attempt: int,
    log_extra: dict[str, Any],
) -> None:
    """Raise a Google Chat alert for a failed LanguageCloud billing call.

    Billing is deferred until after the user already has their file, so a failed
    gateway charge never self-heals into a user-visible error — it must alert on
    its own. Two triggers, deliberately bounded to avoid per-retry spam:

    * A non-retryable client error (HTTP 4xx — e.g. a 422 schema/contract
      mismatch from a partial deploy, or a 403) alerts immediately on the first
      attempt, because retrying the same payload cannot fix a rejected contract.
    * Any error alerts once on the final SAQ attempt, so a transient gateway
      outage still surfaces after retries are exhausted.
    """
    status_code = (
        exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
    )
    is_client_error = status_code is not None and 400 <= status_code < 500
    is_final_attempt = job is not None and not job.retryable

    if is_client_error and attempt == 1:
        notify_exception(
            exc,
            f"{label} rejected by gateway (HTTP {status_code}); debit not recorded",
            extra={**log_extra, "status_code": status_code},
        )
    elif is_final_attempt:
        notify_exception(
            Exception(f"{label} failed after retries"),
            f"{label} failed (final attempt)",
            extra=log_extra,
        )


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
        "client_id": data.client_id,
        "team_id": data.team_id,
        "slack_user_id": data.slack_user_id,
        "submission_id": data.submission_id,
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

    slack_user = await resolve_slack_delivery_user(
        data.client_id,
        team_id=data.team_id,
        slack_user_id=data.slack_user_id,
    )
    if slack_user is None:
        logger.error(
            "Slack user disappeared before MT upload could complete",
            extra=log_extra,
        )
        await update_slack_job(
            task_uuid=data.task_uuid,
            status="failed_delivery",
        )
        if data.submission_id:
            try:
                updated_submission_status(
                    submission_id=data.submission_id,
                    processing_status=SubmissionStatus.FAILED,
                )
            except Exception as e:
                notify_exception(
                    e,
                    "Failed to mark MT submission failed after delivery user miss",
                    extra=log_extra,
                )
        # Org-billed Document MT often hits this when poster context is missing
        # (RAY-79115 / RAY-80198). Alert immediately — there is no SAQ retry for
        # this non-retryable resolution failure, and silent failed_delivery left
        # IBM deliveries invisible in Google Chat.
        notify_exception(
            Exception("Document MT Slack delivery failed: no deliverable Slack user"),
            "Document MT Slack delivery failed (no_slack_user)",
            extra=log_extra,
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
        from app.slack.listener_actions import (
            get_language_name,  # local: avoids import cycle
        )

        language_name = await get_language_name(data.target_language)
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

        if data.mt_charge and data.task_uuid:
            from app.saq_jobs.dispatch import enqueue_document_mt_charge

            charge = dict(data.mt_charge)
            if slack_user.ray_user_group_id:
                charge["group_uuid"] = slack_user.ray_user_group_id
            poster_user_id = data.slack_user_id or slack_user.user_id
            if _is_slack_user_id(poster_user_id):
                try:
                    user_info = await client.users_info(user=poster_user_id)
                    profile = (user_info.get("user") or {}).get("profile") or {}
                    if profile.get("email"):
                        charge["email"] = profile["email"]
                    charge["client_name"] = (
                        profile.get("real_name")
                        or profile.get("real_name_normalized")
                        or charge.get("client_name")
                    )
                except Exception as e:
                    # Best-effort enrichment for usage reports; do not page on
                    # deleted/deactivated posters (user_not_found).
                    logger.warning(
                        "Failed to resolve poster profile for document MT billing",
                        extra={**log_extra, "poster_user_id": poster_user_id},
                        exc_info=e,
                    )
            else:
                logger.info(
                    "Skipping poster profile lookup; no Slack user id on org-billed MT",
                    extra={**log_extra, "poster_user_id": poster_user_id},
                )

            idempotency_key = charge.get("idempotency_key") or data.task_uuid
            await enqueue_document_mt_charge(
                client_id=data.client_id,
                task_uuid=data.task_uuid,
                idempotency_key=idempotency_key,
                charge=charge,
            )

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
            if data.submission_id:
                try:
                    updated_submission_status(
                        submission_id=data.submission_id,
                        processing_status=SubmissionStatus.FAILED,
                    )
                except Exception as e:
                    notify_exception(
                        e,
                        "Failed to mark MT submission failed after delivery error",
                        extra=log_extra,
                    )
            notify_exception(
                Exception("Document MT Slack delivery failed after retries"),
                "Document MT Slack delivery failed (final attempt)",
                extra=log_extra,
            )
        raise
    finally:
        _safe_unlink(file_path)


async def _post_configure_srt_review(
    client: Any,
    *,
    quote_id: str,
    channel_id: str,
    thread_ts: str | None,
) -> None:
    from app.slack.media_workflow_actions import _post_srt_review

    await _post_srt_review(
        client,
        {
            "quote_id": quote_id,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        },
    )


async def _fail_deferred_configure_review(task_uuid: str) -> None:
    from app.ray.events.media_pipeline_events import (
        fail_media_submissions,
        mark_media_quote_cancelled,
    )
    from app.transcriber_tasks.tasks import get_transcription_task

    task = await get_transcription_task(task_uuid)
    extra = dict(task.extra_data or {}) if task is not None else None
    await fail_media_submissions(extra)
    await mark_media_quote_cancelled(extra)


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
    srt_review_quote_id: str | None = None,
    team_id: str | None = None,
    slack_user_id: str | None = None,
    enterprise_id: str | None = None,
    word_file_id: str | None = None,
    word_file_name: str | None = None,
) -> dict[str, Any]:
    """Durable handler for transcription file uploads.

    Replaces ``_handle_transcribe_success_background`` in
    ``app/routers/ray.py``. Downloads the SRT from the file server, renames
    the temp file so Slack preserves the extension, uploads it, then posts
    the optional follow-up message.

    Org-billed media uses the Verify org as ``client_id`` (no deltaray); resolve
    via workspace stamps / ``get_slack_org`` (RAY-81247).
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
        "team_id": team_id,
        "slack_user_id": slack_user_id,
        "attempt": attempt,
    }
    logger.info("Transcription upload starting", extra=log_extra)

    slack_user = await resolve_slack_delivery_user(
        client_id,
        team_id=team_id,
        slack_user_id=slack_user_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id,
    )
    if slack_user is None:
        logger.error(
            "Slack user disappeared before transcription upload",
            extra=log_extra,
        )
        notify_exception(
            Exception("Transcription Slack delivery failed: no deliverable Slack user"),
            "Transcription Slack delivery failed (no_slack_user)",
            extra=log_extra,
        )
        if srt_review_quote_id:
            await _fail_deferred_configure_review(task_uuid)
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
            if srt_review_quote_id:
                await _post_configure_srt_review(
                    client,
                    quote_id=srt_review_quote_id,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
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
        if word_file_id and word_file_name:
            # Best-effort relative to the SRT: a Word failure must not fail
            # the job or block the follow-up / review buttons (RAY-81850).
            word_path: str | None = None
            try:
                word_output = await download_from_file_server_async(word_file_id)
                word_path = word_output.get("file")
                if not word_path:
                    raise RuntimeError(
                        "File-server download returned no path for "
                        f"word_file_id={word_file_id}"
                    )
                word_dir = os.path.dirname(word_path)
                renamed_word_path = os.path.join(word_dir, word_file_name)
                if word_path != renamed_word_path:
                    os.rename(word_path, renamed_word_path)
                    word_path = renamed_word_path
                await upload_file_to_slack_memory_efficient(
                    client=client,
                    file_path=word_path,
                    channel_id=channel_id,
                    title=word_file_name,
                    filename=word_file_name,
                    thread_ts=thread_ts,
                )
            except Exception:
                logger.exception(
                    "Word transcript upload failed; SRT already delivered",
                    extra={**log_extra, "word_file_id": word_file_id},
                )
            finally:
                _safe_unlink(word_path)
        if follow_up_message:
            await client.chat_postMessage(
                channel=channel_id,
                text=follow_up_message,
                thread_ts=thread_ts,
            )
        if srt_review_quote_id:
            await _post_configure_srt_review(
                client,
                quote_id=srt_review_quote_id,
                channel_id=channel_id,
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
            if srt_review_quote_id:
                try:
                    fail_client = AsyncWebClient(token=slack_user.bot_token)
                    await _post_configure_srt_review(
                        fail_client,
                        quote_id=srt_review_quote_id,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                    )
                except Exception:
                    logger.exception(
                        "Failed to post deferred SRT review after upload failure",
                        extra=log_extra,
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
    team_id: str | None = None,
    slack_user_id: str | None = None,
    enterprise_id: str | None = None,
) -> dict[str, Any]:
    """Durable handler for verify-complete file uploads.

    Replaces ``_handle_verify_complete_background`` in ``app/routers/ray.py``.
    Uses workspace stamps when ``client_id`` is the HT service account (no
    deltaray) so the Slack bot token still resolves (RAY-81247).
    """
    from slack_sdk.web.async_client import AsyncWebClient

    from app.slack.web import upload_file_to_slack_memory_efficient

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "grid_file_id": grid_file_id,
        "client_id": client_id,
        "channel_id": channel_id,
        "team_id": team_id,
        "slack_user_id": slack_user_id,
        "attempt": attempt,
    }
    logger.info("Verify-complete upload starting", extra=log_extra)

    slack_user = await resolve_slack_delivery_user(
        client_id,
        team_id=team_id,
        slack_user_id=slack_user_id,
        enterprise_id=enterprise_id,
        channel_id=channel_id,
    )
    if slack_user is None:
        logger.error(
            "Slack user disappeared before verify-complete upload",
            extra=log_extra,
        )
        # Same gap as pre-RAY-79115 Document MT: soft no_slack_user skipped
        # BugLog/Google Chat, so HT SA / stamp misses were silent (RAY-81247).
        notify_exception(
            Exception("HV complete Slack delivery failed: no deliverable Slack user"),
            "HV complete Slack delivery failed (no_slack_user)",
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


async def process_document_mt_quote_preflight(
    ctx: Context,
    *,
    quote_id: str,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    files: list[dict[str, Any]],
    source_language: str | None,
    target_languages: list[str],
) -> dict[str, Any]:
    """Prepare a document MT quote by caching file state and requesting pricing."""
    from slack_sdk.web.async_client import AsyncWebClient

    from app.api.stream_proxy import send_document_mt_quote_request
    from app.auth.connector import (
        get_bot_token_async,
        get_group_mt_engine,
        get_ray_connection,
        get_verify_trial_status,
        mt_bills_workspace_org,
    )
    from app.config import config
    from app.ray.submissions import _hash_file_content_sha256_hex
    from app.ray.utils import (
        SlackFilenameTooLong,
        filename_too_long_user_message,
        upload_to_file_server,
        validate_file,
    )
    from app.slack.document_mt_quotes import (
        QUOTE_STATUS_PENDING,
        save_document_mt_quote_session,
    )
    from app.slack.web import download_file

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "quote_id": quote_id,
        "user_id": user_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "attempt": attempt,
        "file_count": len(files),
    }
    ray_connection = await get_ray_connection(user_id, team_id, enterprise_id)
    if ray_connection is None or not ray_connection.super_group:
        logger.error(
            "Document MT quote preflight has no connected workspace org",
            extra=log_extra,
        )
        return {"status": "no_super_group"}

    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token:
        logger.error(
            "Document MT quote preflight has no Slack bot token", extra=log_extra
        )
        return {"status": "no_bot_token"}

    ray_client = ray_connection.client
    if ray_client is not None and ray_client.is_trial is None:
        ray_client.is_trial, ray_client.trial_remaining = await get_verify_trial_status(
            ray_client.id_token
        )
    max_pdf_size_bytes = (
        config.document_mt_pdf_max_size_bytes
        if ray_client is not None and ray_client.is_trial
        else None
    )

    client = AsyncWebClient(token=bot_token)
    downloaded_files: list[str] = []
    uploaded_files: list[dict[str, Any]] = []
    validation_errors: list[str] = []

    try:
        for file_data in files:
            slack_file_id = file_data["id"]
            file_title = file_data.get("title") or slack_file_id
            try:
                input_file = await download_file(
                    client=client, file_id=slack_file_id, http=None
                )
            except SlackFilenameTooLong as exc:
                validation_errors.append(filename_too_long_user_message(exc.filename))
                continue
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
            uploaded_files.append(
                {
                    "slack_file_id": slack_file_id,
                    "title": file_title,
                    "size": file_data.get("size"),
                    "file_id": input_file_id,
                    "file_name": os.path.basename(input_file),
                    "file_hash": _hash_file_content_sha256_hex(input_file),
                    "file_size": os.path.getsize(input_file),
                }
            )

        for message in validation_errors:
            await client.chat_postMessage(channel=user_id, text=message)

        if not uploaded_files:
            await client.chat_postMessage(
                channel=user_id,
                text=_("No valid files were available to quote."),
            )
            return {"status": "no_valid_files"}

        if mt_bills_workspace_org(ray_connection):
            user_group_id = ray_connection.super_group[0].id
            billing_client_id = ray_connection.super_group[0].verify_organization_uuid
            is_group_id = True
        else:
            assert ray_connection.client is not None
            user_group_id = ray_connection.client.user_group_id
            billing_client_id = ray_connection.client.id
            is_group_id = False
        ai_engine = await get_group_mt_engine(user_group_id, is_group_id)
        if len(target_languages) == 1 and target_languages[0].lower() == "fr-ca":
            ai_engine = "microsoft"

        await save_document_mt_quote_session(
            {
                "quote_id": quote_id,
                "status": QUOTE_STATUS_PENDING,
                "user_id": user_id,
                "team_id": team_id,
                "enterprise_id": enterprise_id,
                "channel_id": channel_id,
                "source_language": source_language,
                "target_languages": target_languages,
                "files": uploaded_files,
            }
        )
        await send_document_mt_quote_request(
            quote_id=quote_id,
            files=[
                {
                    "file_id": file["file_id"],
                    "file_name": file["file_name"],
                    "file_size": file["file_size"],
                }
                for file in uploaded_files
            ],
            client_id=billing_client_id,
            channel_id=channel_id,
            source_language=source_language,
            target_languages=target_languages,
            ai_engine=ai_engine,
            team_id=team_id,
            slack_user_id=user_id if _is_slack_user_id(user_id) else None,
            enterprise_id=enterprise_id,
        )
        return {"status": "quote_requested", "file_count": len(uploaded_files)}
    except Exception:
        logger.exception("Document MT quote preflight failed", extra=log_extra)
        if job is not None and not job.retryable:
            notify_exception(
                Exception("Queued document MT quote preflight failed"),
                "Queued document MT quote preflight failed (final attempt)",
            )
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    "There was an error preparing your translation quote, please try again."
                ),
            )
        raise
    finally:
        for path in downloaded_files:
            _safe_unlink(path)


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
    quote_id: str | None = None,
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
    from app.ray.submissions import (
        check_and_record_submission_async,
        check_and_record_submission_metadata_async,
    )
    from app.ray.utils import (
        SlackFilenameTooLong,
        filename_too_long_user_message,
        upload_to_file_server,
        validate_file,
    )
    from app.slack.document_mt_quotes import get_document_mt_quote_session
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
    if ray_connection is None or not ray_connection.super_group:
        logger.error(
            "Document MT submission has no connected workspace org", extra=log_extra
        )
        return {"status": "no_super_group"}

    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token:
        logger.error("Document MT submission has no Slack bot token", extra=log_extra)
        return {"status": "no_bot_token"}

    ray_client = ray_connection.client
    if ray_client is not None and ray_client.is_trial is None:
        ray_client.is_trial, ray_client.trial_remaining = await get_verify_trial_status(
            ray_client.id_token
        )
    max_pdf_size_bytes = (
        config.document_mt_pdf_max_size_bytes
        if ray_client is not None and ray_client.is_trial
        else None
    )

    client = AsyncWebClient(token=bot_token)
    context = {
        "user_id": user_id,
        "team_id": team_id,
        "enterprise_id": enterprise_id,
        "channel_id": channel_id,
        "ray": RayConnection(ray_connection.super_group, ray_client),
    }
    downloaded_files: list[str] = []
    files_uploaded: list[str] = []
    duplicate_submissions: list[str] = []
    validation_errors: list[str] = []

    try:
        cached_quote = (
            await get_document_mt_quote_session(quote_id) if quote_id else None
        )
        if quote_id and cached_quote is None:
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    "This translation quote has expired. Please request a new quote."
                ),
            )
            return {"status": "quote_expired"}

        cached_files = cached_quote.get("files", []) if cached_quote else []
        files_to_process = cached_files or files
        preflight_task_uuid = (
            cached_quote.get("preflight_task_uuid") if cached_quote else None
        )
        # Quote Adjust Request selections scope the submission to the chosen
        # file/language pairs; empty means the full quoted batch.
        selected_pairs = (
            {str(pair) for pair in cached_quote.get("selected_pairs") or []}
            if cached_quote
            else set()
        )

        for file_data in files_to_process:
            slack_file_id = str(
                file_data.get("id") or file_data.get("slack_file_id") or ""
            )
            file_title = str(
                file_data.get("title") or file_data.get("file_name") or slack_file_id
            )
            input_file = None
            input_file_id = str(file_data.get("file_id") or "")
            if not input_file_id:
                if not slack_file_id:
                    validation_errors.append(_("Invalid Slack file metadata."))
                    continue
                try:
                    input_file = await download_file(
                        client=client, file_id=slack_file_id, http=None
                    )
                except SlackFilenameTooLong as exc:
                    validation_errors.append(
                        filename_too_long_user_message(exc.filename)
                    )
                    continue
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
                    validation_errors.append(
                        error_message or _("Invalid file content.")
                    )
                    continue

                input_file_id = await upload_to_file_server(input_file)
            submitted_languages: list[str] = []
            submission_ids: dict[str, int] = {}
            pair_file_id = str(file_data.get("file_id") or "")
            for target_language in target_languages:
                if (
                    selected_pairs
                    and f"{pair_file_id}:{target_language}" not in selected_pairs
                ):
                    continue
                if cached_quote:
                    is_dup, record = await check_and_record_submission_metadata_async(
                        file_hash=str(file_data["file_hash"]),
                        file_name=str(file_data["file_name"]),
                        file_size=int(file_data.get("file_size") or 0),
                        file_id=input_file_id,
                        user_id=user_id,
                        team_id=team_id,
                        channel_id=channel_id,
                        source_language=source_language or "",
                        target_language=target_language,
                    )
                else:
                    assert input_file is not None
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
                    quote_id=quote_id,
                    preflight_task_uuid=preflight_task_uuid,
                    selected_pairs=sorted(selected_pairs) if selected_pairs else None,
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
    preaccepted_ai_translation_quote: bool = False,
    prequote_message_ts: str | None = None,
    ai_translation_filename_and_languages: list[str] | None = None,
    quote_id: str | None = None,
) -> dict[str, Any]:
    """Durable quality-evaluation / human-translation submission processing."""
    from slack_sdk.web.async_client import AsyncWebClient

    from app.api.stream_proxy import send_document_mt_quote_request
    from app.api.verify import (
        VerifyAPIError,
        VerifyCreateRejected,
        get_verify_languages,
        submit_evaluation_job,
    )
    from app.auth.connector import (
        RayConnection,
        get_bot_token_async,
        get_group_mt_engine,
        get_ray_client,
        get_ray_super_group,
        user_may_receive_quotes,
    )
    from app.constants import (
        EVALUATE_PDF_QUOTE_OUTPUT_STREAM,
        HUMAN_EVALUATION_WORKFLOW_UUID,
    )
    from app.ray.submissions import (
        SubmissionStatus,
        check_and_record_evaluate_submission_async,
        updated_submission_status,
    )
    from app.ray.utils import (
        SlackFilenameTooLong,
        filename_too_long_user_message,
        is_ibm_enterprise,
        upload_to_file_server,
        validate_file,
    )
    from app.slack.evaluation_ai_adjustment import (
        colliding_evaluate_upload_filenames,
        evaluate_upload_filename,
        post_convert_filename_collision_message,
    )
    from app.slack.evaluation_submissions import publish_pdf_evaluate_convert
    from app.slack.pdf_evaluate_quotes import (
        STAGE_QUOTE_PENDING,
        restore_pdf_evaluate_quote_for_retry,
        save_pdf_evaluate_quote_session,
    )
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
    # Evaluate needs a member client for Verify API auth. Do not require a
    # workspace super-group link — individually connected users must still work.
    # IBM non-logged-in HT (RAY-81247): own the job as the HT service account,
    # but keep quote gating based on the Slack poster's own membership.
    from app.ibm_ht_service_account import (
        get_ht_service_account_ray_client,
        resolve_slack_poster_email,
        should_use_ht_service_account,
    )

    poster_client = await get_ray_client(user_id, team_id, enterprise_id)
    super_groups = await get_ray_super_group(team_id, enterprise_id) or []
    poster_connection = RayConnection(super_groups, poster_client)
    # Prefer workspace super group so quote gating is org-scoped (same as
    # Document MT / Media). Individually connected users without a workspace
    # link still fall back to primary-group Admin/Owner.
    may_quote = await user_may_receive_quotes(poster_connection)
    # IBM Slack HT (RAY-81247): prefer the poster's active CRM member; only use
    # the service account when they have no personal CRM link.
    requester_email = ""
    if should_use_ht_service_account(enterprise_id, poster_connection):
        ray_client = await get_ht_service_account_ray_client(
            slack_user_id=user_id,
            slack_team_id=team_id,
            slack_enterprise_id=enterprise_id,
        )
    else:
        ray_client = poster_client
    if ray_client is None:
        logger.error("Evaluation submission has no RAY client", extra=log_extra)
        return {"status": "no_ray_client"}
    # HUMAN_EVALUATION embeds HV and starts TP jobs before Slack Accept
    # ("cancelled" + empty Adjust). Non-admin HT must use synthetic AI+QE with
    # slack_ht_quote_after_qe so HV waits for the HT quote Accept.
    is_human_translation = workflow_uuid == HUMAN_EVALUATION_WORKFLOW_UUID
    if may_quote:
        slack_ht_quote_after_qe = False
        confirmation_required = True
    elif is_human_translation:
        slack_ht_quote_after_qe = True
        confirmation_required = True
    else:
        # Non-admin Quality Evaluation: prod-like auto-run, no quote staging.
        slack_ht_quote_after_qe = False
        confirmation_required = False

    bot_token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not bot_token:
        logger.error("Evaluation submission has no Slack bot token", extra=log_extra)
        return {"status": "no_bot_token"}

    client = AsyncWebClient(token=bot_token)
    # Stamp poster email on HT-SA-owned jobs for usage-report Client Email remap.
    if should_use_ht_service_account(enterprise_id, poster_connection):
        requester_email = await resolve_slack_poster_email(client, user_id)
    downloaded_files: list[str] = []
    input_files: list[str] = []
    file_titles: list[str] = []
    valid_files: list[dict[str, Any]] = []
    new_submission_ids: list[int] = []

    def _mark_new_submissions_failed() -> None:
        for submission_id in new_submission_ids:
            try:
                updated_submission_status(
                    submission_id=submission_id,
                    processing_status=SubmissionStatus.FAILED,
                )
            except Exception:
                logger.exception(
                    "Failed to mark evaluate submission as failed",
                    extra={**log_extra, "submission_id": submission_id},
                )

    try:
        for file_data in files:
            try:
                input_file = await download_file(
                    client=client, file_id=file_data["id"], http=None
                )
            except SlackFilenameTooLong as exc:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=filename_too_long_user_message(exc.filename),
                )
                continue
            downloaded_files.append(input_file)
            is_valid, is_valid_content, error_message = validate_file(input_file)
            if not is_valid or not is_valid_content:
                await client.chat_postMessage(
                    channel=channel_id,
                    text=error_message,
                )
                continue
            if not file_data.get("size") and os.path.exists(input_file):
                file_data["size"] = os.path.getsize(input_file)
            # Slack picker option text is capped at 75 characters, so the queued
            # title may be truncated (and lose .pdf/.docx). Pair keys and PDF
            # detection must use the downloaded files.info name.
            downloaded_name = os.path.basename(input_file)
            file_data = {
                **file_data,
                "title": downloaded_name,
                "name": downloaded_name,
            }
            input_files.append(input_file)
            file_titles.append(downloaded_name)
            valid_files.append(file_data)

        if not input_files:
            return {"status": "no_valid_files"}

        has_pdf = any(title.lower().endswith(".pdf") for title in file_titles)
        verify_languages = await get_verify_languages()
        uuid_to_code = {
            str(language["uuid"]): str(language.get("code") or language["uuid"])[:10]
            for language in verify_languages
        }
        language_names = {
            str(language["uuid"]): str(language.get("name") or language["uuid"])
            for language in verify_languages
        }
        language_uuid_by_code = {
            code: language_uuid for language_uuid, code in uuid_to_code.items() if code
        }
        source_language_code = uuid_to_code.get(
            source_lang_uuid, (source_lang_uuid or "")[:10]
        )
        # Admins get the PDF pre-quote; non-admins skip straight to convert/create.
        # Price AI tokens via the same consumer extract path as Document MT (no
        # Adobe DOCX convert until Accept). Callback:
        # verify:slack:evaluate:pdf:quote → evaluate/HT Service Quote UI.
        if has_pdf and not preaccepted_ai_translation_quote and may_quote:
            uploaded_files: list[dict[str, Any]] = []
            for file_path, file_data in zip(input_files, valid_files, strict=True):
                gridfs_file_id = await upload_to_file_server(file_path)
                uploaded_files.append(
                    {
                        "id": file_data["id"],
                        "title": file_data["title"],
                        "size": file_data.get("size") or os.path.getsize(file_path),
                        "gridfs_file_id": gridfs_file_id,
                        "file_name": os.path.basename(file_path),
                        "pdf_page_count": 0,
                    }
                )
            target_language_codes = [
                uuid_to_code.get(language_uuid, language_uuid[:10])
                for language_uuid in target_langs_uuid
            ]
            ai_engine = await get_group_mt_engine(ray_client.user_group_id, False)
            if (
                len(target_language_codes) == 1
                and target_language_codes[0].lower() == "fr-ca"
            ):
                ai_engine = "microsoft"

            quote_id = await save_pdf_evaluate_quote_session(
                quote_id=quote_id,
                channel_id=channel_id,
                user_id=user_id,
                team_id=team_id,
                enterprise_id=enterprise_id,
                files=uploaded_files,
                target_langs_uuid=target_langs_uuid,
                reference=reference,
                source_lang_uuid=source_lang_uuid,
                workflow_uuid=workflow_uuid,
                job_notes=job_notes,
                language_uuid_by_code=language_uuid_by_code,
                language_names=language_names,
                stage=STAGE_QUOTE_PENDING,
            )
            await send_document_mt_quote_request(
                quote_id=quote_id,
                files=[
                    {
                        "file_id": file_data["gridfs_file_id"],
                        "file_name": file_data["file_name"],
                        "file_size": file_data.get("size"),
                    }
                    for file_data in uploaded_files
                ],
                client_id=ray_client.id,
                channel_id=channel_id,
                source_language=source_language_code or None,
                target_languages=target_language_codes,
                ai_engine=ai_engine,
                team_id=team_id,
                slack_user_id=user_id if _is_slack_user_id(user_id) else None,
                output_stream=EVALUATE_PDF_QUOTE_OUTPUT_STREAM,
                enterprise_id=enterprise_id,
            )
            return {"status": "quote_requested", "quote_id": quote_id}

        requested_pair_keys: set[str] | None = None
        if ai_translation_filename_and_languages:
            requested_pair_keys = {
                str(pair).strip()
                for pair in ai_translation_filename_and_languages
                if str(pair).strip()
            }

        duplicate_submissions: list[str] = []
        allowed_file_indexes: set[int] = set()
        allowed_pairs: list[str] = []
        # Union of target UUIDs across allowed files (preserve modal order).
        allowed_lang_uuids: list[str] = []
        seen_lang_uuids: set[str] = set()

        for file_index, (input_file, file_data, file_title) in enumerate(
            zip(input_files, valid_files, file_titles, strict=True)
        ):
            # Selections are keyed on the name Verify will see, which is the
            # converted name for a PDF. Matching on the raw Slack title drops
            # every PDF from the batch.
            upload_title = evaluate_upload_filename(file_data)
            file_target_uuids: list[str] = []
            for target_lang_uuid in target_langs_uuid:
                pair_key = f"{upload_title}:{target_lang_uuid}"
                if (
                    requested_pair_keys is not None
                    and pair_key not in requested_pair_keys
                ):
                    continue
                file_target_uuids.append(target_lang_uuid)
            if not file_target_uuids:
                continue

            file_target_codes = [
                uuid_to_code.get(lang_uuid, (lang_uuid or "")[:10])
                for lang_uuid in file_target_uuids
            ]
            is_dup, record = await check_and_record_evaluate_submission_async(
                path=input_file,
                file_name=os.path.basename(input_file),
                file_id=str(file_data.get("id") or ""),
                user_id=user_id,
                team_id=team_id,
                channel_id=channel_id,
                source_language=source_language_code,
                target_languages=file_target_codes,
            )
            targets_label = ", ".join(file_target_codes)
            if is_dup:
                duplicate_submissions.append(
                    f"{file_title} ({source_language_code or 'auto'} -> "
                    f"{targets_label})"
                )
                continue

            new_submission_ids.append(record.id)
            allowed_file_indexes.add(file_index)
            for target_lang_uuid in file_target_uuids:
                allowed_pairs.append(f"{upload_title}:{target_lang_uuid}")
                if target_lang_uuid not in seen_lang_uuids:
                    seen_lang_uuids.add(target_lang_uuid)
                    allowed_lang_uuids.append(target_lang_uuid)

        if duplicate_submissions and not allowed_file_indexes:
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    "Please allow the system to complete the ongoing human "
                    "translation request(s) "
                    f"*({', '.join(duplicate_submissions)})* to prevent "
                    "duplicate submissions."
                ),
            )
            return {
                "status": "duplicate",
                "duplicate_count": len(duplicate_submissions),
            }

        if duplicate_submissions:
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    "Please allow the system to complete the ongoing human "
                    "translation request(s) "
                    f"*({', '.join(duplicate_submissions)})* to prevent "
                    "duplicate submissions."
                ),
            )

        submit_input_files = [
            path
            for index, path in enumerate(input_files)
            if index in allowed_file_indexes
        ]
        submit_file_titles = [
            title
            for index, title in enumerate(file_titles)
            if index in allowed_file_indexes
        ]
        submit_target_langs = allowed_lang_uuids
        # Always pass explicit pairs when any file was filtered by Adjust or
        # per-file dedupe so remaining files keep their intended target sets.
        submit_ai_pairs = allowed_pairs or None

        if not submit_input_files or not submit_target_langs:
            # Publishing here would send an empty job that Verify rejects with a
            # 400, leaving the quote stuck on "converting" with nothing running.
            logger.error(
                "Evaluation submission resolved to no files or targets; refusing "
                "to publish",
                extra={
                    **log_extra,
                    "requested_pair_count": len(requested_pair_keys or ()),
                    "duplicate_count": len(duplicate_submissions),
                },
            )
            _mark_new_submissions_failed()
            await restore_pdf_evaluate_quote_for_retry(
                client,
                quote_id=quote_id,
                channel_id=channel_id,
                message_ts=prequote_message_ts,
                is_ibm=is_ibm_enterprise(enterprise_id),
            )
            await client.chat_postMessage(
                channel=user_id,
                text=_(
                    "We could not start your translation because none of the "
                    "selected files and languages could be matched. Please try "
                    "submitting again."
                ),
            )
            return {"status": "nothing_to_submit"}

        submit_files = [
            file_data
            for index, file_data in enumerate(valid_files)
            if index in allowed_file_indexes
        ]
        collisions = colliding_evaluate_upload_filenames(submit_files)
        if collisions:
            # PDF→DOCX rewrite makes report.pdf and report.docx the same
            # Verify upload name. Publishing would 400 after convert with
            # no Slack error, leaving the quote stuck on "converting".
            collision_text = post_convert_filename_collision_message(collisions)
            logger.warning(
                "Evaluation submission has post-convert filename collisions; "
                "refusing to publish",
                extra={
                    **log_extra,
                    "colliding_upload_names": sorted(collisions),
                },
            )
            _mark_new_submissions_failed()
            await restore_pdf_evaluate_quote_for_retry(
                client,
                quote_id=quote_id,
                channel_id=channel_id,
                message_ts=prequote_message_ts,
                is_ibm=is_ibm_enterprise(enterprise_id),
                status_message=collision_text,
            )
            await client.chat_postMessage(
                channel=user_id,
                text=collision_text,
            )
            return {"status": "filename_collision"}

        # Clear HUMAN_EVALUATION for any staged HT path (admin quotes or
        # non-admin HT-after-QE) so CVC builds synthetic AI+QE without early HV.
        submit_workflow_uuid = None if is_human_translation else workflow_uuid

        if has_pdf:
            await publish_pdf_evaluate_convert(
                ray_client=ray_client,
                input_files=submit_input_files,
                file_titles=submit_file_titles,
                target_langs_uuid=submit_target_langs,
                reference=reference,
                channel_id=channel_id,
                source_lang_uuid=source_lang_uuid,
                workflow_uuid=submit_workflow_uuid,
                job_notes=job_notes,
                preaccepted_ai_translation_quote=preaccepted_ai_translation_quote,
                prequote_message_ts=prequote_message_ts,
                ai_translation_filename_and_languages=submit_ai_pairs,
                slack_ht_quote_after_qe=slack_ht_quote_after_qe,
                confirmation_required=confirmation_required,
                slack_user_id=user_id,
                slack_team_id=team_id,
                slack_enterprise_id=enterprise_id,
                requester_email=requester_email,
            )
        else:
            await submit_evaluation_job(
                ray_client,
                submit_input_files,
                submit_target_langs,
                reference,
                source_language_uuid=source_lang_uuid,
                workflow_uuid=submit_workflow_uuid,
                job_notes=job_notes,
                slack_channel_id=channel_id,
                slack_user_id=user_id,
                slack_team_id=team_id,
                slack_enterprise_id=enterprise_id,
                preaccepted_ai_translation_quote=preaccepted_ai_translation_quote,
                prequote_message_ts=prequote_message_ts,
                ai_translation_filename_and_languages=submit_ai_pairs,
                slack_ht_quote_after_qe=slack_ht_quote_after_qe,
                confirmation_required=confirmation_required,
                requester_email=requester_email,
            )
        return {
            "status": "submitted",
            "file_count": len(submit_input_files),
            "duplicate_count": len(duplicate_submissions),
        }
    except VerifyCreateRejected:
        _mark_new_submissions_failed()
        error_msg = (
            "There was an error submitting your human translation request, please try again."
            if workflow_uuid
            else "There was an error submitting your quality evaluation request, please try again."
        )
        await client.chat_postMessage(channel=channel_id, text=_(error_msg))
        return {"status": "verify_rejected"}
    except VerifyAPIError:
        _mark_new_submissions_failed()
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
            _mark_new_submissions_failed()
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


async def charge_inline_mt_usage(
    ctx: Context,
    *,
    billing: dict[str, Any],
    usage_log: dict[str, Any],
) -> dict[str, Any]:
    """Durable inline/channel/shortcut MT billing + usage logging (RAY-80258).

    Decouples billing durability from Slack delivery: the
    ``slack:direct:mt:result`` handler posts the Slack notification inline and
    returns 200, then enqueues this task to charge the LanguageCloud gateway.
    A transient ``ConnectTimeout`` to ``/mt/inline-usage`` therefore no longer
    returns a 422 to ``redis-slack-consumer`` after the user has already been
    notified — the charge is retried here instead of being lost.

    Idempotency:
        The caller passes ``billing["idempotency_key"]`` (the same value used
        as the SAQ job ``key``). The gateway dedupes on that key, so a SAQ
        retry or a redelivered stream entry never double-charges. The Google
        API usage row is written only after the charge succeeds, so it carries
        the gateway ``transaction_uuid`` and is the last side-effect (safe to
        re-run after a partial failure).

    Args:
        ctx: SAQ task context (job, queue, attempts).
        billing: Keyword arguments for ``log_inline_mt_usage_by_client_id``.
            JSON-serialisable identifiers only — no tokens.
        usage_log: Keyword arguments for ``log_google_api_usage`` (without
            ``transaction_uuid``, which is resolved from the charge).

    Returns:
        Status dict with ``status`` and the gateway ``transaction_uuid``.
    """
    from app.api.http_client import retry_on_timeout
    from app.auth.connector import log_inline_mt_usage_by_client_id
    from app.mt.logs import log_google_api_usage

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "client_id": billing.get("client_id"),
        "usage_type": billing.get("usage_type"),
        "idempotency_key": billing.get("idempotency_key"),
        "attempt": attempt,
    }
    logger.info("Inline MT billing starting", extra=log_extra)

    try:
        # Retry transient connect/read timeouts quickly within this attempt;
        # SAQ retries cover longer gateway outages. Suppress the inner
        # notify so only the final SAQ attempt raises a billing alert.
        transaction_uuid = await retry_on_timeout(
            log_inline_mt_usage_by_client_id,
            max_retries=2,
            notify_on_final_failure=False,
            **billing,
        )
        await log_google_api_usage(transaction_uuid=transaction_uuid, **usage_log)
        logger.info(
            "Inline MT billing charged",
            extra={**log_extra, "transaction_uuid": transaction_uuid},
        )
        return {"status": "charged", "transaction_uuid": transaction_uuid}
    except Exception as exc:
        logger.exception("Inline MT billing failed; SAQ will retry", extra=log_extra)
        _alert_gateway_billing_failure(
            exc, "Inline MT billing", job=job, attempt=attempt, log_extra=log_extra
        )
        raise


async def charge_document_mt(
    ctx: Context,
    *,
    client_id: str,
    charge: dict[str, Any],
    task_uuid: str,
) -> dict[str, Any]:
    """Durable document-MT billing after Slack delivery (RAY-80417).

    Charges document MT and, when the source was a converted PDF, the combined
    PDF conversion fee in one ``/mt/transaction`` call. Decoupled from
    ``slack_upload_mt_result`` so a transient gateway timeout after the user
    received their file does not lose the debit; idempotency is enforced by the
    gateway keys in ``charge``.
    """
    from app.api.http_client import retry_on_timeout
    from app.auth.connector import log_document_mt_by_client_id

    job = ctx.get("job")
    attempt = job.attempts if job is not None else 1
    log_extra = {
        "task_uuid": task_uuid,
        "idempotency_key": charge.get("idempotency_key"),
        "attempt": attempt,
    }
    logger.info("Document MT billing starting", extra=log_extra)

    try:
        result = await retry_on_timeout(
            log_document_mt_by_client_id,
            client_id,
            charge,
            max_retries=2,
            notify_on_final_failure=False,
        )
        # A first-attempt replay means the gateway already had this debit (a
        # duplicate enqueue or a prior run whose ack was lost): no new debit was
        # written, so surface it rather than reporting a clean charge.
        if (result or {}).get("replayed") and attempt == 1:
            logger.warning(
                "Document MT billing replayed on first attempt; no new debit",
                extra={
                    **log_extra,
                    "transaction_uuid": (result or {}).get("transaction_uuid"),
                },
            )
        transaction_uuid = (result or {}).get("transaction_uuid")
        logger.info(
            "Document MT billing charged",
            extra={
                **log_extra,
                "transaction_uuid": transaction_uuid,
                "pdf_transaction_uuid": (result or {}).get("pdf_transaction_uuid"),
            },
        )
        # RAY-80941: persist document-MT txn on slack_job so IBM report PDF
        # Transaction Group / identity can remappoint without stem/time heuristics.
        try:
            await update_slack_job_transaction_uuid(task_uuid, transaction_uuid)
        except Exception:
            logger.exception(
                "Failed to link slack_job.transaction_uuid after document MT charge",
                extra={**log_extra, "transaction_uuid": transaction_uuid},
            )
        return {"status": "charged", **(result or {})}
    except Exception as exc:
        logger.exception("Document MT billing failed; SAQ will retry", extra=log_extra)
        _alert_gateway_billing_failure(
            exc, "Document MT billing", job=job, attempt=attempt, log_extra=log_extra
        )
        raise


# --------------------------------------------------------------------------- #
# Task registry
# --------------------------------------------------------------------------- #

FILE_DELIVERY_TASK_FUNCTIONS = [
    slack_upload_mt_result,
    slack_upload_transcription,
    slack_upload_verify_complete,
]

FILE_SUBMISSION_TASK_FUNCTIONS = [
    process_document_mt_quote_preflight,
    process_document_mt_submission,
    process_evaluation_submission,
]

BACKGROUND_TASK_FUNCTIONS = [
    persist_log_notification,
    charge_inline_mt_usage,
    charge_document_mt,
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

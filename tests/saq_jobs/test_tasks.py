"""Unit tests for the durable SAQ task functions (RAY-79638).

Tasks are tested without a running SAQ worker by invoking them directly with
a stub context. External dependencies (Slack SDK, file server, slack_user
lookup, slack_job updates) are mocked so the tests run hermetically.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.saq_jobs.tasks import (
    charge_document_mt,
    charge_inline_mt_usage,
    persist_log_notification,
    process_document_mt_quote_preflight,
    process_document_mt_submission,
    process_evaluation_submission,
    slack_upload_mt_result,
    slack_upload_transcription,
    slack_upload_verify_complete,
)


def _ctx(attempts: int = 1, retryable: bool = True) -> dict[str, Any]:
    """Build a stub SAQ context object."""
    job = MagicMock()
    job.attempts = attempts
    job.retryable = retryable
    return {"job": job}


@pytest.fixture
def slack_user():
    user = MagicMock()
    user.bot_token = "xoxb-fake-test-token"  # noqa: S105 — test fixture
    user.ray_client_id = str(uuid4())
    return user


@pytest.fixture(autouse=True)
def _quiet_notify():
    """Avoid touching BugLog/Google Chat from inside the task body."""
    with patch("app.saq_jobs.tasks.notify_exception"):
        yield


# --------------------------------------------------------------------------- #
# process_document_mt_submission / process_evaluation_submission
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_document_mt_submission_downloads_uploads_and_submits():
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    super_group = MagicMock()
    super_group.verify_organization_uuid = "org-uuid"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = [super_group]
    record = MagicMock(id=123)
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pptx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server", new=AsyncMock(return_value="grid-1")
        ),
        patch(
            "app.ray.submissions.check_and_record_submission_async",
            new=AsyncMock(return_value=(False, record)),
        ),
        patch(
            "app.slack.listener_actions.document_machine_translate", new=AsyncMock()
        ) as mock_mt,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_document_mt_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx"}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    assert result["status"] == "processed"
    mock_mt.assert_awaited_once()
    assert mock_mt.await_args.args[1:] == ("grid-1", "en", ["zh-CN"], {"zh-CN": 123})
    fake_slack.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_document_mt_quote_preflight_uploads_and_requests_quote():
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    ray_client.id = "client-1"
    ray_client.user_group_id = "group-1"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = []
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch(
            "app.auth.connector.get_group_mt_engine",
            new=AsyncMock(return_value="google"),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pptx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.ray.utils.upload_to_file_server", new=AsyncMock(return_value="grid-1")
        ),
        patch(
            "app.ray.submissions._hash_file_content_sha256_hex",
            return_value="hash-1",
        ),
        patch("os.path.getsize", return_value=1234),
        patch(
            "app.slack.document_mt_quotes.save_document_mt_quote_session",
            new=AsyncMock(),
        ) as mock_save,
        patch(
            "app.api.stream_proxy.send_document_mt_quote_request",
            new=AsyncMock(),
        ) as mock_quote,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_document_mt_quote_preflight(
            _ctx(),
            quote_id="quote-1",
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx", "size": 1234}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    assert result == {"status": "quote_requested", "file_count": 1}
    mock_save.assert_awaited_once()
    saved_session = mock_save.await_args.args[0]
    assert saved_session["quote_id"] == "quote-1"
    assert saved_session["files"][0]["file_id"] == "grid-1"
    assert saved_session["files"][0]["file_hash"] == "hash-1"
    mock_quote.assert_awaited_once()
    assert mock_quote.await_args.kwargs["quote_id"] == "quote-1"
    assert mock_quote.await_args.kwargs["files"][0]["file_id"] == "grid-1"


@pytest.mark.asyncio
async def test_process_document_mt_submission_uses_cached_quote_file_state():
    ray_client = MagicMock()
    ray_client.is_trial = False
    ray_client.id_token = "id-token"
    ray_connection = MagicMock()
    ray_connection.client = ray_client
    ray_connection.super_group = []
    record = MagicMock(id=123)
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()
    session = {
        "quote_id": "quote-1",
        "user_id": "U1",
        "team_id": "T1",
        "channel_id": "C1",
        "source_language": "en",
        "target_languages": ["zh-CN"],
        "preflight_task_uuid": "preflight-1",
        "files": [
            {
                "slack_file_id": "F1",
                "title": "a.pptx",
                "file_id": "grid-1",
                "file_name": "a.pptx",
                "file_hash": "hash-1",
                "file_size": 1234,
            }
        ],
    }

    with (
        patch(
            "app.auth.connector.get_ray_connection",
            new=AsyncMock(return_value=ray_connection),
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch(
            "app.slack.document_mt_quotes.get_document_mt_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch("app.slack.web.download_file", new=AsyncMock()) as mock_download,
        patch(
            "app.ray.submissions.check_and_record_submission_metadata_async",
            new=AsyncMock(return_value=(False, record)),
        ) as mock_record,
        patch(
            "app.slack.listener_actions.document_machine_translate", new=AsyncMock()
        ) as mock_mt,
    ):
        result = await process_document_mt_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[],
            source_language="en",
            target_languages=["zh-CN"],
            quote_id="quote-1",
        )

    assert result["status"] == "processed"
    mock_download.assert_not_awaited()
    mock_record.assert_awaited_once()
    assert mock_record.await_args.kwargs["file_hash"] == "hash-1"
    mock_mt.assert_awaited_once()
    assert mock_mt.await_args.args[1:] == ("grid-1", "en", ["zh-CN"], {"zh-CN": 123})
    assert mock_mt.await_args.kwargs == {
        "quote_id": "quote-1",
        "preflight_task_uuid": "preflight-1",
    }


@pytest.mark.asyncio
async def test_process_evaluation_submission_direct_verify_upload():
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_client", new=AsyncMock(return_value=ray_client)
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch("app.api.verify.submit_evaluation_job", new=AsyncMock()) as mock_submit,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {"status": "submitted", "file_count": 1}
    mock_submit.assert_awaited_once()
    assert mock_submit.await_args.args[:4] == (
        ray_client,
        ["/tmp/a.docx"],
        ["lang-1"],
        "ref",
    )


@pytest.mark.asyncio
async def test_process_evaluation_submission_pdf_posts_prequote_before_conversion():
    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock(return_value={"ts": "123.456"})

    with (
        patch(
            "app.auth.connector.get_ray_client", new=AsyncMock(return_value=ray_client)
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.pdf")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.slack.pdf_evaluate_quotes.pdf_page_count_from_file",
            return_value=3,
        ),
        patch(
            "app.slack.pdf_evaluate_quotes.save_pdf_evaluate_quote_session",
            new=AsyncMock(return_value="quote-1"),
        ) as mock_save,
        patch(
            "app.slack.pdf_evaluate_quotes.update_pdf_evaluate_quote_session",
            new=AsyncMock(),
        ) as mock_update,
        patch(
            "app.slack.listeners._publish_pdf_evaluate_convert",
            new=AsyncMock(),
        ) as mock_publish,
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pdf", "size": 1000}],
            target_langs_uuid=["lang-1", "lang-2"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="notes",
        )

    assert result == {"status": "quoted", "quote_id": "quote-1"}
    mock_publish.assert_not_awaited()
    mock_save.assert_awaited_once()
    assert mock_save.await_args.kwargs["pdf_page_count"] == 3
    assert mock_save.await_args.kwargs["ai_token_estimate"] == 4
    mock_update.assert_awaited_once_with("quote-1", message_ts="123.456")


@pytest.mark.asyncio
async def test_process_evaluation_submission_verify_api_error_posts_permission_message():
    from app.api.verify import VerifyAPIError

    ray_client = MagicMock()
    fake_slack = MagicMock()
    fake_slack.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.auth.connector.get_ray_client", new=AsyncMock(return_value=ray_client)
        ),
        patch(
            "app.auth.connector.get_bot_token_async", new=AsyncMock(return_value="xoxb")
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.slack.web.download_file", new=AsyncMock(return_value="/tmp/a.docx")),
        patch("app.ray.utils.validate_file", return_value=(True, True, "")),
        patch(
            "app.api.verify.submit_evaluation_job",
            new=AsyncMock(side_effect=VerifyAPIError("Permission denied")),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch("os.path.exists", return_value=False),
    ):
        result = await process_evaluation_submission(
            _ctx(),
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.docx"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    assert result == {"status": "permission_denied"}
    fake_slack.chat_postMessage.assert_awaited_once()
    assert "permission" in fake_slack.chat_postMessage.await_args.kwargs["text"].lower()
    assert (
        "administrator" in fake_slack.chat_postMessage.await_args.kwargs["text"].lower()
    )


# --------------------------------------------------------------------------- #
# slack_upload_mt_result
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_slack_upload_mt_result_happy_path(slack_user):
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch(
            "app.saq_jobs.tasks.update_slack_job", new=AsyncMock()
        ) as mock_update_job,
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch(
            "app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()
        ) as mock_delete,
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()
        ) as mock_upload,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result == {"status": "delivered", "task_uuid": success_data["task_uuid"]}
    mock_upload.assert_awaited_once()
    mock_delete.assert_awaited_once_with("file-1")
    # First call marks slack_uploading, second marks delivered.
    assert mock_update_job.await_count == 2


@pytest.mark.asyncio
async def test_slack_upload_mt_result_no_slack_user_returns_no_user_status():
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": str(uuid4()),
        "target_language": "fr",
        "channel_id": "C123",
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=None),
        ),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "no_slack_user"


@pytest.mark.asyncio
async def test_slack_upload_mt_result_re_raises_for_saq_retry(slack_user):
    """A failed upload must re-raise so SAQ can retry the job."""
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
    }

    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient",
            new=AsyncMock(side_effect=RuntimeError("file_update_failed")),
        ),
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        with pytest.raises(RuntimeError):
            await slack_upload_mt_result(_ctx(), success_data=success_data)


# --------------------------------------------------------------------------- #
# slack_upload_transcription
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_slack_upload_transcription_renames_temp_file_for_extension(slack_user):
    """The temp download is renamed to the expected filename so Slack keeps the extension."""
    with (
        patch(
            "app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=slack_user)
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/abc/raw"}),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()
        ) as mock_upload,
        patch("os.rename") as mock_rename,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="result.srt",
            task_uuid=str(uuid4()),
            pipeline_type="transcribe",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            thread_ts="123.0",
        )

    mock_rename.assert_called_once_with("/tmp/abc/raw", "/tmp/abc/result.srt")
    mock_upload.assert_awaited_once()
    assert mock_upload.await_args.kwargs["filename"] == "result.srt"
    assert mock_upload.await_args.kwargs["thread_ts"] == "123.0"


@pytest.mark.asyncio
async def test_slack_upload_transcription_posts_follow_up_message(slack_user):
    fake_client = MagicMock()
    fake_client.chat_postMessage = AsyncMock()

    with (
        patch(
            "app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=slack_user)
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/abc/result.srt"}),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_client),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("os.rename"),
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="result.srt",
            task_uuid=str(uuid4()),
            pipeline_type="transcribe",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
            thread_ts=None,
            follow_up_message="Edit and reupload.",
        )

    fake_client.chat_postMessage.assert_awaited_once()
    assert (
        fake_client.chat_postMessage.await_args.kwargs["text"] == "Edit and reupload."
    )


@pytest.mark.asyncio
async def test_slack_upload_transcription_no_slack_user_short_circuits():
    with patch("app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=None)):
        result = await slack_upload_transcription(
            _ctx(),
            file_id="f1",
            file_name="x.srt",
            task_uuid="t1",
            pipeline_type="transcribe",
            client_id="missing",
            channel_id="C1",
            thread_ts=None,
        )
    assert result["status"] == "no_slack_user"


# --------------------------------------------------------------------------- #
# slack_upload_verify_complete
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_slack_upload_verify_complete_happy_path(slack_user):
    with (
        patch(
            "app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=slack_user)
        ),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/x", "file_name": "qe.xlsx"}),
        ),
        patch(
            "app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()
        ) as mock_upload,
        patch("app.saq_jobs.tasks._safe_unlink"),
    ):
        result = await slack_upload_verify_complete(
            _ctx(),
            grid_file_id="grid-1",
            client_id=slack_user.ray_client_id,
            channel_id="C123",
        )

    assert result == {"status": "delivered"}
    mock_upload.assert_awaited_once()
    assert mock_upload.await_args.kwargs["filename"] == "qe.xlsx"


# --------------------------------------------------------------------------- #
# persist_log_notification
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_log_notification_delegates_to_log_notification():
    log = AsyncMock()
    with patch("app.ray.events.logging.log_notification", new=log):
        result = await persist_log_notification(
            _ctx(),
            event="ray:client:signup",
            event_data={"x": 1},
            user_id="U1",
            channel_id="C1",
            ray_client_id="R1",
            message="ClientSignupEventMessage",
        )

    assert result["status"] == "logged"
    log.assert_awaited_once()
    assert log.await_args.kwargs["event"] == "ray:client:signup"


# --------------------------------------------------------------------------- #
# charge_inline_mt_usage (RAY-80258)
# --------------------------------------------------------------------------- #


def _billing() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "text_length": 100,
        "target_languages": ["fr"],
        "usage_type": "channel_translation",
        "idempotency_key": "key-abc",
        "group_uuid": "billing-group",
    }


def _usage_log() -> dict[str, Any]:
    return {
        "user_uuid": "client-1",
        "group_uuid": "billing-group",
        "organization_uuid": "org-1",
        "input_text": "hello",
        "source_lang": "en",
        "translations": {"fr": "bonjour"},
        "usage_type": "channel_translation",
    }


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_happy_path():
    """Charges the gateway then logs usage against the returned transaction."""
    billing = _billing()
    usage_log = _usage_log()
    charge = AsyncMock(return_value="txn-123")
    log_usage = AsyncMock()
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=log_usage),
    ):
        result = await charge_inline_mt_usage(
            _ctx(), billing=billing, usage_log=usage_log
        )

    assert result == {"status": "charged", "transaction_uuid": "txn-123"}
    charge.assert_awaited_once()
    assert charge.await_args.kwargs["idempotency_key"] == "key-abc"
    assert charge.await_args.kwargs["client_id"] == billing["client_id"]
    # Google API usage row carries the gateway transaction uuid.
    log_usage.assert_awaited_once()
    assert log_usage.await_args.kwargs["transaction_uuid"] == "txn-123"
    assert log_usage.await_args.kwargs["group_uuid"] == "billing-group"


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_retries_on_connect_timeout():
    """A transient ConnectTimeout is retried inside the task before logging."""
    charge = AsyncMock(side_effect=[httpx.ConnectTimeout("boom"), "txn-after-retry"])
    log_usage = AsyncMock()
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=log_usage),
        # Skip the backoff sleep so the test stays fast.
        patch("app.api.http_client.asyncio.sleep", new=AsyncMock()),
    ):
        result = await charge_inline_mt_usage(
            _ctx(), billing=_billing(), usage_log=_usage_log()
        )

    assert result["transaction_uuid"] == "txn-after-retry"
    assert charge.await_count == 2
    log_usage.assert_awaited_once()


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_notifies_on_final_attempt():
    """On the final, non-retryable attempt the billing failure raises a BugLog alert."""
    charge = AsyncMock(side_effect=RuntimeError("gateway 500"))
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=AsyncMock()),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_inline_mt_usage(
                _ctx(retryable=False),
                billing=_billing(),
                usage_log=_usage_log(),
            )

    mock_notify.assert_called_once()
    # The billing-specific alert carries the idempotency key for replay.
    assert mock_notify.call_args.kwargs["extra"]["idempotency_key"] == "key-abc"


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_reraises_without_alert_when_retryable():
    """A retryable failure re-raises so SAQ retries, but does not alert yet."""
    charge = AsyncMock(side_effect=RuntimeError("transient"))
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=charge),
        patch("app.mt.logs.log_google_api_usage", new=AsyncMock()),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_inline_mt_usage(
                _ctx(retryable=True),
                billing=_billing(),
                usage_log=_usage_log(),
            )

    mock_notify.assert_not_called()


# --------------------------------------------------------------------------- #
# charge_document_mt + deferred enqueue (RAY-80417)
# --------------------------------------------------------------------------- #


def _mt_charge() -> dict[str, Any]:
    return {
        "text_length": 100,
        "word_count": 20,
        "engine": "google",
        "target_languages": ["fr"],
        "source_language": "en",
        "app_name": "slack",
        "file_name": "out.docx",
        "idempotency_key": "slack:task-1:document_translation:fr:characters",
        "submission_group_uuid": "task-1",
        "pdf_conversion_page_count": 3,
        "pdf_idempotency_key": "slack:task-1:pdf_conversion_fee:pages",
    }


@pytest.mark.asyncio
async def test_slack_upload_mt_result_enqueues_document_mt_charge(slack_user):
    """After delivery, a Slack mt_charge payload is enqueued for billing."""
    charge = _mt_charge()
    slack_user.user_id = "U123"
    slack_user.ray_user_group_id = "billing-group-1"
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
        "mt_charge": charge,
    }
    fake_slack = MagicMock()
    fake_slack.users_info = AsyncMock(
        return_value={
            "user": {
                "profile": {
                    "email": "poster@example.com",
                    "real_name": "Poster Name",
                }
            }
        }
    )
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("slack_sdk.web.async_client.AsyncWebClient", return_value=fake_slack),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    mock_enqueue.assert_awaited_once()
    kwargs = mock_enqueue.await_args.kwargs
    assert kwargs["client_id"] == slack_user.ray_client_id
    assert kwargs["idempotency_key"] == charge["idempotency_key"]
    assert kwargs["charge"]["group_uuid"] == "billing-group-1"
    assert kwargs["charge"]["email"] == "poster@example.com"
    assert kwargs["charge"]["client_name"] == "Poster Name"
    for key, value in charge.items():
        assert kwargs["charge"][key] == value


@pytest.mark.asyncio
async def test_slack_upload_mt_result_no_charge_when_absent(slack_user):
    """Teams (or no-conversion) deliveries carry no mt_charge, so nothing is billed."""
    success_data = {
        "task_uuid": str(uuid4()),
        "file_id": "file-1",
        "tokens": 0,
        "client_id": slack_user.ray_client_id,
        "target_language": "fr",
        "channel_id": "C123",
    }
    with (
        patch(
            "app.saq_jobs.tasks.resolve_slack_delivery_user",
            new=AsyncMock(return_value=slack_user),
        ),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch("app.saq_jobs.tasks.delete_from_file_server", new=AsyncMock()),
        patch(
            "app.slack.listener_actions.get_language_name",
            new=AsyncMock(return_value="French"),
        ),
        patch("app.slack.web.upload_file_to_slack_memory_efficient", new=AsyncMock()),
        patch("app.saq_jobs.tasks._safe_unlink"),
        patch(
            "app.saq_jobs.dispatch.enqueue_document_mt_charge", new=AsyncMock()
        ) as mock_enqueue,
    ):
        result = await slack_upload_mt_result(_ctx(), success_data=success_data)

    assert result["status"] == "delivered"
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_charge_document_mt_happy_path():
    """Relays the prepared charge to the gateway and surfaces both txn uuids."""
    charge = _mt_charge()
    relay = AsyncMock(
        return_value={"transaction_uuid": "txn-1", "pdf_transaction_uuid": "txn-pdf"}
    )
    with patch("app.auth.connector.log_document_mt_by_client_id", new=relay):
        result = await charge_document_mt(
            _ctx(), client_id="client-1", charge=charge, task_uuid="task-1"
        )

    assert result == {
        "status": "charged",
        "transaction_uuid": "txn-1",
        "pdf_transaction_uuid": "txn-pdf",
    }
    relay.assert_awaited_once_with("client-1", charge)


@pytest.mark.asyncio
async def test_charge_document_mt_retries_on_connect_timeout():
    """A transient ConnectTimeout is retried inside the task."""
    relay = AsyncMock(
        side_effect=[httpx.ConnectTimeout("boom"), {"transaction_uuid": "txn-2"}]
    )
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.api.http_client.asyncio.sleep", new=AsyncMock()),
    ):
        result = await charge_document_mt(
            _ctx(), client_id="c", charge={"idempotency_key": "k"}, task_uuid="t"
        )

    assert result["transaction_uuid"] == "txn-2"
    assert relay.await_count == 2


@pytest.mark.asyncio
async def test_charge_document_mt_notifies_on_final_attempt():
    """On the final, non-retryable attempt the failure raises a BugLog alert."""
    relay = AsyncMock(side_effect=RuntimeError("gateway 500"))
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_document_mt(
                _ctx(retryable=False),
                client_id="c",
                charge={"idempotency_key": "key-doc"},
                task_uuid="t",
            )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["extra"]["idempotency_key"] == "key-doc"


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError for a given gateway status code."""
    request = httpx.Request("POST", "https://gateway/mt/transaction")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("rejected", request=request, response=response)


@pytest.mark.asyncio
async def test_charge_document_mt_alerts_immediately_on_client_error():
    """A 4xx gateway rejection alerts on the first attempt (retry cannot fix it)."""
    relay = AsyncMock(side_effect=_http_status_error(422))
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await charge_document_mt(
                _ctx(attempts=1, retryable=True),
                client_id="c",
                charge={"idempotency_key": "key-doc"},
                task_uuid="t",
            )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["extra"]["status_code"] == 422


@pytest.mark.asyncio
async def test_charge_document_mt_silent_on_retryable_transient_error():
    """A non-4xx error on a retryable, non-final attempt does not alert (no spam)."""
    relay = AsyncMock(side_effect=RuntimeError("gateway 500"))
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(RuntimeError):
            await charge_document_mt(
                _ctx(attempts=1, retryable=True),
                client_id="c",
                charge={"idempotency_key": "key-doc"},
                task_uuid="t",
            )

    mock_notify.assert_not_called()


@pytest.mark.asyncio
async def test_charge_document_mt_warns_on_first_attempt_replay(caplog):
    """A first-attempt replay (no new debit) is logged rather than reported clean."""
    import logging

    relay = AsyncMock(return_value={"transaction_uuid": "txn-1", "replayed": True})
    with (
        patch("app.auth.connector.log_document_mt_by_client_id", new=relay),
        caplog.at_level(logging.WARNING),
    ):
        result = await charge_document_mt(
            _ctx(attempts=1, retryable=True),
            client_id="c",
            charge={"idempotency_key": "key-doc"},
            task_uuid="t",
        )

    assert result["status"] == "charged"
    assert any("replayed on first attempt" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_charge_inline_mt_usage_alerts_immediately_on_client_error():
    """Inline billing also alerts immediately on a 4xx gateway rejection."""
    relay = AsyncMock(side_effect=_http_status_error(403))
    with (
        patch("app.auth.connector.log_inline_mt_usage_by_client_id", new=relay),
        patch("app.saq_jobs.tasks.notify_exception") as mock_notify,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await charge_inline_mt_usage(
                _ctx(attempts=1, retryable=True),
                billing={"idempotency_key": "key-inline", "client_id": "c"},
                usage_log={},
            )

    mock_notify.assert_called_once()
    assert mock_notify.call_args.kwargs["extra"]["status_code"] == 403

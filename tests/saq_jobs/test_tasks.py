"""Unit tests for the durable SAQ task functions (RAY-79638).

Tasks are tested without a running SAQ worker by invoking them directly with
a stub context. External dependencies (Slack SDK, file server, slack_user
lookup, slack_job updates) are mocked so the tests run hermetically.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.saq_jobs.tasks import (
    persist_log_notification,
    persist_mt_ts_edit,
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
            "app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=slack_user)
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
            "app.routers.ray._get_language_name", new=AsyncMock(return_value="French")
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
        patch("app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=None)),
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
            "app.saq_jobs.tasks.get_slack_user", new=AsyncMock(return_value=slack_user)
        ),
        patch("app.saq_jobs.tasks.update_slack_job", new=AsyncMock()),
        patch(
            "app.saq_jobs.tasks.download_from_file_server_async",
            new=AsyncMock(return_value={"file": "/tmp/foo", "file_name": "out.docx"}),
        ),
        patch(
            "app.routers.ray._get_language_name", new=AsyncMock(return_value="French")
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
# persist_log_notification / persist_mt_ts_edit
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


@pytest.mark.asyncio
async def test_persist_mt_ts_edit_delegates_to_set_mt_ts_edit():
    set_ts = AsyncMock()
    with patch("app.slack.web.set_mt_ts_edit", new=set_ts):
        result = await persist_mt_ts_edit(_ctx(), send_ts="100.0", reply_ts="200.0")

    assert result["status"] == "cached"
    set_ts.assert_awaited_once_with(send_ts="100.0", reply_ts="200.0")

"""Tests for the typed SAQ dispatch helpers in ``app.saq_jobs.dispatch``.

These verify that each public ``enqueue_*`` helper builds the correct
idempotency key and forwards the configured retry / timeout settings, so
any future refactor accidentally dropping a kwarg fails loudly in CI.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.ray.events.models import MtSuccessResponseSchema
from app.saq_jobs.dispatch import (
    _mt_success_idempotency_key,
    enqueue_log_notification,
    enqueue_mt_success_upload,
    enqueue_mt_ts_edit,
    enqueue_transcription_upload,
    enqueue_verify_complete_upload,
)


def _success_data() -> MtSuccessResponseSchema:
    return MtSuccessResponseSchema.model_validate(
        {
            "task_uuid": str(uuid4()),
            "file_id": "file-abc",
            "tokens": 0,
            "client_id": str(uuid4()),
            "target_language": "fr",
            "channel_id": "C999",
            "submission_id": 1,
        }
    )


def test_mt_success_idempotency_key_includes_target_language():
    data = _success_data()
    key = _mt_success_idempotency_key(data)
    assert "slack_upload_mt_result:" in key
    assert data.task_uuid in key
    assert data.file_id in key
    assert data.channel_id in key
    assert "fr" in key


def test_mt_success_idempotency_key_falls_back_when_task_uuid_missing():
    data = MtSuccessResponseSchema.model_validate(
        {
            "task_uuid": None,
            "file_id": "file-abc",
            "tokens": 0,
            "client_id": str(uuid4()),
            "target_language": "fr",
            "channel_id": "C999",
        }
    )
    key = _mt_success_idempotency_key(data)
    assert "no-task" in key


@pytest.mark.asyncio
async def test_enqueue_mt_success_forwards_payload_and_settings():
    data = _success_data()
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_upload_retries = 7
        mock_cfg.saq_file_upload_timeout_seconds = 444
        await enqueue_mt_success_upload(data)

    mock_enq.assert_awaited_once()
    call_args = mock_enq.await_args
    assert call_args.args == ("slack_upload_mt_result",)
    assert call_args.kwargs["retries"] == 7
    assert call_args.kwargs["timeout"] == 444
    assert call_args.kwargs["retry_backoff"] is True
    assert call_args.kwargs["success_data"]["file_id"] == data.file_id
    assert call_args.kwargs["key"] == _mt_success_idempotency_key(data)


@pytest.mark.asyncio
async def test_enqueue_transcription_upload_forwards_payload():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 300
        await enqueue_transcription_upload(
            file_id="f1",
            file_name="x.srt",
            task_uuid="t1",
            pipeline_type="transcribe",
            client_id="rc1",
            channel_id="C1",
            thread_ts="100.0",
            follow_up_message="hello",
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("slack_upload_transcription",)
    kwargs = call_args.kwargs
    assert kwargs["file_id"] == "f1"
    assert kwargs["file_name"] == "x.srt"
    assert kwargs["task_uuid"] == "t1"
    assert kwargs["client_id"] == "rc1"
    assert kwargs["channel_id"] == "C1"
    assert kwargs["thread_ts"] == "100.0"
    assert kwargs["follow_up_message"] == "hello"
    assert "t1" in kwargs["key"] and "f1" in kwargs["key"]


@pytest.mark.asyncio
async def test_enqueue_verify_complete_upload_forwards_payload():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 300
        await enqueue_verify_complete_upload(
            grid_file_id="g1", client_id="rc1", channel_id="C1"
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("slack_upload_verify_complete",)
    assert call_args.kwargs["grid_file_id"] == "g1"
    assert call_args.kwargs["client_id"] == "rc1"
    assert call_args.kwargs["channel_id"] == "C1"
    assert "g1" in call_args.kwargs["key"]


@pytest.mark.asyncio
async def test_enqueue_log_notification_forwards_payload_without_key():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_logging_retries = 3
        mock_cfg.saq_logging_timeout_seconds = 30
        await enqueue_log_notification(
            event="ray:job:status_changed",
            event_data={"foo": "bar"},
            user_id="U1",
            channel_id="C1",
            ray_client_id="RC1",
            message="StatusChangedMessage",
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("persist_log_notification",)
    assert "key" not in call_args.kwargs
    assert call_args.kwargs["retries"] == 3
    assert call_args.kwargs["timeout"] == 30
    assert call_args.kwargs["event"] == "ray:job:status_changed"
    assert call_args.kwargs["event_data"] == {"foo": "bar"}
    assert call_args.kwargs["message"] == "StatusChangedMessage"


@pytest.mark.asyncio
async def test_enqueue_mt_ts_edit_forwards_payload_with_send_ts_key():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_logging_retries = 3
        mock_cfg.saq_logging_timeout_seconds = 30
        await enqueue_mt_ts_edit(send_ts="1700000000.0001", reply_ts="1700000001.0001")

    call_args = mock_enq.await_args
    assert call_args.args == ("persist_mt_ts_edit",)
    assert call_args.kwargs["key"] == "persist_mt_ts_edit:1700000000.0001"
    assert call_args.kwargs["send_ts"] == "1700000000.0001"
    assert call_args.kwargs["reply_ts"] == "1700000001.0001"

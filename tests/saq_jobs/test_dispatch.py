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
    _submission_queue_name,
    enqueue_document_mt_quote_preflight,
    enqueue_document_mt_submission,
    enqueue_evaluation_submission,
    enqueue_inline_mt_billing,
    enqueue_log_notification,
    enqueue_mt_success_upload,
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


def test_submission_queue_name_routes_known_small_files_to_small_queue():
    with patch("app.saq_jobs.dispatch.app_config") as mock_cfg:
        mock_cfg.saq_large_file_submission_threshold_mb = 10
        mock_cfg.saq_file_submission_queue_name = "large-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-q"

        queue_name = _submission_queue_name(
            [{"id": "F1", "title": "small.docx", "size": 1024}]
        )

    assert queue_name == "small-q"


def test_submission_queue_name_routes_large_or_unknown_files_to_limited_queue():
    with patch("app.saq_jobs.dispatch.app_config") as mock_cfg:
        mock_cfg.saq_large_file_submission_threshold_mb = 10
        mock_cfg.saq_file_submission_queue_name = "large-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-q"

        large_queue = _submission_queue_name(
            [{"id": "F1", "title": "large.docx", "size": 10 * 1024 * 1024}]
        )
        unknown_queue = _submission_queue_name(
            [{"id": "F1", "title": "unknown.docx", "size": None}]
        )

    assert large_queue == "large-q"
    assert unknown_queue == "large-q"


@pytest.mark.asyncio
async def test_enqueue_mt_success_forwards_payload_and_settings():
    data = _success_data()
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_delivery_queue_name = "delivery-q"
        mock_cfg.saq_file_upload_retries = 7
        mock_cfg.saq_file_upload_timeout_seconds = 444
        await enqueue_mt_success_upload(data)

    mock_enq.assert_awaited_once()
    call_args = mock_enq.await_args
    assert call_args.args == ("slack_upload_mt_result",)
    assert call_args.kwargs["queue_name"] == "delivery-q"
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
        mock_cfg.saq_file_delivery_queue_name = "delivery-q"
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
    assert kwargs["queue_name"] == "delivery-q"
    assert kwargs["file_id"] == "f1"
    assert kwargs["file_name"] == "x.srt"
    assert kwargs["task_uuid"] == "t1"
    assert kwargs["client_id"] == "rc1"
    assert kwargs["channel_id"] == "C1"
    assert kwargs["thread_ts"] == "100.0"
    assert kwargs["follow_up_message"] == "hello"
    assert "t1" in kwargs["key"] and "f1" in kwargs["key"]


@pytest.mark.asyncio
async def test_enqueue_document_mt_submission_forwards_payload():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_submission_queue_name = "submissions-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-submissions-q"
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 900
        mock_cfg.saq_small_file_upload_timeout_seconds = 300
        await enqueue_document_mt_submission(
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx"}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("process_document_mt_submission",)
    kwargs = call_args.kwargs
    assert kwargs["queue_name"] == "submissions-q"
    assert kwargs["retries"] == 5
    assert kwargs["timeout"] == 900
    assert kwargs["retry_backoff"] is True
    assert kwargs["user_id"] == "U1"
    assert kwargs["files"] == [{"id": "F1", "title": "a.pptx"}]
    assert kwargs["target_languages"] == ["zh-CN"]
    assert kwargs["key"].startswith("process_document_mt_submission:")


@pytest.mark.asyncio
async def test_enqueue_document_mt_submission_uses_small_file_timeout():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_large_file_submission_threshold_mb = 10
        mock_cfg.saq_file_submission_queue_name = "submissions-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-submissions-q"
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 900
        mock_cfg.saq_small_file_upload_timeout_seconds = 300
        await enqueue_document_mt_submission(
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx", "size": 1024}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    kwargs = mock_enq.await_args.kwargs
    assert kwargs["queue_name"] == "small-submissions-q"
    assert kwargs["timeout"] == 300


@pytest.mark.asyncio
async def test_enqueue_document_mt_quote_preflight_forwards_payload():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_submission_queue_name = "submissions-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-submissions-q"
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 900
        mock_cfg.saq_small_file_upload_timeout_seconds = 300
        await enqueue_document_mt_quote_preflight(
            quote_id="quote-1",
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pptx"}],
            source_language="en",
            target_languages=["zh-CN"],
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("process_document_mt_quote_preflight",)
    kwargs = call_args.kwargs
    assert kwargs["queue_name"] == "submissions-q"
    assert kwargs["quote_id"] == "quote-1"
    assert kwargs["files"] == [{"id": "F1", "title": "a.pptx"}]
    assert kwargs["target_languages"] == ["zh-CN"]
    assert kwargs["key"].startswith("process_document_mt_quote_preflight:")


@pytest.mark.asyncio
async def test_enqueue_evaluation_submission_forwards_payload():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_submission_queue_name = "submissions-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-submissions-q"
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 900
        mock_cfg.saq_small_file_upload_timeout_seconds = 300
        await enqueue_evaluation_submission(
            user_id="U1",
            team_id="T1",
            enterprise_id="E1",
            channel_id="C1",
            files=[{"id": "F1", "title": "a.pdf"}],
            target_langs_uuid=["lang-1"],
            reference="ref",
            source_lang_uuid="src",
            workflow_uuid=None,
            job_notes="",
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("process_evaluation_submission",)
    kwargs = call_args.kwargs
    assert kwargs["queue_name"] == "submissions-q"
    assert kwargs["enterprise_id"] == "E1"
    assert kwargs["target_langs_uuid"] == ["lang-1"]
    assert kwargs["reference"] == "ref"
    assert kwargs["key"].startswith("process_evaluation_submission:")
    assert kwargs["quote_id"]


@pytest.mark.asyncio
async def test_enqueue_evaluation_submission_derives_stable_quote_id():
    enqueue_kwargs = {
        "user_id": "U1",
        "team_id": "T1",
        "enterprise_id": None,
        "channel_id": "C1",
        "files": [{"id": "F1", "title": "a.pdf"}],
        "target_langs_uuid": ["lang-1"],
        "reference": "ref",
        "source_lang_uuid": "src",
        "workflow_uuid": None,
        "job_notes": "",
    }
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_submission_queue_name = "submissions-q"
        mock_cfg.saq_small_file_submission_queue_name = "small-submissions-q"
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 900
        mock_cfg.saq_small_file_upload_timeout_seconds = 300

        await enqueue_evaluation_submission(**enqueue_kwargs)
        first_quote_id = mock_enq.await_args.kwargs["quote_id"]
        await enqueue_evaluation_submission(**enqueue_kwargs)
        second_quote_id = mock_enq.await_args.kwargs["quote_id"]

    assert first_quote_id == second_quote_id


@pytest.mark.asyncio
async def test_enqueue_verify_complete_upload_forwards_payload():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_file_delivery_queue_name = "delivery-q"
        mock_cfg.saq_file_upload_retries = 5
        mock_cfg.saq_file_upload_timeout_seconds = 300
        await enqueue_verify_complete_upload(
            grid_file_id="g1",
            client_id="rc1",
            channel_id="C1",
            team_id="T1",
            slack_user_id="U1",
            enterprise_id="E1",
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("slack_upload_verify_complete",)
    assert call_args.kwargs["queue_name"] == "delivery-q"
    assert call_args.kwargs["grid_file_id"] == "g1"
    assert call_args.kwargs["client_id"] == "rc1"
    assert call_args.kwargs["channel_id"] == "C1"
    assert call_args.kwargs["team_id"] == "T1"
    assert call_args.kwargs["slack_user_id"] == "U1"
    assert call_args.kwargs["enterprise_id"] == "E1"
    assert "g1" in call_args.kwargs["key"]


@pytest.mark.asyncio
async def test_enqueue_log_notification_forwards_payload_without_key():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_background_queue_name = "background-q"
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
    assert call_args.kwargs["queue_name"] == "background-q"
    assert "key" not in call_args.kwargs
    assert call_args.kwargs["retries"] == 3
    assert call_args.kwargs["timeout"] == 30
    assert call_args.kwargs["event"] == "ray:job:status_changed"
    assert call_args.kwargs["event_data"] == {"foo": "bar"}
    assert call_args.kwargs["message"] == "StatusChangedMessage"


@pytest.mark.asyncio
async def test_enqueue_inline_mt_billing_forwards_payload_with_idempotency_key():
    with (
        patch("app.saq_jobs.dispatch.enqueue", new=AsyncMock()) as mock_enq,
        patch("app.saq_jobs.dispatch.app_config") as mock_cfg,
    ):
        mock_cfg.saq_background_queue_name = "background-q"
        mock_cfg.saq_logging_retries = 3
        mock_cfg.saq_logging_timeout_seconds = 30
        await enqueue_inline_mt_billing(
            idempotency_key="key-abc",
            billing={"client_id": "rc1", "idempotency_key": "key-abc"},
            usage_log={"user_uuid": "rc1", "group_uuid": "g1"},
        )

    call_args = mock_enq.await_args
    assert call_args.args == ("charge_inline_mt_usage",)
    kwargs = call_args.kwargs
    assert kwargs["queue_name"] == "background-q"
    assert kwargs["key"] == "charge_inline_mt_usage:key-abc"
    assert kwargs["retries"] == 3
    assert kwargs["timeout"] == 30
    assert kwargs["retry_backoff"] is True
    assert kwargs["billing"] == {"client_id": "rc1", "idempotency_key": "key-abc"}
    assert kwargs["usage_log"] == {"user_uuid": "rc1", "group_uuid": "g1"}

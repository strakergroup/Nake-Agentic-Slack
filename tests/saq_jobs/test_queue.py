"""Unit tests for the SAQ queue helper (RAY-79638).

These tests use a fake ``Queue`` so we don't talk to Redis; they verify the
contract of the public ``enqueue`` helper:

* the function name and kwargs are forwarded verbatim;
* idempotency keys, retries, timeouts and backoff settings are passed through;
* a duplicate-key (``None`` return from SAQ) is handled silently;
* enqueue failures bubble up so callers see Redis outages immediately
  ("fail loud" — see RAY-79638 acceptance criteria).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.saq_jobs import enqueue


@pytest.fixture
def mock_queue():
    """Patch ``app.saq_jobs.queue.get_queue`` to return a fake queue."""
    fake_job = MagicMock(key="job-key", attempts=0)
    fake_queue = MagicMock()
    fake_queue.enqueue = AsyncMock(return_value=fake_job)
    with patch("app.saq_jobs.queue.get_queue", return_value=fake_queue):
        yield fake_queue


@pytest.mark.asyncio
async def test_enqueue_passes_function_name_and_kwargs(mock_queue):
    await enqueue("slack_upload_mt_result", success_data={"task_uuid": "t1"})

    mock_queue.enqueue.assert_awaited_once_with(
        "slack_upload_mt_result",
        success_data={"task_uuid": "t1"},
    )


@pytest.mark.asyncio
async def test_enqueue_includes_optional_job_settings(mock_queue):
    await enqueue(
        "slack_upload_mt_result",
        queue_name="delivery-q",
        key="idem-1",
        retries=5,
        timeout=300,
        retry_delay=2.0,
        retry_backoff=True,
        success_data={"x": 1},
    )

    mock_queue.enqueue.assert_awaited_once_with(
        "slack_upload_mt_result",
        key="idem-1",
        retries=5,
        timeout=300,
        retry_delay=2.0,
        retry_backoff=True,
        success_data={"x": 1},
    )


@pytest.mark.asyncio
async def test_enqueue_omits_unset_job_settings(mock_queue):
    await enqueue("persist_log_notification", event="x")

    args, kwargs = mock_queue.enqueue.await_args
    assert args == ("persist_log_notification",)
    assert "key" not in kwargs
    assert "retries" not in kwargs
    assert "timeout" not in kwargs
    assert "retry_delay" not in kwargs
    assert "retry_backoff" not in kwargs
    assert kwargs == {"event": "x"}


@pytest.mark.asyncio
async def test_enqueue_handles_duplicate_key_silently(mock_queue):
    """SAQ returns ``None`` when a job with the same key is already in flight."""
    mock_queue.enqueue = AsyncMock(return_value=None)

    # Should not raise — duplicate is the desired idempotency outcome.
    await enqueue("slack_upload_mt_result", key="dupe", success_data={})


@pytest.mark.asyncio
async def test_enqueue_propagates_redis_errors(mock_queue):
    """Fail-loud: Redis enqueue errors must propagate (no silent fallback)."""
    boom = ConnectionError("redis is down")
    mock_queue.enqueue = AsyncMock(side_effect=boom)

    with pytest.raises(ConnectionError):
        await enqueue("slack_upload_mt_result", success_data={})

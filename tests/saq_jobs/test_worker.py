import asyncio

import pytest
import pytest_asyncio

from app.saq_jobs import worker


class FakeWorker:
    def __init__(self):
        self._stop_event = asyncio.Event()

    async def start(self):
        await self._stop_event.wait()

    async def stop(self):
        self._stop_event.set()


@pytest_asyncio.fixture(autouse=True)
async def clean_worker_state():
    await worker.stop_worker()
    yield
    await worker.stop_worker()


@pytest.mark.asyncio
async def test_start_worker_starts_every_queue_worker(monkeypatch):
    monkeypatch.setattr(worker, "_build_worker", lambda **_: FakeWorker())

    await worker.start_worker()

    status = worker.worker_status()
    assert status["expected_count"] == 5
    assert status["running_count"] == 5
    assert status["all_running"] is True


@pytest.mark.asyncio
async def test_ensure_worker_running_restarts_dead_queue_worker(monkeypatch):
    monkeypatch.setattr(worker, "_build_worker", lambda **_: FakeWorker())
    await worker.start_worker()
    original_task = worker._worker_tasks["file-delivery"]

    original_task.cancel()
    try:
        await original_task
    except asyncio.CancelledError:
        pass

    await worker.ensure_worker_running()

    replacement_task = worker._worker_tasks["file-delivery"]
    assert replacement_task is not original_task
    assert not replacement_task.done()
    assert worker.worker_status()["all_running"] is True

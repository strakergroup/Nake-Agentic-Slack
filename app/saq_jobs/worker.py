"""SAQ worker lifecycle for the in-process worker (RAY-79638).

The worker runs inside the same uvicorn process as the FastAPI app and is
managed by the FastAPI lifespan (see ``app/main.py``). Per the RAY-79638
acceptance criteria, no other deployment changes are introduced — durability
is provided by the existing Redis instance.

If the operator prefers to run the worker out-of-process later (e.g. as a
separate pod), set ``SAQ_WORKER_ENABLED=false`` in the API deployment and
launch the worker via ``saq app.saq_jobs.worker.settings`` from a sidecar.
The settings dict below is the public entry point for that mode.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from saq.worker import Worker

from app.config import config
from app.saq_jobs.queue import get_queue, shutdown_queue
from app.saq_jobs.tasks import TASK_FUNCTIONS

logger = logging.getLogger(__name__)

_worker: Worker | None = None
_worker_task: asyncio.Task | None = None


def _build_worker() -> Worker:
    """Construct a ``Worker`` bound to the shared queue and registered tasks."""
    return Worker(
        queue=get_queue(),
        functions=list(TASK_FUNCTIONS),
        concurrency=config.saq_worker_concurrency,
    )


async def start_worker() -> None:
    """Start the SAQ worker as an asyncio background task.

    Safe to call multiple times: subsequent calls are no-ops while the
    worker is running. Raises if SAQ initialisation fails so the operator
    sees the failure on app startup rather than silently losing work.
    """
    global _worker, _worker_task
    if not config.saq_worker_enabled:
        logger.info("SAQ worker disabled via SAQ_WORKER_ENABLED=false; skipping start")
        return
    if _worker_task is not None and not _worker_task.done():
        logger.debug("SAQ worker already running; start_worker is a no-op")
        return

    _worker = _build_worker()
    logger.info(
        "Starting SAQ worker",
        extra={
            "queue_name": config.saq_queue_name,
            "concurrency": config.saq_worker_concurrency,
            "registered_tasks": [fn.__name__ for fn in TASK_FUNCTIONS],
        },
    )
    _worker_task = asyncio.create_task(_worker.start(), name="saq-worker")


async def stop_worker() -> None:
    """Stop the SAQ worker and disconnect the shared queue.

    Safe to call multiple times. Errors during shutdown are logged and
    swallowed so they don't block FastAPI lifespan teardown.
    """
    global _worker, _worker_task
    if _worker is not None:
        try:
            await _worker.stop()
        except Exception:
            logger.exception("Failed to stop SAQ worker cleanly")
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=10)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("SAQ worker did not stop within 10s; cancelling")
            _worker_task.cancel()
        except Exception:
            logger.exception("SAQ worker task ended with error")
    _worker = None
    _worker_task = None
    await shutdown_queue()


# --------------------------------------------------------------------------- #
# Out-of-process worker entry point (optional)
# --------------------------------------------------------------------------- #

#: ``saq app.saq_jobs.worker.settings`` discovers this dict and runs a worker
#: with the same task registry. Currently unused in deployment but kept here
#: so the migration path to a separate worker pod is a config change, not a
#: code change.
settings: dict[str, Any] = {
    "queue": None,  # Filled lazily below to avoid opening Redis at import time.
    "functions": list(TASK_FUNCTIONS),
    "concurrency": config.saq_worker_concurrency,
}


def _get_settings() -> dict[str, Any]:
    """Resolve the lazy ``queue`` field in ``settings`` for CLI use."""
    settings["queue"] = get_queue()
    return settings

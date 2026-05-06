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
from app.saq_jobs.tasks import (
    BACKGROUND_TASK_FUNCTIONS,
    FILE_DELIVERY_TASK_FUNCTIONS,
    FILE_SUBMISSION_TASK_FUNCTIONS,
)

logger = logging.getLogger(__name__)

_workers: list[Worker] = []
_worker_tasks: list[asyncio.Task] = []


def _build_worker(
    *,
    queue_name: str,
    functions: list[Any],
    concurrency: int,
) -> Worker:
    """Construct a ``Worker`` bound to a queue and registered task group."""
    return Worker(
        queue=get_queue(queue_name),
        functions=functions,
        concurrency=concurrency,
    )


def _worker_specs() -> list[dict[str, Any]]:
    """Queue/worker layout for isolating large file work from lightweight tasks."""
    return [
        {
            "label": "file-submissions",
            "queue_name": config.saq_file_submission_queue_name,
            "functions": list(FILE_SUBMISSION_TASK_FUNCTIONS),
            "concurrency": config.saq_file_submission_worker_concurrency,
        },
        {
            "label": "small-file-submissions",
            "queue_name": config.saq_small_file_submission_queue_name,
            "functions": list(FILE_SUBMISSION_TASK_FUNCTIONS),
            "concurrency": config.saq_small_file_submission_worker_concurrency,
        },
        {
            "label": "file-delivery",
            "queue_name": config.saq_file_delivery_queue_name,
            "functions": list(FILE_DELIVERY_TASK_FUNCTIONS),
            "concurrency": config.saq_file_delivery_worker_concurrency,
        },
        {
            "label": "background",
            "queue_name": config.saq_background_queue_name,
            "functions": list(BACKGROUND_TASK_FUNCTIONS),
            "concurrency": config.saq_background_worker_concurrency,
        },
    ]


async def start_worker() -> None:
    """Start the SAQ worker as an asyncio background task.

    Safe to call multiple times: subsequent calls are no-ops while the
    worker is running. Raises if SAQ initialisation fails so the operator
    sees the failure on app startup rather than silently losing work.
    """
    global _workers, _worker_tasks
    if not config.saq_worker_enabled:
        logger.info("SAQ worker disabled via SAQ_WORKER_ENABLED=false; skipping start")
        return
    if any(not task.done() for task in _worker_tasks):
        logger.debug("SAQ workers already running; start_worker is a no-op")
        return

    for spec in _worker_specs():
        worker = _build_worker(
            queue_name=spec["queue_name"],
            functions=spec["functions"],
            concurrency=spec["concurrency"],
        )
        _workers.append(worker)
        logger.info(
            "Starting SAQ worker",
            extra={
                "queue_name": spec["queue_name"],
                "concurrency": spec["concurrency"],
                "registered_tasks": [fn.__name__ for fn in spec["functions"]],
            },
        )
        _worker_tasks.append(
            asyncio.create_task(worker.start(), name=f"saq-worker-{spec['label']}")
        )


async def stop_worker() -> None:
    """Stop the SAQ worker and disconnect the shared queue.

    Safe to call multiple times. Errors during shutdown are logged and
    swallowed so they don't block FastAPI lifespan teardown.
    """
    global _workers, _worker_tasks
    for worker in _workers:
        try:
            await worker.stop()
        except Exception:
            logger.exception("Failed to stop SAQ worker cleanly")
    for worker_task in _worker_tasks:
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("SAQ worker did not stop within 10s; cancelling")
            worker_task.cancel()
        except Exception:
            logger.exception("SAQ worker task ended with error")
    _workers = []
    _worker_tasks = []
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
    "functions": list(FILE_DELIVERY_TASK_FUNCTIONS),
    "concurrency": config.saq_file_delivery_worker_concurrency,
}

file_submission_settings: dict[str, Any] = {
    "queue": None,
    "functions": list(FILE_SUBMISSION_TASK_FUNCTIONS),
    "concurrency": config.saq_file_submission_worker_concurrency,
}

small_file_submission_settings: dict[str, Any] = {
    "queue": None,
    "functions": list(FILE_SUBMISSION_TASK_FUNCTIONS),
    "concurrency": config.saq_small_file_submission_worker_concurrency,
}

background_settings: dict[str, Any] = {
    "queue": None,
    "functions": list(BACKGROUND_TASK_FUNCTIONS),
    "concurrency": config.saq_background_worker_concurrency,
}


def _get_settings() -> dict[str, Any]:
    """Resolve the lazy ``queue`` field in ``settings`` for CLI use."""
    settings["queue"] = get_queue(config.saq_file_delivery_queue_name)
    file_submission_settings["queue"] = get_queue(config.saq_file_submission_queue_name)
    small_file_submission_settings["queue"] = get_queue(
        config.saq_small_file_submission_queue_name
    )
    background_settings["queue"] = get_queue(config.saq_background_queue_name)
    return settings

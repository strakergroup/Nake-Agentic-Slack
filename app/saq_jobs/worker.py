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
import contextlib
import logging
import signal
from typing import Any

from saq.worker import Worker

from app.config import config
from app.saq_jobs.queue import get_queue, shutdown_queue
from app.saq_jobs.tasks import (
    BACKGROUND_TASK_FUNCTIONS,
    FILE_DELIVERY_TASK_FUNCTIONS,
    FILE_SUBMISSION_TASK_FUNCTIONS,
    TASK_FUNCTIONS,
)

logger = logging.getLogger(__name__)

_workers: dict[str, Worker] = {}
_worker_tasks: dict[str, asyncio.Task] = {}


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
    specs = [
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
    split_queue_names = {spec["queue_name"] for spec in specs}
    if config.saq_queue_name not in split_queue_names:
        specs.append(
            {
                "label": "legacy",
                "queue_name": config.saq_queue_name,
                "functions": list(TASK_FUNCTIONS),
                "concurrency": config.saq_worker_concurrency,
            }
        )
    return specs


def _task_is_running(task: asyncio.Task | None) -> bool:
    return task is not None and not task.done()


def _start_worker_for_spec(spec: dict[str, Any]) -> None:
    """Start the worker for one queue spec and record it by label."""
    worker = _build_worker(
        queue_name=spec["queue_name"],
        functions=spec["functions"],
        concurrency=spec["concurrency"],
    )
    _workers[spec["label"]] = worker
    logger.info(
        "Starting SAQ worker",
        extra={
            "queue_name": spec["queue_name"],
            "concurrency": spec["concurrency"],
            "registered_tasks": [fn.__name__ for fn in spec["functions"]],
        },
    )
    _worker_tasks[spec["label"]] = asyncio.create_task(
        worker.start(), name=f"saq-worker-{spec['label']}"
    )


def worker_status() -> dict[str, Any]:
    """Return in-process SAQ worker state for health checks and diagnostics."""
    specs = _worker_specs()
    workers = []
    for spec in specs:
        label = spec["label"]
        task = _worker_tasks.get(label)
        workers.append(
            {
                "label": label,
                "queue_name": spec["queue_name"],
                "running": _task_is_running(task),
                "done": bool(task.done()) if task is not None else False,
                "cancelled": bool(task.cancelled()) if task is not None else False,
            }
        )

    running_count = sum(1 for worker in workers if worker["running"])
    expected_count = len(specs)
    all_running = not config.saq_worker_enabled or (
        running_count == expected_count and expected_count > 0
    )
    return {
        "enabled": config.saq_worker_enabled,
        "expected_count": expected_count,
        "running_count": running_count,
        "all_running": all_running,
        "workers": workers,
    }


async def ensure_worker_running() -> None:
    """Start or restart any enabled in-process SAQ worker that is not alive.

    This is intentionally safe to call from event/enqueue paths. It cannot help
    when the whole FastAPI process is down, but it recovers from a worker task
    being cancelled or dying while the app process continues running.
    """
    if not config.saq_worker_enabled:
        return

    for spec in _worker_specs():
        label = spec["label"]
        task = _worker_tasks.get(label)
        if _task_is_running(task):
            continue

        if task is None:
            logger.warning(
                "SAQ worker is missing; starting",
                extra={"queue_name": spec["queue_name"], "worker_label": label},
            )
        elif task.cancelled():
            logger.warning(
                "SAQ worker was cancelled; restarting",
                extra={"queue_name": spec["queue_name"], "worker_label": label},
            )
        else:
            exc = task.exception()
            logger.error(
                "SAQ worker stopped unexpectedly; restarting",
                extra={
                    "queue_name": spec["queue_name"],
                    "worker_label": label,
                    "error_type": type(exc).__name__ if exc else None,
                },
            )
        _start_worker_for_spec(spec)


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
    if worker_status()["all_running"]:
        logger.debug("SAQ workers already running; start_worker is a no-op")
        return

    await ensure_worker_running()


async def stop_worker() -> None:
    """Stop the SAQ worker and disconnect the shared queue.

    Safe to call multiple times. Errors during shutdown are logged and
    swallowed so they don't block FastAPI lifespan teardown.
    """
    global _workers, _worker_tasks
    for worker in _workers.values():
        try:
            await worker.stop()
        except Exception:
            logger.exception("Failed to stop SAQ worker cleanly")
    for worker_task in _worker_tasks.values():
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("SAQ worker did not stop within 10s; cancelling")
            worker_task.cancel()
        except Exception:
            logger.exception("SAQ worker task ended with error")
    _workers = {}
    _worker_tasks = {}
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


async def run_workers_forever() -> None:
    """Run every configured SAQ queue worker in a dedicated process.

    This is the entry point for running the worker out-of-process (local dev
    or an optional sidecar), reusing the same queue/worker layout as the
    in-process worker. It is never invoked by the FastAPI app or the Docker
    deployment: production keeps the in-process worker managed by the app
    lifespan. Run locally with ``python -m app.saq_jobs.worker`` alongside a
    ``SAQ_WORKER_ENABLED=false`` uvicorn ``--reload`` web process so hot reload
    and background job processing do not share (and fight over) one process.
    """
    if not config.saq_worker_enabled:
        logger.info("SAQ worker disabled via SAQ_WORKER_ENABLED=false; nothing to run")
        return

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)

    await start_worker()
    logger.info("SAQ standalone worker started; waiting for jobs")
    try:
        await stop_event.wait()
    finally:
        logger.info("SAQ standalone worker stopping")
        await stop_worker()


def main() -> None:
    """CLI entry point: ``python -m app.saq_jobs.worker``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(run_workers_forever())


if __name__ == "__main__":
    main()

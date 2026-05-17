"""SAQ queue construction and lazy singleton (RAY-79638).

The queue uses a dedicated Redis client configured with ``decode_responses=False``
because SAQ persists job payloads as bytes (JSON-serialised then encoded). The
shared ``app.redis`` client uses ``decode_responses=True`` and cannot be
reused.
"""

from __future__ import annotations

import logging
from typing import Any

from saq import Queue
from saq.queue.redis import RedisQueue
from straker_utils.redis.asyncio import get_redis_auto

from app.config import config
from app.saq_jobs._task_names import TaskName

logger = logging.getLogger(__name__)


_queues: dict[str, Queue] = {}


def get_queue(queue_name: str | None = None) -> Queue:
    """Return the shared SAQ queue, constructing it on first access.

    The queue is created lazily so importing this module does not open a
    Redis connection (important for tests and CLI tooling).
    """
    name = queue_name or config.saq_queue_name
    if name not in _queues:
        # SAQ requires a bytes-mode Redis client; do not share the
        # decode_responses=True client used elsewhere in the app.
        redis = get_redis_auto(decode_responses=False)
        _queues[name] = RedisQueue(redis, name=name)
        logger.info(
            "SAQ queue initialised",
            extra={"queue_name": name},
        )
    return _queues[name]


async def enqueue(
    function: TaskName,
    *,
    queue_name: str | None = None,
    key: str | None = None,
    retries: int | None = None,
    timeout: int | None = None,
    retry_delay: float | None = None,
    retry_backoff: bool | None = None,
    **kwargs: Any,
) -> None:
    """Enqueue a job onto the SAQ queue.

    Fail-loud semantics: any Redis error propagates to the caller so we never
    silently lose durable work (per RAY-79638 acceptance criteria — surface
    Redis outages immediately rather than silently degrading).

    Args:
        function: Task function name as registered with the worker. Typed as
            :data:`app.saq_jobs.tasks.TaskName` (a ``Literal`` of every
            registered task) so pyright catches typos at the call site.
        key: Optional idempotency key. If a job with the same key is already
            queued or in-flight, this enqueue is a no-op.
        retries: Override default retry count for this job.
        timeout: Override default per-attempt timeout in seconds.
        retry_delay: Initial retry delay in seconds (SAQ jitters by default).
        retry_backoff: Whether to apply exponential backoff between retries.
        **kwargs: Keyword arguments forwarded to the task function. Must be
            JSON serialisable. NEVER pass secrets here (Slack bot tokens,
            access tokens, file contents). Pass identifiers and re-fetch
            secrets from the database inside the task.
    """
    from app.saq_jobs.worker import ensure_worker_running

    await ensure_worker_running()
    queue = get_queue(queue_name)
    job_kwargs: dict[str, Any] = {}
    if key is not None:
        job_kwargs["key"] = key
    if retries is not None:
        job_kwargs["retries"] = retries
    if timeout is not None:
        job_kwargs["timeout"] = timeout
    if retry_delay is not None:
        job_kwargs["retry_delay"] = retry_delay
    if retry_backoff is not None:
        job_kwargs["retry_backoff"] = retry_backoff

    job = await queue.enqueue(function, **job_kwargs, **kwargs)
    if job is None:
        # SAQ returns None when a job with the same key already exists.
        logger.info(
            "SAQ enqueue skipped (duplicate key)",
            extra={"function": function, "key": key, "queue_name": queue_name},
        )
    else:
        logger.info(
            "SAQ job enqueued",
            extra={
                "function": function,
                "queue_name": queue_name,
                "key": key,
                "job_key": job.key,
                "attempts": job.attempts,
            },
        )


async def shutdown_queue() -> None:
    """Close the SAQ queue and its Redis connection on app shutdown."""
    global _queues
    for queue in _queues.values():
        try:
            await queue.disconnect()
        except Exception:
            logger.exception("Failed to disconnect SAQ queue")
    _queues = {}

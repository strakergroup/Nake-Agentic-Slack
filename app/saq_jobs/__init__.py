"""Durable background jobs powered by SAQ (Simple Async Queue).

Introduced under RAY-79638 to replace ad-hoc ``asyncio.create_task`` background
work for tasks whose durability matters (file uploads to Slack, structured
logging persistence, MT message timestamp persistence). The worker runs
in-process via the FastAPI lifespan and stores all job state in the existing
shared Redis instance.

Module name is ``app.saq_jobs`` (not ``app.saq``) to avoid colliding with the
top-level ``saq`` package import.
"""

from .dispatch import (
    enqueue_document_mt_submission,
    enqueue_evaluation_submission,
    enqueue_log_notification,
    enqueue_mt_success_upload,
    enqueue_mt_ts_edit,
    enqueue_transcription_upload,
    enqueue_verify_complete_upload,
)
from .queue import enqueue, get_queue, shutdown_queue

__all__ = [
    "enqueue",
    "enqueue_log_notification",
    "enqueue_mt_success_upload",
    "enqueue_mt_ts_edit",
    "enqueue_document_mt_submission",
    "enqueue_evaluation_submission",
    "enqueue_transcription_upload",
    "enqueue_verify_complete_upload",
    "get_queue",
    "shutdown_queue",
]

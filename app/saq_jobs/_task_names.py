"""Leaf module declaring the typed task-name registry (RAY-79638).

Lives in its own module — with **no other imports** — so both
:mod:`app.saq_jobs.queue` (the producer-side enqueue wrapper) and
:mod:`app.saq_jobs.tasks` (the consumer-side task functions) can depend on
it without forming an import cycle. ``tasks.py`` imports a lot of app
modules (``app.ray.*``, ``app.slack.*``, ``app.slack_job``, ...); pulling
those into ``queue.py`` would create a circular import the moment any
producer imports ``app.saq_jobs.queue`` from a cold process.

The drift between :data:`TaskName` and the actual ``TASK_FUNCTIONS`` list
in ``tasks.py`` is asserted by ``tests/saq_jobs/test_task_registry.py``.
"""

from __future__ import annotations

from typing import Literal

#: Type alias enumerating every registered SAQ task name. ``enqueue()``
#: accepts this type instead of a bare ``str`` so pyright/IDE flags typos at
#: the call site (e.g. ``enqueue("slack_upload_mt_resul", ...)``). Add a
#: name here whenever you register a new task in
#: :data:`app.saq_jobs.tasks.TASK_FUNCTIONS`; the registry-sync test will
#: fail CI if you forget.
TaskName = Literal[
    "slack_upload_mt_result",
    "slack_upload_transcription",
    "slack_upload_verify_complete",
    "process_document_mt_submission",
    "process_evaluation_submission",
    "persist_log_notification",
    "persist_mt_ts_edit",
    "charge_inline_mt_usage",
]

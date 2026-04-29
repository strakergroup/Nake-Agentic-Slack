"""Drift check between ``TaskName`` literal and ``TASK_FUNCTIONS`` registry.

Pyright uses :data:`app.saq_jobs.tasks.TaskName` to flag typos in task names
at every ``enqueue(...)`` call site. That only protects callers if the literal
stays in sync with the actual task functions registered with the worker. This
test fails CI when:

* A new task function is added to ``TASK_FUNCTIONS`` but missing from
  ``TaskName`` (would be silently rejected by pyright at producer call sites).
* A name is removed from ``TASK_FUNCTIONS`` but left in ``TaskName`` (a
  producer could enqueue a job that no worker handles).
"""

from __future__ import annotations

from typing import get_args

from app.saq_jobs._task_names import TaskName
from app.saq_jobs.tasks import TASK_FUNCTIONS


def test_task_name_literal_matches_registry() -> None:
    declared = set(get_args(TaskName))
    actual = {fn.__name__ for fn in TASK_FUNCTIONS}
    assert declared == actual, (
        "TaskName Literal is out of sync with TASK_FUNCTIONS — "
        f"missing from TaskName: {sorted(actual - declared)}, "
        f"extra in TaskName: {sorted(declared - actual)}"
    )


def test_task_function_names_are_unique() -> None:
    names = [fn.__name__ for fn in TASK_FUNCTIONS]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, (
        f"TASK_FUNCTIONS contains duplicate task names: {sorted(duplicates)}; "
        "SAQ registers tasks by __name__ so duplicates would silently shadow."
    )

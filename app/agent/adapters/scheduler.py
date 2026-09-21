"""Scheduler hook for job follow-ups and the digest.

The app has no scheduler today (its SAQ workers register no cron jobs). This
module only DEFINES the jobs. They run when Wade's team passes them to a SAQ
worker's `cron_jobs` in app/saq_jobs/worker.py and sets AGENT_FOLLOWUPS_ENABLED.
Until then nothing here is imported by the running app.

The decisions (who gets what, caps, quiet hours, opt-in) live in
app/agent/core/followups.py and are tested there. What is left to write here,
with access to the real data, is `_jobs_for` and `_recipients`.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

FOLLOWUPS_CRON = "0 * * * *"  # hourly
DIGEST_CRON = "0 9 * * *"  # daily, 09:00 UTC; per-person local time is a follow-up


async def run_followups(ctx: dict[str, Any]) -> None:
    from app.config import config

    if not (config.agent_enabled and config.agent_followups_enabled):
        return
    logger.info("agent follow-ups: scheduler hook reached; data access not wired yet")


async def run_digest(ctx: dict[str, Any]) -> None:
    from app.config import config

    if not (config.agent_enabled and config.agent_followups_enabled):
        return
    logger.info("agent digest: scheduler hook reached; data access not wired yet")


def cron_jobs() -> list[Any]:
    """Returned to app/saq_jobs/worker.py when the team switches follow-ups on."""
    from saq import CronJob

    return [
        CronJob(run_followups, cron=FOLLOWUPS_CRON),
        CronJob(run_digest, cron=DIGEST_CRON),
    ]

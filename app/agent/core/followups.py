"""Job follow-ups and the opt-in digest. Pure decisions over state passed in.

Capped on purpose: one reminder per waiting quote, one note per delivery, nothing
at night, and a digest only for people who asked for it and only when there is
something in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from . import copy

QUOTE_REMINDER_AFTER = timedelta(hours=48)
QUIET_FROM, QUIET_UNTIL = 20, 8  # local hours


@dataclass(frozen=True)
class JobView:
    job_id: str
    name: str
    state: Literal["quote_waiting", "in_progress", "delivered"]
    since: datetime  # when it entered this state, timezone-aware


@dataclass(frozen=True)
class Followup:
    job_id: str
    kind: Literal["quote_waiting", "delivered"]
    text: str


def is_quiet_hours(now: datetime, utc_offset_seconds: int) -> bool:
    local = now.astimezone(timezone(timedelta(seconds=utc_offset_seconds)))
    return local.hour >= QUIET_FROM or local.hour < QUIET_UNTIL


def plan_followups(
    jobs: list[JobView],
    already_sent: set[str],
    now: datetime,
    utc_offset_seconds: int = 0,
) -> list[Followup]:
    """`already_sent` holds "<job_id>:<kind>" keys. The caller adds the returned ones after sending."""
    if is_quiet_hours(now, utc_offset_seconds):
        return []
    out: list[Followup] = []
    for job in jobs:
        key = f"{job.job_id}:{job.state}"
        if key in already_sent:
            continue
        if job.state == "quote_waiting" and now - job.since >= QUOTE_REMINDER_AFTER:
            day = job.since.strftime("%A")
            out.append(
                Followup(
                    job.job_id,
                    "quote_waiting",
                    copy.FOLLOWUP_QUOTE_WAITING.format(name=job.name, day=day),
                )
            )
        elif job.state == "delivered":
            out.append(
                Followup(
                    job.job_id,
                    "delivered",
                    copy.FOLLOWUP_DELIVERED.format(name=job.name),
                )
            )
    return out


def build_digest(
    jobs: list[JobView], opted_in: bool, team_words_this_week: int | None = None
) -> list[str] | None:
    """Lines for the digest, or None when nothing should be sent."""
    if not opted_in or not jobs:
        return None
    counts = {
        state: sum(1 for j in jobs if j.state == state)
        for state in ("delivered", "quote_waiting", "in_progress")
    }
    lines = []
    if counts["delivered"]:
        lines.append(copy.DIGEST_DELIVERED.format(count=counts["delivered"]))
    if counts["quote_waiting"]:
        lines.append(copy.DIGEST_WAITING.format(count=counts["quote_waiting"]))
    if counts["in_progress"]:
        lines.append(copy.DIGEST_IN_PROGRESS.format(count=counts["in_progress"]))
    if not lines:
        return None
    if team_words_this_week:
        lines.append(copy.DIGEST_TEAM.format(words=f"{team_words_this_week:,}"))
    return lines

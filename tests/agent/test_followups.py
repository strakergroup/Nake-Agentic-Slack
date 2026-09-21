from datetime import datetime, timedelta, timezone

from app.agent.core import copy
from app.agent.core.followups import (
    JobView,
    build_digest,
    is_quiet_hours,
    plan_followups,
)

NOON = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def job(state, hours_ago, job_id="TJ48190", name="Returns-policy.docx"):
    return JobView(job_id, name, state, NOON - timedelta(hours=hours_ago))


def test_quote_waiting_two_days_gets_one_reminder_only():
    jobs = [job("quote_waiting", 49)]
    first = plan_followups(jobs, set(), NOON)
    assert [f.kind for f in first] == ["quote_waiting"]
    assert (
        "Returns-policy.docx" in first[0].text
        and "won't remind you again" in first[0].text
    )
    assert plan_followups(jobs, {"TJ48190:quote_waiting"}, NOON) == []


def test_quote_waiting_less_than_two_days_is_left_alone():
    assert plan_followups([job("quote_waiting", 47)], set(), NOON) == []


def test_delivery_gets_one_note_with_a_next_step():
    first = plan_followups([job("delivered", 1)], set(), NOON)
    assert first[0].text == copy.FOLLOWUP_DELIVERED.format(name="Returns-policy.docx")
    assert plan_followups([job("delivered", 1)], {"TJ48190:delivered"}, NOON) == []


def test_nothing_at_night_in_the_persons_timezone():
    tokyo = 9 * 3600
    assert is_quiet_hours(NOON, tokyo) is True  # 21:00 in Tokyo
    assert (
        plan_followups([job("delivered", 1)], set(), NOON, utc_offset_seconds=tokyo)
        == []
    )
    assert is_quiet_hours(NOON, 0) is False


def test_digest_only_for_people_who_opted_in():
    assert build_digest([job("delivered", 1)], opted_in=False) is None


def test_empty_digest_is_not_sent():
    assert build_digest([], opted_in=True) is None


def test_digest_lines_and_optional_team_line():
    jobs = [
        job("delivered", 1, "TJ1"),
        job("delivered", 2, "TJ2"),
        job("quote_waiting", 3, "TJ3"),
        job("in_progress", 4, "TJ4"),
    ]
    assert build_digest(jobs, opted_in=True) == [
        "2 delivered",
        "1 waiting on you",
        "1 in progress",
    ]
    with_team = build_digest(jobs, opted_in=True, team_words_this_week=40000)
    assert with_team[-1] == "Your team translated 40,000 words this week."


def test_never_remind_about_a_quote_that_has_expired():
    dead = JobView(
        "TJ1",
        "Deck.pptx",
        "quote_waiting",
        NOON - timedelta(hours=49),
        expires_at=NOON - timedelta(hours=37),
    )
    alive = JobView(
        "TJ2",
        "Notice.pdf",
        "quote_waiting",
        NOON - timedelta(hours=49),
        expires_at=NOON + timedelta(days=28),
    )
    assert [f.job_id for f in plan_followups([dead, alive], set(), NOON)] == ["TJ2"]

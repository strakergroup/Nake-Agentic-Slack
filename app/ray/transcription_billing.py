"""Stable billing keys for Slack media transcription spends (RAY-80734)."""

from __future__ import annotations

from typing import Any


def transcription_billing_submission_id(
    *,
    task_uuid: str,
    duration_ms: int | None,
    extra_data: dict[str, Any] | None,
) -> str:
    """Return the producer-owned id used for transcription spend idempotency.

    Prefer Slack ``file_id`` + duration so quote/confirm (empty target → filled
    target) that creates two transcription tasks for the same file collapses to
    one debit. Fall back to ``task_uuid`` when file identity is unavailable.
    """
    extra = extra_data or {}
    slack_file_id = str(
        extra.get("slack_file_id")
        or extra.get("original_video_file_id")
        or extra.get("file_id")
        or ""
    ).strip()
    if slack_file_id and duration_ms:
        return f"{slack_file_id}:{int(duration_ms)}"
    return task_uuid

"""Resolve media Quote1 duration from Slack, then ffprobe, then 1 minute."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.ray.utils import get_media_duration

logger = logging.getLogger(__name__)

MEDIA_QUOTE_DURATION_FALLBACK_MS = 60_000

MediaDurationProbe = Callable[[str, str], Awaitable[int]]


def slack_file_duration_ms(slack_file: Mapping[str, Any]) -> int:
    """Return Slack file duration_ms when present and positive, else 0."""
    raw = slack_file.get("duration_ms") or 0
    try:
        duration_ms = int(raw)
    except (TypeError, ValueError):
        return 0
    return duration_ms if duration_ms > 0 else 0


async def resolve_media_quote_duration_ms(
    *,
    slack_file: Mapping[str, Any],
    download_url: str,
    bot_token: str,
    probe_duration: MediaDurationProbe = get_media_duration,
) -> int:
    """Quote1 duration in milliseconds.

    Prefer Slack files.info duration_ms. When Slack omits it (common for uploaded
    MP4s), probe the private download URL with ffprobe. Last resort is 1 minute.
    """
    slack_ms = slack_file_duration_ms(slack_file)
    if slack_ms:
        return slack_ms
    if download_url and bot_token:
        try:
            probed = int(await probe_duration(download_url, bot_token) or 0)
        except (TypeError, ValueError, OSError):
            probed = 0
        if probed > 0:
            return probed
    logger.warning(
        "media Quote1 duration missing from Slack and ffprobe; using 1-minute fallback"
    )
    return MEDIA_QUOTE_DURATION_FALLBACK_MS


async def file_info_with_quote_duration(
    file_info: dict[str, Any],
    slack_file: Mapping[str, Any],
    *,
    download_url: str,
    bot_token: str,
    probe_duration: MediaDurationProbe = get_media_duration,
) -> dict[str, Any]:
    """Copy file_info with Quote1 duration resolved from Slack, then ffprobe."""
    duration_ms = await resolve_media_quote_duration_ms(
        slack_file=slack_file,
        download_url=download_url,
        bot_token=bot_token,
        probe_duration=probe_duration,
    )
    return {**file_info, "duration_ms": duration_ms}

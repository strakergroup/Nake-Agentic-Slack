"""Quote1 duration: Slack duration_ms, then ffprobe, then 1-minute last resort."""

from __future__ import annotations

import pytest

from app.slack.media_duration import (
    MEDIA_QUOTE_DURATION_FALLBACK_MS,
    file_info_with_quote_duration,
    resolve_media_quote_duration_ms,
)

VIDEO_1_DURATION_MS = 148_259
VIDEO_2_DURATION_MS = 571_350


async def _unused_probe(download_url: str, token: str) -> int:
    raise AssertionError("ffprobe should not run when Slack duration_ms is present")


async def _probe_video_1(download_url: str, token: str) -> int:
    assert download_url == "https://files.slack.com/video.mp4"
    assert token == "xoxb-test"
    return VIDEO_1_DURATION_MS


async def _probe_zero(download_url: str, token: str) -> int:
    return 0


@pytest.mark.asyncio
async def test_uses_slack_duration_ms_without_probing():
    duration_ms = await resolve_media_quote_duration_ms(
        slack_file={"duration_ms": VIDEO_2_DURATION_MS},
        download_url="https://files.slack.com/video.mp4",
        bot_token="xoxb-test",
        probe_duration=_unused_probe,
    )
    assert duration_ms == VIDEO_2_DURATION_MS


@pytest.mark.asyncio
async def test_probes_file_when_slack_omits_duration_ms():
    duration_ms = await resolve_media_quote_duration_ms(
        slack_file={"name": "video 1.mp4"},
        download_url="https://files.slack.com/video.mp4",
        bot_token="xoxb-test",
        probe_duration=_probe_video_1,
    )
    assert duration_ms == VIDEO_1_DURATION_MS


@pytest.mark.asyncio
async def test_one_minute_last_resort_when_slack_and_probe_have_no_duration():
    duration_ms = await resolve_media_quote_duration_ms(
        slack_file={"duration_ms": 0},
        download_url="https://files.slack.com/video.mp4",
        bot_token="xoxb-test",
        probe_duration=_probe_zero,
    )
    assert duration_ms == MEDIA_QUOTE_DURATION_FALLBACK_MS
    assert duration_ms == 60_000


@pytest.mark.asyncio
async def test_quote_file_info_overwrites_stale_one_minute_with_probe():
    updated = await file_info_with_quote_duration(
        {
            "file_id": "F1",
            "file_name": "video 1.mp4",
            "duration_ms": 60_000,
        },
        {"name": "video 1.mp4"},
        download_url="https://files.slack.com/video.mp4",
        bot_token="xoxb-test",
        probe_duration=_probe_video_1,
    )
    assert updated["duration_ms"] == VIDEO_1_DURATION_MS
    assert updated["file_id"] == "F1"

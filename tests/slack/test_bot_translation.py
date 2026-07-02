from unittest.mock import AsyncMock, patch

import pytest

from app.slack.bot_translation import (
    bump_channel_mt_generation,
    is_channel_source_deleted,
    is_stale_channel_mt_generation,
    is_untranslatable_placeholder,
    mark_channel_source_deleted,
)


class TestIsUntranslatablePlaceholder:
    @pytest.mark.parametrize(
        "text",
        [
            ":3dotsloading:",
            ":white_check_mark:",
            ":wave: :smile:",
            "  :hourglass:  ",
            "",
            "   ",
            "...",
            ". . .",
            "\u2026",  # unicode ellipsis
            "!?",
            "\U0001f44d",  # thumbs up emoji
            ":3dotsloading: ...",
        ],
    )
    def test_detects_placeholder_messages(self, text: str):
        assert is_untranslatable_placeholder(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Hello",
            ":wave: hello",
            ":3dotsloading:\n```\n1. step (In Progress)",
            "Part 1...",
            "123",
        ],
    )
    def test_allows_messages_with_real_text(self, text: str):
        assert is_untranslatable_placeholder(text) is False


class TestChannelMtGeneration:
    @pytest.mark.asyncio
    async def test_bump_and_stale_detection(self):
        with (
            patch(
                "app.slack.bot_translation.redis_conn.incr", new_callable=AsyncMock
            ) as mock_incr,
            patch(
                "app.slack.bot_translation.redis_conn.expire", new_callable=AsyncMock
            ) as mock_expire,
            patch(
                "app.slack.bot_translation.redis_conn.get", new_callable=AsyncMock
            ) as mock_get,
        ):
            mock_incr.return_value = 1
            generation = await bump_channel_mt_generation("111.001")

            assert generation == 1
            mock_expire.assert_awaited_once()

            mock_get.return_value = "2"
            assert await is_stale_channel_mt_generation("111.001", 1) is True
            assert await is_stale_channel_mt_generation("111.001", 2) is False


class TestChannelSourceDeletedTombstone:
    @pytest.mark.asyncio
    async def test_mark_writes_tombstone_with_ttl(self):
        with patch(
            "app.slack.bot_translation.redis_conn.set", new_callable=AsyncMock
        ) as mock_set:
            await mark_channel_source_deleted("111.001")

            mock_set.assert_awaited_once()
            args, kwargs = mock_set.call_args
            assert args[0] == "channel_mt_deleted:111.001"
            assert kwargs.get("ex")

    @pytest.mark.asyncio
    async def test_is_deleted_reflects_tombstone(self):
        with patch(
            "app.slack.bot_translation.redis_conn.get", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = "1"
            assert await is_channel_source_deleted("111.001") is True

            mock_get.return_value = None
            assert await is_channel_source_deleted("111.001") is False

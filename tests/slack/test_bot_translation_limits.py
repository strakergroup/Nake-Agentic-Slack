from unittest.mock import AsyncMock, patch

import pytest


class TestCanTranslateBotMessage:
    """Tests for bot channel translation rate limits."""

    @pytest.mark.asyncio
    async def test_skips_rate_limit_for_edits(self):
        from app.slack.bot_translation_limits import can_translate_bot_message

        with patch(
            "app.slack.bot_translation_limits.redis_conn.incr",
            new_callable=AsyncMock,
        ) as mock_incr:
            result = await can_translate_bot_message("C123", "B123", is_edit=True)

            assert result is True
            mock_incr.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_first_message_and_sets_expiry(self):
        from app.slack.bot_translation_limits import can_translate_bot_message

        with (
            patch(
                "app.slack.bot_translation_limits.redis_conn.incr",
                new_callable=AsyncMock,
            ) as mock_incr,
            patch(
                "app.slack.bot_translation_limits.redis_conn.expire",
                new_callable=AsyncMock,
            ) as mock_expire,
        ):
            mock_incr.return_value = 1

            result = await can_translate_bot_message("C123", "B123")

            assert result is True
            mock_incr.assert_awaited_once_with("bot_translation_rate:C123:B123")
            mock_expire.assert_awaited_once_with("bot_translation_rate:C123:B123", 300)

    @pytest.mark.asyncio
    async def test_blocks_after_ten_messages(self):
        from app.slack.bot_translation_limits import can_translate_bot_message

        with (
            patch(
                "app.slack.bot_translation_limits.redis_conn.incr",
                new_callable=AsyncMock,
            ) as mock_incr,
            patch(
                "app.slack.bot_translation_limits.redis_conn.expire",
                new_callable=AsyncMock,
            ) as mock_expire,
        ):
            mock_incr.return_value = 11

            result = await can_translate_bot_message("C123", "B123")

            assert result is False
            mock_expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_fails_closed_on_redis_error(self):
        from app.slack.bot_translation_limits import can_translate_bot_message

        with patch(
            "app.slack.bot_translation_limits.redis_conn.incr",
            new_callable=AsyncMock,
        ) as mock_incr:
            mock_incr.side_effect = RuntimeError("redis unavailable")

            result = await can_translate_bot_message("C123", "B123")

            assert result is False

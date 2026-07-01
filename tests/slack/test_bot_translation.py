import json
from unittest.mock import AsyncMock, patch

import pytest

from app.slack.bot_translation import (
    bump_channel_mt_generation,
    is_slack_emoji_only,
    is_stale_channel_mt_generation,
    run_debounced_bot_translation,
    schedule_bot_message_translation,
)


class TestIsSlackEmojiOnly:
    @pytest.mark.parametrize(
        "text",
        [
            ":3dotsloading:",
            ":white_check_mark:",
            ":wave: :smile:",
            "  :hourglass:  ",
            "",
        ],
    )
    def test_detects_emoji_only_messages(self, text: str):
        assert is_slack_emoji_only(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Hello",
            ":wave: hello",
            ":3dotsloading:\n```\n1. step (In Progress)",
            "...",
        ],
    )
    def test_allows_messages_with_real_text(self, text: str):
        assert is_slack_emoji_only(text) is False


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


class TestBotDebounce:
    @pytest.mark.asyncio
    async def test_schedule_stores_latest_payload_and_enqueues_scheduled_job(self):
        context = {
            "channel_id": "C123",
            "team_id": "T123",
            "enterprise_id": None,
            "bot_user_id": "BAPP",
        }
        message = {"ts": "111.001", "text": "final bot answer", "bot_id": "B123"}

        with (
            patch(
                "app.slack.bot_translation.redis_conn.set", new_callable=AsyncMock
            ) as mock_set,
            patch("app.slack.bot_translation.time.time", return_value=1000.0),
            patch(
                "app.saq_jobs.enqueue_debounced_bot_translation",
                new_callable=AsyncMock,
            ) as mock_enqueue,
        ):
            await schedule_bot_message_translation(AsyncMock(), context, message)

            mock_set.assert_awaited_once()
            stored_payload = json.loads(mock_set.await_args[0][1])
            assert stored_payload["text"] == "final bot answer"

            from app.config import config

            mock_enqueue.assert_awaited_once()
            kwargs = mock_enqueue.await_args.kwargs
            assert kwargs["channel_id"] == "C123"
            assert kwargs["bot_id"] == "B123"
            assert kwargs["team_id"] == "T123"
            assert kwargs["scheduled"] == int(
                1000.0 + config.bot_translation_debounce_seconds
            )

    @pytest.mark.asyncio
    async def test_schedule_skips_when_bot_or_channel_missing(self):
        with patch(
            "app.saq_jobs.enqueue_debounced_bot_translation", new_callable=AsyncMock
        ) as mock_enqueue:
            await schedule_bot_message_translation(
                AsyncMock(),
                {"team_id": "T123"},
                {"ts": "1.0", "text": "hi"},  # no bot_id / channel_id
            )
            mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_debounced_translation_translates_latest_payload(self):
        message = {"ts": "111.001", "text": "final bot answer", "bot_id": "B123"}

        with (
            patch(
                "app.slack.bot_translation._load_bot_debounce_payload",
                new_callable=AsyncMock,
                return_value=message,
            ),
            patch(
                "app.slack.bot_translation.get_bot_token_async",
                new_callable=AsyncMock,
                return_value="xoxb-test",
            ),
            patch(
                "app.slack.bot_translation.get_ray_super_group",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "app.slack.listener_actions.auto_translate_message",
                new_callable=AsyncMock,
            ) as mock_translate,
        ):
            await run_debounced_bot_translation(
                channel_id="C123",
                bot_id="B123",
                team_id="T123",
                enterprise_id=None,
                bot_user_id="BAPP",
            )

            mock_translate.assert_awaited_once()
            assert mock_translate.await_args.kwargs["skip_bot_debounce"] is True
            assert mock_translate.await_args.args[2] == message

    @pytest.mark.asyncio
    async def test_run_debounced_translation_skips_emoji_only_payload(self):
        with (
            patch(
                "app.slack.bot_translation._load_bot_debounce_payload",
                new_callable=AsyncMock,
                return_value={"ts": "1.0", "text": ":3dotsloading:"},
            ),
            patch(
                "app.slack.listener_actions.auto_translate_message",
                new_callable=AsyncMock,
            ) as mock_translate,
        ):
            await run_debounced_bot_translation(
                channel_id="C123",
                bot_id="B123",
                team_id="T123",
                enterprise_id=None,
                bot_user_id="BAPP",
            )
            mock_translate.assert_not_called()

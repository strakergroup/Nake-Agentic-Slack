from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from app.auth.connector import SlackUser
from app.dependencies import RayEvent
from app.ray.events.logging import post_channel_translation_notification


@pytest.fixture
def slack_user():
    return SlackUser(
        user_id="U123",
        team_id="T123",
        enterprise_id=None,
        channel_id="",
        is_subscribed=True,
        bot_token="xoxb-test",
        ray_client_id="client-uuid",
        ray_username="test@example.com",
        ray_user_group_id="group-uuid",
    )


@pytest.fixture
def ray_event():
    return RayEvent(event="slack:direct:mt:result", data={"task_id": "task-1"})


@pytest.fixture
def translation_message():
    message = MagicMock()
    message.text = "translated text"
    message.blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
    return message


def _slack_response(ts: str) -> MagicMock:
    response = MagicMock()
    response.get = MagicMock(
        side_effect=lambda key, default=None: ts if key == "ts" else default
    )
    return response


def _slack_api_error(error: str) -> SlackApiError:
    response = MagicMock()
    response.get = MagicMock(
        side_effect=lambda key, default=None: error if key == "error" else default
    )
    return SlackApiError(
        message=f"The server responded with: {{'ok': False, 'error': '{error}'}}",
        response=response,
    )


@pytest.mark.asyncio
@patch("app.ray.events.logging.enqueue_log_notification", new_callable=AsyncMock)
@patch("app.ray.events.logging.set_mt_ts_edit", new_callable=AsyncMock)
@patch("app.ray.events.logging.get_mt_ts_cached", new_callable=AsyncMock)
async def test_post_channel_translation_update_success(
    mock_get_cached,
    mock_set_mt_ts,
    mock_log_notification,
    slack_user,
    ray_event,
    translation_message,
):
    mock_get_cached.return_value = "999.001"
    client = AsyncMock()
    client.chat_update = AsyncMock(return_value=_slack_response("999.001"))

    await post_channel_translation_notification(
        client,
        ray_event,
        slack_user,
        translation_message,
        channel_id="C123",
        is_edit=True,
        display_format="thread",
        message_ts="111.001",
    )

    client.chat_update.assert_awaited_once()
    client.chat_postMessage.assert_not_awaited()
    mock_set_mt_ts.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.ray.events.logging.enqueue_log_notification", new_callable=AsyncMock)
@patch("app.ray.events.logging.set_mt_ts_edit", new_callable=AsyncMock)
@patch("app.ray.events.logging.clear_mt_ts_cached", new_callable=AsyncMock)
@patch("app.ray.events.logging.get_mt_ts_cached", new_callable=AsyncMock)
async def test_post_channel_translation_falls_back_when_update_message_not_found(
    mock_get_cached,
    mock_clear_cached,
    mock_set_mt_ts,
    mock_log_notification,
    slack_user,
    ray_event,
    translation_message,
):
    mock_get_cached.return_value = "999.001"
    client = AsyncMock()
    client.chat_update = AsyncMock(side_effect=_slack_api_error("message_not_found"))
    client.chat_postMessage = AsyncMock(return_value=_slack_response("999.002"))

    await post_channel_translation_notification(
        client,
        ray_event,
        slack_user,
        translation_message,
        channel_id="C123",
        is_edit=True,
        display_format="thread",
        message_ts="111.001",
    )

    mock_clear_cached.assert_awaited_once_with("111.001")
    client.chat_update.assert_awaited_once()
    client.chat_postMessage.assert_awaited_once_with(
        channel="C123",
        text=translation_message.text,
        blocks=translation_message.blocks,
        thread_ts="111.001",
    )
    mock_set_mt_ts.assert_awaited_once_with(send_ts="111.001", reply_ts="999.002")


@pytest.mark.asyncio
@patch("app.ray.events.logging.enqueue_log_notification", new_callable=AsyncMock)
@patch("app.ray.events.logging.get_mt_ts_cached", new_callable=AsyncMock)
async def test_post_channel_translation_reraises_non_message_not_found_update_errors(
    mock_get_cached,
    mock_log_notification,
    slack_user,
    ray_event,
    translation_message,
):
    mock_get_cached.return_value = "999.001"
    client = AsyncMock()
    client.chat_update = AsyncMock(side_effect=_slack_api_error("invalid_blocks"))

    with pytest.raises(SlackApiError):
        await post_channel_translation_notification(
            client,
            ray_event,
            slack_user,
            translation_message,
            channel_id="C123",
            is_edit=True,
            display_format="thread",
            message_ts="111.001",
        )

    client.chat_postMessage.assert_not_awaited()

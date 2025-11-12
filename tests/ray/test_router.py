"""Tests for ray router endpoint."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.dependencies import RayEvent, RayEventAuth
from app.ray.events.models import MtErrorResponseSchema, MtErrorTypes
from app.routers.ray import ray_events
from app.slack.templates.messages import DocMtMessage


@pytest.fixture
def mock_slack_user():
    """Create a mock SlackUser."""
    from app.auth.connector import SlackUser

    return SlackUser(
        user_id="U123456",
        team_id="T123456",
        enterprise_id=None,
        channel_id="C123456",
        is_subscribed=True,
        bot_token="xoxb-test-token",
        ray_client_id=str(uuid4()),
        ray_username="test.user",
        ray_user_group_id=str(uuid4()),
    )


@pytest.fixture
def mock_ray_event_auth(mock_slack_user):
    """Create a mock RayEventAuth."""
    auth = RayEventAuth()
    auth.slack_user = mock_slack_user
    return auth


@pytest.fixture
def mock_async_web_client():
    """Create a mock AsyncWebClient."""
    client = AsyncMock()
    client.users_info = AsyncMock(
        return_value={
            "user": {
                "id": "U123456",
                "locale": "en-US",
            }
        }
    )
    return client


@pytest.mark.asyncio
@patch("app.routers.ray.post_notification_ephemeral")
@patch("app.routers.ray.AsyncWebClient")
@patch("app.routers.ray.get_ray_event_auth")
async def test_verify_slack_document_translated_error_type_other(
    mock_get_auth,
    mock_client_class,
    mock_post_notification,
    mock_ray_event_auth,
    mock_async_web_client,
):
    """Test that error_type 'other' uses DocMtMessage."""
    # Setup mocks
    mock_get_auth.return_value = mock_ray_event_auth
    mock_client_class.return_value = mock_async_web_client

    # Create event with error_type "other"
    event_data = {
        "error": True,
        "error_type": "other",
        "client_id": str(uuid4()),
        "channel_id": "C123456",
        "tokens": 0,
        "error_data": {},
    }

    event = RayEvent(event="verify:slack:document:translated", data=event_data)

    # Call the endpoint
    await ray_events(event, mock_ray_event_auth)

    # Verify post_notification_ephemeral was called with DocMtMessage
    mock_post_notification.assert_called_once()
    call_args = mock_post_notification.call_args

    # Verify the message is DocMtMessage
    assert isinstance(call_args[0][4], DocMtMessage)

    # Verify it was called with correct parameters
    assert call_args[0][1] == "C123456"  # channel_id
    assert call_args[0][2].event == "verify:slack:document:translated"
    assert call_args[0][3] == mock_ray_event_auth.slack_user


@pytest.mark.asyncio
@patch("app.routers.ray.post_notification_ephemeral")
@patch("app.routers.ray.AsyncWebClient")
@patch("app.routers.ray.get_ray_event_auth")
async def test_verify_slack_document_translated_error_type_other_validates_schema(
    mock_get_auth,
    mock_client_class,
    mock_post_notification,
    mock_ray_event_auth,
    mock_async_web_client,
):
    """Test that error_type 'other' validates as MtErrorResponseSchema."""
    # Setup mocks
    mock_get_auth.return_value = mock_ray_event_auth
    mock_client_class.return_value = mock_async_web_client

    # Create event with error_type "other" (validate it matches schema)
    event_data = {
        "error": True,
        "error_type": MtErrorTypes.OTHER,
        "client_id": str(uuid4()),
        "channel_id": "C123456",
        "tokens": 0,
        "error_data": {},
    }

    # Validate it can be parsed as MtErrorResponseSchema
    error_response = MtErrorResponseSchema.model_validate(event_data)
    assert error_response.error_type == MtErrorTypes.OTHER
    assert error_response.error_data == {}

    event = RayEvent(event="verify:slack:document:translated", data=event_data)

    # Call the endpoint
    await ray_events(event, mock_ray_event_auth)

    # Verify post_notification_ephemeral was called
    mock_post_notification.assert_called_once()
    call_args = mock_post_notification.call_args

    # Verify the message is DocMtMessage
    assert isinstance(call_args[0][4], DocMtMessage)

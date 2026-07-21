"""Tests for Slack modal trigger helpers."""

from unittest.mock import AsyncMock

import pytest
from slack_sdk.errors import SlackApiError

from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)


@pytest.mark.asyncio
async def test_open_loading_modal_returns_view_id():
    client = AsyncMock()
    client.views_open.return_value = {"view": {"id": "V123"}}
    view_id = await open_loading_modal(client, "trigger-1")
    assert view_id == "V123"
    client.views_open.assert_awaited_once()
    assert client.views_open.await_args.kwargs["trigger_id"] == "trigger-1"


@pytest.mark.asyncio
async def test_safe_views_update_ignores_view_closed():
    client = AsyncMock()
    response = {"error": "view_closed"}
    client.views_update.side_effect = SlackApiError("closed", response)
    assert await safe_views_update(client, "V123", {"type": "modal"}) is False


def test_status_and_error_modals():
    view = status_modal("Title", "Message")
    assert view["title"]["text"] == "Title"
    assert view["blocks"][0]["text"]["text"] == "Message"
    assert request_error_modal()["title"]["text"]

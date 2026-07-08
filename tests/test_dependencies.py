"""Ray event auth resolution tests."""

from unittest.mock import AsyncMock, patch

import pytest

from app.auth.connector import SlackUser
from app.dependencies import RayEvent, RayEventAuth


@pytest.mark.asyncio
async def test_ray_event_auth_resolves_org_billed_channel_mt_from_extra_data():
    auth = RayEventAuth()
    org_user = SlackUser(
        user_id="org-uuid",
        team_id="T1",
        enterprise_id=None,
        channel_id="",
        is_subscribed=False,
        bot_token="xoxb",
        ray_client_id="org-uuid",
        ray_username="",
        ray_user_group_id="group-1",
    )
    event = RayEvent(
        event="slack:direct:mt:result",
        data={
            "extra_data": {
                "client_id": "org-uuid",
                "team_id": "T1",
                "slack_user_id": "U_POSTER",
            }
        },
    )

    with (
        patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
        patch(
            "app.dependencies.resolve_slack_delivery_user",
            new=AsyncMock(return_value=org_user),
        ) as mock_resolve,
        patch("app.dependencies.get_demo_link", new=AsyncMock(return_value=[])),
    ):
        await auth.initialize(event, token="secret")

    mock_resolve.assert_awaited_once_with(
        "org-uuid",
        team_id="T1",
        slack_user_id="U_POSTER",
    )
    assert auth.slack_user is org_user


@pytest.mark.asyncio
async def test_ray_event_auth_resolves_org_billed_document_mt_from_root_fields():
    auth = RayEventAuth()
    org_user = SlackUser(
        user_id="U_POSTER",
        team_id="T1",
        enterprise_id=None,
        channel_id="",
        is_subscribed=False,
        bot_token="xoxb",
        ray_client_id="org-uuid",
        ray_username="",
        ray_user_group_id="group-1",
    )
    event = RayEvent(
        event="verify:slack:document:translated",
        data={
            "client_id": "org-uuid",
            "team_id": "T1",
            "slack_user_id": "U_POSTER",
        },
    )

    with (
        patch("app.dependencies.validate_queue_proxy_secret", return_value=True),
        patch(
            "app.dependencies.resolve_slack_delivery_user",
            new=AsyncMock(return_value=org_user),
        ) as mock_resolve,
        patch("app.dependencies.get_demo_link", new=AsyncMock(return_value=[])),
    ):
        await auth.initialize(event, token="secret")

    mock_resolve.assert_awaited_once_with(
        "org-uuid",
        team_id="T1",
        slack_user_id="U_POSTER",
    )
    assert auth.slack_user is org_user

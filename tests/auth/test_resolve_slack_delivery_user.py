"""RAY-81247 — Slack delivery when deltaray is missing / HT SA owns the job."""

from unittest.mock import AsyncMock, patch

import pytest

from app.auth.connector import SlackUser, resolve_slack_delivery_user


@pytest.mark.asyncio
async def test_resolve_slack_delivery_user_workspace_stamp_fallback():
    """HT SA / inactive deltaray: bot from team install + poster from stamps."""
    stamped = SlackUser(
        user_id="UKVHQ6UJX",
        team_id="T03PE1PGBV5",
        enterprise_id="E04RDMG8XP1",
        channel_id="C123",
        is_subscribed=False,
        bot_token="xoxb-test",
        ray_client_id="6BB48BEF-1EAD-4824-8724-5298CA10AA86",
        ray_username="",
    )
    with (
        patch(
            "app.auth.connector.get_slack_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.auth.connector.get_slack_org",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.auth.connector.get_slack_user_from_workspace_stamps",
            new=AsyncMock(return_value=stamped),
        ) as mock_stamps,
    ):
        user = await resolve_slack_delivery_user(
            "6BB48BEF-1EAD-4824-8724-5298CA10AA86",
            team_id="T03PE1PGBV5",
            slack_user_id="UKVHQ6UJX",
            enterprise_id="E04RDMG8XP1",
            channel_id="C123",
        )

    assert user is stamped
    mock_stamps.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_slack_delivery_user_prefers_active_deltaray():
    linked = SlackUser(
        user_id="U_LINKED",
        team_id="T03PE1PGBV5",
        enterprise_id="E04RDMG8XP1",
        channel_id="C1",
        is_subscribed=True,
        bot_token="xoxb-linked",
        ray_client_id="A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
        ray_username="wade.norman@strakergroup.com",
    )
    with (
        patch(
            "app.auth.connector.get_slack_user",
            new=AsyncMock(return_value=linked),
        ),
        patch(
            "app.auth.connector.get_slack_org",
            new=AsyncMock(),
        ) as mock_org,
        patch(
            "app.auth.connector.get_slack_user_from_workspace_stamps",
            new=AsyncMock(),
        ) as mock_stamps,
    ):
        user = await resolve_slack_delivery_user(
            "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
            team_id="T03PE1PGBV5",
            slack_user_id="UKVHQ6UJX",
        )

    assert user is not None
    assert user.user_id == "UKVHQ6UJX"  # poster stamp wins
    assert user.bot_token == "xoxb-linked"
    mock_org.assert_not_awaited()
    mock_stamps.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_slack_delivery_user_copies_event_enterprise_id():
    """Deltaray/org rows may omit Grid id; the callback stamp must still win."""
    linked = SlackUser(
        user_id="U_LINKED",
        team_id="T03PE1PGBV5",
        enterprise_id=None,
        channel_id="C1",
        is_subscribed=True,
        bot_token="xoxb-linked",
        ray_client_id="A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
        ray_username="tester",
    )
    with (
        patch(
            "app.auth.connector.get_slack_user",
            new=AsyncMock(return_value=linked),
        ),
        patch(
            "app.auth.connector.get_slack_org",
            new=AsyncMock(),
        ),
        patch(
            "app.auth.connector.get_slack_user_from_workspace_stamps",
            new=AsyncMock(),
        ),
    ):
        user = await resolve_slack_delivery_user(
            "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
            team_id="T03PE1PGBV5",
            slack_user_id="UKVHQ6UJX",
            enterprise_id="E27SFGS2W",
        )

    assert user is not None
    assert user.enterprise_id == "E27SFGS2W"


@pytest.mark.asyncio
async def test_get_slack_user_from_workspace_stamps_loads_bot():
    from app.auth.connector import get_slack_user_from_workspace_stamps

    with patch(
        "app.auth.connector.get_bot_token_async",
        new=AsyncMock(return_value="xoxb-workspace"),
    ):
        user = await get_slack_user_from_workspace_stamps(
            "6BB48BEF-1EAD-4824-8724-5298CA10AA86",
            team_id="T03PE1PGBV5",
            slack_user_id="UKVHQ6UJX",
            enterprise_id="E04RDMG8XP1",
            channel_id="D123",
        )

    assert user is not None
    assert user.bot_token == "xoxb-workspace"
    assert user.user_id == "UKVHQ6UJX"
    assert user.team_id == "T03PE1PGBV5"
    assert user.channel_id == "D123"

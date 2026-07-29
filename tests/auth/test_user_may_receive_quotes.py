"""Tests for admin-only quote gating."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.connector import user_may_receive_quotes


@pytest.mark.asyncio
async def test_quote_admin_only_off_allows_everyone():
    with patch("app.config.config") as mock_config:
        mock_config.quote_admin_only = False
        assert await user_may_receive_quotes(None) is True
        assert await user_may_receive_quotes(SimpleNamespace(client=None)) is True


@pytest.mark.asyncio
async def test_quote_admin_only_requires_workspace_super_group():
    """Without workspace super-group context, default group is not consulted."""
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="group-1"),
        super_group=[],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(return_value="Admin"),
        ) as mock_get_client_type,
        patch(
            "app.auth.connector.user_is_organization_group_admin",
            new=AsyncMock(return_value=True),
        ) as mock_org_admin,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is False

    mock_get_client_type.assert_not_awaited()
    mock_org_admin.assert_not_awaited()


@pytest.mark.asyncio
async def test_quote_admin_only_super_group_admin_allows_quotes():
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="baker-hughes"),
        super_group=[
            SimpleNamespace(
                id="ibm-super-group",
                verify_organization_uuid="ibm-org",
            )
        ],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(return_value="Admin"),
        ) as mock_get_client_type,
        patch(
            "app.auth.connector.user_is_organization_group_admin",
            new=AsyncMock(),
        ) as mock_org_admin,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is True

    mock_get_client_type.assert_awaited_once_with("client-1", "ibm-super-group")
    mock_org_admin.assert_not_awaited()


@pytest.mark.asyncio
async def test_quote_admin_only_org_child_group_admin_allows_quotes():
    """Admin of IBM Slack App under workspace org unlocks quotes (any default)."""
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="baker-hughes"),
        super_group=[
            SimpleNamespace(
                id="ibm-super-group",
                verify_organization_uuid="ibm-org",
            )
        ],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(return_value="Normal"),
        ) as mock_get_client_type,
        patch(
            "app.auth.connector.user_is_organization_group_admin",
            new=AsyncMock(return_value=True),
        ) as mock_org_admin,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is True

    mock_get_client_type.assert_awaited_once_with("client-1", "ibm-super-group")
    mock_org_admin.assert_awaited_once_with("client-1", "ibm-org")


@pytest.mark.asyncio
async def test_quote_admin_only_org_child_group_normal_denies_quotes():
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="baker-hughes"),
        super_group=[
            SimpleNamespace(
                id="ibm-super-group",
                verify_organization_uuid="ibm-org",
            )
        ],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(return_value="Normal"),
        ),
        patch(
            "app.auth.connector.user_is_organization_group_admin",
            new=AsyncMock(return_value=False),
        ),
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is False


@pytest.mark.asyncio
async def test_quote_admin_only_denies_missing_member_client():
    with patch("app.config.config") as mock_config:
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(None) is False
        assert await user_may_receive_quotes(SimpleNamespace(client=None)) is False

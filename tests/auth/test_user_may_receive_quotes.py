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
async def test_quote_admin_only_requires_admin_or_owner():
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="group-1"),
        super_group=[],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(side_effect=["Admin", "Owner", "Normal", None]),
        ) as mock_get_client_type,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is True
        assert await user_may_receive_quotes(ray) is True
        assert await user_may_receive_quotes(ray) is False
        assert await user_may_receive_quotes(ray) is False
    assert all(
        call.args == ("client-1", "group-1")
        for call in mock_get_client_type.await_args_list
    )


@pytest.mark.asyncio
async def test_quote_admin_only_unrelated_primary_denied_even_if_admin():
    """Primary group outside the workspace Verify org must not unlock quotes."""
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="straker-test-group"),
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
            "app.auth.connector.group_belongs_to_organization",
            new=AsyncMock(return_value=False),
        ) as mock_group_in_org,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is False

    mock_get_client_type.assert_awaited_once_with("client-1", "ibm-super-group")
    mock_group_in_org.assert_awaited_once_with("straker-test-group", "ibm-org")


@pytest.mark.asyncio
async def test_quote_admin_only_super_group_admin_allows_quotes():
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="ibm-slack-app"),
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
            "app.auth.connector.group_belongs_to_organization",
            new=AsyncMock(),
        ) as mock_group_in_org,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is True

    mock_get_client_type.assert_awaited_once_with("client-1", "ibm-super-group")
    mock_group_in_org.assert_not_awaited()


@pytest.mark.asyncio
async def test_quote_admin_only_primary_group_under_workspace_org_allows_quotes():
    """Admin of primary group under workspace org (e.g. IBM Slack App)."""
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="ibm-slack-app"),
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
            new=AsyncMock(side_effect=["Normal", "Admin"]),
        ) as mock_get_client_type,
        patch(
            "app.auth.connector.group_belongs_to_organization",
            new=AsyncMock(return_value=True),
        ) as mock_group_in_org,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is True

    assert mock_get_client_type.await_args_list[0].args == (
        "client-1",
        "ibm-super-group",
    )
    assert mock_get_client_type.await_args_list[1].args == (
        "client-1",
        "ibm-slack-app",
    )
    mock_group_in_org.assert_awaited_once_with("ibm-slack-app", "ibm-org")


@pytest.mark.asyncio
async def test_quote_admin_only_denies_missing_member_client():
    with patch("app.config.config") as mock_config:
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(None) is False
        assert await user_may_receive_quotes(SimpleNamespace(client=None)) is False

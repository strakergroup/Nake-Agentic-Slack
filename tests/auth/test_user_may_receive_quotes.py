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
async def test_quote_admin_only_uses_workspace_super_group_not_primary_group():
    """Admin of an unrelated primary group must not unlock quotes in IBM Slack."""
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="straker-test-group"),
        super_group=[SimpleNamespace(id="ibm-super-group")],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(return_value="Normal"),
        ) as mock_get_client_type,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is False

    mock_get_client_type.assert_awaited_once_with("client-1", "ibm-super-group")


@pytest.mark.asyncio
async def test_quote_admin_only_super_group_admin_allows_quotes():
    ray = SimpleNamespace(
        client=SimpleNamespace(id="client-1", user_group_id="straker-test-group"),
        super_group=[SimpleNamespace(id="ibm-super-group")],
    )
    with (
        patch("app.config.config") as mock_config,
        patch(
            "app.auth.connector.get_client_type",
            new=AsyncMock(return_value="Admin"),
        ) as mock_get_client_type,
    ):
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(ray) is True

    mock_get_client_type.assert_awaited_once_with("client-1", "ibm-super-group")


@pytest.mark.asyncio
async def test_quote_admin_only_denies_missing_member_client():
    with patch("app.config.config") as mock_config:
        mock_config.quote_admin_only = True
        assert await user_may_receive_quotes(None) is False
        assert await user_may_receive_quotes(SimpleNamespace(client=None)) is False

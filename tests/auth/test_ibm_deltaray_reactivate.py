"""IBM Slack email → CRM member resolution (RAY-81247)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.connector import (
    ensure_active_ibm_deltaray_link,
    get_active_crm_member_by_email,
    get_ray_client,
    get_ray_client_ibm_by_email,
    resolve_slack_user_email,
    user_may_attach_workspace_client,
)
from app.slack.templates.messages import LoginMessage


@pytest.mark.asyncio
async def test_resolve_slack_user_email_prefers_live_api():
    with (
        patch(
            "app.auth.connector._slack_email_from_api",
            new=AsyncMock(return_value="wade.norman@strakergroup.com"),
        ),
        patch(
            "app.auth.connector._slack_email_from_details",
            new=AsyncMock(return_value="wade.norman@strakertranslations.com"),
        ) as mock_details,
    ):
        email = await resolve_slack_user_email(
            "UKVHQ6UJX", "T03PE1PGBV5", "E04RDMG8XP1"
        )

    assert email == "wade.norman@strakergroup.com"
    mock_details.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_ray_client_ibm_by_email_skips_stale_cached_email():
    """Stale slack_user_details email must not block live Slack → CRM match."""
    member = {
        "member_uuid": "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
        "login": "wade.norman@strakergroup.com",
        "email_primary": "wade.norman@strakergroup.com",
        "given_name": "Wade",
        "family_name": "Norman",
        "active": 1,
        "groupid": "C9E4513A-41BC-419A-BEB9-6EDAFCD04470",
        "settings_id": None,
    }
    mock_secret = MagicMock()
    mock_secret.get_secret_value.return_value = "secret"

    async def crm_by_email(email: str):
        if email.lower() == "wade.norman@strakergroup.com":
            return member
        return None

    with (
        patch(
            "app.auth.connector.resolve_slack_user_email_candidates",
            new=AsyncMock(
                return_value=[
                    "wade.norman@strakergroup.com",
                    "wade.norman@strakertranslations.com",
                ]
            ),
        ),
        patch(
            "app.auth.connector.get_active_crm_member_by_email",
            new=AsyncMock(side_effect=crm_by_email),
        ),
        patch(
            "app.auth.connector.user_may_attach_workspace_client",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.ensure_active_ibm_deltaray_link",
            new=AsyncMock(),
        ) as mock_ensure,
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ),
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(return_value={"obj_uuid": "tok"}),
        ),
        patch("app.auth.connector.config") as mock_config,
    ):
        mock_config.languagecloud_api_key = mock_secret
        client = await get_ray_client_ibm_by_email(
            "UKVHQ6UJX", "T03PE1PGBV5", "E04RDMG8XP1"
        )

    assert client is not None
    assert client.id == member["member_uuid"]
    mock_ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_active_crm_member_by_email_queries_member():
    member = {"member_uuid": "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952"}
    with patch(
        "app.auth.connector.fetch_one", new=AsyncMock(return_value=member)
    ) as mock_fetch:
        row = await get_active_crm_member_by_email("wade.norman@strakergroup.com")

    assert row == member
    sql = str(mock_fetch.await_args.args[0])
    assert "obj_m_member" in sql
    assert "LOWER(m.login)" in sql


@pytest.mark.asyncio
async def test_ensure_active_ibm_deltaray_link_inserts_when_missing():
    with (
        patch("app.auth.connector.fetch_one", new=AsyncMock(return_value=None)),
        patch("app.auth.connector.execute", new=AsyncMock()) as mock_execute,
    ):
        await ensure_active_ibm_deltaray_link(
            user_id="UKVHQ6UJX",
            team_id="T03PE1PGBV5",
            enterprise_id="E04RDMG8XP1",
            member_uuid="A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
        )

    assert mock_execute.await_count == 1
    sql = str(mock_execute.await_args.args[0])
    assert "INSERT INTO slack_deltaray_link" in sql
    assert "is_active" in sql


@pytest.mark.asyncio
async def test_ensure_active_ibm_deltaray_link_updates_when_present():
    with (
        patch("app.auth.connector.fetch_one", new=AsyncMock(return_value={"id": 1})),
        patch("app.auth.connector.execute", new=AsyncMock()) as mock_execute,
    ):
        await ensure_active_ibm_deltaray_link(
            user_id="UKVHQ6UJX",
            team_id="T03PE1PGBV5",
            enterprise_id="E04RDMG8XP1",
            member_uuid="A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
        )

    sql = str(mock_execute.await_args.args[0])
    assert "UPDATE slack_deltaray_link" in sql
    assert "is_active = 1" in sql


@pytest.mark.asyncio
async def test_get_ray_client_ibm_by_email_builds_client_and_upserts_link():
    member = {
        "member_uuid": "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952",
        "login": "wade.norman@strakergroup.com",
        "email_primary": "wade.norman@strakergroup.com",
        "given_name": "Wade",
        "family_name": "Norman",
        "active": 1,
        "groupid": "C9E4513A-41BC-419A-BEB9-6EDAFCD04470",
        "settings_id": None,
    }
    mock_secret = MagicMock()
    mock_secret.get_secret_value.return_value = "secret"

    with (
        patch(
            "app.auth.connector.resolve_slack_user_email_candidates",
            new=AsyncMock(return_value=["wade.norman@strakergroup.com"]),
        ),
        patch(
            "app.auth.connector.get_active_crm_member_by_email",
            new=AsyncMock(return_value=member),
        ),
        patch(
            "app.auth.connector.user_may_attach_workspace_client",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.auth.connector.ensure_active_ibm_deltaray_link",
            new=AsyncMock(),
        ) as mock_ensure,
        patch(
            "app.auth.connector.create_languagecloud_id_token",
            return_value="id-token",
        ),
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(return_value={"obj_uuid": "tok"}),
        ),
        patch("app.auth.connector.config") as mock_config,
    ):
        mock_config.languagecloud_api_key = mock_secret
        client = await get_ray_client_ibm_by_email(
            "UKVHQ6UJX", "T03PE1PGBV5", "E04RDMG8XP1"
        )

    assert client is not None
    assert client.id == member["member_uuid"]
    assert client.username == member["login"]
    mock_ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_ray_client_uses_ibm_email_path_first():
    ibm_client = MagicMock()
    ibm_client.id = "A02F3A2F-C6D4-4D19-BD1E-BD0A5C990952"

    with (
        patch("app.ray.utils.is_ibm_customer_enterprise", return_value=True),
        patch(
            "app.auth.connector.get_ray_client_ibm_by_email",
            new=AsyncMock(return_value=ibm_client),
        ) as mock_ibm,
        patch("app.auth.connector.fetch_one", new=AsyncMock()) as mock_fetch,
    ):
        client = await get_ray_client("UKVHQ6UJX", "T03PE1PGBV5", "E04RDMG8XP1")

    assert client is ibm_client
    mock_ibm.assert_awaited_once()
    mock_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_ray_client_ibm_by_email_skips_personal_verify_group():
    """Inactive IBM Slack App mglink / personal Verify org must not attach a client."""
    member = {
        "member_uuid": "1B551A68-1D9A-4669-9E75-F0F44343F1AA",
        "login": "ibm.user@example.com",
        "email_primary": "ibm.user@example.com",
        "given_name": "Test",
        "family_name": "User",
        "active": 1,
        "groupid": "",
        "settings_id": None,
    }
    with (
        patch(
            "app.auth.connector.resolve_slack_user_email_candidates",
            new=AsyncMock(return_value=["ibm.user@example.com"]),
        ),
        patch(
            "app.auth.connector.get_active_crm_member_by_email",
            new=AsyncMock(return_value=member),
        ),
        patch(
            "app.auth.connector.user_may_attach_workspace_client",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.auth.connector.ensure_active_ibm_deltaray_link",
            new=AsyncMock(),
        ) as mock_ensure,
    ):
        client = await get_ray_client_ibm_by_email(
            "W7U7YQPGB", "TN4NQ9GK0", "E27SFGS2W"
        )

    assert client is None
    mock_ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_ray_client_ibm_deltaray_fallback_requires_crm_group_and_verify_team():
    """Deltaray fallback must not mint a client unless CRM group and Verify team match."""
    with (
        patch("app.ray.utils.is_ibm_customer_enterprise", return_value=True),
        patch(
            "app.auth.connector.get_ray_client_ibm_by_email",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(
                return_value={
                    "member_uuid": "1B551A68-1D9A-4669-9E75-F0F44343F1AA",
                    "login": "ibm.user@example.com",
                    "email_primary": "ibm.user@example.com",
                    "given_name": "Test",
                    "family_name": "User",
                    "slack_team_id": "TN4NQ9GK0",
                    "active": 1,
                    "groupid": "",
                    "is_sso": 1,
                    "access_token": None,
                    "settings_id": None,
                }
            ),
        ),
        patch(
            "app.auth.connector.user_may_attach_workspace_client",
            new=AsyncMock(return_value=False),
        ) as mock_attach,
    ):
        client = await get_ray_client("W7U7YQPGB", "TN4NQ9GK0", "E27SFGS2W")

    assert client is None
    mock_attach.assert_awaited_once_with(
        "1B551A68-1D9A-4669-9E75-F0F44343F1AA", "TN4NQ9GK0", "E27SFGS2W"
    )


@pytest.mark.asyncio
async def test_get_ray_client_non_ibm_deltaray_also_requires_crm_group_and_verify_team():
    with (
        patch("app.ray.utils.is_ibm_customer_enterprise", return_value=False),
        patch(
            "app.auth.connector.get_ray_client_ibm_by_email",
            new=AsyncMock(),
        ) as mock_ibm,
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(
                return_value={
                    "member_uuid": "member-1",
                    "login": "user@example.com",
                    "email_primary": "user@example.com",
                    "given_name": "Test",
                    "family_name": "User",
                    "slack_team_id": "T123",
                    "active": 1,
                    "groupid": "g1",
                    "is_sso": 0,
                    "access_token": None,
                    "settings_id": None,
                }
            ),
        ),
        patch(
            "app.auth.connector.user_may_attach_workspace_client",
            new=AsyncMock(return_value=False),
        ) as mock_attach,
    ):
        client = await get_ray_client("U123", "T123", "E_OTHER")

    assert client is None
    mock_ibm.assert_not_awaited()
    mock_attach.assert_awaited_once_with("member-1", "T123", "E_OTHER")


@pytest.mark.asyncio
async def test_user_may_attach_workspace_client_true_without_super_group():
    with patch(
        "app.auth.connector.get_ray_super_group",
        new=AsyncMock(return_value=[]),
    ) as mock_sg:
        assert (
            await user_may_attach_workspace_client("member-1", "TN4NQ9GK0", "E27SFGS2W")
            is True
        )
    mock_sg.assert_awaited_once_with("TN4NQ9GK0", "E27SFGS2W")


@pytest.mark.asyncio
async def test_user_may_attach_workspace_client_requires_crm_group_and_verify_team():
    with (
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        id="9ADE9F44-92A4-4EEE-9BCC-96AFEF9B6D36",
                        verify_organization_uuid="41286c93-725e-4fba-9d47-98788488231e",
                    )
                ]
            ),
        ),
        patch(
            "app.auth.connector.fetch_one",
            new=AsyncMock(return_value={"ok": 1}),
        ) as mock_fetch,
    ):
        assert (
            await user_may_attach_workspace_client("member-1", "TN4NQ9GK0", "E27SFGS2W")
            is True
        )

    sql = str(mock_fetch.await_args.args[0])
    assert "obj_m_mglink" in sql
    assert "link.is_active = 1" in sql
    assert "verify_team_user_link" in sql
    assert "vt.organization_uuid" in sql


@pytest.mark.asyncio
async def test_get_ray_client_skips_ibm_email_path_for_straker_dev():
    """Straker Dev is IBM-like for UI but must not auto-resolve / upsert by email."""
    with (
        patch("app.ray.utils.is_ibm_customer_enterprise", return_value=False),
        patch(
            "app.auth.connector.get_ray_client_ibm_by_email",
            new=AsyncMock(),
        ) as mock_ibm,
        patch("app.auth.connector.fetch_one", new=AsyncMock(return_value=None)),
    ):
        client = await get_ray_client("U123", "TGGSMATJ8", "E04RDMG8XP1")

    assert client is None
    mock_ibm.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_ray_client_skips_ibm_email_path_when_not_ibm():
    with (
        patch("app.ray.utils.is_ibm_customer_enterprise", return_value=False),
        patch(
            "app.auth.connector.get_ray_client_ibm_by_email",
            new=AsyncMock(),
        ) as mock_ibm,
        patch("app.auth.connector.fetch_one", new=AsyncMock(return_value=None)),
    ):
        client = await get_ray_client("U123", "T123", "E_OTHER")

    assert client is None
    mock_ibm.assert_not_awaited()


def test_login_message_ibm_without_client_never_shows_provisioned_admin_copy():
    with (
        patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True),
        patch(
            "app.slack.templates.messages.is_ibm_customer_enterprise",
            return_value=True,
        ),
    ):
        msg = LoginMessage(
            user_id="UKVHQ6UJX",
            team_id="T03PE1PGBV5",
            enterprise_id="E04RDMG8XP1",
            channel_id="D04CQDKNLR2",
            ray_client=None,
            variation=LoginMessage.QUALITY_EVALUATION,
        )

    text = msg.blocks[0]["text"]["text"]
    assert "provisioned by your administrator" not in text.lower()
    assert "Connect your account to evaluate" in text
    # Real IBM customer auth failures never show Connect.
    assert not any(b.get("type") == "actions" for b in msg.blocks)


def test_login_message_ibm_customer_ht_hides_connect():
    with (
        patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True),
        patch(
            "app.slack.templates.messages.is_ibm_customer_enterprise",
            return_value=True,
        ),
    ):
        msg = LoginMessage(
            user_id="U123",
            team_id="T03PE1PGBV5",
            enterprise_id="E27SFGS2W",
            channel_id="D123",
            ray_client=None,
            variation=LoginMessage.HUMAN_TRANSLATION,
        )

    text = msg.blocks[0]["text"]["text"]
    assert "does not require a LanguageCloud login" in text
    assert not any(b.get("type") == "actions" for b in msg.blocks)


def test_login_message_ibm_customer_channel_settings_contacts_admin():
    with (
        patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True),
        patch(
            "app.slack.templates.messages.is_ibm_customer_enterprise",
            return_value=True,
        ),
    ):
        msg = LoginMessage(
            user_id="U123",
            team_id="T03PE1PGBV5",
            enterprise_id="E27SFGS2W",
            channel_id="D123",
            ray_client=None,
            variation=LoginMessage.CHANNEL_TRANSLATION_SETTINGS,
        )

    text = msg.blocks[0]["text"]["text"]
    assert "contact an admin" in text
    assert "Connect your account" not in text
    assert not any(b.get("type") == "actions" for b in msg.blocks)


def test_login_message_straker_dev_requires_connect_for_ht():
    with (
        patch("app.slack.templates.messages.is_ibm_enterprise", return_value=True),
        patch(
            "app.slack.templates.messages.is_ibm_customer_enterprise",
            return_value=False,
        ),
    ):
        msg = LoginMessage(
            user_id="U123",
            team_id="TGGSMATJ8",
            enterprise_id="E04RDMG8XP1",
            channel_id="D123",
            ray_client=None,
            variation=LoginMessage.HUMAN_TRANSLATION,
        )

    text = msg.blocks[0]["text"]["text"]
    assert "does not require a LanguageCloud login" not in text
    assert "Connect your account to perform human translation." in text
    assert any(b.get("type") == "actions" for b in msg.blocks)


def test_login_message_non_ibm_channel_settings_keeps_connect():
    with (
        patch("app.slack.templates.messages.is_ibm_enterprise", return_value=False),
        patch(
            "app.slack.templates.messages.is_ibm_customer_enterprise",
            return_value=False,
        ),
    ):
        msg = LoginMessage(
            user_id="U123",
            team_id="T123",
            enterprise_id="E123",
            channel_id="C123",
            ray_client=None,
            variation=LoginMessage.CHANNEL_TRANSLATION_SETTINGS,
        )

    assert (
        "Connect your account to manage channel translation settings."
        in (msg.blocks[0]["text"]["text"])
    )
    assert any(b.get("type") == "actions" for b in msg.blocks)
    assert "Connect account" in str(msg.blocks)

"""IBM Slack email → CRM member resolution (RAY-81247)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.connector import (
    ensure_active_ibm_deltaray_link,
    get_active_crm_member_by_email,
    get_ray_client,
    get_ray_client_ibm_by_email,
    resolve_slack_user_email,
)
from app.slack.templates.messages import LoginMessage


@pytest.mark.asyncio
async def test_resolve_slack_user_email_prefers_cached_details():
    with (
        patch(
            "app.auth.connector._slack_email_from_details",
            new=AsyncMock(return_value="wade.norman@strakergroup.com"),
        ),
        patch(
            "app.auth.connector._slack_email_from_api",
            new=AsyncMock(return_value="other@example.com"),
        ) as mock_api,
    ):
        email = await resolve_slack_user_email(
            "UKVHQ6UJX", "T03PE1PGBV5", "E04RDMG8XP1"
        )

    assert email == "wade.norman@strakergroup.com"
    mock_api.assert_not_awaited()


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
            "app.auth.connector.resolve_slack_user_email",
            new=AsyncMock(return_value="wade.norman@strakergroup.com"),
        ),
        patch(
            "app.auth.connector.get_active_crm_member_by_email",
            new=AsyncMock(return_value=member),
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
    # Non-HT IBM customer flows still show Connect when no CRM member.
    assert any(b.get("type") == "actions" for b in msg.blocks)


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

"""Direct Login reactivation for inactive LC members / mglinks (RAY-80562, RAY-81311)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.auth.connector import (
    add_client_to_slack_group,
    connect_ray_account_sso,
    reactivate_member_for_direct_login,
)


@pytest.mark.asyncio
async def test_reactivate_member_for_direct_login_updates_inactive_row():
    mock_execute = AsyncMock()
    with patch("app.auth.connector.execute", new=mock_execute):
        await reactivate_member_for_direct_login("MEMBER-UUID")

    assert mock_execute.await_count == 1
    sql, engine = mock_execute.await_args.args[:2]
    assert "UPDATE obj_m_member" in str(sql)
    assert "active = 1" in str(sql)
    assert "is_deleted = 0" in str(sql)
    assert engine is not None
    assert mock_execute.await_args.kwargs["commit_after"] is True


@pytest.mark.asyncio
async def test_connect_ray_account_sso_reactivates_existing_member():
    member_id = "1F1416EE-70DA-4632-A199-9D3E060537D3"
    result_row = SimpleNamespace(obj_uuid=member_id)
    result1 = MagicMock()
    result1.rowcount = 1
    result1.first.return_value = result_row

    mock_conn = MagicMock()
    mock_conn.execute.return_value = result1
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn

    with (
        patch("app.auth.connector.engines", {"sitemanager": mock_engine}),
        patch(
            "app.auth.connector.reactivate_member_for_direct_login",
            new=AsyncMock(),
        ) as mock_reactivate,
        patch(
            "app.auth.connector.create_client_access_tokens",
            new=AsyncMock(),
        ) as mock_tokens,
        patch(
            "app.auth.connector.create_slack_deltaray_link_sso",
            new=AsyncMock(),
        ) as mock_link,
        patch(
            "app.auth.connector.add_client_to_slack_group",
            new=AsyncMock(),
        ) as mock_group,
        patch(
            "app.auth.connector.add_to_verify_team",
            new=AsyncMock(),
        ) as mock_team,
    ):
        returned = await connect_ray_account_sso(
            user_id="U0863T834EA",
            team_id="TL1E0HR4M",
            email_id="Aftab.Dange@ibm.com",
            first_name="Aftab",
            last_name="Dange",
            channel_id="D08DC638PS8",
            enterprise_id="E27SFGS2W",
        )

    assert returned == member_id
    mock_reactivate.assert_awaited_once_with(member_id)
    mock_tokens.assert_awaited_once()
    mock_link.assert_awaited_once()
    mock_group.assert_awaited_once()
    mock_team.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_ray_account_sso_new_member_raises_lookup_error():
    """RAY-81247: Slack must not mint new CRM People via Direct Login."""
    result1 = MagicMock()
    result1.rowcount = 0

    mock_conn = MagicMock()
    mock_conn.execute.return_value = result1
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.__exit__.return_value = False

    mock_engine = MagicMock()
    mock_engine.connect.return_value = mock_conn

    with patch("app.auth.connector.engines", {"sitemanager": mock_engine}):
        with pytest.raises(LookupError, match="No LanguageCloud account found"):
            await connect_ray_account_sso(
                user_id="UNEW",
                team_id="TNEW",
                email_id="new.user@ibm.com",
                first_name="New",
                last_name="User",
                channel_id="DNEW",
                enterprise_id="E27SFGS2W",
            )


@pytest.mark.asyncio
async def test_add_client_to_slack_group_reactivates_inactive_mglink():
    """RAY-81311: existing inactive IBM Slack App mglink must be set is_active=1."""
    mglink_uuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
    mock_fetch = AsyncMock(
        return_value={"obj_uuid": mglink_uuid, "is_active": 0},
    )
    mock_execute = AsyncMock()

    with (
        patch("app.auth.connector.fetch_one", new=mock_fetch),
        patch("app.auth.connector.execute", new=mock_execute),
        patch(
            "app.auth.connector.get_direct_login_group",
            return_value="07DA6A86-D635-4383-9598-724D368EF1C3",
        ),
    ):
        await add_client_to_slack_group(
            user_data={"enterprise_id": "E27SFGS2W"},
            member_id="MEMBER-UUID",
        )

    assert mock_execute.await_count == 2
    reactivate_sql = str(mock_execute.await_args_list[0].args[0])
    assert "UPDATE obj_m_mglink" in reactivate_sql
    assert "is_active = 1" in reactivate_sql
    assert "INSERT INTO obj_m_mglink" not in reactivate_sql
    member_sql = str(mock_execute.await_args_list[1].args[0])
    assert "UPDATE obj_m_member SET groupid" in member_sql


@pytest.mark.asyncio
async def test_add_client_to_slack_group_skips_reactivate_when_mglink_active():
    mock_fetch = AsyncMock(
        return_value={
            "obj_uuid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
            "is_active": 1,
        },
    )
    mock_execute = AsyncMock()

    with (
        patch("app.auth.connector.fetch_one", new=mock_fetch),
        patch("app.auth.connector.execute", new=mock_execute),
        patch(
            "app.auth.connector.get_direct_login_group",
            return_value="07DA6A86-D635-4383-9598-724D368EF1C3",
        ),
    ):
        await add_client_to_slack_group(
            user_data={"enterprise_id": "E27SFGS2W"},
            member_id="MEMBER-UUID",
        )

    assert mock_execute.await_count == 1
    member_sql = str(mock_execute.await_args.args[0])
    assert "UPDATE obj_m_member SET groupid" in member_sql
    assert "UPDATE obj_m_mglink" not in member_sql


@pytest.mark.asyncio
async def test_add_client_to_slack_group_inserts_when_no_mglink():
    mock_fetch = AsyncMock(return_value=None)
    mock_execute = AsyncMock()

    with (
        patch("app.auth.connector.fetch_one", new=mock_fetch),
        patch("app.auth.connector.execute", new=mock_execute),
        patch(
            "app.auth.connector.get_direct_login_group",
            return_value="07DA6A86-D635-4383-9598-724D368EF1C3",
        ),
        patch(
            "app.auth.connector.uuid4",
            return_value=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        ),
    ):
        await add_client_to_slack_group(
            user_data={"enterprise_id": "E27SFGS2W"},
            member_id="MEMBER-UUID",
        )

    assert mock_execute.await_count == 2
    insert_sql = str(mock_execute.await_args_list[0].args[0])
    assert "INSERT INTO obj_m_mglink" in insert_sql
    assert "is_active" in insert_sql

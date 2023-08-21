"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import asyncio
import time
import json
from uuid import uuid4
from dataclasses import dataclass
from urllib.parse import urlencode
from sqlalchemy import text
from sqlalchemy.engine import Connection
import httpx

from .algorithms import encrypt_aes, hash_hmac_sha1
from ..config import config, domains
from ..database import engines


@dataclass(frozen=True, slots=True)
class SlackUser:
    """Dataclass representing a Slack user."""

    user_id: str
    team_id: str
    enterprise_id: str | None
    channel_id: str
    is_subscribed: bool
    bot_token: str
    ray_client_id: str
    ray_username: str


@dataclass(frozen=True, slots=True)
class RaySuperGroup:
    """Dataclass representing a LanguageCloud super group."""

    id: str
    """The RAY group UUID (`obj_m_group.obj_uuid`)."""
    name: str
    """The name of the group (`obj_m_group.label`)."""
    slack_team_id: str
    """The Slack team ID linked to the RAY client."""
    slack_enterprise_id: str | None
    """The Slack enterprise ID linked to the RAY client."""


@dataclass(frozen=True, slots=True)
class RayClient:
    """Dataclass representing a LanguageCloud client."""

    id: str
    """The RAY client UUID (`obj_m_member.obj_uuid`)."""
    username: str
    """The RAY client username (`obj_m_member.login`)."""
    access_token: str
    """The API token linked to the client."""
    slack_user_id: str
    """The Slack user ID."""
    slack_team_id: str
    """The Slack team ID."""
    slack_enterprise_id: str | None
    """The Slack enterprise ID."""


@dataclass(frozen=True, slots=True)
class RayConnection:
    """Dataclass representing a connection between a Slack user and a LanguageCloud
    client. This also includes the connection between the Slack workspace and
    the LanguageCloud group.
    """

    super_group: RaySuperGroup
    client: RayClient | None


def validate_queue_proxy_secret(secret: str) -> bool:
    """Validates an integration secret key for the queue proxy app.

    Args:
        secret (str): The secret key to validate.

    Returns:
        bool: The validation result.
    """
    return secret == config.slack_queue_proxy_secret


def validate_api_callback_signature(
    payload: bytes, api_token: str, signature: str
) -> bool:
    """Validate the HTTP callback request for when a job which was created in
    Slack is updated.
    https://help.strakertranslations.com/hc/en-us/articles/115004089033-Webhooks

    Args:
        payload (bytes): The raw request body.
        api_token (str): The client's API token
        signature (str): The hashed value in the `X-Straker-Signature` header.

    Returns:
        bool: The validation result.
    """
    hash = hash_hmac_sha1(payload, api_token.encode())
    return signature == hash


def get_bot_token(
    conn: Connection,
    team_id: str,
    enterprise_id: str | None = None,
) -> str | None:
    """Gets the Slack bot token for a workspace."""
    if enterprise_id:
        sql = text(
            """
            SELECT bot_token FROM slack_bots
            WHERE enterprise_id = :enterprise_id
            ORDER BY id DESC
            LIMIT 1
            """
        ).bindparams(enterprise_id=enterprise_id)
    else:
        sql = text(
            """
            SELECT bot_token FROM slack_bots
            WHERE team_id = :team_id
            ORDER BY id DESC
            LIMIT 1
            """
        ).bindparams(team_id=team_id)
    result = conn.execute(sql).first()
    return result[0] if result else None


def get_slack_user(ray_client_id: str) -> SlackUser | None:
    """Gets the Slack user connected to a RAY client.

    Args:
        ray_client_id (str): The LanguageCloud user ID.
    """
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.slack_user_id,link.slack_team_id,link.slack_enterprise_id,
                link.slack_channel_id,link.is_subscribed,mem.login
            FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            WHERE link.member_uuid = :member_uuid
            AND link.is_active = 1
            AND mem.active = 1
            AND mem.is_deleted = 0
            LIMIT 1
            """
        ).bindparams(member_uuid=ray_client_id)
        result = conn.execute(sql)
        row = result.first()
        if row:
            bot_token = get_bot_token(
                conn, team_id=row.slack_team_id, enterprise_id=row.slack_enterprise_id
            )
            if bot_token:
                return SlackUser(
                    user_id=row.slack_user_id,
                    team_id=row.slack_team_id,
                    enterprise_id=row.slack_enterprise_id,
                    channel_id=row.slack_channel_id,
                    is_subscribed=bool(row.is_subscribed),
                    bot_token=bot_token,
                    ray_client_id=ray_client_id,
                    ray_username=row.login,
                )
    return None


def get_demo_link(member_uuid: str) -> list[str]:
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT member_uuid
            FROM slack_demo_users
            WHERE member_uuid = :member_uuid
            """
        ).bindparams(member_uuid=member_uuid)
        result = conn.execute(sql)
        row = result.first()
        if row:
            sql = text(
                """
                SELECT link.slack_user_id
                FROM slack_demo_link link
                """
            )
            result = conn.execute(sql)
            slack_user_ids = [row[0] for row in result]
            return slack_user_ids
        return []


def get_client_access_tokens(ray_client_id: str) -> tuple[str]:
    """Gets all the active API access tokens of a RAY client."""
    with engines["api_readonly"].connect() as conn:
        sql = text(
            """
            SELECT obj_uuid FROM access_token
            WHERE account_id = :client_id
            AND active = 1
            """
        ).bindparams(client_id=ray_client_id)
        result = conn.execute(sql)
        rows = result.all()
    return tuple(row[0] for row in rows)


async def get_demo_super_group(
    team_id: str, enterprise_id: str
) -> RaySuperGroup | None:
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.super_group_uuid, g.label
            FROM slack_super_group_link link
            INNER JOIN sitemanager.obj_m_group g
            ON link.super_group_uuid = g.obj_uuid
            INNER JOIN slack_deltaray_link dlink
            ON dlink.slack_enterprise_id = link.slack_enterprise_id
            INNER JOIN slack_demo_users dmem on dmem.member_uuid = dlink.member_uuid
            WHERE link.is_active = 1
            AND link.slack_enterprise_id = :enterprise_id
            LIMIT 1
            """
        ).bindparams(enterprise_id=enterprise_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
    return RaySuperGroup(
        id=row.super_group_uuid,
        name=row.label,
        slack_team_id=team_id,
        slack_enterprise_id=enterprise_id,
    )


async def get_ray_super_group(
    team_id: str, enterprise_id: str | None = None
) -> list[RaySuperGroup] | None:
    """Gets the LanguageCloud super group linked to the Slack workspace if an active
    link exists, otherwise returns None.

    Args:
        team_id (str): The ID of the team.
    """
    with engines["ray_integration_readonly"].connect() as conn:
        if enterprise_id:
            sql = text(
                """
                SELECT link.super_group_uuid, g.label
                FROM slack_super_group_link link
                INNER JOIN sitemanager.obj_m_group g
                ON link.super_group_uuid = g.obj_uuid
                WHERE link.slack_enterprise_id = :enterprise_id
                AND link.is_active = 1
                """
            ).bindparams(enterprise_id=enterprise_id)
        else:
            sql = text(
                """
                SELECT link.super_group_uuid, g.label
                FROM slack_super_group_link link
                INNER JOIN sitemanager.obj_m_group g
                ON link.super_group_uuid = g.obj_uuid
                WHERE link.slack_team_id = :team_id
                AND link.is_active = 1
                """
            ).bindparams(team_id=team_id)
        result = conn.execute(sql)
        rows = result.fetchall()
        if not rows:
            return None
    return [
        RaySuperGroup(
            id=row.super_group_uuid,
            name=row.label,
            slack_team_id=team_id,
            slack_enterprise_id=enterprise_id,
        )
        for row in rows
    ]


async def get_ray_demo_client(
    user_id: str, team_id: str, slack_enterprise_id: str
) -> RayClient | None:
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
                SELECT id
                FROM slack_demo_link link
                WHERE slack_user_id = :user_id
                """
        ).bindparams(user_id=user_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.member_uuid, mem.login, link.slack_enterprise_id
            FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            INNER JOIN slack_demo_users dmem
            ON dmem.member_uuid = link.member_uuid
            WHERE mem.active = 1
            AND link.slack_enterprise_id = :slack_enterprise_id
            AND mem.is_deleted = 0
            LIMIT 1
            """
        ).bindparams(slack_enterprise_id=slack_enterprise_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
        ray_client_id, username, slack_enterprise_id = (
            row.member_uuid,
            row.login,
            row.slack_enterprise_id,
        )
    # Now get the access token for authentication.
    with engines["api_readonly"].connect() as conn:
        sql = text(
            """
            SELECT obj_uuid FROM access_token
            WHERE account_id = :client_id
            AND active = 1
            LIMIT 1
            """
        ).bindparams(client_id=ray_client_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
        access_token = row[0]
    return RayClient(
        id=ray_client_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=slack_enterprise_id,
    )


async def get_ray_client(
    user_id: str, team_id: str, enterprise_id: str | None = None
) -> RayClient | None:
    """Gets the LanguageCloud client id and username linked to the Slack account if an active link
    exists, otherwise returns None.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
    """
    # First find the client details.
    with engines["ray_integration_readonly"].connect() as conn:
        if enterprise_id:
            sql = text(
                """
                SELECT link.member_uuid, mem.login
                FROM slack_deltaray_link link
                INNER JOIN sitemanager.obj_m_member mem
                ON link.member_uuid = mem.obj_uuid
                WHERE link.slack_user_id = :user_id
                AND link.slack_enterprise_id = :enterprise_id
                AND link.is_active = 1
                AND mem.active = 1
                AND mem.is_deleted = 0
                LIMIT 1
                """
            ).bindparams(user_id=user_id, enterprise_id=enterprise_id)
        else:
            sql = text(
                """
                SELECT link.member_uuid, mem.login
                FROM slack_deltaray_link link
                INNER JOIN sitemanager.obj_m_member mem
                ON link.member_uuid = mem.obj_uuid
                WHERE link.slack_user_id = :user_id
                AND link.slack_team_id = :team_id
                AND link.is_active = 1
                AND mem.active = 1
                AND mem.is_deleted = 0
                LIMIT 1
                """
            ).bindparams(user_id=user_id, team_id=team_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
        ray_client_id, username = row.member_uuid, row.login
    # Now get the access token for authentication.
    with engines["api_readonly"].connect() as conn:
        sql = text(
            """
            SELECT obj_uuid FROM access_token
            WHERE account_id = :client_id
            AND active = 1
            LIMIT 1
            """
        ).bindparams(client_id=ray_client_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
        access_token = row[0]
    return RayClient(
        id=ray_client_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=enterprise_id,
    )


async def get_ray_connection(
    user_id: str, team_id: str, enterprise_id: str | None
) -> RayConnection | None:
    """Gets the LanguageCloud super group and client linked to the Slack workspace
    and user. If the Slack workspace is not linked, ignore the Slack user link.
    A Slack workspace can have a connection without a Slack user connection.
    """
    super_group, client = await asyncio.gather(
        get_ray_super_group(team_id, enterprise_id),
        get_ray_client(user_id, team_id, enterprise_id),
    )
    if super_group is None:
        return None
    return RayConnection(super_group, client)


async def get_ray_connection_demo(
    user_id: str, team_id: str, enterprise_id: str | None
) -> RayConnection | None:
    """Gets the LanguageCloud super group and client linked to the Slack workspace
    and user. If the Slack workspace is not linked, ignore the Slack user link.
    A Slack workspace can have a connection without a Slack user connection.
    """
    super_group, client = await asyncio.gather(
        get_demo_super_group(team_id, enterprise_id),
        get_ray_demo_client(user_id, team_id, enterprise_id),
    )
    if super_group is None:
        return None
    return RayConnection(super_group, client)


def get_group_admin_slack_users(group_id: str) -> list[SlackUser]:
    """Gets the Slack users of the admins of a LanguageCloud group."""
    with engines["sitemanager_readonly"].connect() as conn:
        sql = text(
            """
            SELECT slack.slack_user_id, slack.slack_team_id, slack.slack_enterprise_id,
                slack.slack_channel_id, slack.is_subscribed, bots.bot_token,
                mem.obj_uuid, mem.login
            FROM obj_m_mglink link
            INNER JOIN obj_m_member mem
            ON link.memberid = mem.obj_uuid
            INNER JOIN ray_integration.slack_deltaray_link slack
            ON link.memberid = slack.member_uuid
            INNER JOIN ray_integration.slack_bots bots
            ON bots.id = (
                SELECT id FROM ray_integration.slack_bots bots2
                WHERE bots2.team_id = slack.slack_team_id
                OR bots2.enterprise_id = slack.slack_enterprise_id
                ORDER BY id DESC LIMIT 1
            )
            WHERE link.groupid = :group_id
            AND link.client_type IN ('Admin', 'Owner')
            AND mem.active = 1
            AND mem.is_deleted = 0
            AND slack.is_active = 1
            GROUP BY link.memberid
            """
        ).bindparams(group_id=group_id)
        result = conn.execute(sql)

    slack_users = []
    for row in result:
        slack_users.append(
            SlackUser(
                user_id=row.slack_user_id,
                team_id=row.slack_team_id,
                enterprise_id=row.slack_enterprise_id,
                channel_id=row.slack_channel_id,
                is_subscribed=bool(row.is_subscribed),
                bot_token=row.bot_token,
                ray_client_id=row.obj_uuid,
                ray_username=row.login,
            )
        )
    return slack_users


def disconnect_ray_account(
    user_id: str, team_id: str, enterprise_id: str | None = None
) -> bool:
    """Disconnect the LanguageCloud account of a slack user.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str): The Slack enterprise ID.

    Returns:
        bool: The Slack user had a connected LanguageCloud account.
    """
    with engines["ray_integration"].begin() as conn:
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE slack_user_id = :user_id
            AND (
                slack_team_id = :team_id
                OR slack_enterprise_id = :enterprise_id
            )
            AND is_active = 1
            """
        ).bindparams(user_id=user_id, team_id=team_id, enterprise_id=enterprise_id)
        result = conn.execute(sql)
    return result.rowcount > 0


def disconnect_ray_super_group_and_users(
    team_id: str, enterprise_id: str | None = None
) -> bool:
    """Disconnect the LanguageCloud super group and all connected users
    of a Slack Workspace.

    Args:
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.

    Returns:
        bool: An active Slack-LanguageCloud connection was deactivated.
    """
    with engines["ray_integration"].begin() as conn:
        sql = text(
            """
            UPDATE slack_super_group_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE (
                slack_team_id = :team_id
                OR slack_enterprise_id = :enterprise_id
            )
            AND is_active = 1
            """
        ).bindparams(team_id=team_id, enterprise_id=enterprise_id)
        result1 = conn.execute(sql)
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE (
                slack_team_id = :team_id
                OR slack_enterprise_id = :enterprise_id
            )
            AND is_active = 1
            """
        ).bindparams(team_id=team_id, enterprise_id=enterprise_id)
        result2 = conn.execute(sql)
    return result1.rowcount > 0 or result2.rowcount > 0


def encrpyt_slack_integration_token(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    expire_seconds: int = 7200,
) -> str:
    """Generates time-sensitive token to allow the Slack app to communicate
    with the RAY platform securely.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 7200.

    Returns:
        str: The encrypted token.
    """
    epoch = int(time.time())
    data = {
        "userId": user_id,
        "teamId": team_id,
        "enterpriseId": enterprise_id or None,
        "channelId": channel_id,
        "created": epoch,
        "expires": epoch + expire_seconds,
    }

    return encrypt_aes(json.dumps(data), config.slack_deltaray_key)


def get_language_cloud_connect_url(
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    expire_seconds: int = 7200,
) -> str:
    """Generates a URL for a user to connect their Slack account to their
    LanguageCloud account.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 7200.

    Returns:
        str: The URL to connect a user's Slack account and LanguageCloud account.
    """
    params = {
        "token": encrpyt_slack_integration_token(
            user_id, team_id, enterprise_id, channel_id, expire_seconds
        )
    }
    return f"{domains.languagecloud}/app/slack?{urlencode(params)}"


async def approve_pending_groups(
    admin_client_id: str, pending_client_id: str, pending_client_username: str
) -> tuple[str]:
    """Approve the pending groups of a new client that the client is an admin of.
    This function will be moved to a REST API in the future.
    """
    with engines["sitemanager_readonly"].connect() as conn:
        # First get the groups that the client is an admin of.
        sql = text(
            """
            SELECT DISTINCT groupid
            FROM obj_m_mglink
            WHERE memberid = :client_id
            AND client_type IN ('Admin', 'Owner')
            """
        ).bindparams(client_id=admin_client_id)
        result = conn.execute(sql)
        admin_groups = [row[0] for row in result]
        if not admin_groups:
            return tuple()
        # Then get the new client's pending groups.
        sql = text(
            """
            SELECT g.obj_uuid, g.label
            FROM obj_m_group_pending p
            JOIN obj_m_group g
            ON p.group_uuid = g.obj_uuid
            WHERE p.member_uuid = :client_id
            AND p.is_active = 1
            AND p.is_approved = 0
            GROUP BY g.obj_uuid
            """
        ).bindparams(client_id=pending_client_id)
        result = conn.execute(sql)
        pending_groups = [{"uuid": row.obj_uuid, "label": row.label} for row in result]
    # Approve the intersection of these groups.
    groups_to_approve = [g for g in pending_groups if g["uuid"] in admin_groups]
    if groups_to_approve:
        with engines["sitemanager"].begin() as conn:
            for group in groups_to_approve:
                sql = text(
                    """
                    INSERT INTO obj_m_mglink
                        (obj_uuid, groupid, memberid, label, client_type, created, modified)
                    VALUES
                        (:uuid, :group_id, :client_id, :label, :client_type, NOW(), NOW())
                    """
                ).bindparams(
                    uuid=str(uuid4()).upper(),
                    group_id=group["uuid"],
                    client_id=pending_client_id,
                    label=f"{pending_client_id}-{group['uuid']}",
                    client_type="Normal",
                )
                conn.execute(sql)
            sql = text(
                """
                UPDATE obj_m_group_pending
                SET is_active = 0, is_approved = 1
                WHERE member_uuid = :client_id
                AND group_uuid IN :groups
                AND is_active = 1
                """
            ).bindparams(
                client_id=pending_client_id,
                groups=tuple(g["uuid"] for g in groups_to_approve),
            )
            conn.execute(sql)
        # Publish client approved event (this is hard-coded for now).
        async with httpx.AsyncClient() as http:
            await http.post(
                f"{domains.stream_proxy}/events/ray:client:approved",
                json={
                    "data": {
                        "client_id": pending_client_id,
                        "username": pending_client_username,
                        "groups": groups_to_approve,
                        "approver": {"client_id": admin_client_id},
                    },
                    "source": "Straker Translate for Slack",
                },
            )
    return tuple(g["uuid"] for g in groups_to_approve)

"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import asyncio
import math
import time
import json
import hashlib
from uuid import uuid4
from dataclasses import dataclass
from urllib.parse import urlencode
import uuid

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection
from slack_sdk.oauth.installation_store import Installation
from straker_auth.languagecloud import create_languagecloud_id_token
from buglog import notify_exception

from .algorithms import encrypt_aes, hash_hmac_sha1
from ..config import config, domains, Environment
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
    ray_user_group_id: str | None = None


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
class GetCreditBalanceResponse:
    ai_token: int
    mt_token: int


@dataclass(slots=True)
class RayClient:
    """Dataclass representing a LanguageCloud client."""

    id: str
    """The RAY client UUID (`obj_m_member.obj_uuid`)."""
    username: str
    """The RAY client username (`obj_m_member.login`)."""
    user_group_id: str
    """The RAY client default group UUID (`obj_m_member.groupid`)."""
    access_token: str
    """The API token linked to the client."""
    slack_user_id: str
    """The Slack user ID."""
    slack_team_id: str
    """The Slack team ID."""
    slack_enterprise_id: str | None
    """The Slack enterprise ID."""
    slack_access_token: str | None
    """The user's Slack access token."""
    settings_id: int | None
    """The user's Slack settings ID (`ray_integration.slack_user_settings.id`)."""
    id_token: str | None
    """an ID Token according to the OpenID Connect spec"""
    planname: str | None
    """The Slack enterprise ID."""
    sso: str | None
    """The SSO flag."""


@dataclass(frozen=True, slots=True)
class RayConnection:
    """Dataclass representing a connection between a Slack user and a LanguageCloud
    client. This also includes the connection between the Slack workspace and
    the LanguageCloud group.
    """

    super_group: list[RaySuperGroup]
    client: RayClient | None


def validate_queue_proxy_secret(secret: str) -> bool:
    """Validates an integration secret key for the queue proxy app.

    Args:
        secret (str): The secret key to validate.

    Returns:
        bool: The validation result.
    """
    return secret == config.slack_queue_proxy_secret.get_secret_value()


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
            AND team_id = :team_id
            ORDER BY id DESC
            LIMIT 1
            """
        ).bindparams(enterprise_id=enterprise_id, team_id=team_id)
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


async def save_user_token_from_installation(
    installation: Installation,
) -> RayClient | None:
    """Saves the Slack user token to a connected LanguageCloud client after a
    successfull Slack app installation. Does nothing if the Slack user is not
    connected to a LanguageCloud account.

    Args:
        installation (Installation): The Slack installation object.

    Returns:
        RayClient | None: The LC client if the Slack user has a connected LC
            account, otherwise `None`.
    """
    user = await get_ray_client(
        installation.user_id, installation.team_id, installation.enterprise_id
    )
    if not user:
        return None
    if not installation.user_token or not installation.team_id:
        return user

    scopes_string = (
        ",".join(installation.user_scopes) if installation.user_scopes else None
    )
    with engines["ray_integration"].begin() as conn:
        if installation.enterprise_id:
            sql = text(
                """
                UPDATE slack_deltaray_link SET
                    slack_team_id = :team_id,
                    access_token = :access_token,
                    access_token_scopes = :access_token_scopes
                WHERE slack_user_id = :user_id
                AND slack_enterprise_id = :enterprise_id
                """
            ).bindparams(
                user_id=installation.user_id,
                team_id=installation.team_id,
                enterprise_id=installation.enterprise_id,
                access_token=installation.user_token,
                access_token_scopes=scopes_string,
            )
        else:
            sql = text(
                """
                UPDATE slack_deltaray_link SET
                    access_token = :access_token,
                    access_token_scopes = :access_token_scopes
                WHERE slack_user_id = :user_id
                AND slack_team_id = :team_id
                """
            ).bindparams(
                user_id=installation.user_id,
                team_id=installation.team_id,
                access_token=installation.user_token,
                access_token_scopes=scopes_string,
            )
        conn.execute(sql)
    return RayClient(
        id=user.id,
        username=user.username,
        access_token=user.access_token,
        slack_user_id=user.slack_user_id,
        slack_team_id=user.slack_team_id,
        slack_enterprise_id=user.slack_enterprise_id,
        slack_access_token=installation.user_token,
        settings_id=user.settings_id,
        id_token=user.id_token,
        planname=user.planname,
        sso=user.sso,
    )


def get_slack_user(ray_client_id: str) -> SlackUser | None:
    """Gets the Slack user connected to a RAY client.

    Args:
        ray_client_id (str): The LanguageCloud user ID.
    """
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT link.slack_user_id,link.slack_team_id,link.slack_enterprise_id,
                link.slack_channel_id,link.is_subscribed,mem.login,mem.groupid
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
                    ray_user_group_id=row.groupid,
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
) -> list[RaySuperGroup] | None:
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
    return [
        RaySuperGroup(
            id=row.super_group_uuid,
            name=row.label,
            slack_team_id=team_id,
            slack_enterprise_id=enterprise_id,
        )
    ]


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


def is_ibm_super_group(
    enterprise_id: str | None = None,
) -> bool:
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
                AND (
                    link.super_group_uuid = '9ADE9F44-92A4-4EEE-9BCC-96AFEF9B6D36'
                    OR link.super_group_uuid = '13D8D894-3DC5-49DC-9DD0-AD9EA537E597'
                )
                """
            ).bindparams(enterprise_id=enterprise_id)
        result = conn.execute(sql)
        rows = result.fetchall()
        if not rows:
            return False
    return True


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
            SELECT link.member_uuid, link.slack_enterprise_id, mem.login, mem.email_primary, mem.given_name, mem.family_name,
            mem.active, mem.groupid, link.access_token, settings.id AS settings_id
            FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            INNER JOIN slack_demo_users dmem
            ON dmem.member_uuid = link.member_uuid
            LEFT JOIN slack_user_settings settings
            ON link.member_uuid = settings.member_uuid
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
        (
            ray_client_id,
            user_group_id,
            username,
            slack_enterprise_id,
            slack_access_token,
            settings_id,
        ) = (
            row.member_uuid,
            row.groupid,
            row.login,
            row.slack_enterprise_id,
            row.access_token,
            row.settings_id,
        )
        id_token = create_languagecloud_id_token(
            uuid=ray_client_id,
            given_name=row.given_name,
            family_name=row.family_name,
            email=row.email_primary,
            is_active=bool(row.active),
            aud="languagecloud-api",
            secret=config.languagecloud_api_key.get_secret_value(),
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
        user_group_id=user_group_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=slack_enterprise_id,
        slack_access_token=slack_access_token,
        settings_id=settings_id,
        id_token=id_token,
        planname="Enterprise",
        sso=False,
    )


async def get_ray_client(
    user_id: str, team_id: str, enterprise_id: str | None = None
) -> RayClient | None:
    """Gets the LanguageCloud client id and username linked to the Slack account if an active
    link exists, otherwise returns None.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str | None): The Slack enterprise ID.
    """
    # First find the client details.
    with engines["ray_integration"].connect() as conn:
        if enterprise_id:
            sql = text(
                """
                SELECT link.member_uuid, mem.login, mem.email_primary, mem.given_name, mem.family_name,
                mem.active, mem.groupid, link.is_sso, link.access_token, settings.id AS settings_id
                FROM slack_deltaray_link link
                INNER JOIN sitemanager.obj_m_member mem
                ON link.member_uuid = mem.obj_uuid
                LEFT JOIN slack_user_settings settings
                ON link.member_uuid = settings.member_uuid
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
                SELECT link.member_uuid, mem.login, mem.email_primary, mem.given_name, mem.family_name,
                mem.active, mem.groupid, link.is_sso, link.access_token, settings.id AS settings_id
                FROM slack_deltaray_link link
                INNER JOIN sitemanager.obj_m_member mem
                ON link.member_uuid = mem.obj_uuid
                LEFT JOIN slack_user_settings settings
                ON link.member_uuid = settings.member_uuid
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
        id_token = create_languagecloud_id_token(
            uuid=row.member_uuid,
            given_name=row.given_name,
            family_name=row.family_name,
            email=row.email_primary,
            is_active=bool(row.active),
            aud="languagecloud-api",
            secret=config.languagecloud_api_key.get_secret_value(),
        )
        (
            ray_client_id,
            user_group_id,
            username,
            groupid,
            is_sso,
            slack_access_token,
            settings_id,
        ) = (
            row.member_uuid,
            row.groupid,
            row.login,
            row.groupid,
            row.is_sso,
            row.access_token,
            row.settings_id,
        )
    # Now get the access token for authentication.
    with engines["api"].connect() as conn:
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

    # TODO Fix this, sometimes the plan is incorrect.
    # get group subscription plan
    with engines["sitemanager_readonly"].connect() as conn:
        sql = text(
            """
                    SELECT psp.plan_name
                    FROM ps_service ps
                    LEFT JOIN ps_plan psp
                    ON psp.service_type_uuid = ps.service_type_uuid
                    LEFT JOIN ps_subscription pss
                    ON pss.ps_service_uuid = ps.obj_uuid
                    LEFT JOIN ps_subscription_billing psb
                    ON psb.ps_subscription_uuid = pss.obj_uuid
                    WHERE ps.group_uuid = :group_uuid
                    AND pss.is_active = 1
                    AND psb.expiry > NOW()
                    """
        ).bindparams(group_uuid=groupid)
        result = conn.execute(sql)
        row = result.fetchall()
        if not row:
            plan = "Free"
        else:
            plan = row[0].plan_name
    return RayClient(
        id=ray_client_id,
        user_group_id=user_group_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_enterprise_id=enterprise_id,
        slack_access_token=slack_access_token,
        settings_id=settings_id,
        id_token=id_token,
        planname=plan,
        sso=is_sso,
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


async def connect_ray_account(
    user_id: str,
    team_id: str,
    enterprise_id: str | None = None,
    channel_id: str = None,
) -> str:
    """Connect the LanguageCloud account of a slack user.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        enterprise_id (str): The Slack enterprise ID.

    Returns:
        bool: The Slack user had a connected LanguageCloud account.
    """

    try:
        url = get_language_cloud_connect_url(
            user_id, team_id, enterprise_id, channel_id
        )
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(url)
        return "success"
    except Exception as e:
        notify_exception(e)
        return "failed"


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
        "clientId": config.slack_client_id,
        "created": epoch,
        "expires": epoch + expire_seconds,
    }

    return encrypt_aes(json.dumps(data), config.slack_deltaray_key.get_secret_value())


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
) -> tuple[str, ...]:
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


# log new user info
async def log_new_user_info(user):
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            INSERT IGNORE INTO slack_user_details
                (slack_user_id, slack_team_id, slack_enterprise_id, slack_enterprise_name, timezone, timezone_label, client_email, client_full_name)
            VALUES
                (:user_id, :team_id, :enterprise_id, :enterprise_name, :timezone, :timezone_label, :client_email, :client_full_name)
            """
        ).bindparams(
            user_id=user.get("id", ""),
            team_id=user.get("team_id", ""),
            enterprise_id=user.get("enterprise_user", {}).get("enterprise_id", ""),
            enterprise_name=user.get("enterprise_user", {}).get("enterprise_name", ""),
            timezone=user.get("tz", ""),
            timezone_label=user.get("tz_label", ""),
            client_email=user.get("profile", {}).get("email", ""),
            client_full_name=user.get("profile", {}).get("real_name_normalized", ""),
        )
        conn.execute(sql)
        conn.commit()


def connect_ray_account_sso(
    user_id: str,
    team_id: str,
    email_id: str,
    first_name: str,
    last_name: str,
    channel_id: str,
    enterprise_id: str | None,
) -> str:
    """Connect the LanguageCloud account of a slack user via SSO.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        email_id (str): The User email ID.
        first_name (str): The User first name ID.
        last_name (str): The User last name ID.
        channel_id (str): The Slack channel ID.
        enterprise_id (str): The Slack enterprise ID.

    Returns:
        bool: The Slack user had a connected LanguageCloud account.
    """
    slack_data = {
        "user_id": user_id,
        "team_id": team_id,
        "email_id": email_id,
        "first_name": first_name,
        "last_name": last_name,
        "channel_id": channel_id,
        "enterprise_id": enterprise_id if enterprise_id is not None else "",
    }
    with engines["sitemanager"].connect() as conn:
        sql = text(
            """
            SELECT obj_uuid, email_primary, login FROM obj_m_member WHERE login = :login
            """
        ).bindparams(login=email_id)
        conn.execute(sql)
        result1 = conn.execute(sql)
    if result1.rowcount == 0:
        member_id = str(uuid4()).upper()
        # Create User and User Group Link
        create_client_and_mglink(
            user_data=json.dumps(slack_data),
            member_id=member_id,
        )
        # Create log
        # crete_slack_logs_sso(user_data=json.dumps(slack_data), member_id=member_id, message="New User")
        # Create User Access Token
        create_client_access_tokens(client_id=member_id, type="public")
        # Create User Slack Link
        create_slack_deltaray_link_sso(
            user_data=json.dumps(slack_data), member_id=member_id
        )
        return member_id
    else:
        member_id = result1.first().obj_uuid
        create_client_access_tokens(client_id=member_id, type="public")
        create_slack_deltaray_link_sso(
            user_data=json.dumps(slack_data), member_id=member_id
        )
        return member_id


def create_client_and_mglink(
    user_data: str,
    member_id: str,
):
    json_data = json.loads(user_data)
    group_id = "0D750948-74A8-4932-B344-0880BDCB5215"
    if json_data.get("enterprise_id") == "E04RDMG8XP1":
        group_id = "173231FA-D524-42BF-9AF3F4834CAA88A0"
        if (
            config.environment != Environment.production
            and config.environment != Environment.local
        ):
            group_id = "B988B8ED-142B-465E-9CCE-831A0D92DD1D"
    password = "secret".encode("utf-8")  # Convert the password to bytes
    hash_object = hashlib.sha512(password)
    with engines["sitemanager"].connect() as conn:
        # Get Account Manager
        sqlAm = text(
            """
                SELECT account_manager
                FROM obj_m_group
                WHERE obj_uuid = :obj_uuid
            """
        ).bindparams(obj_uuid=group_id)
        conn.execute(sqlAm)
        resultAm = conn.execute(sqlAm).first()
        # Create User
        sqlMem = text(
            """
            INSERT INTO obj_m_member
                (obj_uuid, email_primary, login, password, password_updated, given_name, family_name, created, modified, account_manager, groupid, subscribed, active, email_active)
            VALUES
                (:obj_uuid, :email_primary, :email_primary, :password, now(), :given_name, :family_name, now(), now(), :account_manager, :groupid, 1, 1, 1)
            """
        ).bindparams(
            obj_uuid=member_id,
            email_primary=json_data.get("email_id"),
            given_name=json_data.get("first_name"),
            family_name=json_data.get("last_name"),
            password=hash_object.hexdigest().upper(),
            account_manager=resultAm.account_manager,
            groupid=group_id,
        )
        conn.execute(sqlMem)
        conn.commit()
        # Create User Group Link
        sqlMgLink = text(
            """
            INSERT INTO obj_m_mglink
                (obj_uuid, groupid, memberid, label, client_type, created, modified)
            VALUES
                (:obj_uuid, :groupid, :memberid, :label, :client_type, now(), now())
            """
        ).bindparams(
            obj_uuid=str(uuid4()).upper(),
            groupid=group_id,
            memberid=member_id,
            label=f"{member_id}-{group_id}",
            client_type="Normal",
        )
        conn.execute(sqlMgLink)
        conn.commit()


def create_client_access_tokens(client_id: str, type: str = "public"):
    """Create API access tokens of a RAY client."""
    with engines["api"].connect() as conn:
        # Check if an access token for the account_id already exists
        sql_check = text(
            """
                SELECT 1 FROM access_token
                WHERE account_id = :account_id
            """
        ).bindparams(account_id=client_id)
        result = conn.execute(sql_check).fetchone()

        # If an access token for the account_id does not exist, insert a new one
        if result is None:
            sql_insert = text(
                """
                    INSERT INTO access_token
                        (obj_uuid, account_id, active, `type`, application_id, created_at)
                    VALUES
                        (:obj_uuid, :account_id, 1, :type, '', now())
                """
            ).bindparams(obj_uuid=str(uuid4()).upper(), account_id=client_id, type=type)
            conn.execute(sql_insert)
            conn.commit()
    # Enable API Access
    with engines["sitemanager"].connect() as conn:
        sqlMemUpdate = text(
            """
                UPDATE obj_m_member
                SET api_access = 1
                WHERE obj_uuid = :obj_uuid
            """
        ).bindparams(obj_uuid=client_id)
        conn.execute(sqlMemUpdate)
        conn.commit()


def create_slack_deltaray_link_sso(user_data: str, member_id: str):
    json_data = json.loads(user_data)
    with engines["ray_integration"].connect() as conn:
        sqlSlackDelete = text(
            """
            DELETE FROM slack_deltaray_link
            WHERE member_uuid = :member_uuid
            """
        ).bindparams(member_uuid=member_id)
        conn.execute(sqlSlackDelete)
        conn.commit()
    with engines["ray_integration"].connect() as conn:
        sqlSlackAccount = text(
            """
            SELECT slack_user_id
            FROM slack_deltaray_link
            WHERE slack_user_id = :slack_user_id
            AND slack_team_id = :slack_team_id
            AND is_active = 0
            """
        ).bindparams(
            slack_user_id=json_data.get("user_id"),
            slack_team_id=json_data.get("team_id"),
        )
        if json_data.get("enterprise_id") is not None:
            sqlSlackAccount = text(
                """
                SELECT slack_user_id
                FROM slack_deltaray_link
                WHERE slack_user_id = :slack_user_id
                AND (slack_enterprise_id = :slack_enterprise_id OR slack_team_id = :slack_team_id)
                AND is_active = 0
                """
            ).bindparams(
                slack_user_id=json_data.get("user_id"),
                slack_team_id=json_data.get("team_id"),
                slack_enterprise_id=json_data.get("enterprise_id"),
            )
        resultSlackAccount = conn.execute(sqlSlackAccount).first()
    if resultSlackAccount is not None:
        with engines["ray_integration"].connect() as conn:
            if json_data.get("enterprise_id") is not None:
                sqlRayUpdate = text(
                    """
                    UPDATE slack_deltaray_link SET
                        member_uuid = :member_uuid,
                        slack_user_id = :user_id,
                        slack_team_id = :team_id,
                        slack_enterprise_id = :enterprise_id,
                        slack_channel_id = :channel_id,
                        is_subscribed = 1,
                        is_active = 1,
                        is_sso = 1,
                        activated_at = now()
                    WHERE (slack_user_id = :user_id)
                    AND (
                        slack_team_id = :team_id
                        OR slack_enterprise_id = :enterprise_id
                    )
                    """
                ).bindparams(
                    member_uuid=member_id,
                    user_id=json_data.get("user_id"),
                    team_id=json_data.get("team_id"),
                    enterprise_id=json_data.get("enterprise_id"),
                    channel_id=json_data.get("channel_id"),
                )
            else:
                sqlRayUpdate = text(
                    """
                    UPDATE slack_deltaray_link SET
                        member_uuid = :member_uuid,
                        slack_user_id = :user_id,
                        slack_team_id = :team_id,
                        slack_enterprise_id = :enterprise_id,
                        slack_channel_id = :channel_id,
                        is_subscribed = 1,
                        is_active = 1,
                        is_sso = 1,
                        activated_at = now()
                    WHERE slack_user_id = :user_id
                    AND slack_team_id = :team_id
                    """
                ).bindparams(
                    member_uuid=member_id,
                    user_id=json_data.get("user_id"),
                    team_id=json_data.get("team_id"),
                    enterprise_id=json_data.get("enterprise_id"),
                    channel_id=json_data.get("channel_id"),
                )
            conn.execute(sqlRayUpdate)
            conn.commit()
    else:
        with engines["ray_integration"].connect() as conn:
            sqlRay = text(
                """
                INSERT INTO slack_deltaray_link
                    (slack_user_id, slack_team_id, slack_enterprise_id, slack_channel_id, member_uuid, is_subscribed, is_active, is_sso, activated_at)
                VALUES
                    (:user_id, :team_id, :enterprise_id, :channel_id, :member_uuid, 1, 1, 1, now())
                """
            ).bindparams(
                user_id=json_data.get("user_id"),
                team_id=json_data.get("team_id"),
                enterprise_id=json_data.get("enterprise_id"),
                channel_id=json_data.get("channel_id"),
                member_uuid=member_id,
            )
            conn.execute(sqlRay)
            conn.commit()


def crete_slack_logs_sso(user_data: str, member_id: str, message: str):
    json_data = json.loads(user_data)
    with engines["ray_integration_log"].begin() as conn:
        sql = text(
            """
            INSERT INTO slack_logs_sso
                (user_id, team_id, channel_id, client_uuid, payload, message)
            VALUES
                (:user_id, :team_id, :channel_id, :client_uuid, :payload, :message)
            """
        ).bindparams(
            user_id=json_data.get("user_id"),
            team_id=json_data.get("team_id"),
            channel_id=json_data.get("channel_id"),
            client_uuid=member_id,
            payload=user_data,
            message=message,
        )
        conn.execute(sql)


def encrpyt_slack_sso_token(
    email_id: str,
) -> str:
    """Generates sso token to allow the Slack app users to communicate
    with the RAY platform securely.

    Args:
        email_id (str): The Slack user Email ID.

    Returns:
        str: The encrypted token.
    """
    data = {"email_id": email_id}
    params = {
        "token": encrypt_aes(
            json.dumps(data), config.slack_deltaray_key.get_secret_value()
        )
    }
    return f"{domains.languagecloud}/auth/slacksso?{urlencode(params)}"


async def get_client_tokens(languagecloud_api_key: str) -> GetCreditBalanceResponse:
    """http languagecloud API to get the client tokens."""
    url = f"{domains.languagecloud_api}/credits/balance"
    headers = {
        "Authorization": f"Bearer {languagecloud_api_key}",
    }
    try:
        async with httpx.AsyncClient() as http:
            response = await http.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            return GetCreditBalanceResponse(
                ai_token=data["ai_token"], mt_token=data["mt_token"]
            )
    except Exception as e:
        notify_exception(e)
        return GetCreditBalanceResponse(0, 0)


async def get_group_tokens(super_group_uuid: str) -> GetCreditBalanceResponse:
    """read sitemanager.obj_m_member_credit_transactions to get the group tokens balance."""
    list_group_uuid = []
    with engines["sitemanager_readonly"].connect() as conn:
        sql = text(
            """
            SELECT group_uuid
            FROM super_group_glink
            WHERE super_group_uuid = :super_group_uuid
            """
        ).bindparams(super_group_uuid=super_group_uuid)
        result = conn.execute(sql)
        rows = result.fetchall()
        for row in rows:
            list_group_uuid.append(row.group_uuid)
    # first get
    with engines["sitemanager_readonly"].connect() as conn:
        sql = text(
            """
            SELECT SUM(amount) AS total
            FROM obj_m_member_credit_transactions
            WHERE group_uuid IN :group_uuids
            AND credit_type = 'ai_token'
            """
        ).bindparams(bindparam("group_uuids", expanding=True))
        result = conn.execute(sql, {"group_uuids": list_group_uuid})
        row = result.first()
        if not row:
            return GetCreditBalanceResponse(0, 0)
    return GetCreditBalanceResponse(ai_token=row.total, mt_token=0)


async def spend_mt_tokens(
    credits: int,
    user: SlackUser = None,
    ray_connection: RayConnection = None,
) -> int:
    """Insert into the database obj_m_member_credit_transactions to record transaction"""
    if ray_connection is None:
        ray_connection = await get_ray_connection(
            user.user_id, user.team_id, user.enterprise_id
        )
    # If no client spend under super group
    if ray_connection.client is None:
        client_uuid = ray_connection.super_group[0].id
        group_uuid = ray_connection.super_group[0].id
    else:
        client_uuid = ray_connection.client.id
        group_uuid = ray_connection.client.user_group_id
    description = "Machine Translation"
    mt_scale = 0.1
    amount = math.ceil(credits * mt_scale)
    with engines["sitemanager"].begin() as conn:
        sql = text(
            """
            INSERT INTO obj_m_member_credit_transactions
                (uuid, client_uuid, group_uuid, amount, credit_type, transaction_type, description)
            VALUES
                (:uuid, :client_uuid, :group_uuid, :amount, :credit_type, :transaction_type, :description)
            """
        ).bindparams(
            uuid=str(uuid.uuid4()),
            client_uuid=client_uuid,
            group_uuid=group_uuid,
            amount=0 - amount,
            credit_type="ai_token",
            transaction_type="spend",
            description=description,
        )
        conn.execute(sql)

    return amount


async def get_client_type(client_id: str, group_id: str) -> str:
    """Get the client type for a group. Owner Admin or Normal client"""
    with engines["sitemanager_readonly"].connect() as conn:
        sql = text(
            """
            SELECT client_type
            FROM obj_m_mglink
            WHERE memberid = :client_id
            AND groupid = :group_id
            """
        ).bindparams(client_id=client_id, group_id=group_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
    return row.client_type

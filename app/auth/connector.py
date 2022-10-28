"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import asyncio
import time
import json
from dataclasses import dataclass
from urllib.parse import urlencode
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .algorithms import encrypt_aes, hash_hmac_sha1
from ..config import config, domains
from ..database import engines


@dataclass(frozen=True, slots=True)
class SlackUser:
    """Dataclass representing a Slack user."""

    user_id: str
    team_id: str
    app_id: str
    channel_id: str
    is_subscribed: bool
    bot_token: str
    ray_client_id: str
    ray_username: str


@dataclass(frozen=True, slots=True)
class RaySuperGroup:
    """Dataclass representing a DeltaRAY super group."""

    id: str
    """The RAY group UUID (`obj_m_group.obj_uuid`)."""
    name: str
    """The name of the group (`obj_m_group.label`)."""
    slack_team_id: str
    """The Slack team ID linked to the RAY client."""


@dataclass(frozen=True, slots=True)
class RayClient:
    """Dataclass representing a DeltaRAY client."""

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
    slack_app_id: str
    """The Slack app ID."""


@dataclass(frozen=True, slots=True)
class RayConnection:
    """Dataclass representing a connection between a Slack user and a DeltaRAY
    client. This also includes the connection between the Slack workspace and
    the DeltaRAY group.
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


def get_bot_token(conn: Connection, team_id: str, app_id: str) -> str | None:
    """Gets the Slack bot token for a workspace."""
    sql = text(
        """
        SELECT bot_token FROM slack_bots
        WHERE team_id = :team_id AND app_id = :app_id
        ORDER BY id DESC
        LIMIT 1
        """
    ).bindparams(team_id=team_id, app_id=app_id)
    result = conn.execute(sql).first()
    return result[0] if result else None


def get_slack_user(ray_client_id: str) -> SlackUser | None:
    """Gets the Slack user connected to a RAY client.

    Args:
        ray_client_id (str): The DeltaRAY user ID.
    """
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.slack_user_id,link.slack_team_id,link.slack_app_id,
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
            bot_token = get_bot_token(conn, row.slack_team_id, row.slack_app_id)
            if bot_token:
                return SlackUser(
                    user_id=row.slack_user_id,
                    team_id=row.slack_team_id,
                    app_id=row.slack_app_id,
                    channel_id=row.slack_channel_id,
                    is_subscribed=bool(row.is_subscribed),
                    bot_token=bot_token,
                    ray_client_id=ray_client_id,
                    ray_username=row.login,
                )
    return None


async def get_ray_super_group(team_id: str) -> RaySuperGroup | None:
    """Gets the DeltaRAY super group linked to the Slack workspace if an active
    link exists, otherwise returns None.

    Args:
        team_id (str): The ID of the team.
    """
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.super_group_uuid, g.label
            FROM slack_super_group_link link
            INNER JOIN sitemanager.obj_m_group g
            ON link.super_group_uuid = g.obj_uuid
            WHERE link.slack_team_id = :team_id
            AND link.is_active = 1
            LIMIT 1
            """
        ).bindparams(team_id=team_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
    return RaySuperGroup(id=row.super_group_uuid, name=row.label, slack_team_id=team_id)


async def get_ray_client(user_id: str, team_id: str, app_id: str) -> RayClient | None:
    """Gets the RAY client id and username linked to the Slack account if an active link
    exists, otherwise returns None.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
    """
    # First find the client details.
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT link.member_uuid, mem.login
            FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            WHERE link.slack_user_id = :user_id
            AND link.slack_team_id = :team_id
            AND link.slack_app_id = :app_id
            AND link.is_active = 1
            AND mem.active = 1
            AND mem.is_deleted = 0
            LIMIT 1
            """
        ).bindparams(user_id=user_id, team_id=team_id, app_id=app_id)
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
        slack_app_id=app_id,
    )


async def get_ray_connection(
    user_id: str, team_id: str, app_id: str
) -> RayConnection | None:
    """Gets the DeltaRAY super group and client linked to the Slack workspace
    and user. If the Slack workspace is not linked, ignore the Slack user link.
    A Slack workspace can have a connection without a Slack user connection.
    """
    super_group, client = await asyncio.gather(
        get_ray_super_group(team_id), get_ray_client(user_id, team_id, app_id)
    )
    if super_group is None:
        return None
    return RayConnection(super_group, client)


def disconnect_ray_account(user_id: str, team_id: str, app_id: str) -> bool:
    """Disconnect the DeltaRAY account of a slack user.

    Args:
        user_id (str): The Slack user ID.
        team_id (str): The Slack team ID.
        app_id (str): The Slack app ID.

    Returns:
        bool: The Slack user had a connected DeltaRAY account.
    """
    with engines["ray_integration"].begin() as conn:
        sql = text(
            """
            UPDATE slack_deltaray_link SET
                is_active = 0,
                deactivated_at = NOW()
            WHERE slack_user_id = :user_id
            AND slack_team_id = :team_id
            AND slack_app_id = :app_id
            AND is_active = 1
            """
        ).bindparams(user_id=user_id, team_id=team_id, app_id=app_id)
        result = conn.execute(sql)
    return result.rowcount > 0


def get_app_id(bot_token: str, team_id: str) -> str:
    """Get the app_id from a bot token and team_id. Use this to get the app_id
    if the Slack API does not provide it.
    """
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT app_id from slack_installations
            WHERE bot_token = :bot_token
            AND team_id = :team_id
            ORDER BY id DESC
            LIMIT 1
            """
        ).bindparams(bot_token=bot_token, team_id=team_id)
        result = conn.execute(sql)
        row = result.first()
    return row[0] if row else ""


def encrpyt_slack_integration_token(
    user_id: str, team_id: str, app_id: str, channel_id: str, expire_seconds: int = 3600
) -> str:
    """Generates time-sensitive token to allow the Slack app to communicate
    with the RAY platform securely.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 3600.

    Raises:
        AssertionError: The RAY_INTEGRATION_KEY environment variable is not set.

    Returns:
        str: The encrypted token.
    """
    epoch = int(time.time())
    data = {
        "appId": app_id,
        "teamId": team_id,
        "userId": user_id,
        "channelId": channel_id,
        "created": epoch,
        "expires": epoch + expire_seconds,
    }

    return encrypt_aes(json.dumps(data), config.slack_deltaray_key)


def get_slack_deltaray_integration_url(
    user_id: str, team_id: str, app_id: str, channel_id: str, expire_seconds: int = 3600
) -> str:
    """Generates a URL for a user to connect their Slack account to their
    DeltaRAY account.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 3600.

    Returns:
        str: The URL to connect a user's Slack account and DeltaRAY account.
    """
    params = {
        "token": encrpyt_slack_integration_token(
            user_id, team_id, app_id, channel_id, expire_seconds
        )
    }
    return f"{domains.deltaray}/app/slack?{urlencode(params)}"

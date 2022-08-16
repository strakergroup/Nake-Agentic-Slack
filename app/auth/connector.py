"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import time
import json
from dataclasses import dataclass
from urllib.parse import urlencode
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .algorithms import encrypt_aes, decrypt_aes, hash_hmac_sha1
from ..config import config
from ..database import engines


@dataclass(frozen=True, slots=True)
class SlackUser:
    """Dataclass containing details of a Slack account."""

    user_id: str
    team_id: str
    app_id: str
    channel_id: str
    is_subscribed: bool
    bot_token: str
    ray_client_id: str | None = None


@dataclass(frozen=True, slots=True)
class RayClient:
    """Dataclass containing details of a RAY client account."""

    id: str
    """The RAY client ID (member_uuid)."""
    username: str
    """The RAY client username."""
    access_token: str
    """The API token linked to the client."""
    slack_user_id: str
    """The Slack user ID."""
    slack_team_id: str
    """The Slack team ID."""
    slack_app_id: str
    """The Slack app ID."""


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


def get_slack_users(ray_client_id: str) -> list[SlackUser]:
    """Gets the Slack user accounts connected to a RAY client.

    Args:
        ray_client_id (str): The deltaRAY user ID.

    Returns:
        list[SlackUser]: The connected Slack user accounts.
    """
    users: list[SlackUser] = []
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT slack_user_id,slack_team_id,
                slack_app_id,slack_channel_id,is_subscribed
            FROM slack_deltaray_link
            WHERE member_uuid = :client_id
            AND is_active = 1
            AND is_revoked = 0
            ORDER BY id DESC
            """
        ).bindparams(client_id=ray_client_id)
        result = conn.execute(sql)
        for row in result:
            bot_token = get_bot_token(conn, row.slack_team_id, row.slack_app_id)
            if bot_token:
                users.append(
                    SlackUser(
                        user_id=row.slack_user_id,
                        team_id=row.slack_team_id,
                        app_id=row.slack_app_id,
                        channel_id=row.slack_channel_id,
                        is_subscribed=bool(row.is_subscribed),
                        bot_token=bot_token,
                        ray_client_id=ray_client_id,
                    )
                )
    return users


def get_ray_client(user_id: str, team_id: str, app_id: str) -> RayClient | None:
    """Gets the RAY client id and username linked to the Slack account if an active link
    exists, otherwise return None.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.

    Returns:
        dict[str, str] | None: A dict containing the client's id and username,
        or None if the account is not linked.
    """
    # First find the client details.
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT link.member_uuid, mem.login FROM slack_deltaray_link link
            INNER JOIN sitemanager.obj_m_member mem
            ON link.member_uuid = mem.obj_uuid
            WHERE slack_user_id = :user_id
            AND slack_team_id = :team_id
            AND slack_app_id = :app_id
            AND is_active = 1
            AND is_revoked = 0
            """
        ).bindparams(user_id=user_id, team_id=team_id, app_id=app_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
        ray_client_id, username = row[0], row[1]
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
    return RayClient(
        id=ray_client_id,
        username=username,
        access_token=access_token,
        slack_user_id=user_id,
        slack_team_id=team_id,
        slack_app_id=app_id,
    )


def get_app_id(bot_token: str, team_id: str) -> str:
    """Get the app_id from a bot token and team_id. Use this to get the app_id
    if the Slack API does not provide it.
    """
    # TODO Create DB index
    with engines["ray_integration"].connect() as conn:
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
    DeltaRay account.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires.
        Defaults to 3600.

    Returns:
        str: The URL to connect a user's Slack account and DeltaRay account.
    """
    params = {
        "token": encrpyt_slack_integration_token(
            user_id, team_id, app_id, channel_id, expire_seconds
        )
    }
    return f"{config.deltaray_domain}/integration/slack?{urlencode(params)}"


def validate_ray_authentication_token(token: str) -> str:
    """Decrypts and validates an authentication token used by Ray apps
    to send events to this app.

    Args:
        token (str): The token to validate.

    Raises:
        ValueError: The token is invalid.

    Returns:
        The client id of the client the request is for.
    """
    try:
        raw_data = decrypt_aes(token, config.slack_deltaray_key)
        data = json.loads(raw_data)
        if not isinstance(data, dict):
            raise ValueError("The decrypted data has an invalid format")
        if not isinstance(data["client_id"], str) or not data["client_id"]:
            raise ValueError("The decrypted data has an invalid format")
        if (
            not isinstance(data["expires"], (int, float))
            or data["expires"] <= time.time()
        ):
            raise ValueError("The token has expired")
        return data["client_id"]
    except Exception as e:
        raise ValueError("Token validation failed") from e

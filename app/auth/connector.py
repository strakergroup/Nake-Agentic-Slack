"""This module contains functions to connect this app to to
other services, e.g. Slack, RAY apps.
"""

import time
import json
from urllib.parse import urlencode
from sqlalchemy import text

from .algorithms import encrypt_aes, decrypt_aes
from ..config import straker_config
from ..database import engine


def get_ray_client(user_id: str, team_id: str, app_id: str) -> dict[str, str] | None:
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
    with engine.connect() as conn:
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
        ray_client = {"id": row[0], "username": row[1]}
        # Now get the access token for authentication.
        sql = text(
            """
            SELECT t.access_token FROM client_tokens t
            INNER JOIN client_credentials c
            ON t.client_credentials_uuid = c.obj_uuid
            WHERE c.app_team_id = :team_id
            AND c.app_name = 'slack-ray-translator'
            AND c.active = 1
            AND t.active = 1
            AND t.expired_at IS NULL
            LIMIT 1
            """
        ).bindparams(team_id=team_id)
        result = conn.execute(sql)
        row = result.first()
        if not row:
            return None
        ray_client["access_token"] = row[0]
    return ray_client


def get_app_id(bot_token: str, team_id: str) -> str:
    """Get the app_id from a bot token and team_id. Use this to get the app_id
    if the Slack API does not provide it.
    """
    # TODO Create DB index
    with engine.connect() as conn:
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

    return encrypt_aes(json.dumps(data), straker_config.slack_deltaray_key)


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
    return f"{straker_config.deltaray_domain}/integration/slack?{urlencode(params)}"


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
        raw_data = decrypt_aes(token, straker_config.slack_deltaray_key)
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

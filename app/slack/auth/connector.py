import time
import json
from urllib.parse import urlencode
from .encryption import encrypt_aes, decrypt_aes
from ...config import straker_config


def encrpyt_slack_integration_token(
    user_id: str,
    team_id: str,
    app_id: str,
    channel_id: str,
    expire_seconds: int = 3600
) -> str:
    """Generates time-sensitive token to allow the Slack app to communicate
    with the RAY platform securely.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires. Defaults to 3600.

    Raises:
        AssertionError: The RAY_INTEGRATION_KEY environment variable is not set.

    Returns:
        str: The encrypted token.
    """
    epoch = int(time.time())
    data = {
        'appId': app_id,
        'teamId': team_id,
        'userId': user_id,
        'channelId': channel_id,
        'created': epoch,
        'expires': epoch + expire_seconds,
    }

    return encrypt_aes(json.dumps(data), straker_config.slack_deltaray_key)


def get_slack_deltaray_integration_url(
    user_id: str,
    team_id: str,
    app_id: str,
    channel_id: str,
    expire_seconds: int = 3600
) -> str:
    """Generates a URL for a user to connect their Slack account to their
    DeltaRay account.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
        channel_id (str): The ID of channel where the login command was called.
        expire_seconds (int, optional): The time in seconds before the token expires. Defaults to 3600.

    Returns:
        str: The URL to connect a user's Slack account and DeltaRay account.
    """
    params = {'token': encrpyt_slack_integration_token(user_id, team_id, app_id, channel_id, expire_seconds)}
    return f'{straker_config.deltaray_domain}/integration/slack?{urlencode(params)}'


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
            raise ValueError('The decrypted data has an invalid format')
        if not isinstance(data['client_id'], str) or not data['client_id']:
            raise ValueError('The decrypted data has an invalid format')
        if not isinstance(data['expires'], (int, float)) or data['expires'] <= time.time():
            raise ValueError('The token has expired')
        return data['client_id']
    except Exception as e:
        raise ValueError('Token validation failed') from e

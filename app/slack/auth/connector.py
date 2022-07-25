import time
import json
from urllib.parse import urlencode
from .encryption import encrypt_aes
from ...config import straker_config


def encrpyt_slack_integration_token(user_id: str, team_id: str, app_id: str, expire_seconds: int = 3600) -> str:
    """Generates time-sensitive token to allow the Slack app to communicate
    with the RAY platform securely.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
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
        'created': epoch,
        'expires': epoch + expire_seconds,
    }

    return encrypt_aes(json.dumps(data), straker_config.slack_deltaray_key)


def get_slack_deltaray_integration_url(user_id: str, team_id: str, app_id: str, expire_seconds: int = 3600) -> str:
    """Generates a URL for a user to connect their Slack account to their
    DeltaRay account.

    Args:
        user_id (str): The ID of the user.
        team_id (str): The ID of the team.
        app_id (str): The ID of the Slack app.
        expire_seconds (int, optional): The time in seconds before the token expires. Defaults to 3600.

    Returns:
        str: The URL to connect a user's Slack account and DeltaRay account.
    """
    params = {'token': encrpyt_slack_integration_token(user_id, team_id, app_id, expire_seconds)}
    return f'{straker_config.deltaray_domain}/integration/slack?{urlencode(params)}'

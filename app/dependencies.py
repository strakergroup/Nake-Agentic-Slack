from typing import Any
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from .auth.connector import (
    SlackUser,
    validate_queue_proxy_secret,
    get_slack_users,
    validate_ray_authentication_token,
)


# Sub-dependency to get the bearer token.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="")


class SlackAuth:
    """Dependency class to validate the bearer token and return the client id
    of the client the request is for.
    """

    def __init__(self, token: str = Depends(_oauth2_scheme)) -> None:
        try:
            self.client_id = validate_ray_authentication_token(token)
        except Exception:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)


class SlackRayAuth:
    """Dependency class to validate the bearer token and validate that the
    Slack account is connected with a DeltaRay account. This is similar to
    `SlackAuth` except this also contains the connected Slack user accounts.
    """

    def __init__(self, auth: SlackAuth = Depends()) -> None:
        self.client_id = auth.client_id
        self.slack_accounts = get_slack_users(auth.client_id)
        # Return 401 error if there are no connected active Slack accounts.
        if not self.slack_accounts:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)


class RayEvent(BaseModel):
    """The payload format for the RAY events endpoint."""

    event: str
    """The name of the event."""
    source: str
    """The source of the event."""
    message: str | None = None
    client_id: str | None = None
    job_id: str | None = None
    data: dict[str, Any] | None = None


class RayEventAuth:
    """Dependency class to validate the bearer token for the RAY events
    endpoint. Raises a 401 HTTPException if the bearer token is invalid.
    Provides a list of connected Slack user accounts if a client_id is given.
    """

    def __init__(
        self,
        event: RayEvent,
        token: str = Depends(_oauth2_scheme),
    ) -> None:
        self.slack_users: list[SlackUser] = []
        is_token_valid = validate_queue_proxy_secret(token)
        if not is_token_valid:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
        # Get all the Slack accounts connected to the RAY client ID.
        if event.client_id:
            self.slack_users = get_slack_users(event.client_id)

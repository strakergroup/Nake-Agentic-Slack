from typing import Annotated, Any

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from .auth.connector import (
    SlackUser,
    get_demo_link,
    get_slack_org,
    get_slack_user,
    validate_queue_proxy_secret,
)

# Sub-dependency to get the bearer token.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="")


class RayEvent(BaseModel):
    """The payload format for the RAY events endpoint."""

    event: str
    """The name of the event (Redis stream name)."""
    data: dict[str, Any]


class RayEventAuth:
    """Dependency class to validate the bearer token for the RAY events
    endpoint. Raises a 401 HTTPException if the bearer token is invalid.
    Loads the connected Slack user if a client_id is given.
    """

    def __init__(
        self,
        event: RayEvent,
        token: Annotated[str, Depends(_oauth2_scheme)],
    ) -> None:
        self.slack_user: SlackUser | None = None
        self.demo_slack_users = []
        is_token_valid = validate_queue_proxy_secret(token)
        if not is_token_valid:
            raise HTTPException(401)
        # Get the Slack account connected to the RAY client ID.
        if "client_id" in event.data:
            self.slack_user = get_slack_user(event.data["client_id"])
            self.demo_slack_users = get_demo_link(event.data["client_id"])
        if "extra_data" in event.data:
            if "client_id" in event.data["extra_data"]:
                self.slack_user = get_slack_user(event.data["extra_data"]["client_id"])
                if not self.slack_user:
                    self.slack_user = get_slack_org(
                        event.data["extra_data"]["client_id"]
                    )
                    if self.slack_user:
                        self.slack_user.user_id = event.data["extra_data"][
                            "slack_user_id"
                        ]
                self.demo_slack_users = get_demo_link(
                    event.data["extra_data"]["client_id"]
                )

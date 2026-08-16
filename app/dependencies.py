from typing import Annotated, Any

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel

from .auth.connector import (
    SlackUser,
    get_demo_link,
    resolve_slack_delivery_user,
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

    def __init__(self) -> None:
        self.slack_user: SlackUser | None = None
        self.demo_slack_users: list[str] = []

    async def initialize(
        self,
        event: RayEvent,
        token: Annotated[str, Depends(_oauth2_scheme)],
    ) -> None:
        is_token_valid = validate_queue_proxy_secret(token)
        if not is_token_valid:
            raise HTTPException(401)

        # Inline MT (channel/shortcut/DM) carries billing context in extra_data;
        # Document MT puts it on the event root. One resolver handles both.
        extra = event.data.get("extra_data") or {}
        client_id = extra.get("client_id") or event.data.get("client_id")
        if client_id:
            self.slack_user = await resolve_slack_delivery_user(
                client_id,
                team_id=extra.get("team_id") or event.data.get("team_id"),
                slack_user_id=extra.get("slack_user_id")
                or event.data.get("slack_user_id"),
                enterprise_id=extra.get("enterprise_id")
                or event.data.get("enterprise_id"),
                channel_id=extra.get("channel_id") or event.data.get("channel_id"),
            )
            self.demo_slack_users = await get_demo_link(client_id)


async def get_ray_event_auth(
    event: RayEvent,
    token: Annotated[str, Depends(_oauth2_scheme)],
) -> RayEventAuth:
    """Factory function to create and initialize RayEventAuth with async calls."""
    auth = RayEventAuth()
    await auth.initialize(event, token)
    return auth

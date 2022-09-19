from typing import Any
import asyncio
from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    Form,
    Header,
    status,
    Request,
)
from pydantic import BaseModel

from ..auth.connector import (
    validate_api_callback_signature,
    get_slack_users,
    get_ray_client,
)
from ..dependencies import SlackRayAuth, RayEventAuth, RayEvent
from ..slack import app
from ..slack.templates.messages import (
    SlackMessage,
    SuccessfulLoginMessage,
    JobCreationMessage,
    JobStatusChangeEventMessage,
    JobCompletedEventMessage,
    JobCancelledEventMessage,
    JobQuotedEventMessage,
)


router = APIRouter(tags=["ray"])


# TODO: refactor this
event_types = [
    "job_status",
    "job_completed",
    "job_cancelled",
    "quote_created",
]


def get_ray_event_message(event: RayEvent) -> SlackMessage:
    """Gets the SlackMessage based on the event type."""
    match event.event:
        case "job_status":
            return JobStatusChangeEventMessage(event.client_id or "", event.data)
        case "job_completed":
            return JobCompletedEventMessage(event.client_id or "", event.data)
        case "job_cancelled":
            return JobCancelledEventMessage(event.client_id or "", event.data)
        case "quote_created":
            return JobQuotedEventMessage(event.client_id or "", event.data)
    raise AssertionError(f"Unhandled RAY event: {event.event}")


@router.post("/ray/events")
async def ray_events(event: RayEvent, auth: RayEventAuth = Depends()):
    """Receives and responds to an event from the RAY platform."""
    if event.event not in event_types:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "The event type is not valid"
        )
    subscribed_users = [u for u in auth.slack_users if u.is_subscribed]
    # message = get_ray_event_message(event)
    if subscribed_users:
        for user in subscribed_users:
            app.client.token = user.bot_token
            # TODO: Enable when notifications are ready
            # asyncio.create_task(
            #     app.client.chat_postMessage(
            #         channel=user.user_id, text=message.text, blocks=message.blocks
            #     )
            # )
    return {"message": "success"}


class RayCallback(BaseModel):
    """The expected body format for the RAY callback endpoint."""

    event_types: list[str]
    job: list[dict[str, Any]]


@router.post("/ray/callback")
async def api_job_callback(
    request: Request,
    client_id: str,
    body: RayCallback,
    x_straker_signature: str = Header(),
):
    """Callback endpoint for API jobs."""
    # Check if the callback can be linked to a Slack user.
    subscribed_users = [u for u in get_slack_users(client_id) if u.is_subscribed]
    if not subscribed_users:
        # TODO: log this
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    # Validate X-Straker-Signature.
    ray_client = await get_ray_client(
        subscribed_users[0].user_id,
        subscribed_users[0].team_id,
        subscribed_users[0].app_id,
    )
    assert ray_client is not None
    is_header_valid = validate_api_callback_signature(
        await request.body(),
        ray_client.access_token,
        x_straker_signature,
    )
    if not is_header_valid:
        # TODO: log this
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    # Only listen to job creation callbacks for now.
    if "JOB_NUMBER" in body.event_types:
        # Notify Slack users.
        try:
            job_data = body.job[0]
            message = JobCreationMessage(job_data["tj_number"])
        except (KeyError, IndexError):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "The payload format is invalid"
            )
        for user in subscribed_users:
            app.client.token = user.bot_token
            # TODO: Investigate concurrency issues.
            asyncio.create_task(
                app.client.chat_postMessage(
                    channel=user.user_id,
                    text=message.text,
                )
            )
        return {
            "message": "success",
            "detail": f"{len(subscribed_users)} Slack users notified",
        }
    else:
        return {
            "message": "success",
            "detail": f"Unhandled callback event: {body.event_types}",
        }


@router.post("/ray/connect", status_code=status.HTTP_204_NO_CONTENT)
async def connect(
    user_id: str = Form(),
    team_id: str = Form(),
    app_id: str = Form(),
    channel_id: str = Form(),
    username: str = Form(),
    auth: SlackRayAuth = Depends(),
):
    """Called by DeltaRay to notify a user that their Slack account has been
    successfully connected to their DeltaRay account.
    """
    # Make sure the Slack account in the token matches the body for extra validation.
    accounts = [
        acc
        for acc in auth.slack_accounts
        if acc.user_id == user_id and acc.team_id == team_id and acc.app_id == app_id
    ]
    if not accounts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    account = accounts[0]

    app.client.token = account.bot_token
    message = SuccessfulLoginMessage(account.user_id, username)
    await app.client.chat_postEphemeral(
        channel=channel_id,
        user=account.user_id,
        blocks=message.blocks,
        text=message.text,
    )

from typing import Any
from sentry_sdk import capture_message
from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    Header,
    status,
    Request,
)
from pydantic import BaseModel, ValidationError

from ..auth.connector import (
    SlackUser,
    validate_api_callback_signature,
    get_slack_user,
    get_ray_client,
    get_group_admin_slack_users,
)
from ..dependencies import RayEventAuth, RayEvent
from ..slack import app
from ..slack.templates.messages import (
    SuccessfulLoginMessage,
    ClientSignupEventMessage,
    ClientSignupEventAdminMessage,
    ClientApprovedEventMessage,
    JobCreationMessage,
)
from ..ray.events.parse import get_ray_event_message
from ..ray.events.models import ClientGroup
from ..ray.events.logging import post_notification, post_notification_ephemeral


router = APIRouter(tags=["ray"])


@router.post("/ray/events")
async def ray_events(event: RayEvent, auth: RayEventAuth = Depends()):
    """Receives and responds to an event from the RAY platform."""
    try:
        message = get_ray_event_message(event.event, event.data)
    except ValidationError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {
                "message": f"The event data is invalid for the event type: {event.event}",
                "detail": e.errors(),
            },
        )
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The event type is invalid: {event.event}",
        )
    if message is not None and auth.slack_user is not None:
        # Send login message to the same conversation where it was prompted.
        if isinstance(message, SuccessfulLoginMessage):
            await post_notification_ephemeral(
                app.client, auth.slack_user.channel_id, event, auth.slack_user, message
            )
        elif (
            # Send important messages regardless of subscribed status.
            isinstance(message, (ClientSignupEventMessage, ClientApprovedEventMessage))
            # Send all other messages if the client is subscribed to notifications.
            or auth.slack_user.is_subscribed
        ):
            await post_notification(app.client, event, auth.slack_user, message)

    # Send notifications to group admins when a new client signs up.
    if isinstance(message, ClientSignupEventMessage):
        admins: dict[str, tuple[SlackUser, list[ClientGroup]]] = {}
        for group in message.event.groups:
            admin_slack_users = get_group_admin_slack_users(group.uuid)
            for admin in admin_slack_users:
                if admin.ray_client_id not in admins:
                    admins[admin.ray_client_id] = (admin, [])
                admins[admin.ray_client_id][1].append(group)

        for user, groups in admins.values():
            admin_message = ClientSignupEventAdminMessage(
                event=message.event,
                groups=groups,
            )
            await post_notification(app.client, event, user, admin_message)

    return {"message": "success", "data": {"event": event.event}}


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
    slack_user = get_slack_user(client_id)
    if slack_user is None:
        capture_message("Slack user not found in callback endpoint", "warning")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    # Validate X-Straker-Signature.
    ray_client = await get_ray_client(
        slack_user.user_id,
        slack_user.team_id,
        slack_user.app_id,
    )
    assert ray_client is not None
    is_header_valid = validate_api_callback_signature(
        await request.body(),
        ray_client.access_token,
        x_straker_signature,
    )
    if not is_header_valid:
        capture_message("Callback X-Straker-Signature is invalid", "warning")
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
        app.client.token = slack_user.bot_token
        await app.client.chat_postMessage(
            channel=slack_user.user_id,
            text=message.text,
        )
        return {
            "message": "success",
            "detail": "Slack user notified of event: JOB_NUMBER",
        }
    else:
        return {
            "message": "success",
            "detail": f"Unhandled callback event: {body.event_types}",
        }

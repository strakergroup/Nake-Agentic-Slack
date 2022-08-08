from fastapi import APIRouter, HTTPException, Depends, Form, status

from ..dependencies import SlackRayAuth, RayEventAuth, RayEvent
from ..slack import app
from ..slack.templates.messages import SuccessfulLoginMessage


router = APIRouter(tags=["ray"])


@router.post("/ray/events")
async def ray_events(event: RayEvent, auth: RayEventAuth = Depends()):
    """Receives and responds to an event from the RAY platform."""
    subscribed_users = [u for u in auth.slack_users if u.is_subscribed]
    for user in subscribed_users:
        app.client.token = user.bot_token
        message = f"[{event.event}] {event.message}"
        await app.client.chat_postMessage(
            channel=user.user_id,
            text=message,
        )
    return {"message": "success"}


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

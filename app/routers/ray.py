from fastapi import APIRouter, HTTPException, Depends, Form, status
from pydantic import BaseModel
from ..dependencies import SlackRayAuth
from ..slack import app
from ..slack.blocks import successful_login_block


router = APIRouter(tags=['ray'])


class RayEvent(BaseModel):
    event: str
    source: str
    job_id: str | None
    message: str = ''
    data: dict | None


@router.post('/ray/events', status_code=status.HTTP_204_NO_CONTENT)
async def ray_events(
    event: RayEvent,
    auth: SlackRayAuth = Depends()
):
    """Receives and responds to an event from the RAY platform."""
    # TODO: loop every account and check is subscribed.
    app.client.token = auth.slack_accounts[0].bot_token
    message = f'{event.message} ({event.event})'
    if event.job_id:
        message += f' ({event.job_id})'
    await app.client.chat_postMessage(
        channel="C03Q3KR98ER",  # hard-coded for now
        text=message,
    )


@router.post('/ray/connect', status_code=status.HTTP_204_NO_CONTENT)
async def connect(
    user_id: str = Form(),
    team_id: str = Form(),
    app_id: str = Form(),
    channel_id: str = Form(),
    username: str = Form(),
    auth: SlackRayAuth = Depends()
):
    """Called by DeltaRay to notify a user that their Slack account has been
    successfully connected to their DeltaRay account.
    """
    # Make sure the Slack account in the token matches the body for extra validation.
    accounts = [acc for acc in auth.slack_accounts if acc.user_id ==
                user_id and acc.team_id == team_id and acc.app_id == app_id]
    if not accounts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    account = accounts[0]

    app.client.token = account.bot_token
    await app.client.chat_postEphemeral(
        channel=channel_id,
        user=account.user_id,
        blocks=successful_login_block(account.user_id, username),
        text='Login was successful!',
    )

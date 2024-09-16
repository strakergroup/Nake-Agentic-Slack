import asyncio
import json
from typing import Any

from sqlalchemy import text
from slack_sdk.web.async_client import AsyncWebClient
from buglog import notify_exception

from ...auth.connector import SlackUser
from ...dependencies import RayEvent
from ...database import engines
from ...slack.templates.messages import SlackMessage


async def log_notification(
    event: str,
    event_data: dict[str, Any],
    user_id: str,
    channel_id: str,
    ray_client_id: str,
    message: str,
):
    """Logs a Slack notification which was sent to a Slack user to the database."""
    try:
        with engines["ray_integration_log"].begin() as conn:
            sql = text(
                """
                INSERT INTO slack_logs_notifications
                    (event, user_id, channel_id, client_uuid, payload, message)
                VALUES
                    (:event, :user_id, :channel_id, :client_uuid, :payload, :message)
                """
            ).bindparams(
                event=event,
                user_id=user_id,
                channel_id=channel_id,
                client_uuid=ray_client_id,
                payload=event_data,
                message=message,
            )
            conn.execute(sql)
    except Exception as e:
        notify_exception(e)


async def post_notification(
    client: AsyncWebClient,
    event: RayEvent,
    slack_user: SlackUser,
    message: SlackMessage,
):
    """Post a notification message to a Slack user. This is logged to the
    database.
    """
    client.token = slack_user.bot_token
    await client.chat_postMessage(
        channel=slack_user.user_id,
        text=message.text,
        blocks=message.blocks,
    )
    asyncio.create_task(
        log_notification(
            event=event.event,
            event_data=event.data,
            user_id=slack_user.user_id,
            channel_id=slack_user.user_id,
            ray_client_id=slack_user.ray_client_id,
            message=type(message).__name__,
        )
    )


async def post_notification_ephemeral(
    client: AsyncWebClient,
    channel_id: str,
    event: RayEvent,
    slack_user: SlackUser,
    message: SlackMessage,
):
    """Post a notification message (ephemeral) to a Slack user. This is logged to the
    database.
    """
    client.token = slack_user.bot_token
    await client.chat_postEphemeral(
        channel=channel_id,
        user=slack_user.user_id,
        text=message.text,
        blocks=message.blocks,
    )
    asyncio.create_task(
        log_notification(
            event=event.event,
            event_data=event.data,
            user_id=slack_user.user_id,
            channel_id=channel_id,
            ray_client_id=slack_user.ray_client_id,
            message=type(message).__name__,
        )
    )

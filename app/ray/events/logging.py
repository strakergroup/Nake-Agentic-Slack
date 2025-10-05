import asyncio
import json
from typing import Any

from buglog import notify_exception
from slack_bolt.context.respond.async_respond import AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import text

from ...auth.connector import SlackUser
from ...database import engines
from ...dependencies import RayEvent
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
                payload=json.dumps(event_data),
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
    channel_id: str | None = None,
    thread_ts: str | None = None,
    is_edit: bool = False,
    response_url: str | None = None,
):
    """Post a notification message to a Slack user. This is logged to the
    database.

    Args:
        client: The Slack client
        event: The Ray event
        slack_user: The Slack user
        message: The message to send
        channel_id: Optional channel ID (defaults to user_id)
        thread_ts: Optional thread timestamp for threading
        is_edit: Whether this is an edit operation
        response_url: Optional response URL for interactive responses
    """
    client.token = slack_user.bot_token
    target_channel = channel_id or slack_user.user_id

    # Determine the appropriate response method based on parameters
    if response_url and not is_edit:
        # Use AsyncRespond for webhook responses
        respond = AsyncRespond(response_url=response_url)
        response = await respond(
            text=message.text,
            blocks=message.blocks,
            thread_ts=thread_ts,
        )
    elif is_edit and thread_ts:
        # Use chat_update for editing existing messages
        response = await client.chat_update(
            channel=target_channel,
            text=message.text,
            blocks=message.blocks,
            ts=thread_ts,
        )
    else:
        # Default to chat_postMessage
        response = await client.chat_postMessage(
            channel=target_channel,
            text=message.text,
            blocks=message.blocks,
            thread_ts=thread_ts,
        )

    asyncio.create_task(
        log_notification(
            event=event.event,
            event_data=event.data,
            user_id=slack_user.user_id,
            channel_id=target_channel,
            ray_client_id=slack_user.ray_client_id,
            message=type(message).__name__,
        )
    )
    return response


async def post_notification_ephemeral(
    client: AsyncWebClient,
    channel_id: str,
    event: RayEvent,
    slack_user: SlackUser,
    message: SlackMessage,
    thread_ts: str | None = None,
    is_edit: bool = False,
    response_url: str | None = None,
):
    """Post a notification message (ephemeral) to a Slack user. This is logged to the
    database.

    Args:
        client: The Slack client
        channel_id: The channel ID to post to
        event: The Ray event
        slack_user: The Slack user
        message: The message to send
        thread_ts: Optional thread timestamp for threading
        is_edit: Whether this is an edit operation
        response_url: Optional response URL for interactive responses
    """
    client.token = slack_user.bot_token

    # For ephemeral messages, we typically use chat_postEphemeral
    # but we can also support other methods based on parameters
    if response_url and not is_edit:
        # Use AsyncRespond for webhook responses
        respond = AsyncRespond(response_url=response_url)
        await respond(
            text=message.text,
            blocks=message.blocks,
            thread_ts=thread_ts,
        )
    elif is_edit and thread_ts:
        # Use chat_update for editing existing ephemeral messages
        await client.chat_update(
            channel=channel_id,
            text=message.text,
            blocks=message.blocks,
            ts=thread_ts,
        )
    else:
        # Default to chat_postEphemeral
        await client.chat_postEphemeral(
            channel=channel_id,
            user=slack_user.user_id,
            text=message.text,
            blocks=message.blocks,
            thread_ts=thread_ts,
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

import asyncio
import json
from typing import Any, Union

import buglog
from slack_bolt.context.respond.async_respond import AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook import WebhookResponse
from sqlalchemy import text
from straker_utils.sql.async_engine import execute

from ...auth.connector import SlackUser
from ...database import async_engines
from ...dependencies import RayEvent
from ...slack.templates.messages import SlackMessage
from ...slack.web import get_mt_ts_cached, set_mt_ts_edit


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
        # Use async engine for database operations
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
        await execute(sql, async_engines["ray_integration_log"], commit_after=True)
    except Exception as e:
        buglog.notify_exception(e)


async def post_notification(
    client: AsyncWebClient,
    event: RayEvent,
    slack_user: SlackUser,
    message: SlackMessage,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    is_edit: bool = False,
    response_url: str | None = None,
    display_format: str | None = None,
    post_thread: bool = False,
) -> Union[AsyncSlackResponse, WebhookResponse]:
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
        display_format: Display format for the message ("thread" or "message")
        post_thread: Whether to post in thread
    """
    client.token = slack_user.bot_token
    target_channel = channel_id or slack_user.user_id

    # Handle display_format logic for channel_translation
    if display_format:
        timestamp = None
        if is_edit:
            timestamp = await get_mt_ts_cached(thread_ts) if thread_ts else None

        if display_format == "thread":
            if timestamp:
                response = await client.chat_update(
                    channel=target_channel,
                    text=message.text,
                    blocks=message.blocks,
                    ts=timestamp,
                )
            else:
                response = await client.chat_postMessage(
                    channel=target_channel,
                    text=message.text,
                    blocks=message.blocks,
                    thread_ts=thread_ts,
                )
                # save timestamp to cache
                if thread_ts:
                    asyncio.create_task(
                        set_mt_ts_edit(send_ts=thread_ts, reply_ts=response["ts"])
                    )
        elif display_format == "message":
            if timestamp:
                response = await client.chat_update(
                    channel=target_channel,
                    text=message.text,
                    blocks=message.blocks,
                    ts=timestamp,
                )
            else:
                response = await client.chat_postMessage(
                    channel=target_channel,
                    text=message.text,
                    blocks=message.blocks,
                    thread_ts=thread_ts if post_thread else None,
                )
                # save timestamp to cache
                if thread_ts:
                    asyncio.create_task(
                        set_mt_ts_edit(send_ts=thread_ts, reply_ts=response["ts"])
                    )
    else:
        # Determine the appropriate response method based on parameters
        if response_url and not is_edit:
            # Use AsyncRespond for webhook responses
            respond = AsyncRespond(response_url=response_url)
            response: Union[AsyncSlackResponse, WebhookResponse] = await respond(
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


async def post_channel_translation_notification(
    client: AsyncWebClient,
    event: RayEvent,
    slack_user: SlackUser,
    message: SlackMessage,
    channel_id: str,
    thread_ts: str | None = None,
    is_edit: bool = False,
    display_format: str | None = None,
    message_ts: str | None = None,
):
    """Post a channel translation notification with display format handling.

    Args:
        client: The Slack client
        event: The Ray event
        slack_user: The Slack user
        message: The message to send
        channel_id: Channel ID to post to
        thread_ts: Optional thread timestamp for threading
        is_edit: Whether this is an edit operation
        display_format: Display format for the message ("thread" or "message")
        message_ts: Message timestamp to use for thread creation
    """
    client.token = slack_user.bot_token

    # Handle display_format logic for channel_translation
    timestamp = None
    if is_edit:
        timestamp = await get_mt_ts_cached(message_ts) if message_ts else None

    # Use message_ts for thread creation if available, otherwise fall back to thread_ts
    thread_timestamp = message_ts or thread_ts

    # Determine thread behavior based on display_format
    use_thread = False
    if display_format == "thread" or thread_ts:
        use_thread = True
    elif display_format == "message":
        use_thread = False  # Messages are posted as standalone messages, not in threads
    # If no display_format specified, default to no thread

    if timestamp:
        response = await client.chat_update(
            channel=channel_id,
            text=message.text,
            blocks=message.blocks,
            ts=timestamp,
        )
    else:
        response = await client.chat_postMessage(
            channel=channel_id,
            text=message.text,
            blocks=message.blocks,
            thread_ts=thread_timestamp if use_thread else None,
        )
        # save timestamp to cache
        if thread_timestamp:
            asyncio.create_task(
                set_mt_ts_edit(send_ts=thread_timestamp, reply_ts=response["ts"])
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

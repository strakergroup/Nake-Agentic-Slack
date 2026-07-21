"""Handlers for Slack message and channel lifecycle events."""

from typing import Any, Dict

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext, get_bot_token_async
from app.ray.settings import delete_channel_id
from app.redis import is_duplicate_event
from app.slack.bot_translation import mark_channel_source_deleted
from app.slack.listener_actions import (
    auto_translate_message,
    is_srt_file,
    maybe_show_thread_media_embed_option,
    respond_to_message,
)
from app.slack.utils import is_channel_im
from app.slack.web import get_mt_ts_cached


async def handle_message_event(
    client: AsyncWebClient,
    context: RayContext,
    message: Dict[str, Any],
    body: Dict[str, Any],
):
    # Check for duplicate events
    if context.enterprise_id and await is_duplicate_event(
        context.enterprise_id, "message", message.get("ts")
    ):
        return

    # https://api.slack.com/events/message
    # Respond to messages without threads in 1-on-1 DMs with the bot only,
    # use threads in channels or group conversations (see the "app_mention" event).
    channel_id = context.get("channel_id")
    is_direct_message = message.get("channel_type") == "im" or is_channel_im(channel_id)
    bot_user_id = context.get("bot_user_id")
    text = message.get("text")

    if context.is_bot and is_direct_message:
        return

    if (
        not context.is_bot
        and message.get("thread_ts")
        and message.get("files")
        and any(is_srt_file(file) for file in message.get("files", []))
    ):
        handled = await maybe_show_thread_media_embed_option(client, context, message)
        if handled:
            return
    elif not context.is_bot and is_direct_message:
        # extract team id from body
        body_team_id = body.get("event", {}).get("team")
        if body_team_id:
            token = await get_bot_token_async(
                team_id=body_team_id,
                enterprise_id=context.enterprise_id,
            )
            if token:
                if token != client.token:
                    client.token = token
        await respond_to_message(client, context, message, use_thread=False)
    elif (
        text
        and (not bot_user_id or f"<@{bot_user_id}>" not in text)
        and message.get("user") != bot_user_id
    ):
        # Do not auto-translate if this app is mentioned or posted the message.
        await auto_translate_message(client, context, message)
    else:
        # Do nothing if the Slack app is not mentioned in group chats and
        # auto-translate is disabled.
        pass


async def handle_channel_deleted(event: Dict[str, Any]):
    await delete_channel_id(event.get("channel"))


async def handle_app_mention(
    client: AsyncWebClient, context: RayContext, event: Dict[str, Any]
):
    # Check for duplicate events
    if context.enterprise_id and await is_duplicate_event(
        context.enterprise_id, "app_mention", str(event.get("ts"))
    ):
        return

    # https://api.slack.com/events/app_mention
    # Respond to messages with threads in channel and group chats if mentioned.
    # Remove user mentions from text before processing.
    if not context["is_bot"]:
        if event["text"] or event.get("files", []):
            await respond_to_message(client, context, event, use_thread=True)
        else:
            ...  # TODO Show auto-translate settings modal


async def handle_message_deleted(
    message: Dict[str, Any],
    client: AsyncWebClient,
    body: Dict[str, Any],
    context: RayContext,
):
    if message.get("subtype") == "message_deleted":
        deleted_ts = body["event"]["deleted_ts"]
        # Tombstone the source so an in-flight translation callback skips delivery.
        await mark_channel_source_deleted(deleted_ts)
        timestamp = await get_mt_ts_cached(deleted_ts)
        if timestamp and context.channel_id:
            await client.chat_delete(ts=timestamp, channel=context.channel_id)


async def handle_message_changed(
    client: AsyncWebClient,
    body: Dict[str, Any],
    context: RayContext,
    message: Dict[str, Any],
):
    # Check for duplicate events
    if (
        context.enterprise_id
        and message.get("ts")
        and await is_duplicate_event(
            context.enterprise_id, "message", str(message.get("ts"))
        )
    ):
        return

    if message.get("subtype") == "message_changed":
        if message.get("message", {}).get("subtype") == "tombstone":
            deleted_ts = body["event"]["previous_message"]["ts"]
            # Tombstone the source so an in-flight translation callback skips delivery.
            await mark_channel_source_deleted(deleted_ts)
            timestamp = await get_mt_ts_cached(deleted_ts)
            if timestamp and context.channel_id:
                await client.chat_delete(ts=timestamp, channel=context.channel_id)
        else:
            is_edit = True
            if (
                message["message"].get("text")
                and f"<@{context['bot_user_id']}>" not in message["message"]["text"]
            ):
                # compare text of old message and new message
                old_message = body["event"]["previous_message"]
                if old_message["text"] != message["message"]["text"]:
                    await auto_translate_message(
                        client, context, message["message"], is_edit
                    )

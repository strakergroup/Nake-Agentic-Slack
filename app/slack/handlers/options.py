"""Handlers for Slack select/options endpoints and MT language selection."""

import asyncio
import json
from typing import Any, Dict

from slack_bolt.kwargs_injection.async_args import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.redis import redis_conn
from app.slack.listener_actions import get_groups
from app.slack.middleware import require_ray_client
from app.slack.select_options import get_file_options_cached, get_language_options
from app.slack.web import files_list_simple


async def handle_language_mt_options_selected(body: Dict[str, Any]):
    file_id = body["actions"][0]["block_id"]
    # Handle both single select (selected_option) and multi select (selected_options)
    if "selected_options" in body["actions"][0]:
        # Multi-select: store as JSON array
        selected_languages = [
            option["value"] for option in body["actions"][0]["selected_options"]
        ]
        await redis_conn.set(f"output_file_{file_id}", json.dumps(selected_languages))
    elif "selected_option" in body["actions"][0]:
        # Single select: store as string for backward compatibility
        selected_language = body["actions"][0]["selected_option"]["value"]
        await redis_conn.set(f"output_file_{file_id}", selected_language)


async def handle_language_options(ack: AsyncAck, payload: Dict[str, Any]):
    options = await get_language_options(payload.get("value"))
    await ack(options=options)


async def handle_language_options_uuid(ack: AsyncAck, payload: Dict[str, Any]):
    # returns the language options where the value is the uuid
    options = await get_language_options(payload.get("value"), "uuid")
    await ack(options=options)


async def handle_source_language_option_uuid(ack: AsyncAck, payload: Dict[str, Any]):
    options = await get_language_options(payload.get("value"), "uuid", source_only=True)
    await ack(options=options)


async def handle_group_options(ack: AsyncAck, context: RayContext):
    if await require_ray_client(context):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        options = await get_groups(context["ray"].client)
        await ack(options=options)


async def handle_file_options(
    ack: AsyncAck, payload: Dict[str, Any], client: AsyncWebClient
):
    """This select options endpoint is used as a backup in case there are
    no files available for the new job files input.
    """
    channel_id = payload["action_id"].split("_")[2]
    # Include a bit more than the max 100 options due to filters
    # refresh cache this should not be awaited since this can take time. Seems to cause issue with timeout
    task = asyncio.create_task(
        files_list_simple(client, channel_id=channel_id, count=120)
    )
    # only respond with cached files since time can cause timeout unless files empty
    files = await get_file_options_cached(channel_id)
    if not files:
        files = await task
    if filter := payload.get("value"):
        files = [
            f for f in files if filter.lower().strip() in f["text"]["text"].lower()
        ]
    await ack(options=files[:100])

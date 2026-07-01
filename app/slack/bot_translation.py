"""Helpers for bot channel auto-translation (RAY-80512).

Covers placeholder detection, bot-message debouncing, and edit-generation
tracking so only the latest translation for a source message is delivered.

Debounce strategy
-----------------
Bursty streaming bots (e.g. IBM AskTECHNO) post several messages in quick
succession before the final answer. Instead of translating each one, we:

1. Store the *latest* bot message payload in Redis (overwritten each event).
2. Enqueue a single deferred SAQ job keyed by ``(channel_id, bot_id)`` with a
   ``scheduled`` timestamp ``debounce`` seconds in the future. SAQ's unique-key
   dedup collapses every enqueue in the window into one job, so the timer does
   not need per-message bookkeeping and the work is durable across restarts.
3. When the job fires it reads the latest stored payload and translates that
   one message.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.web.async_client import AsyncWebClient

from ..auth.connector import RayConnection, get_bot_token_async, get_ray_super_group
from ..config import config
from ..redis import redis_conn

logger = logging.getLogger(__name__)

# Matches one or more Slack emoji tokens (:name:) with optional whitespace between.
SLACK_EMOJI_ONLY = re.compile(r"^(?::[\w+-]+:|\s)+$")

CHANNEL_MT_GEN_PREFIX = "channel_mt_gen:"
BOT_DEBOUNCE_PAYLOAD_PREFIX = "bot_translate_debounce:"
GENERATION_TTL_SECONDS = 3600
# Payload outlives the debounce window so the deferred job can always read it.
DEBOUNCE_PAYLOAD_TTL_SECONDS = 120


def is_slack_emoji_only(text: str) -> bool:
    """Return True when *text* is empty or contains only Slack :emoji: tokens."""
    stripped = text.strip()
    if not stripped:
        return True
    return SLACK_EMOJI_ONLY.fullmatch(stripped) is not None


async def bump_channel_mt_generation(message_ts: str) -> int:
    """Increment and return the translation generation for a source message."""
    key = f"{CHANNEL_MT_GEN_PREFIX}{message_ts}"
    generation = await redis_conn.incr(key)
    if int(generation) == 1:
        await redis_conn.expire(key, GENERATION_TTL_SECONDS)
    return int(generation)


async def is_stale_channel_mt_generation(message_ts: str, generation: int) -> bool:
    """Return True when *generation* is older than the latest enqueued generation."""
    cached = await redis_conn.get(f"{CHANNEL_MT_GEN_PREFIX}{message_ts}")
    if not cached:
        return False
    return int(cached) != generation


def _debounce_payload_key(channel_id: str, bot_id: str) -> str:
    return f"{BOT_DEBOUNCE_PAYLOAD_PREFIX}{channel_id}:{bot_id}"


async def _store_bot_debounce_payload(
    channel_id: str, bot_id: str, message: dict[str, Any]
) -> None:
    """Persist the latest bot message so the deferred job can translate it."""
    await redis_conn.set(
        _debounce_payload_key(channel_id, bot_id),
        json.dumps(message, default=str),
        ex=DEBOUNCE_PAYLOAD_TTL_SECONDS,
    )


async def _load_bot_debounce_payload(
    channel_id: str, bot_id: str
) -> dict[str, Any] | None:
    payload = await redis_conn.get(_debounce_payload_key(channel_id, bot_id))
    if not payload:
        return None
    loaded = json.loads(payload)
    if not isinstance(loaded, dict):
        return None
    return loaded


async def schedule_bot_message_translation(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    message: dict[str, Any],
) -> None:
    """Debounce bursty bot posts so only the last message in a window is translated.

    Stores the latest payload and enqueues one deferred, unique-keyed SAQ job
    per ``(channel_id, bot_id)``; repeat enqueues within the window collapse to
    that single job (see module docstring).
    """
    bot_id = message.get("bot_id")
    channel_id = context.get("channel_id")
    team_id = context.get("team_id")
    if not isinstance(bot_id, str) or not isinstance(channel_id, str):
        return
    if not isinstance(team_id, str):
        return

    # Import here to avoid a circular import at module load
    # (dispatch -> queue -> worker -> tasks -> bot_translation).
    from ..saq_jobs import enqueue_debounced_bot_translation

    await _store_bot_debounce_payload(channel_id, bot_id, message)
    await enqueue_debounced_bot_translation(
        channel_id=channel_id,
        bot_id=bot_id,
        team_id=team_id,
        enterprise_id=context.get("enterprise_id"),
        bot_user_id=context.get("bot_user_id"),
        scheduled=int(time.time() + config.bot_translation_debounce_seconds),
    )


async def run_debounced_bot_translation(
    *,
    channel_id: str,
    bot_id: str,
    team_id: str,
    enterprise_id: str | None,
    bot_user_id: str | None,
) -> None:
    """Translate the latest debounced bot message (invoked from the SAQ task)."""
    message = await _load_bot_debounce_payload(channel_id, bot_id)
    if message is None:
        return

    # Late guard: the final debounced payload may still be emoji-only.
    text = message.get("text")
    if not isinstance(text, str) or is_slack_emoji_only(text):
        return

    token = await get_bot_token_async(team_id=team_id, enterprise_id=enterprise_id)
    if not token:
        logger.warning(
            "Skipping debounced bot translation: missing bot token",
            extra={"channel_id": channel_id, "bot_id": bot_id},
        )
        return

    super_group = await get_ray_super_group(team_id, enterprise_id)
    context = AsyncBoltContext(
        {
            "team_id": team_id,
            "enterprise_id": enterprise_id,
            "channel_id": channel_id,
            "bot_user_id": bot_user_id,
            "ray": RayConnection(super_group=super_group or [], client=None),
            "is_bot": True,
        }
    )

    client = AsyncWebClient(token=token)
    from .listener_actions import auto_translate_message

    await auto_translate_message(
        client,
        context,
        message,
        is_edit=False,
        skip_bot_debounce=True,
    )

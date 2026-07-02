"""Helpers for bot channel auto-translation (RAY-80512).

Covers placeholder detection and edit-generation tracking so only the latest
translation for a given source message timestamp is delivered when a message is
edited (``message_changed``). Each distinct channel post is translated on its
own; coalescing applies to edits of the same ``ts``, not to separate messages.
"""

from __future__ import annotations

import re

from ..redis import redis_conn

# Slack :emoji: tokens (e.g. :3dotsloading:). Stripped out before checking for
# real content so emoji-only messages count as placeholders even though the token
# names themselves contain letters.
EMOJI_TOKEN = re.compile(r":[\w+-]+:")
# A single Unicode letter or digit marks real, translatable content. Its absence
# (after emoji tokens are removed) means the message is only placeholder filler:
# whitespace, ellipsis ("..."/"\u2026"), punctuation, or bare Unicode emoji.
TRANSLATABLE_CHAR = re.compile(r"[^\W_]")

CHANNEL_MT_GEN_PREFIX = "channel_mt_gen:"
GENERATION_TTL_SECONDS = 3600

# Marks a source deleted while its translation was in flight, so the late MT
# callback skips delivery (streaming bots post -> delete -> repost each frame).
DELETED_SOURCE_PREFIX = "channel_mt_deleted:"
DELETED_SOURCE_TTL_SECONDS = 900


def is_untranslatable_placeholder(text: str) -> bool:
    """Return True when *text* has no translatable content.

    Covers the placeholders streaming bots emit before their real answer:
    empty/whitespace strings, Slack emoji-only messages (``:3dotsloading:``),
    bare ellipsis (``...`` / ``\u2026``), other punctuation, and Unicode emoji.
    Emoji tokens are removed first so a message that is *only* emoji (whose token
    names contain letters) still counts as a placeholder. Anything containing a
    Unicode letter or digit is treated as real content and left to translate.
    """
    without_emoji = EMOJI_TOKEN.sub("", text)
    return TRANSLATABLE_CHAR.search(without_emoji) is None


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


async def mark_channel_source_deleted(message_ts: str) -> None:
    """Record that *message_ts* was deleted so an in-flight MT callback skips it."""
    key = f"{DELETED_SOURCE_PREFIX}{message_ts}"
    try:
        await redis_conn.set(key, "1", ex=DELETED_SOURCE_TTL_SECONDS)
    except Exception:  # pragma: no cover - best-effort marker
        pass


async def is_channel_source_deleted(message_ts: str) -> bool:
    """Return True when *message_ts* was deleted while its translation was in flight."""
    try:
        return bool(await redis_conn.get(f"{DELETED_SOURCE_PREFIX}{message_ts}"))
    except Exception:  # pragma: no cover - best-effort marker
        return False

from ..redis import redis_conn

BOT_TRANSLATION_RATE_LIMIT = 10
BOT_TRANSLATION_RATE_WINDOW_SECONDS = 300


async def can_translate_bot_message(
    channel_id: str, bot_id: str, *, is_edit: bool = False
) -> bool:
    """Allow up to 10 bot messages per bot, per channel, per 5-minute window.

    Edits are excluded from the quota because they update an already-counted
    source message rather than representing a new bot post.
    """
    if is_edit:
        return True
    key = f"bot_translation_rate:{channel_id}:{bot_id}"
    try:
        count = await redis_conn.incr(key)
        if int(count) == 1:
            await redis_conn.expire(key, BOT_TRANSLATION_RATE_WINDOW_SECONDS)
        return int(count) <= BOT_TRANSLATION_RATE_LIMIT
    except Exception:
        return False

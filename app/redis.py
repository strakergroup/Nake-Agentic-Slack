from straker_utils.redis import get_redis_auto as get_redis_sync
from straker_utils.redis.asyncio import get_redis_auto

redis_conn = get_redis_auto()

redis_sync = get_redis_sync()


async def is_duplicate_event(
    enterprise_id: str, event_type: str, event_ts: str | None, ttl: int = 3600
) -> bool:
    """Check if an event has already been processed.

    Args:
        enterprise_id: The enterprise ID
        event_ts: The event timestamp
        ttl: Time to live in seconds (default 1 hour)

    Returns:
        bool: True if event is a duplicate, False otherwise
    """
    if event_ts is None:
        return False
    try:
        key = f"event:{enterprise_id}:{event_type}:{event_ts}"
        # Atomically claim this event key. If key already exists, this is a duplicate.
        was_set = await redis_conn.set(key, "1", ex=ttl, nx=True)
        if isinstance(was_set, bool):
            return not was_set
        if was_set is None:
            return True
        if isinstance(was_set, str):
            return was_set.upper() != "OK"
        return not bool(was_set)
    except Exception:
        return False

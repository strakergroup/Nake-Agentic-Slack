from straker_utils.redis.asyncio import get_redis_auto
from straker_utils.redis import get_redis_auto as get_redis_sync

redis_conn = get_redis_auto()

redis_sync = get_redis_sync()


async def is_duplicate_event(
    enterprise_id: str, event_ts: str, ttl: int = 3600
) -> bool:
    """Check if an event has already been processed.

    Args:
        enterprise_id: The enterprise ID
        event_ts: The event timestamp
        ttl: Time to live in seconds (default 1 hour)

    Returns:
        bool: True if event is a duplicate, False otherwise
    """
    try:
        key = f"event:{enterprise_id}:{event_ts}"
        # Check if key exists - Redis exists returns 1 or 0
        result = await redis_conn.exists(key)
        if result is None:
            return False
        exists = int(result) == 1
        if not exists:
            # If key doesn't exist, set it with TTL
            await redis_conn.set(key, "1", ex=ttl)
        return exists
    except Exception:
        return False

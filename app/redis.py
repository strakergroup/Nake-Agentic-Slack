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
    key = f"event:{enterprise_id}:{event_ts}"
    # Try to set the key with NX (only if not exists)
    # Returns True if key was set, False if it already existed
    is_new = await redis_conn.set(key, "1", nx=True, ex=ttl)
    return not is_new

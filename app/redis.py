from straker_utils.redis.asyncio import get_redis_auto
from straker_utils.redis import get_redis_auto as get_redis_sync

redis_conn = get_redis_auto()

redis_sync = get_redis_sync()

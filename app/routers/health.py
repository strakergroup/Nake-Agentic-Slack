import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Response, status

from ..config import config
from ..constants import APP_VERSION
from ..database import async_engines, engines
from ..redis import redis_conn
from ..slack import app as slack_app

router = APIRouter()


@router.get("/health")
async def health_check(response: Response, password: str | None = None):
    show_details = password == config.health_check_password.get_secret_value()
    errors: dict[str, Any] = {}
    info: dict[str, Any] = {}

    # Execute tests in parallel.
    # await asyncio.gather(
    #     # _check_database(errors),
    #     _check_slack_api(errors),
    #     # _check_redis(errors),
    #     # TODO: Watson
    # )

    result = {
        "message": "There are some issues" if len(errors) else "OK",
        "environment": config.environment.value,
        "errors": errors,
        "info": info,
        "x": 1,  # TODO Indicator, remove later
    }

    if errors:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        logging.warning(json.dumps(result, indent=4))

    if not show_details:
        result.pop("environment", None)
        result.pop("errors", None)
        result.pop("info", None)
    return result


@router.get("/version")
async def version_info():
    """Endpoint to return application version and current date/time."""
    return {
        "app_version": APP_VERSION,
        "datetime": datetime.now().isoformat(),
    }


@router.get("/connection-pool-stats")
async def connection_pool_stats():
    """Endpoint to return connection pool statistics for all database engines."""
    return _get_connection_pool_stats()


async def _check_database(errors: dict[str, Any]):
    try:
        await async_engines.ping_all()
    except Exception as e:
        errors["database"] = str(e)


async def _check_redis(errors: dict[str, Any]):
    try:
        await redis_conn.ping()
    except Exception as e:
        errors["redis"] = str(e)


async def _check_slack_api(errors: dict[str, Any]):
    try:
        response = await slack_app.client.api_test()
    except Exception as e:
        errors["slack_api"] = str(e)
        return
    if response.status_code != 200:
        errors["slack_api"] = (
            f"api.test returned the status code: {response.status_code}"
        )


def _get_connection_pool_stats() -> dict[str, Any]:
    """Get connection pool statistics for all database engines."""
    stats: dict[str, Any] = {
        "synchronous": {},
        "asynchronous": {},
    }

    # Synchronous engines
    try:
        for db_name, engine in engines._engines.items():
            try:
                pool = engine.pool
                stats["synchronous"][db_name] = {
                    "size": pool.size(),  # Current pool size
                    "checked_in": pool.checked_in(),  # Connections returned to pool
                    "checked_out": pool.checked_out(),  # Connections currently in use
                    "overflow": pool.overflow(),  # Overflow connections
                    "invalidated": pool.invalidated(),  # Invalidated connections
                }
            except Exception as e:
                stats["synchronous"][db_name] = {"error": str(e)}
    except Exception as e:
        stats["synchronous"] = {"error": str(e)}

    # Asynchronous engines
    try:
        if hasattr(async_engines, "_engines"):
            for db_name, engine in async_engines._engines.items():
                try:
                    pool = engine.pool
                    stats["asynchronous"][db_name] = {
                        "size": pool.size(),
                        "checked_in": pool.checked_in(),
                        "checked_out": pool.checked_out(),
                        "overflow": pool.overflow(),
                        "invalidated": pool.invalidated(),
                    }
                except Exception as e:
                    stats["asynchronous"][db_name] = {"error": str(e)}
        else:
            stats["asynchronous"] = {"error": "AsyncDBEnginePool._engines not found"}
    except Exception as e:
        stats["asynchronous"] = {"error": str(e)}

    return stats
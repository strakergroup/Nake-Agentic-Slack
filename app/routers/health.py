import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Response, status

from ..config import config
from ..constants import APP_VERSION
from ..database import async_engines
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

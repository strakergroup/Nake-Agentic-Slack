import asyncio
import json
from typing import Any

from fastapi import APIRouter, Response, status

from ..config import config
from ..database import engines
from ..redis import redis_conn
from ..slack import app as slack_app


# Connect the Slack Bolt endpoints to FastAPI
router = APIRouter()


@router.get("/health")
async def health_check(response: Response, password: str | None = None):
    show_details = password == config.health_check_password
    errors = {}
    info = {}

    checks = [
        _check_database(errors),
        _check_slack_api(errors),
        _check_redis(errors),
        # TODO: Watson
    ]

    # Execute tests in parallel.
    await asyncio.gather(*checks)

    result = {
        "message": "There are some issues" if len(errors) else "OK",
        "environment": config.environment.value,
        "errors": errors,
        "info": info,
    }

    if errors:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        print(json.dumps(result, indent=4))

    if not show_details:
        result.pop("environment", None)
        result.pop("errors", None)
        result.pop("info", None)
    return result


async def _check_database(errors: dict[str, Any]) -> None:
    try:
        await engines.ping_all_async()
    except Exception as e:
        errors["database"] = str(e)


async def _check_redis(errors: dict[str, Any]) -> None:
    try:
        await redis_conn.ping()
    except Exception as e:
        errors["redis"] = str(e)


async def _check_slack_api(errors: dict[str, Any]) -> None:
    try:
        response = await slack_app.client.api_test()
    except Exception as e:
        errors["slack_api"] = str(e)
        return
    if response.status_code != 200:
        errors[
            "slack_api"
        ] = f"api.test returned the status code: {response.status_code}"

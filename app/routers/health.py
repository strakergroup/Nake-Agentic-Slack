from typing import Any
import asyncio
import sentry_sdk
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
    if show_details:
        # Check sentry behind a password to prevent spamming.
        checks.append(_check_sentry(errors, info))

    # Execute tests in parallel.
    await asyncio.gather(*checks)

    if errors:
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    result = {"message": "There are some issues" if len(errors) else "OK"}
    # TODO: status code
    if show_details:
        result["environment"] = config.environment.value
        result["errors"] = errors
        result["info"] = info
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


async def _check_sentry(errors: dict[str, Any], info: dict[str, Any]) -> None:
    event_id = sentry_sdk.capture_message("Health check", "debug")
    if event_id:
        info["sentry"] = "Check for a 'Health check' (debug) issue in Sentry"
    else:
        errors["sentry"] = "Sentry is not set up"

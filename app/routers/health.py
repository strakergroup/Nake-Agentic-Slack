from typing import Any
import asyncio
import sentry_sdk
from fastapi import APIRouter

from ..config import config
from ..database import engines
from ..slack import app as slack_app


# Connect the Slack Bolt endpoints to FastAPI
router = APIRouter()


@router.get("/health")
async def health_check(password: str | None = None):
    show_details = password == config.health_check_password
    errors = {}
    info = {}

    checks = [
        _check_database(errors, info),
        _check_slack_api(errors, info)
        # TODO: Redis when applicable
        # TODO: Watson
    ]
    if show_details:
        # Check sentry behind a password to prevent spamming.
        checks.append(_check_sentry(errors, info))

    # Execute tests in parallel.
    await asyncio.gather(*checks)

    result = {"message": "There are some issues" if len(errors) else "OK"}
    if show_details:
        result["environment"] = config.environment.value
        result["errors"] = errors
        result["info"] = info
    return result


async def _check_database(errors: dict[str, Any], info: dict[str, Any]) -> None:
    try:
        engines.ping_all()
    except Exception as e:
        errors["database"] = str(e)


async def _check_slack_api(errors: dict[str, Any], info: dict[str, Any]) -> None:
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

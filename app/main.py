import logging
from fastapi import FastAPI
import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from .database import engines
from .config import config, Environment
from .routers import slack, ray


if not config.sentry_dsn:
    logging.warning("Sentry is not set up (SENTRY_DSN is missing)")
sentry_sdk.init(
    dsn=config.sentry_dsn,
    environment=(
        config.environment.value if config.environment != Environment.live else None
    ),
    integrations=[
        StarletteIntegration(),
        FastApiIntegration(),
        SqlalchemyIntegration(),
    ],
    send_default_pii=True,
    request_bodies="medium",
    traces_sample_rate=0.1,
)


# Configure FastAPI
app = FastAPI()
app.include_router(slack.router)
app.include_router(ray.router)


@app.get("/")
async def root():
    return {"message": "Slack Ray Translator App"}


@app.get("/health")
async def health_check(password: str | None = None):
    show_details = password == config.health_check_password
    errors = {}
    info = {}

    # Databases.
    try:
        engines.ping_all()
    except Exception as e:
        errors["database"] = str(e)
    # TODO: Redis when applicable
    # TODO: Watson
    # Sentry / GlitchTip
    if show_details:
        # Check sentry behind a password to prevent spamming.
        event_id = sentry_sdk.capture_message("Health check", "debug")
        if event_id:
            info["sentry"] = "Check for a 'Health check' (debug) issue in Sentry"
        else:
            errors["sentry"] = "Sentry is not set up"

    result = {"message": "There are some issues" if len(errors) else "OK"}
    if show_details:
        result["environment"] = config.environment.value
        result["errors"] = errors
        result["info"] = info
    return result

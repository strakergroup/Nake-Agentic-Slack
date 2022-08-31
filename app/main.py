import logging
from fastapi import FastAPI
import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration

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
    ],
    traces_sample_rate=1.0,
)


# Configure FastAPI
app = FastAPI()
app.include_router(slack.router)
app.include_router(ray.router)


@app.get("/")
async def root():
    return {"message": "Slack Ray Translator App"}


@app.get("/health")
async def health_check():
    # TODO: Check .env, DB, Redis, sentry/GlitchTip, etc.
    return {"message": "OK", "errors": []}


if config.environment != Environment.live:
    # This endpoint is for testing only, disable on live.
    @app.get("/sentry-debug")
    async def sentry_debug():
        raise Exception("Testing Sentry configuration")

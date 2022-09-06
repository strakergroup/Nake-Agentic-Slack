import logging
from fastapi import FastAPI
import sentry_sdk
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from .config import config, Environment
from .routers import slack, ray, health


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
app.include_router(health.router)


@app.get("/")
async def root():
    return {"message": "Slack Ray Translator App"}

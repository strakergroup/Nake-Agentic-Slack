from contextlib import asynccontextmanager

import buglog
from elasticapm.contrib.starlette import ElasticAPM, make_apm_client
from fastapi import FastAPI

# IMPORTANT: Patch notify_exception BEFORE any other imports that use buglog.notify_exception
# This ensures all modules get the wrapped version
from app.slack.utils import _wrap_notify_exception

from .config import Environment, config, domains

# Configure BugLog
buglog.init(
    listener=config.buglog_listener_url,
    app_name="Slack RAY Translator",
    hostname=domains.slack_ray_translator,
)

# Patch buglog.notify_exception to also send to Slack
# This wraps the function so every call to notify_exception also sends to Slack
_original_notify_exception = buglog.notify_exception
buglog.notify_exception = _wrap_notify_exception(_original_notify_exception)

# Import other modules AFTER patching notify_exception
# This ensures all modules that import notify_exception get the wrapped version
# noqa: E402 - imports must be after patch to ensure all modules use wrapped notify_exception
from .routers import health, ray, slack  # noqa: E402
from .slack.select_options import (  # noqa: E402
    initialize_languages_cache,
)

# Log application version
APP_VERSION = "1.0.0"  # Replace with your actual version
print("Starting Slack RAY Translator - Version: {APP_VERSION}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for app startup/shutdown events."""
    # Startup
    try:
        await initialize_languages_cache()
    except Exception as e:
        buglog.notify_exception(e)

    yield

    # Shutdown (if needed)


# Configure FastAPI
app = FastAPI(
    title="Straker Translate for Slack",
    description="The Slack app API for Straker LanguageCloud",
    docs_url="/docs" if config.environment != Environment.production else None,
    redoc_url="/redoc" if config.environment != Environment.production else None,
    lifespan=lifespan,
)
app.include_router(slack.router, tags=["slack"])
app.include_router(ray.router, tags=["ray"])
app.include_router(health.router, tags=["health"])


@app.middleware("http")
async def buglog_middleware(request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        buglog.notify_exception(e)
        raise


# Configure Elastic APM
if config.elastic_apm_server_url:
    apm = make_apm_client(
        {
            "SERVICE_NAME": "int-slack-ray-translator",
            "SERVER_URL": config.elastic_apm_server_url,
            "ENVIRONMENT": config.environment.value,
            "TRANSACTION_IGNORE_URLS": ["/health"],
            "TRANSACTIONS_IGNORE_PATTERNS": ["^OPTIONS ", "/health"],
        }
    )
    app.add_middleware(ElasticAPM, client=apm)


@app.get("/")
async def root():
    return {"message": "Straker Translate API"}

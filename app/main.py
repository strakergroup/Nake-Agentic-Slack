from contextlib import asynccontextmanager

import buglog
from elasticapm.contrib.starlette import ElasticAPM, make_apm_client
from fastapi import FastAPI

from .api.http_client import close_shared_client
from .config import Environment, config, domains
from .constants import APP_VERSION
from .routers import health, ray, slack
from .saq_jobs.worker import start_worker, stop_worker
from .slack.buglog_notifier import notify_exception
from .slack.select_options import (
    initialize_languages_cache,
)

# Configure BugLog
buglog.init(
    listener=config.buglog_listener_url,
    app_name="Slack RAY Translator",
    hostname=domains.slack_ray_translator,
)

# Log application version
print(f"Starting Slack RAY Translator - Version: {APP_VERSION}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for app startup/shutdown events."""
    try:
        await initialize_languages_cache()
    except Exception as e:
        notify_exception(e)

    try:
        await start_worker()
    except Exception as e:
        notify_exception(e, "Failed to start SAQ worker on app startup")
        raise

    yield

    try:
        await stop_worker()
    except Exception as e:
        notify_exception(e, "Failed to stop SAQ worker on app shutdown")
    await close_shared_client()


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
async def csp_middleware(request, call_next):
    """Middleware to add Content Security Policy header to all responses."""
    response = await call_next(request)
    # Basic CSP policy: only allow resources from same origin, block inline scripts/styles
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response


def _notify_exception_from_middleware(exc: BaseException) -> None:
    """Notify for each ``Exception`` leaf under ``BaseExceptionGroup`` or a single error."""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            _notify_exception_from_middleware(sub)
    elif isinstance(exc, Exception):
        notify_exception(exc)


@app.middleware("http")
async def buglog_middleware(request, call_next):
    try:
        return await call_next(request)
    except BaseExceptionGroup as e:
        _notify_exception_from_middleware(e)
        raise
    except Exception as e:
        notify_exception(e)
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

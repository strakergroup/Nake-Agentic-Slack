from fastapi import FastAPI
from elasticapm.contrib.starlette import make_apm_client, ElasticAPM
import buglog

from .config import Environment, config, domains
from .routers import slack, ray, health


# Configure BugLog
buglog.init(
    listener=config.buglog_listener_url,
    app_name="Slack RAY Translator",
    hostname=domains.slack_ray_translator,
)

# Log application version
APP_VERSION = "1.0.0"  # Replace with your actual version
print("Starting Slack RAY Translator - Version: {APP_VERSION}")

# Configure FastAPI
app = FastAPI(
    title="Straker Translate for Slack",
    description="The Slack app API for Straker LanguageCloud",
    docs_url="/docs" if config.environment != Environment.production else None,
    redoc_url="/redoc" if config.environment != Environment.production else None,
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

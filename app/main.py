from socket import gethostname

import buglog
from fastapi import FastAPI
from elasticapm.contrib.starlette import make_apm_client, ElasticAPM

from .config import config, domains
from .routers import slack, ray, health


# Configure BugLog
buglog.init(
    listener=config.buglog_listener_url,
    app_name="Slack RAY Translator",
    hostname=f"{domains.slack_ray_translator.split('//')[1]} ({gethostname()})",
)


# Configure FastAPI
app = FastAPI()
app.include_router(slack.router)
app.include_router(ray.router)
app.include_router(health.router)


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
            "ENVIRONMENT": "production"
            if config.environment.value == "live"
            else config.environment.value,
            "TRANSACTION_IGNORE_URLS": ["/", "/health"],
        }
    )
    app.add_middleware(ElasticAPM, client=apm)


@app.get("/")
async def root():
    return {"message": "Slack Ray Translator App"}

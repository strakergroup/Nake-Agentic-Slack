import base64
import hashlib
from enum import Enum
from pydantic import BaseSettings, Field, HttpUrl, SecretBytes, SecretStr, validator
from sqlalchemy import text
from straker_utils.domain import StrakerDomains

from .database import engines


class Environment(str, Enum):
    local = "local"
    dev = "dev"
    uat = "uat"
    live = "live"


class StrakerConfig(BaseSettings):
    """App configuration settings related to Straker apps and environment.
    Some settings come from the environment variables and some settings are
    derived from from the `environment` setting.
    """

    # Settings from environment variables.
    environment: Environment = Field(env="ENVIRONMENT")
    elastic_apm_server_url: str | None = Field(None, env="ELASTIC_APM_SERVER_URL")
    # Derived settings.
    base_url: HttpUrl = None
    slack_deltaray_key: SecretBytes = None
    slack_queue_proxy_secret: SecretStr = None
    health_check_password: SecretStr = None

    @validator("base_url")
    def default_base_url(cls, v, values):
        if v:
            return v.strip("/")
        match values["environment"]:
            case (Environment.local | Environment.dev | Environment.uat) as env:
                return f"https://{env.value}-slack-deltaray.strakertranslations.com"
            case Environment.live:
                return "https://slack-deltaray.strakertranslations.com"
        raise AssertionError(f"Invalid environment value: {values['environment']}")

    @validator("slack_deltaray_key")
    def default_slack_deltaray_key(cls, v, values):
        if v:
            return v
        with engines["ray_integration_readonly"].connect() as conn:
            sql = text(
                """
                SELECT secret_key FROM slack_integration_keys
                WHERE name = :name AND environment = :env
                LIMIT 1
                """
            )
            result = conn.execute(
                sql, {"name": "slack_deltaray", "env": values["environment"].value}
            )
            row = result.first()
            if not row:
                raise AssertionError(
                    "The Slack-DeltaRAY integration key is not in the database"
                )
            return base64.b64decode(row[0].encode())

    @validator("slack_queue_proxy_secret")
    def default_slack_queue_proxy_secret(cls, v, values):
        if v:
            return v
        with engines["ray_integration_readonly"].connect() as conn:
            sql = text(
                """
                SELECT secret_key FROM slack_integration_keys
                WHERE name = :name AND environment = :env
                LIMIT 1
                """
            )
            result = conn.execute(
                sql, {"name": "slack_queue_proxy", "env": values["environment"].value}
            )
            row = result.first()
            if not row:
                raise AssertionError(
                    "The Slack-Queue-Proxy integration key is not in the database"
                )
            return row[0]

    @validator("health_check_password")
    def default_health_check_password(cls, v, values):
        return hashlib.sha512(
            base64.b64encode(values["slack_deltaray_key"])
        ).hexdigest()

    class Config:
        allow_mutation = False


config = StrakerConfig()
domains = StrakerDomains.from_environment()

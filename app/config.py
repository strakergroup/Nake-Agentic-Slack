import base64
import hashlib

from pydantic import BaseSettings, Field, HttpUrl, SecretBytes, SecretStr, validator
from sqlalchemy import text
from straker_utils.domain import StrakerDomains
from straker_utils.environment import Environment, get_current_environment

from .database import engines


domains = StrakerDomains.from_environment()


class StrakerConfig(BaseSettings):
    """App configuration settings related to Straker apps and environment.
    Some settings come from the environment variables and some settings are
    derived from from the `environment` setting.
    """

    environment: Environment = None
    # Settings from environment variables.
    slack_client_id: str = Field(env="SLACK_CLIENT_ID", min_length=1)
    slack_client_secret: SecretStr = Field(env="SLACK_CLIENT_SECRET", min_length=1)
    # taus_api_key: SecretStr = Field(env="TAUS_API_KEY", min_length=1)
    elastic_apm_server_url: str | None = Field(None, env="ELASTIC_APM_SERVER_URL")
    # Derived settings.
    base_url: HttpUrl = None
    buglog_listener_url: str = ""
    slack_deltaray_key: SecretBytes = None
    slack_queue_proxy_secret: SecretStr = None
    health_check_password: SecretStr = None
    languagecloud_api_key: SecretStr = None

    @validator("environment", pre=True)
    def environment_validator(cls, v, values):
        return get_current_environment()

    @validator("buglog_listener_url")
    def default_buglog_listener_url(cls, v, values):
        return f"{domains.buglog}/bugLog/listeners/bugLogListenerREST.cfm"

    @validator("base_url")
    def default_base_url(cls, v, values):
        if v and values["environment"] == Environment.local:
            return v.strip("/")
        return domains.slack_ray_translator

    @validator("slack_deltaray_key")
    def default_slack_deltaray_key(cls, v, values):
        if v:
            return v
        with engines["ray_integration_readonly"].connect() as conn:
            sql = text(
                """
                SELECT secret_key FROM integration_keys
                WHERE name = :name AND environment = :env
                LIMIT 1
                """
            )
            result = conn.execute(
                sql,
                {
                    "name": "slack_deltaray",
                    "env": (
                        "live"
                        if values["environment"] == Environment.production
                        else values["environment"].value
                    ),
                },
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
                SELECT secret_key FROM integration_keys
                WHERE name = :name AND environment = :env
                LIMIT 1
                """
            )
            result = conn.execute(
                sql,
                {
                    "name": "slack_streams",
                    "env": (
                        "live"
                        if values["environment"] == Environment.production
                        else values["environment"].value
                    ),
                },
            )
            row = result.first()
            if not row:
                raise AssertionError(
                    "The Slack-Streams integration key is not in the database"
                )
            return row[0]

    @validator("health_check_password")
    def default_health_check_password(cls, v, values):
        return hashlib.sha512(
            base64.b64encode(values["slack_deltaray_key"])
        ).hexdigest()

    @validator("languagecloud_api_key")
    def default_languagecloud_api_key(cls, v, values):
        if v:
            return v
        with engines["ray_integration_readonly"].connect() as conn:
            sql = text(
                """
                SELECT secret_key FROM integration_keys
                WHERE name = :name AND environment = :env
                LIMIT 1
                """
            )
            result = conn.execute(
                sql,
                {"name": "languagecloud_api", "env": values["environment"].value},
            )
            row = result.first()
            if not row:
                raise AssertionError(
                    "The languagecloud_api integration key is not in the database"
                )
            return SecretStr(row[0])

    class Config:
        allow_mutation = False


config = StrakerConfig()  # type: ignore

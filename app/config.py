import base64
import hashlib

from dotenv import load_dotenv
from pydantic import (
    Field,
    SecretBytes,
    SecretStr,
    field_validator,
    ValidationInfo,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from straker_utils.domain import StrakerDomains
from straker_utils.environment import Environment

from .database import engines


load_dotenv()


domains = StrakerDomains.from_environment()


class StrakerConfig(BaseSettings):
    """App configuration settings related to Straker apps and environment.
    Some settings come from the environment variables and some settings are
    derived from from the `environment` setting.
    """

    model_config = SettingsConfigDict(frozen=True)

    # Settings from environment variables.
    environment: Environment
    slack_client_id: str = Field(min_length=1)
    slack_client_secret: SecretStr = Field(min_length=1)
    slack_signing_secret: SecretStr = Field(min_length=1)
    watson_assistant_id: str = Field(min_length=1)
    watson_environment_id: str = Field(min_length=1)
    google_mt_api_key: SecretStr = SecretStr("")
    microsoft_mt_api_key: SecretStr = SecretStr("")
    microsoft_mt_website: str = ""
    microsoft_mt_url: str = ""
    # taus_api_key: SecretStr = Field(min_length=1)
    elastic_apm_server_url: str | None = None
    # Derived settings.
    buglog_listener_url: str = ""
    slack_deltaray_key: SecretBytes = SecretBytes(b"")
    slack_queue_proxy_secret: SecretStr = SecretStr("")
    health_check_password: SecretStr = SecretStr("")
    languagecloud_api_key: SecretStr = SecretStr("")
    path_wb_shared: str = ""

    @field_validator("google_mt_api_key", mode="after")
    def validate_google_mt_api_key(cls, v, info: ValidationInfo):
        if info.data["environment"] in [Environment.production, Environment.uat]:
            if not v:
                raise ValueError("GOOGLE_MT_API_KEY must be set in production and uat")
        return v
    def validate_microsoft_mt_api_key(cls, v, info: ValidationInfo):
        if info.data["environment"] in [Environment.production, Environment.uat]:
            if not v:
                raise ValueError("MICROSOFT_MT_API_KEY must be set in production and uat")
        return v

    @field_validator("buglog_listener_url", mode="before")
    def default_buglog_listener_url(cls, v):
        return f"{domains.buglog}/bugLog/listeners/bugLogListenerREST.cfm"

    @field_validator("slack_deltaray_key", mode="before")
    def default_slack_deltaray_key(cls, v, info: ValidationInfo):
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
                        if info.data["environment"] == Environment.production
                        else info.data["environment"].value
                    ),
                },
            )
            row = result.first()
        if not row:
            raise AssertionError(
                "The Slack-DeltaRAY integration key is not in the database"
            )
        return SecretBytes(base64.b64decode(row[0].encode()))

    @field_validator("slack_queue_proxy_secret", mode="before")
    def default_slack_queue_proxy_secret(cls, v, info: ValidationInfo):
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
                        if info.data["environment"] == Environment.production
                        else info.data["environment"].value
                    ),
                },
            )
        row = result.first()
        if not row:
            raise AssertionError(
                "The Slack-Streams integration key is not in the database"
            )
        return SecretStr(row[0])

    @field_validator("health_check_password", mode="before")
    def default_health_check_password(cls, v, info: ValidationInfo):
        return SecretStr(
            hashlib.sha512(
                base64.b64encode(info.data["slack_deltaray_key"].get_secret_value())
            ).hexdigest()
        )

    @field_validator("languagecloud_api_key", mode="before")
    def default_languagecloud_api_key(cls, v, info: ValidationInfo):
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
                {"name": "languagecloud_api", "env": info.data["environment"].value},
            )
            row = result.first()
        if not row:
            raise AssertionError(
                "The languagecloud_api integration key is not in the database"
            )
        return SecretStr(row[0])


config = StrakerConfig()  # type: ignore

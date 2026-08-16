import base64
import hashlib

from dotenv import load_dotenv
from pydantic import (
    Field,
    SecretBytes,
    SecretStr,
    ValidationInfo,
    field_validator,
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
    document_mt_pdf_max_size_mb: int = Field(default=25, ge=1)
    document_mt_quote_ttl_seconds: int = Field(default=43200, ge=60, le=86400)
    # Media transcription / embedding / translation quote session TTL (default 12h).
    media_quote_ttl_seconds: int = Field(default=43200, ge=60, le=86400)
    # taus_api_key: SecretStr = Field(min_length=1)
    elastic_apm_server_url: str | None = None
    # Derived settings.
    buglog_listener_url: str = ""
    slack_deltaray_key: SecretBytes = SecretBytes(b"")
    slack_queue_proxy_secret: SecretStr = SecretStr("")
    health_check_password: SecretStr = SecretStr("")
    languagecloud_api_key: SecretStr = SecretStr("")

    # Google Chat incoming webhook for dev alerts; env: GOOGLE_CHAT_WEBHOOK
    google_chat_webhook: SecretStr = SecretStr("")

    # SAQ (Simple Async Queue) settings for durable background tasks (RAY-79638).
    # The SAQ worker is embedded inside this uvicorn process via the FastAPI
    # lifespan; jobs are persisted in Redis so they survive process restarts.
    saq_queue_name: str = Field(default="slack-ray-translator", min_length=1)
    saq_worker_enabled: bool = True
    saq_worker_concurrency: int = Field(default=10, ge=1, le=100)
    saq_file_submission_queue_name: str = Field(
        default="slack-ray-translator-file-submissions", min_length=1
    )
    saq_file_submission_worker_concurrency: int = Field(default=3, ge=1, le=100)
    saq_small_file_submission_queue_name: str = Field(
        default="slack-ray-translator-small-file-submissions", min_length=1
    )
    saq_small_file_submission_worker_concurrency: int = Field(default=10, ge=1, le=100)
    saq_large_file_submission_threshold_mb: int = Field(default=10, ge=1)
    saq_small_file_upload_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    saq_file_delivery_queue_name: str = Field(
        default="slack-ray-translator-file-delivery", min_length=1
    )
    saq_file_delivery_worker_concurrency: int = Field(default=10, ge=1, le=100)
    saq_background_queue_name: str = Field(
        default="slack-ray-translator-background", min_length=1
    )
    saq_background_worker_concurrency: int = Field(default=5, ge=1, le=100)
    saq_file_upload_retries: int = Field(default=5, ge=0, le=20)
    saq_file_upload_timeout_seconds: int = Field(default=900, ge=10, le=3600)
    saq_logging_retries: int = Field(default=3, ge=0, le=20)
    saq_logging_timeout_seconds: int = Field(default=30, ge=5, le=600)

    # Default 30 days — HT quotes are often accepted well after the first Slack message.
    evaluate_quote_ttl_seconds: int = Field(default=2592000, ge=3600)
    # When true, only Verify Admin/Owner users see quote Accept UI. Non-admins
    # auto-proceed AI+QE on evaluate, then still get a Human Translation quote.
    quote_admin_only: bool = True
    # RAY-81247: CRM member that owns IBM Slack HT jobs when the poster is not
    # logged in. Default is slackhtjobs@ibm.com (RAY-81247 flyway member).
    ht_service_account_member_uuid: str = "6BB48BEF-1EAD-4824-8724-5298CA10AA86"

    @field_validator("google_mt_api_key", mode="after")
    def validate_google_mt_api_key(cls, v, info: ValidationInfo):
        if info.data["environment"] in [Environment.production, Environment.uat]:
            if not v:
                raise ValueError("GOOGLE_MT_API_KEY must be set in production and uat")
        return v

    @field_validator("buglog_listener_url", mode="before")
    def default_buglog_listener_url(cls, v):
        return f"{domains.buglog}/bugLog/listeners/bugLogListenerREST.cfm"

    @property
    def document_mt_pdf_max_size_bytes(self) -> int:
        return self.document_mt_pdf_max_size_mb * 1024 * 1024

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

import os
import base64
from dataclasses import dataclass, field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from .database import engine


@dataclass(frozen=True, slots=True)
class StrakerConfig:
    """App configuration settings related to Straker apps and environment."""

    environment: str
    deltaray_domain: str = field(init=False)
    stingray_domain: str = field(init=False)
    slack_deltaray_key: bytes = field(init=False)

    def __post_init__(self):
        # Validate and format the Straker environment.
        object.__setattr__(self, "environment", self.environment.lower())
        if self.environment not in ["live", "uat", "dev", "local"]:
            raise ValueError(
                "The Straker environment must be one of: live, uat, dev, or local"
            )
        # Get the domains for the environment.
        object.__setattr__(
            self, "deltaray_domain", self.get_deltaray_domain(self.environment)
        )
        object.__setattr__(
            self, "stingray_domain", self.get_stingray_domain(self.environment)
        )
        # Get the keys for the environment.
        object.__setattr__(
            self,
            "slack_deltaray_key",
            self.get_slack_deltaray_key(self.environment, engine),
        )

    @staticmethod
    def get_deltaray_domain(env: str) -> str:
        match env:
            case "local" | "dev" | "uat":
                return f"https://{env}-deltaray.strakertranslations.com"
            case "live":
                return "https://deltaray.strakertranslations.com"
        return ""

    @staticmethod
    def get_stingray_domain(env: str) -> str:
        match env:
            case "local" | "dev" | "uat":
                return f"https://{env}-api.strakertranslations.com"
            case "live":
                return "https://api.strakertranslations.com"
        return ""

    @staticmethod
    def get_slack_deltaray_key(env: str, engine: Engine) -> bytes:
        with engine.connect() as conn:
            sql = text(
                """
                SELECT secret_key FROM integration_keys
                WHERE name = :name AND environment = :env
                LIMIT 1
                """
            )
            result = conn.execute(sql, {"name": "slack_deltaray", "env": env})
            all = result.first()
            if not all:
                raise ValueError(
                    "The Slack-DeltaRay integration key is not in the database"
                )
            return base64.b64decode(all[0].encode())


straker_config = StrakerConfig(os.getenv("STRAKER_ENVIRONMENT", ""))

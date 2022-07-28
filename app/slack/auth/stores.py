from typing import Optional
from slack_sdk.oauth.installation_store.sqlalchemy import SQLAlchemyInstallationStore
from slack_sdk.oauth.installation_store.async_installation_store import AsyncInstallationStore
from slack_sdk.oauth.installation_store.models.installation import Installation
from slack_sdk.oauth.installation_store.models.bot import Bot
from slack_sdk.oauth.state_store.sqlalchemy import SQLAlchemyOAuthStateStore
from slack_sdk.oauth.state_store.async_state_store import AsyncOAuthStateStore


class AsyncSQLAlchemyInstallationStore(SQLAlchemyInstallationStore, AsyncInstallationStore):
    """The install store for async SQLAlchemy apps.

    The included SQLAlchemyInstallStore does not support async apps, so this
    class is required to fill in the gap.
    """

    async def async_save(self, installation: Installation):
        return self.save(installation)

    async def async_save_bot(self, bot: Bot):
        return self.save_bot(bot)

    async def async_find_bot(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        is_enterprise_install: Optional[bool] = False,
    ) -> Optional[Bot]:
        return self.find_bot(
            enterprise_id=enterprise_id,
            team_id=team_id,
            is_enterprise_install=is_enterprise_install,
        )

    async def async_find_installation(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        user_id: Optional[str] = None,
        is_enterprise_install: Optional[bool] = False,
    ) -> Optional[Installation]:
        return self.find_installation(
            enterprise_id=enterprise_id,
            team_id=team_id,
            user_id=user_id,
            is_enterprise_install=is_enterprise_install,
        )

    async def async_delete_bot(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
    ) -> None:
        return self.delete_bot(enterprise_id=enterprise_id, team_id=team_id)

    async def async_delete_installation(
        self,
        *,
        enterprise_id: Optional[str],
        team_id: Optional[str],
        user_id: Optional[str] = None,
    ) -> None:
        return self.delete_installation(enterprise_id=enterprise_id, team_id=team_id, user_id=user_id)


class AsyncSQLAlchemyOAuthStateStore(SQLAlchemyOAuthStateStore, AsyncOAuthStateStore):
    """The OAuth state store for async SQLAlchemy apps.

    The included SQLAlchemyOAuthStateStore does not support async apps, so this
    class is required to fill in the gap.
    """

    async def async_issue(self, *args, **kwargs) -> str:
        return self.issue(*args, **kwargs)

    async def async_consume(self, state: str) -> bool:
        return self.consume(state)

from slack_sdk.oauth.state_store.sqlalchemy import SQLAlchemyOAuthStateStore
from slack_sdk.oauth.state_store.async_state_store import AsyncOAuthStateStore


class AsyncSQLAlchemyOAuthStateStore(SQLAlchemyOAuthStateStore, AsyncOAuthStateStore):
    """The OAuth state store for async SQLAlchemy apps.

    The included SQLAlchemyOAuthStateStore does not support async apps, so this
    class is required to fill in the gap.
    """

    async def async_issue(self, *args, **kwargs) -> str:
        return self.issue(*args, **kwargs)

    async def async_consume(self, state: str) -> bool:
        return self.consume(state)

"""
Read this if you are new to SQLAlchemy ORM:
https://docs.sqlalchemy.org/en/20/orm/quickstart.html

Full documentation here:
https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html
"""

from sqlalchemy import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GoogleApiLog(Base):
    """The table for logging Google API usage.

    Table: ray_integration_log.google_api_log
    """

    __tablename__ = "google_api_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_uuid: Mapped[str]
    group_uuid: Mapped[str]
    super_group_uuid: Mapped[str]
    app_name: Mapped[str | None]
    sl: Mapped[str | None]
    tl: Mapped[str | None]
    source_text: Mapped[str | None]
    target_text: Mapped[str | None]
    response: Mapped[dict | None] = mapped_column(JSON)
    word_count: Mapped[int]
    character_count: Mapped[int]

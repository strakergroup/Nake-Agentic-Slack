"""
Read this if you are new to SQLAlchemy ORM:
https://docs.sqlalchemy.org/en/20/orm/quickstart.html

Full documentation here:
https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html
"""

import datetime
from typing import TypeAlias, Literal

from sqlalchemy import JSON, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SlackGroupSettings(Base):
    """Slack settings for a LanguageCloud group.

    Table: `ray_integration.slack_group_settings`
    """

    __tablename__ = "slack_group_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    slack_team_id: Mapped[str] = mapped_column(String(50))
    slack_enterprise_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    modified_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class SlackGroupSettingsTranslation(Base):
    """Auto-translate settings of a Slack channel.

    Table: `ray_integration.slack_group_settings_translation`
    """

    __tablename__ = "slack_group_settings_translation"

    DisplayFormatType: TypeAlias = Literal["thread", "message", "edit"]

    id: Mapped[int] = mapped_column(primary_key=True)
    settings_id: Mapped[int] = mapped_column(Integer, index=True)
    channel_id: Mapped[str] = mapped_column(String(50), index=True)
    display_format: Mapped[DisplayFormatType] = mapped_column(
        Enum("thread", "message", "edit"), server_default="thread"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    modified_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class SlackAutoTranslateChannelsGroupSettings(Base):
    """The Slack channels to enable auto-translation for.

    Table: `ray_integration.slack_group_settings_auto_translate_channels`
    """

    __tablename__ = "slack_group_settings_auto_translate_channels"

    # TODO: might not need this
    id: Mapped[int] = mapped_column(primary_key=True)
    settings_id: Mapped[int] = mapped_column(Integer, index=True)
    channel_id: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    modified_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class SlackGroupSettingsTranslationLangs(Base):
    """The target languages to translate to for Slack auto-translation.

    Table: `ray_integration.slack_group_settings_translation_langs`
    """

    __tablename__ = "slack_group_settings_translation_langs"

    id: Mapped[int] = mapped_column(primary_key=True)
    translation_settings_id: Mapped[int] = mapped_column(Integer, index=True)
    lang: Mapped[str] = mapped_column(String(50), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )
    modified_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp()
    )


class GoogleApiLog(Base):
    """The table for logging Google API usage.

    Table: `ray_integration_log.google_api_log`
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

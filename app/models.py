"""
Read this if you are new to SQLAlchemy ORM:
https://docs.sqlalchemy.org/en/20/orm/quickstart.html

Full documentation here:
https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html
"""

import datetime
from typing import Literal, Optional, TypeAlias

from pydantic import BaseModel
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


class MicrosoftApiLog(Base):
    """The table for logging Microsoft API usage.

    Table: `ray_integration_log.microsoft_api_log`
    """

    __tablename__ = "microsoft_api_log"

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


class Language(Base):
    """Languages that we support for translation.

    Table: `translators.obj_m_langs`
    """

    __tablename__ = "obj_m_langs"

    uuid: Mapped[str] = mapped_column(name="obj_uuid", primary_key=True)
    label: Mapped[str]
    code: Mapped[str] = mapped_column(name="lang")
    bcp_47: Mapped[str] = mapped_column(name="bcp_47")
    shortname: Mapped[str]
    site_shortname: Mapped[str]
    google_code: Mapped[str]
    parent_lang: Mapped[str]
    is_char_lang: Mapped[bool]  # TODO fix this, always True
    tiers: Mapped[int]


class TranscriptionTaskData(BaseModel):
    """Model representing input data for a transcription task"""

    client_id: str
    file_name: str
    download_url: str
    app_token: str
    out_stream_name: str
    service: str
    model: str
    embed_subtitles: bool = False
    tokens_consumed: int
    sandbox: bool = False


class ASRTask(BaseModel):
    """Model representing input data for an ASR task"""

    member_uuid: str
    event_name: str
    app_source: str
    len_ms: int
    service: str
    model: str
    extra_data: dict
    task_data: TranscriptionTaskData


class JobTranscribedResult(BaseModel):
    client_id: str
    error: str | None = None
    task_uuid: str
    file_name: str
    source_file_name: str
    file_id: str
    tokens_consumed: int


class SlackFileTranslationSubmission(Base):
    """Track file translation submissions to prevent duplicates.

    Table: `ray_integration.slack_file_translation_submissions`
    """

    __tablename__ = "slack_file_translation_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(50), index=True)
    team_id: Mapped[str] = mapped_column(String(50), index=True)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int]
    source_language: Mapped[str] = mapped_column(String(10), default="")
    target_language: Mapped[str] = mapped_column(String(10), default="")
    file_id: Mapped[str] = mapped_column(String(50))
    channel_id: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<SlackFileTranslationSubmission(user_id='{self.user_id}', "
            f"file_hash='{self.file_hash}', target_language='{self.target_language}')>"
        )

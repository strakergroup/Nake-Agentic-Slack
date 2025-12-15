"""
Read this if you are new to SQLAlchemy ORM:
https://docs.sqlalchemy.org/en/20/orm/quickstart.html

Full documentation here:
https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html
"""

import datetime
from typing import Literal, Optional, TypeAlias

from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, DateTime, Enum, Integer, String, func
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
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    verify_organization_uuid: Mapped[str | None]
    app_name: Mapped[str | None]
    sl: Mapped[str | None]
    tl: Mapped[str | None]
    source_text: Mapped[str | None]
    target_text: Mapped[str | None]
    response: Mapped[dict | None] = mapped_column(JSON)
    word_count: Mapped[int]
    character_count: Mapped[int]
    email: Mapped[str | None]
    usage_type: Mapped[str | None]
    transaction_uuid: Mapped[str | None]
    channel_name: Mapped[str | None]
    gridfs_file_id: Mapped[str | None]


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
    sandbox: bool = False


class ASRTask(BaseModel):
    """Model representing input data for an ASR task"""

    member_uuid: str
    event_name: str
    app_source: str
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


class TranscriptionRequest(BaseModel):
    """Request model for transcription service - only task_uuid needed."""

    task_uuid: str


class TranscriptionTaskInfo(BaseModel):
    """Model representing transcription task information from database."""

    task_uuid: str
    client_id: str
    file_name: str
    download_url: str
    bot_token: str
    pipeline_type: str
    status: str
    error_message: str | None
    result_file_id: str | None
    result_file_name: str | None
    detected_language: str | None
    extra_data: dict | None
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    # Usage metrics for external billing
    duration_ms: int | None
    source_text_length: int | None
    num_target_languages: int | None
    model: str | None
    service: str | None
    app_source: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ASRTaskResult(BaseModel):
    """Model representing ASR task result data."""

    task_uuid: str
    file_id: str | None
    file_name: str | None
    status: str
    error: str | None


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
    target_language: Mapped[str] = mapped_column(String(10), default="")
    source_language: Mapped[str] = mapped_column(String(10), default="")
    file_id: Mapped[str] = mapped_column(String(50))
    channel_id: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), index=True
    )
    processing_status: Mapped[str] = mapped_column(
        Enum("created", "completed", "failed", name="submission_status"),
        default="created",
        nullable=False,
    )
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False)
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<SlackFileTranslationSubmission(user_id='{self.user_id}', "
            f"file_hash='{self.file_hash}', target_language='{self.target_language}')>"
        )


class TranscriptionTask(Base):
    """Track transcription task status and metadata.

    Table: `sitecommons.transcription_tasks`
    """

    __tablename__ = "transcription_tasks"
    __table_args__ = {"schema": "sitecommons"}

    task_uuid: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    client_id: Mapped[str] = mapped_column(String(50), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    download_url: Mapped[str] = mapped_column(String(500))
    bot_token: Mapped[str] = mapped_column(
        String(255)
    )  # Slack bot token for file download
    pipeline_type: Mapped[str] = mapped_column(String(50), default="transcribe")
    status: Mapped[str] = mapped_column(
        Enum("pending", "processing", "completed", "failed", name="task_status"),
        default="pending",
        nullable=False,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    result_file_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    result_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Performance and analytics fields
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    service: Mapped[str | None] = mapped_column(String(50), nullable=True)
    app_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Usage metrics for external billing (platforms calculate their own costs)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_text_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_target_languages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), index=True
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<TranscriptionTask(task_uuid='{self.task_uuid}', "
            f"status='{self.status}', file_name='{self.file_name}')>"
        )

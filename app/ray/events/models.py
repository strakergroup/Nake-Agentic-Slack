import datetime
from enum import Enum
from typing import Any, Dict, Union

from dateutil.parser import parse
from pydantic import (
    BaseModel,
    Field,
    RootModel,
    field_validator,
    model_validator,
)


class ClientGroup(BaseModel):
    uuid: str
    label: str


class Language(BaseModel):
    code: str
    label: str


class QuoteLangPrice(BaseModel):
    price: float


class QuoteInfo(BaseModel):
    currency: str
    quote: float
    quote_nett: float
    quote_detail_url: str
    quote_accept_url: str
    quote_cancel_url: str
    tl: dict[str, QuoteLangPrice]


class SlackAccountConnectedEvent(BaseModel):
    client_id: str
    username: str
    user_id: str
    team_id: str
    enterprise_id: str | None = None
    channel_id: str | None = None


class ClientSignupEvent(BaseModel):
    client_id: str
    username: str
    email: str
    first_name: str
    last_name: str
    groups: list[ClientGroup]


class ClientApprovedEvent(BaseModel):
    client_id: str
    username: str
    groups: list[ClientGroup]


class JobStatusChangedEvent(BaseModel):
    uuid: str
    id: str
    client_id: str
    status: str
    previous_status: str | None
    sl: Language
    tl: list[Language]


class JobQuoteCreatedEvent(BaseModel):
    uuid: str
    id: str
    client_reference: str
    client_id: str
    status: str
    sl: Language
    tl: list[Language]
    service: str
    turnaround_days: float
    quote: QuoteInfo


class JobQuoteAcceptedEvent(BaseModel):
    uuid: str
    target_date: datetime.datetime
    id: str
    client_id: str

    @field_validator("target_date", mode="before")
    @classmethod
    def parse_target_date(cls, v):
        """Convert string to datetime."""
        if isinstance(v, datetime.datetime):
            return v
        return parse(v)


class JobQuoteCancelledEvent(BaseModel):
    uuid: str
    target_date: datetime.datetime
    id: str
    client_id: str

    @field_validator("target_date", mode="before")
    @classmethod
    def parse_target_date(cls, v):
        """Convert string to datetime."""
        if isinstance(v, datetime.datetime):
            return v
        return parse(v)


class JobTranscribedPath(BaseModel):
    output_file: str


class JobTranscribedEvent(BaseModel):
    """Transcription result event from transcription-service.

    Maps to TranscriptionResult format from transcription-service.
    Includes task_uuid, client_id (for auth), and error.
    All other info is looked up from database.
    """

    task_uuid: str
    client_id: str
    error: str | None = None

    @model_validator(mode="before")
    def extract_output_file(cls, values):
        """Handle both old and new result formats for backward compatibility."""
        # New format (TranscriptionResult from transcription-service) - includes task_uuid, client_id, error
        if "task_uuid" in values:
            return values

        # Old format (legacy support)
        result = values.get("result")
        if result:
            values["task_uuid"] = result.get("task_uuid")
            values["client_id"] = result.get("client_id", "")
            values["error"] = result.get("error")
        return values


class MtErrorTypes(str, Enum):
    INSUFFICIENT_BALANCE = "insufficient_balance"
    SAMPLE_TEXT_NOT_FOUND = "sample_text_not_found"
    CONVERSION_ERROR = "conversion_error"
    FILE_COMPLEXITY_ERROR = "file_complexity_error"
    INVALID_PDF = "invalid_pdf"
    OTHER = "other"


class MtFileRequestSchema(BaseModel):
    task_uuid: str | None = None
    file_id: str
    client_id: str
    channel_id: str
    source_language: str | None = None
    target_language: str | None = None
    target_languages: list[str] = Field(default_factory=list)
    submission_ids: Dict[str, int] = Field(default_factory=dict)
    ai_engine: str
    data_source: str
    submission_id: int | None = None
    embed_subtitles: bool = False
    original_video_file_id: str | None = None
    original_video_file_name: str | None = None

    @model_validator(mode="after")
    def normalize_target_languages(self) -> "MtFileRequestSchema":
        targets = [language for language in self.target_languages if language]
        if self.target_language:
            targets.insert(0, self.target_language)

        deduped_targets = list(dict.fromkeys(targets))
        if not deduped_targets:
            raise ValueError("target_language or target_languages is required")

        self.target_language = deduped_targets[0]
        self.target_languages = deduped_targets
        return self


class MtSuccessResponseSchema(BaseModel):
    task_uuid: str | None = None
    file_id: str
    tokens: int
    client_id: str
    target_language: str
    channel_id: str
    submission_id: int | None = None
    # Deferred charge (RAY-80417): the prepared /mt/transaction payload (document
    # MT + optional combined PDF fee) charged after successful Slack delivery.
    mt_charge: Dict[str, Any] | None = None


class Balance(BaseModel):
    required: int
    balance: int


class MtErrorResponseSchema(BaseModel):
    error: bool
    client_id: str
    channel_id: str
    error_type: MtErrorTypes
    error_data: Dict[str, Any]
    submission_id: int | None = None


class MtFileReponseSchema(RootModel):
    root: Union[MtSuccessResponseSchema, MtErrorResponseSchema]

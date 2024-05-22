import datetime
from enum import Enum
from typing import Any, Dict, Union

from dateutil.parser import parse
from pydantic import BaseModel, RootModel, field_validator, root_validator


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
        return parse(v, dayfirst=True)


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
        return parse(v, dayfirst=True)


class JobTranscribedPath(BaseModel):
    output_file: str


class JobTranscribedEvent(BaseModel):
    output_file: str
    client_id: str
    error: str | None = None

    @root_validator(pre=True)
    def extract_output_file(cls, values):
        result = values.get("result")
        if result:
            values["output_file"] = result.get("output_file")
        return values


class MtErrorTypes(str, Enum):
    INSUFFICIENT_BALANCE = "insufficient_balance"
    SAMPLE_TEXT_NOT_FOUND = "sample_text_not_found"
    OTHER = "other"


class MtFileRequestSchema(BaseModel):
    download_url: str
    file_name: str
    file_token: str
    client_id: str
    target_language: str


class MtSuccessResponseSchema(BaseModel):
    file_id: str
    tokens: int
    client_id: str
    target_language: str


class MtErrorResponseSchema(BaseModel):
    error: bool
    client_id: str
    error_type: MtErrorTypes
    error_data: Dict[str, Any]


class MtFileReponseSchema(RootModel):
    root: Union[MtSuccessResponseSchema, MtErrorResponseSchema]

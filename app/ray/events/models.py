import datetime
from dateutil.parser import parse
from pydantic import BaseModel, validator


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

    @validator("target_date", pre=True)
    def parse_target_date(cls, v):
        """Convert string to datetime."""
        return parse(v, dayfirst=True)


class JobQuoteCancelledEvent(BaseModel):
    uuid: str
    target_date: datetime.datetime
    id: str
    client_id: str

    @validator("target_date", pre=True)
    def parse_target_date(cls, v):
        """Convert string to datetime."""
        return parse(v, dayfirst=True)

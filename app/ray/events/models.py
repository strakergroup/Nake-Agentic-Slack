from pydantic import BaseModel


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
    app_id: str
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


class JobQuoteCreatedEvent(BaseModel):
    uuid: str
    id: str
    client_reference: str
    client_id: str
    status: str
    sl: Language
    tl: list[Language]
    service: str
    turnaround_days: int
    quote: QuoteInfo

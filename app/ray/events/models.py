from pydantic import BaseModel


class Language(BaseModel):
    code: str
    label: str


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


class JobStatusChangedEvent(BaseModel):
    uuid: str
    id: str
    client_id: str
    status: str


class JobQuoteCreatedEvent(BaseModel):
    uuid: str
    id: str
    client_id: str
    status: str
    sl: Language
    tl: list[Language]
    service: str
    turnaround_days: int
    quote_currency: str
    quote: float
    quote_nett: float
    quote_detail_url: str
    quote_accept_url: str
    quote_cancel_url: str

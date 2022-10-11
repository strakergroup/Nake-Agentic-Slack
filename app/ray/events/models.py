from pydantic import BaseModel


class SlackAccountConnectedEvent(BaseModel):
    client_id: str
    username: str
    user_id: str
    team_id: str
    app_id: str
    channel_id: str | None = None


class JobStatusChangedEvent(BaseModel):
    uuid: str
    id: str
    client_id: str
    status: str

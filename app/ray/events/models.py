from pydantic import BaseModel


class SlackAccountConnectedEvent(BaseModel):
    client_id: str
    username: str
    user_id: str
    team_id: str
    app_id: str
    channel_id: str | None = None

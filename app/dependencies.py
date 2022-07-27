from dataclasses import dataclass
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.engine import Connection
from .database import engine
from .slack.auth.connector import validate_ray_authentication_token


# Sub-dependency to get the bearer token.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl='')


class SlackAuth:
    """Dependency class to validate the bearer token and return the client id
    of the client the request is for.
    """

    def __init__(self, token: str = Depends(_oauth2_scheme)) -> None:
        try:
            self.client_id = validate_ray_authentication_token(token)
        except Exception:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Not authenticated')


class SlackRayAuth:
    """Dependency class to validate the bearer token and validate that the
    Slack account is connected with a DeltaRay account. This is similar to
    `SlackAuth` except this also contains the api_key and the connected
    Slack accounts.
    """

    def __init__(self, auth: SlackAuth = Depends()) -> None:
        self.client_id = auth.client_id
        self.api_key = 'test_api_key' # TODO
        self.slack_accounts: list[SlackRayAuth.SlackIdentity] = []
        with engine.connect() as conn:
            sql = text("""
                SELECT slack_user_id,slack_team_id,slack_app_id,slack_channel_id,is_subscribed
                FROM slack_deltaray_link
                WHERE member_uuid = :client_id
                AND is_active = 1
                AND is_revoked = 0
                ORDER BY id DESC
            """).bindparams(client_id=self.client_id)
            result = conn.execute(sql)
            for row in result:
                bot_token = self.get_bot_token(conn, row.slack_team_id, row.slack_app_id)
                if bot_token:
                    self.slack_accounts.append(SlackRayAuth.SlackIdentity(
                        user_id=row.slack_user_id,
                        team_id=row.slack_team_id,
                        app_id=row.slack_app_id,
                        channel_id=row.slack_channel_id,
                        is_subscribed=row.is_subscribed,
                        bot_token=bot_token
                    ))
            # Return 401 error if no connected active Slack accounts.
            if not self.slack_accounts:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Not authenticated')
    
    @staticmethod
    def get_bot_token(conn: Connection, team_id: str, app_id: str) -> str:
        sql = text("""
            SELECT bot_token FROM slack_bots
            WHERE team_id = :team_id AND app_id = :app_id
            ORDER BY id DESC
            LIMIT 1
        """).bindparams(team_id=team_id, app_id=app_id)
        result = conn.execute(sql).all()
        return result[0][0] if result else ''

    @dataclass(frozen=True, slots=True)
    class SlackIdentity:
        user_id: str
        team_id: str
        app_id: str
        channel_id: str
        is_subscribed: bool
        bot_token: str

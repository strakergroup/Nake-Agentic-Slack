import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from jose.jwt import get_unverified_claims  # type: ignore
from buglog import notify_exception, notify_message

from ..auth.connector import get_ray_client
from ..config import config, domains
from ..slack import slack_handler


# Connect the Slack Bolt endpoints to FastAPI
router = APIRouter()


@router.api_route("/slack/openid/connect", methods=["GET", "POST"])
async def slack_openid_connect(request: Request):
    """A user is redirected to this page after authorizing SSO with Slack.
    Receives the temporary access code from Slack then exchanges it for the user
    access token. Part of the OpenID Connect process.
    See https://api.slack.com/authentication/sign-in-with-slack
    """
    # Disable this endpoint for now.
    # This endpoint was used to authorise Slack SSO to a LanguageCloud account,
    # but is not needed for now. Add to Slack manifest redirect_urls when re-enabled.
    return RedirectResponse(f"{config.base_url}/slack/install")
    lc_success_redirect_url = f"{domains.languagecloud}/app/slackopenid?success=1"
    lc_failure_redirect_url = f"{domains.languagecloud}/app/slackopenid?success=0"
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code:
        # The user declined to authorize their Slack account.
        return RedirectResponse(lc_failure_redirect_url)
    if not state:
        notify_message(
            "Slack app: State parameter missing during OpenID Connect",
            extra={"state": state},
            severity="ERROR",
        )
        return RedirectResponse(lc_failure_redirect_url)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://slack.com/api/openid.connect.token",
            params={
                "client_id": config.slack_client_id,
                "client_secret": config.slack_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": f"{config.base_url}/slack/openid/connect",
            },
        )
    try:
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            notify_message(
                "Slack app: Failed to exchange authorization code for user access token",
                extra=data,
                severity="ERROR",
            )
            return RedirectResponse(lc_failure_redirect_url)
        access_token: str = data.get("access_token")
        id_token: str = data.get("id_token")
        assert access_token, "Access token is missing from Slack response"
        assert id_token, "ID token is missing from Slack response"
        id_token_claims = get_unverified_claims(id_token)
        nonce: str | None = id_token_claims.get("nonce")
        user_id: str = id_token_claims["https://slack.com/user_id"]
        team_id: str = id_token_claims["https://slack.com/team_id"]
        enterprise_id: str | None = id_token_claims.get(
            "https://slack.com/enterprise_id"
        )
    except Exception as e:
        notify_exception(
            e, "Slack app: Failed to exchange authorization code for user access token"
        )
        return RedirectResponse(lc_failure_redirect_url)

    ray_client = await get_ray_client(user_id, team_id, enterprise_id)
    if not ray_client:
        return RedirectResponse(lc_failure_redirect_url)

    # Validate state and nonce for security reasons.
    if (
        state.casefold() != ray_client.id.casefold()
        or not nonce
        or nonce.casefold() != ray_client.id.casefold()
    ):
        notify_message(
            "Slack app: Failed to exchange authorization code for user access token",
            extra={
                "response": data,
                "claims": id_token_claims,
                "member_uuid": ray_client.id,
            },
            severity="ERROR",
        )
        return RedirectResponse(lc_failure_redirect_url)
    # TODO Make this work for SSO login
    # await save_user_access_token(
    #     user_id=user_id,
    #     team_id=team_id,
    #     enterprise_id=enterprise_id,
    #     user_token=access_token,
    #     scopes=["openid", "profile", "email"],
    # )
    return RedirectResponse(lc_success_redirect_url)


@router.api_route("/slack/{path:path}", methods=["GET", "POST"])
async def slack(request: Request):
    """Called by the Slack API to handle events, actions, commands, etc."""
    return await slack_handler.handle(request)

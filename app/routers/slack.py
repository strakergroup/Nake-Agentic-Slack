import time

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..config import domains
from ..slack import slack_handler
from ..slack.logging import get_memory_mb

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
    return RedirectResponse(f"{domains.slack_ray_translator}/slack/install")


@router.api_route("/slack/{path:path}", methods=["GET", "POST"])
async def slack(request: Request):
    """Called by the Slack API to handle events, actions, commands, etc."""
    start_time = time.time()
    mem_start = get_memory_mb()

    response = await slack_handler.handle(request)
    end_time = time.time()
    mem_end = get_memory_mb()
    duration = end_time - start_time
    mem_delta = mem_end - mem_start
    if duration > 5 or mem_delta > 50:
        data = await request.json()
        event = data.get("event", {})
        ts = event.get("event_ts", "")
        event_type = event.get("type", "")
        channel_type = event.get("channel_type", "")
        if ts:
            print(
                f"Warning: Slack request performance issue {ts} took {duration:.2f} seconds {event_type} {channel_type} | Memory +{mem_delta:.2f} MB (total {mem_end:.2f} MB)"
            )
    return response

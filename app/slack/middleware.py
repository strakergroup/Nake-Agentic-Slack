"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

from ray_logger.slack import SlackAppLog

from .app import app
from .logging import init_slack_app_log
from .templates.messages import LoginMessage
from ..auth.connector import get_ray_client, get_app_id


# -----------------------------------------------------------------------------
# Global Middleware
# https://slack.dev/bolt-python/concepts#global-middleware
# -----------------------------------------------------------------------------


@app.use
async def ray_log(context, body, next):
    """Global middleware for logging. Adds a `SlackAppLog` object from
    the internal `ray_logger` library to the context with the key "log".
    """
    context["log"] = init_slack_app_log(body, context)
    await next()


# -----------------------------------------------------------------------------
# Listener Middleware
# https://slack.dev/bolt-python/concepts#listener-middleware
# -----------------------------------------------------------------------------


async def load_ray_client(context, body, next) -> None:
    """Gets and saves the DeltaRay client information of the Slack user to
    the context if the accounts are connected. Also add a `login_prompt` message
    to the context containing the blocks and text to be sent to the user asking
    them to connect their DeltaRay account.
    """
    app_id = body.get(
        "api_app_id", get_app_id(context["bot_token"], context["team_id"])
    )
    context["ray_client"] = get_ray_client(
        context["user_id"],
        context["team_id"],
        app_id,
    )
    context["login_prompt"] = LoginMessage(
        context["user_id"],
        context["team_id"],
        app_id,
        context.get("channel_id", context["user_id"]),
    )
    # Log the RAY client ID if available.
    if (
        context["ray_client"] is not None
        and "log" in context
        and isinstance(context["log"], SlackAppLog)
    ):
        context["log"].slack_log.client_id = context["ray_client"].id

    await next()

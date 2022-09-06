"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

import logging
from typing import Any
from sentry_sdk import capture_message
from slack_bolt.context.async_context import AsyncBoltContext
from ray_logger.slack import SlackAppLog

from .app import app
from .logging import init_slack_app_log
from .templates.messages import SlackMessage, LoginMessage
from ..auth.connector import RayClient, get_ray_client, get_app_id


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


async def load_ray_client(
    context: AsyncBoltContext, body: dict[str, Any], next
) -> None:
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


# -----------------------------------------------------------------------------
# Middleware helper functions
# -----------------------------------------------------------------------------


async def require_ray_client(context: AsyncBoltContext) -> bool:
    """Checks if a Slack user is connected to a DeltaRAY account by checking
    the context. If not connected, then post a message prompting the user
    to connect their account. (Requires the `load_ray_client` middleware.)

    Returns:
        bool: The Slack user has a connected DeltaRAY account.
    """
    if isinstance(context.get("ray_client"), RayClient):
        return True

    if not isinstance(login_message := context.get("login_prompt"), SlackMessage):
        logging.warning('"login_prompt" is not in the context')
        capture_message('Slack: "login_prompt" is not in the context', "warning")
        return False

    # Send login prompt if no DeltaRAY account is connected.
    if context.respond.response_url:
        await context.respond(
            text=login_message.text,
            blocks=login_message.blocks,
        )
    else:
        await context.client.chat_postEphemeral(
            channel=context.get("channel_id") or context.get("user_id"),
            user=context.get("user_id"),
            text=login_message.text,
            blocks=login_message.blocks,
        )
    return False

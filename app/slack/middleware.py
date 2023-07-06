"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

from typing import Any
import logging

from buglog import notify_message
from slack_bolt.context.async_context import AsyncBoltContext
from ray_logger.slack import SlackAppLog

from .app import app
from .logging import init_slack_app_log
from .templates.messages import SlackMessage, LoginMessage
from ..auth.connector import (
    RayConnection,
    get_ray_connection,
    get_app_id,
    # get_bot_token,
)

# from ..database import engines


# -----------------------------------------------------------------------------
# Global Middleware
# https://slack.dev/bolt-python/concepts#global-middleware
# -----------------------------------------------------------------------------


# @app.use
# async def straker_workspace_fix(context, body, next):
#     """Global middleware for logging. Adds a `SlackAppLog` object from
#     the internal `ray_logger` library to the context with the key "log".
#     """
#     if context["enterprise_id"] == "E04RDMG8XP1" and context["team_id"] in [
#         "T03PE1PGBV5",
#         "T02FDFCGK",
#     ]:
#         context["team_id"] = "T058B4G5QQ1"
#         with engines["ray_integration_readonly"].connect() as conn:
#             bot_token = get_bot_token(conn, context["team_id"])
#         context["bot_token"] = bot_token
#         context["client"].token = bot_token
#     # Peter
#     if context["user_id"] == "U03PN1FB6Q6":
#         context["user_id"] = "UC78X13PC"
#     # Boren
#     if context["user_id"] == "U03QD69H6G1":
#         context["user_id"] = "U01TRBX3MFY"
#     await next()


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


async def ray_connection(context: AsyncBoltContext, body: dict[str, Any], next) -> None:
    """Gets and saves the LanguageCloud super group and client information of the
    Slack user to the context. The `RayConnection` object is stored as `ray` in
    the context if the Slack workspace has a connected super group.

    Also add a `login_prompt` message to the context containing the message to
    be sent to the user asking them to connect their LanguageCloud account.
    """
    app_id = body.get(
        "api_app_id", get_app_id(context["bot_token"], context["team_id"])
    )
    context["ray"] = await get_ray_connection(
        context["user_id"], context["team_id"], app_id
    )
    context["login_prompt"] = LoginMessage(
        user_id=context["user_id"],
        team_id=context["team_id"],
        app_id=app_id,
        channel_id=context.get("channel_id", context["user_id"]),
        ray_client=context["ray"].client if context["ray"] is not None else None,
    )
    # Log the RAY client ID if available.
    if (
        context["ray"] is not None
        and "log" in context
        and isinstance(context["log"], SlackAppLog)
    ):
        context["log"].slack_log.super_group_uuid = context["ray"].super_group.id
        if context["ray"].client is not None:
            context["log"].slack_log.client_uuid = context["ray"].client.id

    await next()


# -----------------------------------------------------------------------------
# Middleware helper functions
# -----------------------------------------------------------------------------


async def require_ray_client(
    context: AsyncBoltContext, prompt_login: bool = True, variation: str | None = None
) -> bool:
    """Checks if a Slack user is connected to a LanguageCloud account by checking
    the context. If not connected, then optionally post a message prompting the
    user to connect their LanguageCloud account. (Requires the `ray_connection` middleware.)

    Args:
        context (AsyncBoltContext): The Slack listener context.
        prompt_login (bool, optional): Post a login message if the Slack user does
            not have a connected LanguageCloud account. Defaults to True.
        variation (str | None, optional): The variation of the login message to use.
            Defaults to None.

    Returns:
        bool: The Slack user has a connected LanguageCloud account.
    """
    if (
        isinstance(context.get("ray"), RayConnection)
        and context["ray"].client is not None
    ):
        return True

    if prompt_login:
        if not isinstance(login_message := context.get("login_prompt"), SlackMessage):
            logging.warning('"login_prompt" is not in the context')
            notify_message(
                'Slack: "login_prompt" is not in the context', severity="WARNING"
            )
            return False

        login_message: LoginMessage = login_message.with_variation(variation)
        # Send login prompt if no LanguageCloud account is connected.
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

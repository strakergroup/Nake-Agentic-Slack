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
    get_ray_connection_demo,
    log_new_user_info,
)


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


async def ray_connection(context: AsyncBoltContext, body: dict[str, Any], next) -> None:
    """Gets and saves the LanguageCloud super group and client information of the
    Slack user to the context. The `RayConnection` object is stored as `ray` in
    the context if the Slack workspace has a connected super group.

    Also add a `login_prompt` message to the context containing the message to
    be sent to the user asking them to connect their LanguageCloud account.
    """
    context["ray"] = await get_ray_connection(
        context["user_id"], context["team_id"], context.get("enterprise_id")
    )
    if context["ray"] is None or context["ray"].client is None:
        demo_connection = await get_ray_connection_demo(
            context["user_id"], context["team_id"], context.get("enterprise_id")
        )
        if demo_connection is not None:
            context["ray"] = demo_connection
    context["login_prompt"] = LoginMessage(
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.get("enterprise_id"),
        channel_id=context.get("channel_id", context["user_id"]),
        ray_client=context["ray"].client if context["ray"] is not None else None,
    )
    # Log the RAY client ID if available.
    if "log" in context and isinstance(context["log"], SlackAppLog):
        if context["ray"] is not None:
            if context["ray"].super_group:
                context["log"].slack_log.super_group_uuid = (
                    context["ray"].super_group[0].id
                )

            if context["ray"].client is not None:
                context["log"].slack_log.client_uuid = context["ray"].client.id
        if context["ray"] is None or context["ray"].client is None:
            # get slack user info from api and log it
            try:
                user_info = await context.client.users_info(user=context["user_id"])
                # insert to db
                await log_new_user_info(user_info["user"])
            except Exception as e:
                error_message = str(e)
                print(error_message)

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
        if not isinstance(login_message := context.get("login_prompt"), LoginMessage):
            logging.warning('"login_prompt" is not in the context')
            notify_message(
                'Slack: "login_prompt" is not in the context', severity="WARNING"
            )
            return False

        login_message = login_message.with_variation(variation)
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

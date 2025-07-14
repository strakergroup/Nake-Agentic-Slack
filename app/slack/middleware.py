"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

import math
from typing import Awaitable, Callable
import logging

from buglog import notify_exception, notify_message
from slack_bolt.context.async_context import AsyncBoltContext
from ray_logger.slack import SlackAppLog  # type: ignore

from app.ray.utils import is_ibm_enterprise, set_user_language

from .app import app
from .logging import init_slack_app_log
from .templates.messages import (
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
    LoginMessage,
)
from ..auth.connector import (
    RayConnection,
    get_client_tokens,
    get_group_tokens,
    get_client_type,
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


async def ray_connection(
    context: AsyncBoltContext,
    next: Callable[[], Awaitable[None]],
) -> None:
    """Gets and saves the LanguageCloud super group and client information of the
    Slack user to the context. The `RayConnection` object is stored as `ray` in
    the context if the Slack workspace has a connected super group.

    Also add a `login_prompt` message to the context containing the message to
    be sent to the user asking them to connect their LanguageCloud account.
    """
    context["ray"] = await get_ray_connection(
        context["user_id"], context["team_id"], context.enterprise_id
    )
    if context["ray"] is None or context["ray"].client is None:
        demo_connection = await get_ray_connection_demo(
            context["user_id"], context["team_id"], context.enterprise_id
        )
        if demo_connection is not None:
            context["ray"] = demo_connection
    try:
        context["is_bot"] = False
        if context.client:
            user_info = await context.client.users_info(
                user=context["user_id"], include_locale=True
            )
            context["is_bot"] = user_info["user"]["is_bot"]
            set_user_language(user_info, context)
            context["user_info"] = user_info["user"]
    except Exception as e:
        context["is_bot"] = False
        notify_exception(e)
    context["login_prompt"] = LoginMessage(
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.enterprise_id,
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
                # insert to db
                await log_new_user_info(user_info["user"])
            except Exception as e:
                print(e)
                notify_exception(e)

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

    if prompt_login and context.client:
        if not isinstance(login_message := context.get("login_prompt"), LoginMessage):
            logging.warning('"login_prompt" is not in the context')
            notify_message(
                'Slack: "login_prompt" is not in the context', severity="WARNING"
            )
            return False

        login_message = login_message.with_variation(variation)
        # Send login prompt if no LanguageCloud account is connected.
        if context.response_url and context.respond:
            await context.respond(
                text=login_message.text,
                blocks=login_message.blocks,
            )
        else:
            await context.client.chat_postEphemeral(
                channel=context.get("channel_id") or context.get("user_id", ""),
                user=context.get("user_id", ""),
                text=login_message.text,
                blocks=login_message.blocks,
            )

    return False


async def require_mt_tokens(context: AsyncBoltContext, value=1) -> bool:
    """Check if the user has the required minimum translation credits to perform the operation"""
    ai_tokens = 0
    mt_scale = 0.1
    value = math.ceil(value * mt_scale)
    if context["ray"].client is not None:
        user_tokens = await get_client_tokens(context["ray"].client.id_token)
        ai_tokens = user_tokens.ai_token
        if ai_tokens >= value:
            return True
    elif context["ray"].super_group is not None:
        client_tokens = await get_group_tokens(
            context["ray"].super_group[0].verify_organization_uuid
        )
        ai_tokens = client_tokens.ai_token
        if ai_tokens and ai_tokens >= value:
            return True
    client_type = None
    if context["ray"].client is not None:
        client_type = await get_client_type(
            context["ray"].client.id, context["ray"].client.user_group_id
        )

    # Determine which message to show based on client type and enterprise status
    message = (
        RequiresMtTokenMessage(ai_tokens, value)
        if client_type in ["Admin", "Owner"]
        and not is_ibm_enterprise(enterprise_id=context.enterprise_id)
        else RequiresMtTokenAdminMessage(ai_tokens, value)
    )

    # Use respond if available, otherwise fall back to ephemeral message
    if context.response_url and context.respond:
        await context.respond(text=message.text, blocks=message.blocks)
        return False

    if context.client:
        await context.client.chat_postEphemeral(
            channel=context.get("channel_id") or context.get("user_id", ""),
            user=context.get("user_id", ""),
            text=message.text,
            blocks=message.blocks,
        )

    return False

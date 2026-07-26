"""Middleware for Slack Bolt listeners.

See https://slack.dev/bolt-python/concepts#listener-middleware.
"""

import logging
import math
from typing import Awaitable, Callable

from ray_logger.slack import SlackAppLog  # type: ignore
from slack_bolt.context.async_context import AsyncBoltContext

from app.ray.utils import is_ibm_enterprise, set_user_language
from app.slack.buglog_notifier import notify_exception, notify_message

from ..auth.connector import (
    RayConnection,
    get_client_tokens,
    get_client_type,
    get_group_tokens,
    get_ray_connection,
    get_ray_connection_demo,
    get_ray_super_group,
    log_new_user_info,
)
from .app import app
from .logging import init_slack_app_log
from .templates.messages import (
    LoginMessage,
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
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


async def populate_ray_connection(context: AsyncBoltContext) -> None:
    """Populate LanguageCloud connection fields on a Slack listener context.

    Used by ``ray_connection`` middleware and by modal-open handlers that must
    call ``views.open`` before this I/O (Slack ``trigger_id`` TTL).
    """
    if "team_id" not in context:
        context["ray"] = None
        return

    # Bot message events may not include a Slack user. Match the not-logged-in
    # user path by loading the connected workspace org with no client attached.
    if "user_id" not in context:
        super_group = await get_ray_super_group(
            context["team_id"], context.enterprise_id
        )
        context["ray"] = RayConnection(super_group=super_group or [], client=None)
        context["is_bot"] = True
        return

    context["ray"] = await get_ray_connection(
        context["user_id"], context["team_id"], context.enterprise_id
    )
    if context["ray"] is None or context["ray"].client is None:
        demo_connection = await get_ray_connection_demo(
            context["user_id"], context["team_id"], context.enterprise_id
        )
        if demo_connection is not None:
            context["ray"] = demo_connection
    user: dict | None = None
    try:
        context["is_bot"] = False
        if context.client:
            user_info = await context.client.users_info(
                user=context["user_id"], include_locale=True
            )
            if user_info is None:
                raise ValueError("Slack users_info returned no response")
            user = user_info.get("user")
            if not isinstance(user, dict):
                raise ValueError("Slack users_info response is missing user data")
            context["is_bot"] = bool(user.get("is_bot", False))
            set_user_language(user_info, context)
            context["user_info"] = user
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
                await log_new_user_info(user)
            except Exception as e:
                logging.warning("Failed to log new Slack user info", exc_info=True)
                notify_exception(e)


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
    await populate_ray_connection(context)
    await next()


# -----------------------------------------------------------------------------
# Middleware helper functions
# -----------------------------------------------------------------------------


async def require_ray_client(
    context: AsyncBoltContext,
    prompt_login: bool = True,
    variation: str | None = None,
    *,
    allow_org_billing: bool = False,
) -> bool:
    """Checks whether MT may proceed for this Slack user/workspace.

    By default (``allow_org_billing=False``) requires a connected LanguageCloud
    member — used for HT/QE, account actions, etc.

    With ``allow_org_billing=True`` (AI MT — channel, direct, document, DM file upload)
    also allows a linked workspace super group, matching org-billed MT. Balance is still gated
    separately by ``require_mt_tokens``; group-token minting happens at charge
    time, not here.

    Args:
        context (AsyncBoltContext): The Slack listener context.
        prompt_login (bool, optional): Post a login message when access is denied.
        variation (str | None, optional): Login message variation.
        allow_org_billing (bool): Accept org-billed workspace without member login.

    Returns:
        bool: Access is allowed.
    """
    if isinstance(ray := context.get("ray"), RayConnection):
        if ray.client is not None or (allow_org_billing and ray.super_group):
            return True

    if prompt_login and context.client:
        if not isinstance(login_message := context.get("login_prompt"), LoginMessage):
            logging.warning('"login_prompt" is not in the context')
            notify_message(
                'Slack: "login_prompt" is not in the context', severity="WARNING"
            )
            return False

        login_message = login_message.with_variation(variation)
        # HT/QE buttons sit on shared New Job messages — login must be ephemeral
        # so the AI Translation / job chooser blocks are not replaced.
        login_ephemeral_only = variation in (
            LoginMessage.HUMAN_TRANSLATION,
            LoginMessage.QUALITY_EVALUATION,
        )
        if login_ephemeral_only or not (context.response_url and context.respond):
            await context.client.chat_postEphemeral(
                channel=context.get("channel_id") or context.get("user_id", ""),
                user=context.get("user_id", ""),
                text=login_message.text,
                blocks=login_message.blocks,
            )
        else:
            await context.respond(
                text=login_message.text,
                blocks=login_message.blocks,
                replace_original=False,
            )

    return False


async def require_mt_tokens(context: AsyncBoltContext, value=1):
    """Check if the user has the required minimum translation credits to perform the operation"""
    ai_tokens = 0
    # SOW MT rate — matches pt-languagecloud-api (RAY-80492).
    sow_tokens_per_character = 0.002
    value = math.ceil(value * sow_tokens_per_character)
    if context["ray"].client is not None:
        user_tokens = await get_client_tokens(context["ray"].client.id_token)
        if user_tokens is None:
            return False
        ai_tokens = user_tokens.ai_token
        if ai_tokens >= value:
            return True
    elif context["ray"].super_group is not None:
        client_tokens = await get_group_tokens(
            context["ray"].super_group[0].verify_organization_uuid
        )
        if client_tokens is None:
            return False
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

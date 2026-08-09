"""Handlers for account connection, disconnection and approval actions."""

import json
from typing import Any, Dict, Optional

from slack_bolt.kwargs_injection.async_args import AsyncRespond, AsyncSay
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import (
    RayContext,
    disconnect_ray_account,
)
from app.slack.buglog_notifier import notify_exception
from app.slack.listener_actions import approve_pending_client
from app.slack.middleware import require_ray_client
from app.slack.templates.messages import (
    ClientAlreadyApprovedMessage,
    ClientApprovedMessage,
    SlackMessage,
    SuccessfulLogoutMessage,
)


async def handle_login_account(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    try:
        # LanguageCloud website connect flow — Direct Login SSO removed (RAY-81247).
        pass
    except Exception as e:
        notify_exception(e)
        await respond(
            text="There was an error connecting your account, please try again."
        )


async def handle_disconnect_account(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    disconnect_ray_account(
        context["user_id"], context["team_id"], context.enterprise_id
    )
    # action["value"] should contain the LanguageCloud account username.
    assert context["ray"] is not None
    assert context["ray"].client is not None
    if action is not None:
        username = action.get("value") if isinstance(action.get("value"), str) else None
        msg: SlackMessage = SuccessfulLogoutMessage(
            context.user_id, context["ray"].client.sso, username
        )
    else:
        msg = SuccessfulLogoutMessage(context.user_id, context["ray"].client.sso)
    await respond(text=msg.text, blocks=msg.blocks, replace_original=True)


async def handle_approve_pending_client(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    say: AsyncSay,
    client: AsyncWebClient,
):
    if await require_ray_client(context):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        try:
            assert action is not None
            # action["value"] should contain the new client details.
            pending_client_details = json.loads(action["value"])
            client_id = pending_client_details["id"]
            client_username = pending_client_details["username"]
        except Exception as e:
            notify_exception(e)
        else:
            approved_groups = await approve_pending_client(
                context["ray"].client,
                pending_client_id=client_id,
                pending_client_username=client_username,
            )
            if approved_groups:
                await say(ClientApprovedMessage(client_username).text)
            else:
                await client.chat_postEphemeral(
                    channel=context["channel_id"],
                    user=context["user_id"],
                    text=ClientAlreadyApprovedMessage(client_username).text,
                )


async def handle_delete_ephemeral_message(respond: AsyncRespond):
    await respond(delete_original=True)

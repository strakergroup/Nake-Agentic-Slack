"""Handlers for account connection, disconnection and approval actions."""

import json
from typing import Any, Dict, Optional

from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncRespond, AsyncSay
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import (
    RayContext,
    connect_ray_account_sso,
    disconnect_ray_account,
    get_ray_connection,
)
from app.config import domains
from app.ray.utils import is_ibm_enterprise
from app.slack.buglog_notifier import notify_exception
from app.slack.listener_actions import approve_pending_client
from app.slack.middleware import require_ray_client
from app.slack.templates.messages import (
    ClientAlreadyApprovedMessage,
    ClientApprovedMessage,
    ConnectionInfoMessage,
    SlackMessage,
    SsoConnectionInfoMessage,
    SuccessfulLoginMessage,
    SuccessfulLogoutMessage,
)


async def handle_login_sso(
    ack: AsyncAck,
    context: RayContext,
    respond: AsyncRespond,
    client: AsyncWebClient,
    view: Optional[Dict[str, Any]],
):
    try:
        if "channel_id" not in context:
            context["channel_id"] = context["user_id"]
        if context["ray"] is not None:
            if context["ray"].client is None:
                # The API endpoint to get user info
                info_response_json = await client.users_info(user=context["user_id"])
                user_info = (
                    info_response_json.get("user") if info_response_json["ok"] else None
                )
                profile = user_info.get("profile") if user_info else None
                if profile:
                    await connect_ray_account_sso(
                        context["user_id"],
                        context["team_id"],
                        profile["email"],
                        profile["first_name"],
                        profile["last_name"],
                        context["channel_id"],
                        context.enterprise_id,
                    )
                    context["ray"] = await get_ray_connection(
                        context["user_id"],
                        context["team_id"],
                        context.enterprise_id,
                    )
                    # Show connection success message
                    sso_msg = SsoConnectionInfoMessage(
                        context["ray"],
                        is_ibm=(is_ibm_enterprise(context.enterprise_id)),
                    )
                    await ack(response_action="clear")
                    if context.response_url:
                        await respond(text=sso_msg.text, blocks=sso_msg.blocks)
                    else:
                        await client.chat_postMessage(
                            channel=context["channel_id"],
                            text=sso_msg.text,
                            blocks=sso_msg.blocks,
                        )

                    msg: SlackMessage = SuccessfulLoginMessage(
                        context["user_id"],
                        profile["email"],
                        context["ray"],
                        context.enterprise_id,
                    )
                    await ack(response_action="clear")
                    if msg:
                        await client.chat_postMessage(
                            channel=context["user_id"],
                            text=msg.text,
                            blocks=msg.blocks,
                        )
            else:
                await ack(response_action="clear")
                if context["ray"].client.sso:
                    msg = SsoConnectionInfoMessage(
                        context["ray"],
                        is_ibm=(is_ibm_enterprise(context.enterprise_id)),
                    )
                # need else block if triggered from old message
                else:
                    msg = ConnectionInfoMessage(
                        context["ray"],
                        user_id=context["user_id"],
                        team_id=context["team_id"],
                        enterprise_id=context.enterprise_id,
                        channel_id=context["channel_id"],
                        is_ibm=is_ibm_enterprise(context.enterprise_id),
                    )
                # check if respond is available
                if context.response_url:
                    await respond(text=msg.text, blocks=msg.blocks)

        else:
            await ack(response_action="clear")
            await respond(
                text="Your organisation requires a Super Group to connect your account to Slack."
            )
    except SlackApiError as sae:
        if sae.response["error"] == "missing_scope":
            await ack(response_action="clear")
            await respond(
                text="This app requires the 'user_read' scope to access user information. "
                "Please grant the necessary permissions and try again. You can reinstall the app "
                f"from this URL: {domains.slack_ray_translator}/slack/install"
            )
        else:
            notify_exception(sae)
            await ack()
            await respond(
                text="There was an error retrieving user information. Please try again."
            )
    except Exception as e:
        notify_exception(e)
        await ack()
        await respond(
            text="There was an error connecting your account, please try again."
        )


async def handle_login_account(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    respond: AsyncRespond,
):
    try:
        # Use language cloud API to send success message
        pass
        # result = await connect_ray_account(
        #     context["user_id"],
        #     context["team_id"],
        #     context.enterprise_id,
        #     channel_id=context["channel_id"],
        # )
        # if result == "success":
        #     msg = SuccessfulLoginMessage(context["user_id"], action.get("value"))
        #     await respond(text=msg.text, blocks=msg.blocks, replace_original=True)
        # else :
        #     await respond(
        #         text="Login required on language cloud website. Please try again."
        #     )
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

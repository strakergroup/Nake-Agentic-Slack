"""Handler for Slack slash commands."""

import asyncio
import re
from typing import Any, Dict, Optional, cast

from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext, is_slack_team_admin
from app.models import SlackGroupSettingsTranslation
from app.ray.settings import get_auto_translate_settings_and_langs
from app.ray.utils import is_ibm_enterprise
from app.slack.buglog_notifier import notify_exception
from app.slack.listener_actions import (
    post_job_list,
    post_job_status,
    post_job_summary,
)
from app.slack.middleware import populate_ray_connection, require_ray_client
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from app.slack.templates.messages import (
    ConnectionInfoMessage,
    HelpMessage,
    InvalidCommandMessage,
    LoginMessage,
    LogoutMessage,
    QuoteMessage,
    SlackMessage,
)
from app.slack.templates.views import translation_settings_view
from app.slack.utils import strip_command_formatting
from app.translate import _

_TRANSLATE_SETTINGS_FETCH_TIMEOUT_SECONDS = 10


async def _ensure_ray_connection(context: RayContext) -> None:
    """Load Ray/LC context after ack (slash commands skip ray_connection middleware)."""
    if context.get("ray") is None:
        await populate_ray_connection(context)


async def _handle_translate_settings_command(
    command: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
) -> None:
    """Open channel translation settings without racing Slack's trigger_id TTL.

    ``/ustraker translate`` used to run Ray/DB/IBM checks before ``views.open``,
    then preselect the current channel via ``initial_conversations``. Slack
    rejects that preselect when the bot is not in the channel (private / Slack
    Connect), and BugLog notify-before-update left the loading modal stuck.
    """
    view_id = await open_loading_modal(client, command["trigger_id"])
    try:
        await _ensure_ray_connection(context)
        if not await require_ray_client(
            context,
            prompt_login=False,
            variation=LoginMessage.CHANNEL_TRANSLATION_SETTINGS,
        ):
            login = context.get("login_prompt")
            message = _("Please sign in to LanguageCloud to continue.")
            if isinstance(login, LoginMessage):
                login = login.with_variation(LoginMessage.CHANNEL_TRANSLATION_SETTINGS)
                if login.blocks:
                    message = str(login.blocks[0]["text"]["text"])
                elif login.text:
                    message = login.text
            await safe_views_update(
                client,
                view_id,
                status_modal(_("Sign in required"), message),
            )
            return

        is_straker_admin = (
            context["ray"]
            and context["ray"].client
            and await is_slack_team_admin(
                context["ray"].client.id, context.get("enterprise_id")
            )
        )
        ibm_enterprise = await asyncio.to_thread(
            is_ibm_enterprise, context.get("enterprise_id")
        )
        translation_settings_enabled = not ibm_enterprise or (
            context["ray"] and context["ray"].client and is_straker_admin
        )
        if not translation_settings_enabled:
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Translation Settings"),
                    _(
                        "Channel translation settings are managed by your "
                        "administrator. Please contact an admin to change these "
                        "settings."
                    ),
                ),
            )
            return

        settings = await asyncio.wait_for(
            get_auto_translate_settings_and_langs(context, context.channel_id),
            timeout=_TRANSLATE_SETTINGS_FETCH_TIMEOUT_SECONDS,
        )
        auto_translate_langs = [setting["target_lang"] for setting in settings]
        display_format = cast(
            SlackGroupSettingsTranslation.DisplayFormatType,
            settings[0].get("display_format", "thread") if settings else "thread",
        )
        team_id = str(context.get("team_id") or "")
        # Slash commands already have a current conversation — do not pass
        # initial_conversations (Slack invalid_conversations when the bot is
        # not a member of a private / shared channel).
        await safe_views_update(
            client,
            view_id,
            translation_settings_view(
                None,
                auto_translate_langs,
                display_format,
                team_id,
            ),
        )
    except Exception as e:
        try:
            await safe_views_update(client, view_id, request_error_modal())
        finally:
            notify_exception(e)


async def handle_ray_command(
    ack: AsyncAck,
    respond: AsyncRespond,
    command: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    await ack()

    assert command is not None
    command_formatted = strip_command_formatting(command.get("text", "").strip())
    command_args = re.split(r"\s+", command_formatted.lower())
    command_args = [strip_command_formatting(arg) for arg in command_args]

    if command_args == ["translate"]:
        await _handle_translate_settings_command(command, context, client)
        return

    await _ensure_ray_connection(context)

    # Use match to handle different command arguments.
    match command_args:
        case ["info" | "account"]:
            # Get connection info and respond with message.
            msg: SlackMessage = ConnectionInfoMessage(
                context["ray"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=context["channel_id"],
            )
            await respond(text=msg.text, blocks=msg.blocks)

        case ["login" | "signin" | "connect"]:
            # Respond with login prompt.
            await respond(
                text=context["login_prompt"].text,
                blocks=context["login_prompt"].blocks,
            )

        case ["logout" | "signout" | "disconnect"]:
            # Logout and respond with message.
            if await require_ray_client(context):
                assert context["ray"] is not None
                assert context["ray"].client is not None
                msg = LogoutMessage(context["ray"].client)
                await respond(text=msg.text, blocks=msg.blocks)

        case ["job", reference, *reference_other]:
            # Get job status or list of jobs.
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                assert context["ray"] is not None
                assert context["ray"].client is not None
                # Try searching job by TJ number if the format is correct.
                if not reference_other and re.fullmatch(
                    r"tj\d+", reference, re.IGNORECASE
                ):
                    await post_job_status(
                        client, context, context["ray"].client, reference
                    )
                # Otherwise, search job by client reference.
                else:
                    client_reference = command_formatted.removeprefix("job").strip()
                    await post_job_list(
                        client,
                        context,
                        context["ray"].client,
                        preset="CLIENT_REFERENCE",
                        client_ref=client_reference,
                    )

        case ["jobs"] | ["my", "jobs"]:
            # Get summary of jobs.
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                assert context["ray"] is not None
                assert context["ray"].client is not None
                await post_job_summary(client, context, context["ray"].client)
        case ["quote"] | ["new"]:
            # Show quote message.
            if await require_ray_client(
                context, variation=LoginMessage.QUALITY_EVALUATION
            ):
                # quote is like new job except it doesn't open the modal.
                await ack()
                quote_msg = QuoteMessage()
                await respond(text=quote_msg.text, blocks=quote_msg.blocks)

        case ["help" | ""]:
            # Show help message.
            await respond(
                blocks=HelpMessage(context).blocks, text=HelpMessage(context).text
            )

        case [command_text]:
            # Get job status by TJ number.
            match = re.fullmatch(r"tj\d+", command_text, re.IGNORECASE)
            if match:
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    assert context["ray"] is not None
                    assert context["ray"].client is not None
                    await post_job_status(
                        client, context, context["ray"].client, command_text
                    )
            else:
                await respond(text=InvalidCommandMessage().text)

        case _:
            # Invalid command.
            await respond(text=InvalidCommandMessage().text)

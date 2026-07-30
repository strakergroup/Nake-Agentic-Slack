"""Handlers for channel auto-translate settings."""

import asyncio
import json
import logging
from typing import Any, Dict, Optional, cast

from pydantic import ValidationError
from slack_bolt.kwargs_injection.async_args import AsyncAck
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import (
    RayContext,
    get_all_tokens_for_enterprise,
    get_token_for_team,
    resolve_channels_to_team,
)
from app.models import SlackGroupSettingsTranslation
from app.ray.settings import (
    disable_auto_translate_group_settings,
    get_auto_translate_settings_and_langs,
    update_auto_translate_group_settings,
)
from app.slack.buglog_notifier import notify_exception, notify_message
from app.slack.middleware import populate_ray_connection
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
)
from app.slack.templates.messages import (
    AutoTranslateSettingsChangedMessage,
    AutoTranslateSettingsDisabledMessage,
)
from app.slack.templates.models import (
    AutoTranslationSettingsForm,
    convert_pydantic_to_slack_error,
)
from app.slack.templates.views import home_view, translation_settings_view
from app.translate import _

logger = logging.getLogger(__name__)


async def _join_channel(client: AsyncWebClient, channel_id: str):
    try:
        await client.conversations_join(channel=channel_id)
    except SlackApiError:
        pass  # Cannot join private channel, or cannot find channel.
    except Exception as e:
        notify_exception(e)


async def _notify_channel_disabled(
    client: AsyncWebClient, context: RayContext, channel_id: str
):
    try:
        msg = AutoTranslateSettingsDisabledMessage(context["user_id"], channel_id)
        await client.chat_postMessage(channel=channel_id, text=msg.text)
    except SlackApiError:
        pass  # Must be in channel to post. TODO check other events, e.g. app_mention
    except Exception as e:
        notify_exception(e)


async def _join_channel_with_token(
    client: AsyncWebClient, channel_id: str, bot_token: str
):
    try:
        client.token = bot_token
        await client.conversations_join(channel=channel_id)
    except SlackApiError:
        pass  # Cannot join private channel, or cannot find channel.
    except Exception as e:
        notify_exception(e)


async def _notify_channel_update(
    client: AsyncWebClient,
    context: RayContext,
    form: AutoTranslationSettingsForm,
    channel_id: str,
    bot_token: str,
):
    try:
        client.token = bot_token
        if form.languages:
            msg = AutoTranslateSettingsChangedMessage(
                context["user_id"],
                channel_id,
                form.languages,
                form.display_format,
            )
            await client.chat_postMessage(channel=channel_id, text=msg.text)
        else:
            disabled_msg = AutoTranslateSettingsDisabledMessage(
                context["user_id"], channel_id
            )
            await client.chat_postMessage(channel=channel_id, text=disabled_msg.text)
    except Exception as e:
        notify_exception(e)
        if context.enterprise_id:
            all_tokens = await get_all_tokens_for_enterprise(context.enterprise_id)
            if all_tokens:
                for token in all_tokens:
                    client.token = token["bot_token"]
                    try:
                        await client.chat_postMessage(channel=channel_id, text=msg.text)
                        break
                    except Exception:
                        pass


async def handle_show_auto_translate_settings(
    context: RayContext,
    payload: Dict[str, Any],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    channel_info = json.loads(payload["value"])
    channel_id = channel_info.get("channel_id")
    team_id = channel_info.get("team_id", "")
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        settings = await get_auto_translate_settings_and_langs(
            context, channel_id, team_id
        )
        auto_translate_langs = [setting["target_lang"] for setting in settings]
        await safe_views_update(
            client,
            view_id,
            translation_settings_view(
                [channel_id] if channel_id else None,
                auto_translate_langs,
                cast(
                    SlackGroupSettingsTranslation.DisplayFormatType,
                    settings[0].get("display_format", "thread")
                    if settings
                    else "thread",
                ),
                team_id,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_disable_auto_translate_settings(
    ack: AsyncAck,
    context: RayContext,
    payload: Dict[str, Any],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    try:
        await ack()
        channel_info = json.loads(payload["value"])
        channel_id = channel_info.get("channel_id")
        team_id = channel_info.get("team_id", "")
        await disable_auto_translate_group_settings(context, channel_id)
        context["team_id"] = team_id
        team_channel = await resolve_channels_to_team(
            channel_id, client, context.enterprise_id, team_id
        )
        if isinstance(team_channel["bot_token"], str):
            client.token = str(team_channel["bot_token"])
        else:
            notify_message("Bot token not found in team channel", extra=team_channel)
            return
        await client.views_publish(
            user_id=context["user_id"],
            view=await home_view(context, body["api_app_id"], context.get("ray")),
        )

        if not channel_id:
            notify_message("Channel ID not found in payload", extra=payload)
            return
        await ack()
        if team_id:
            context["team_id"] = team_id

        await asyncio.gather(
            *[_join_channel(client, channel_id)],
            return_exceptions=True,
        )
        await asyncio.gather(
            *[_notify_channel_disabled(client, context, channel_id)],
            return_exceptions=True,
        )
    except Exception as e:
        notify_exception(e)


async def handle_update_auto_translate_settings(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    try:
        assert view is not None
        assert context.team_id is not None
        # get team_id from private_metadata
        team_id = view.get("private_metadata", "")
        form_data = view.get("state", {}).get("values") if view else {}
        form = AutoTranslationSettingsForm.parse_slack(form_data)
        team_channels: list[dict[str, bool | str | None]] = []
        await ack(response_action="clear")
        for channel in form.channels:
            try:
                team_channels.append(
                    await resolve_channels_to_team(
                        channel, client, context.enterprise_id, context.team_id
                    )
                )
            except SlackApiError as e:
                if e.response["error"] == "channel_not_found":
                    error_msg = _(
                        "Channel not found when creating channel translation setting. Please /invite @Straker to the channel <#{channel}> and recreate the setting."
                    )
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=error_msg,
                    )

                if e.response["error"] == "missing_scope":
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=_("Reinstall the app"),
                    )
                return
    except ValidationError as e:
        errors = convert_pydantic_to_slack_error(e)
        await ack(response_action="errors", errors=errors)
        return
    try:
        if not form.languages:
            for channel_list in team_channels:
                if channel_list:  # Check if the list is not empty
                    channel_id = channel_list["channel_id"]
                    if isinstance(channel_id, str):
                        await disable_auto_translate_group_settings(context, channel_id)
        else:
            # Filter team_channels to include channel info for storage
            filtered_channels = [
                {
                    "channel_id": str(channel["channel_id"]),
                    "team_id": str(channel["team_id"]),
                    "name": channel.get("name"),
                    "is_private": channel.get("is_private", False),
                }
                for channel in team_channels
                if channel.get("channel_id") and channel.get("team_id")
            ]
            await update_auto_translate_group_settings(
                context,
                channels=filtered_channels,
                languages=form.languages,
                display_format=form.display_format,
            )
        team_token = await get_token_for_team(team_id) if team_id else None
        if team_token:
            client.token = team_token
        context["team_id"] = team_id
        await client.views_publish(
            user_id=context["user_id"],
            view=await home_view(context, body["api_app_id"], context.get("ray")),
        )

        for channel_list in team_channels:
            bot_token = channel_list["bot_token"]
            channel_id = channel_list["channel_id"]
            if isinstance(channel_id, str) and isinstance(bot_token, str):
                await _join_channel_with_token(client, channel_id, bot_token)
                await _notify_channel_update(
                    client, context, form, channel_id, bot_token
                )

    except Exception as e:
        logger.warning("Channel translation settings submit failed", exc_info=True)
        notify_exception(e)

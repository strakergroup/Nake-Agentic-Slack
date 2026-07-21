"""Handlers for the Slack App Home tab."""

import json
from datetime import datetime, timedelta
from typing import Any, Dict

from slack_bolt.kwargs_injection.async_args import AsyncSay
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext, get_token_for_team
from app.ray.utils import is_ibm_enterprise
from app.slack.buglog_notifier import notify_exception
from app.slack.templates.messages import (
    OnboardingMessage,
    SlackMessage,
    WelcomeBackMessage,
)
from app.slack.templates.views import home_view


async def handle_home_opened(
    event: Dict[str, Any],
    context: RayContext,
    body: Dict[str, Any],
    say: AsyncSay,
    client: AsyncWebClient,
):
    # https://api.slack.com/events/app_home_opened
    # Send an onboarding message if the app home is opened for the first time.
    # TODO: put try catch around this
    try:
        channel = event.get("channel")
        if isinstance(channel, str):
            history = await client.conversations_history(channel=channel, limit=1)
            is_ibm = is_ibm_enterprise(enterprise_id=context.enterprise_id)
            if not history.get("messages"):
                message: SlackMessage = OnboardingMessage(
                    context["user_id"],
                    context["team_id"],
                    context.enterprise_id,
                    channel,
                    not is_ibm,
                )
                await say(blocks=message.blocks, text=message.text)
            # Send a welcome message if the app home has been idle for 24 hours
            else:
                history_last_24_hours = await client.conversations_history(
                    channel=channel,
                    oldest=str((datetime.now() - timedelta(hours=24)).timestamp()),
                    latest=str(datetime.now().timestamp()),
                )
                if not history_last_24_hours.get("messages"):
                    message = WelcomeBackMessage(
                        context["user_id"], context["ray"], context.enterprise_id
                    )
                    await say(blocks=message.blocks, text=message.text)
                else:
                    # There had been some activity in the last 24 hours
                    pass
    except SlackApiError as e:
        notify_exception(e)
    # Publish view to home tab.
    await client.views_publish(
        user_id=context["user_id"],
        view=await home_view(context, body["api_app_id"], context.get("ray")),
    )


async def handle_home_load(
    action: Dict[str, Any] | None,
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    # submit from next button on transation settings view
    assert action is not None
    home_info = json.loads(action["value"]) if isinstance(action["value"], str) else {}
    page = int(home_info.get("page", 1))
    team_id = home_info.get("team_id", "")
    if team_id:
        token = await get_token_for_team(team_id)
        if token:
            client.token = token
        context["team_id"] = team_id
    await client.views_publish(
        user_id=context["user_id"],
        view=await home_view(context, body["api_app_id"], context.get("ray"), page),
    )

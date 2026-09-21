"""Slack listeners that belong to the agent. Registered from app/slack/listeners.py
above the catch-all event listener, and only when AGENT_ENABLED is true.

None of these handlers opens a modal, so the app's modal trigger-safety rule holds
by construction: hand-off buttons carry the existing openers' action_ids and are
handled by the existing listeners.
"""

from __future__ import annotations

import logging
from typing import Any

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.core import copy

logger = logging.getLogger(__name__)


def register(app: AsyncApp) -> None:
    from app.slack.middleware import ray_connection

    async def _click(
        body: dict[str, Any], context: Any, client: AsyncWebClient, approved: bool
    ) -> None:
        from app.agent.adapters.entry import build_runner
        from app.agent.adapters.facts import facts_from_context
        from app.slack.buglog_notifier import notify_exception

        try:
            source = body.get("message") or {}
            context["channel_id"] = (body.get("channel") or {}).get(
                "id"
            ) or context.get("channel_id")
            surface = "dm" if str(context["channel_id"]).startswith("D") else "mention"
            facts = await facts_from_context(context, source, surface)
            approval_id = body["actions"][0]["value"]
            runner = build_runner(context, client, source)
            await runner.handle_approval(
                facts, approval_id, context["user_id"], approved
            )
        except Exception as exc:
            notify_exception(exc, msg="Arbitr agent approval click failed")

    @app.action("agent_approve", middleware=[ray_connection])
    async def agent_approve(ack, body, context, client):
        await ack()
        await _click(body, context, client, approved=True)

    @app.action("agent_decline", middleware=[ray_connection])
    async def agent_decline(ack, body, context, client):
        await ack()
        await _click(body, context, client, approved=False)

    @app.event("agent_session_stopped")
    async def agent_session_stopped(event, context, client):
        from app.agent.adapters.entry import build_runner
        from app.agent.core.types import AgentFacts

        facts = AgentFacts(
            user_id=event["user"],
            team_id=context["team_id"],
            channel_id=event["channel"],
            thread_ts=event.get("thread_ts"),
        )
        await build_runner(context, client, {}).handle_stop(facts)

    @app.event("agent_session_title_changed")
    async def agent_session_title_changed(event, logger=logger):
        # Titles are Slack's to keep. Nothing to sync yet; subscribing avoids the catch-all's log noise.
        logger.debug(
            "agent session renamed", extra={"thread_ts": event.get("thread_ts")}
        )

    @app.event("app_context_changed")
    async def app_context_changed(event, logger=logger):
        logger.debug("agent context changed")

    @app.action("agent_quick_help")
    async def agent_quick_help(ack, body, client):
        await ack()
        await client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text=copy.HELP_GENERAL,
        )

    @app.action("agent_quick_jobs", middleware=[ray_connection])
    async def agent_quick_jobs(ack, body, context, client):
        """The model is down: fall back to the app's own job list."""
        from app.slack.listener_actions import post_job_list
        from app.slack.middleware import require_ray_client

        await ack()
        if await require_ray_client(context):
            await post_job_list(
                client,
                context,
                context["ray"].client,
                preset="IN_PROGRESS",
                channel_id=body["channel"]["id"],
            )

    @app.action("agent_quick_new")
    async def agent_quick_new(ack, body, client):
        await ack()
        await client.chat_postEphemeral(
            channel=body["channel"]["id"],
            user=body["user"]["id"],
            text=copy.HANDOFF_INTRO["new_job"],
        )

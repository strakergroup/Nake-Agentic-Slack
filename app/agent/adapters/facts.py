"""Turn what the app already knows about a request into `AgentFacts`.

Runs after `ray_connection` middleware, so `context["ray"]`, `context["locale"]`
and `context["user_info"]` are populated. The agent is told these facts; it never
works them out for itself.
"""

from __future__ import annotations

from typing import Any, Literal

from slack_bolt.context.async_context import AsyncBoltContext

from app.agent.core.types import AgentFacts


async def facts_from_context(
    context: AsyncBoltContext,
    message: dict[str, Any],
    surface: Literal["dm", "mention", "panel"],
) -> AgentFacts:
    # Imports inside the function, as elsewhere in the app, to avoid import cycles.
    from app.auth.connector import is_slack_team_admin, user_may_receive_quotes
    from app.ray.utils import is_ibm_customer_enterprise

    ray = context.get("ray")
    ray_client = getattr(ray, "client", None)
    enterprise_id = context.get("enterprise_id")
    # Real IBM customer enterprises only. Straker's own IBM-like sandbox is not
    # treated as IBM here, so the connect prompt stays testable there. Open
    # question for Wade's team in docs/arbitr-agent-runbook.md.
    is_ibm = bool(is_ibm_customer_enterprise(enterprise_id))

    is_admin = False
    if ray_client is not None and enterprise_id:
        is_admin = bool(await is_slack_team_admin(ray_client.id, enterprise_id))

    user_info = (context.get("user_info") or {}).get("user", {})
    profile = user_info.get("profile", {})
    thread_ts = message.get("thread_ts") or message.get("ts")

    return AgentFacts(
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=enterprise_id,
        channel_id=context["channel_id"],
        thread_ts=thread_ts,
        locale=context.get("locale") or "en-US",
        is_connected=ray_client is not None,
        can_see_quotes=bool(await user_may_receive_quotes(ray)),
        is_ibm=is_ibm,
        is_workspace_admin=is_admin,
        surface=surface,
        display_name=profile.get("display_name") or user_info.get("real_name"),
    )

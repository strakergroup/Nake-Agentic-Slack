"""Let the agent's task cards follow what the app's services report.

Called once from app/routers/ray.py. It must never raise into that router: the
app's own event handling carries on whatever happens here.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_KINDS = {
    "verify:slack:document:quote": "quote_ready",
    "verify:slack:document:translated": "delivered",
}


def _get(data: Any, name: str) -> Any:
    if isinstance(data, dict):
        return data.get(name)
    return getattr(data, name, None)


async def notify_backend_event(event_name: str, data: Any) -> None:
    try:
        from app.config import config

        if not config.agent_enabled:
            return
        kind = _KINDS.get(event_name)
        if kind is None:
            return
        from app.agent.adapters.entry import get_store
        from app.agent.core.approvals import ApprovalGate
        from app.agent.core.audit import LoggingAuditSink
        from app.agent.core.runner import AgentRunner
        from app.agent.core.tools import ToolRegistry

        store = get_store()
        key = None
        quote_id = _get(data, "quote_id") or _get(_get(data, "extra_data"), "quote_id")
        if quote_id:
            key = await store.session_for_quote(str(quote_id))
        if key is None:
            team_id, channel_id = _get(data, "team_id"), _get(data, "channel_id")
            user_id = _get(data, "slack_user_id") or _get(data, "user_id")
            if team_id and channel_id and user_id:
                key = await store.session_for_channel_user(
                    str(team_id), str(channel_id), str(user_id)
                )
        if key is None:
            return  # not a job the agent started

        session = await store.load(key)
        if session is None:
            return
        from slack_sdk.web.async_client import AsyncWebClient

        from app.agent.adapters.slack_port import BoltSlackPort
        from app.auth.connector import get_bot_token_async

        token = await get_bot_token_async(
            session.facts.team_id, session.facts.enterprise_id
        )
        import time

        tools = ToolRegistry()
        runner = AgentRunner(
            None,  # type: ignore[arg-type]  # backend events never call the model
            BoltSlackPort(AsyncWebClient(token=token)),
            store,
            tools,
            ApprovalGate(time.time, is_gated=tools.is_gated),
            time.time,
            LoggingAuditSink(),
        )
        await runner.handle_backend_event(key, kind)
    except Exception as exc:  # never break the app's own event flow
        logger.exception("agent event bridge failed")
        try:
            from app.slack.buglog_notifier import notify_exception

            notify_exception(
                exc, msg="Arbitr agent event bridge failed", severity="WARNING"
            )
        except Exception:
            pass

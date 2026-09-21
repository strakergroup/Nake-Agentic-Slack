"""The one function the app calls to hand a free-text message to the agent.

`respond_to_message` in app/slack/listener_actions.py calls `respond_with_agent`
just before the Watson call when `agent_enabled_for` is true. If anything here
fails unexpectedly the caller falls through to Watson, so a broken agent degrades
to today's behaviour.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.core.approvals import ApprovalGate
from app.agent.core.audit import LoggingAuditSink
from app.agent.core.llm import ClaudeLlm
from app.agent.core.runner import AgentRunner
from app.agent.core.session import RedisSessionStore
from app.agent.core.tools import ToolRegistry

logger = logging.getLogger(__name__)

_store: RedisSessionStore | None = None
_llm: ClaudeLlm | None = None


def agent_enabled_for(context: AsyncBoltContext) -> bool:
    from app.config import config

    return bool(config.agent_enabled and config.anthropic_api_key)


def get_store() -> RedisSessionStore:
    global _store
    if _store is None:
        from app.redis import redis_conn

        _store = RedisSessionStore(redis_conn)
    return _store


def _get_llm() -> ClaudeLlm:
    global _llm
    if _llm is None:
        from app.config import config

        assert config.anthropic_api_key is not None
        _llm = ClaudeLlm(
            api_key=config.anthropic_api_key.get_secret_value(),
            model=config.agent_model,
        )
    return _llm


def build_runner(
    context: AsyncBoltContext, client: AsyncWebClient, message: dict[str, Any]
) -> AgentRunner:
    """Cheap to build per request: the model client and the store are shared."""
    from app.agent.adapters.slack_port import BoltSlackPort
    from app.agent.adapters.straker_tools import build_tools
    from app.config import config

    tools: ToolRegistry = build_tools(
        context, client, message, config.agent_native_document_quotes
    )
    gate = ApprovalGate(time.time, is_gated=tools.is_gated)
    return AgentRunner(
        _get_llm(),
        BoltSlackPort(client),
        get_store(),
        tools,
        gate,
        time.time,
        LoggingAuditSink(),
    )


async def respond_with_agent(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    message: dict[str, Any],
    use_thread: bool,
) -> bool:
    """Returns True when the agent handled the message, False to fall back to Watson."""
    from app.agent.adapters.facts import facts_from_context
    from app.slack.buglog_notifier import notify_exception

    try:
        surface: Literal["dm", "mention", "panel"] = "mention" if use_thread else "dm"
        facts = await facts_from_context(context, message, surface)
        # Slack's event id when present, else the message timestamp: either is stable across retries.
        event_id = str(
            context.get("event_id")
            or f"{facts.team_id}:{facts.channel_id}:{message.get('ts')}"
        )
        runner = build_runner(context, client, message)
        await runner.handle_message(
            facts, message.get("text", ""), event_id, message.get("files")
        )
        return True
    except Exception as exc:
        logger.exception("agent failed; falling back to the existing reply path")
        notify_exception(exc, msg="Arbitr agent failed; fell back to Watson")
        return False

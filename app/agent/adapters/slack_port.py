"""`SlackPort` over a Bolt `AsyncWebClient`.

The agent-session and streaming methods are newer than the app's locked
slack-sdk (3.41), so they are called by name with `api_call`. If a workspace
cannot use them (free plan, feature not enabled) the port switches that
conversation to ordinary messages and keeps going.
"""

from __future__ import annotations

import logging
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.core import slack_ui
from app.agent.core.types import AgentFacts

logger = logging.getLogger(__name__)

_UNAVAILABLE = {
    "unknown_method",
    "not_allowed",
    "feature_not_enabled",
    "not_allowed_token_type",
    "paid_teams_only",
    "missing_scope",
    "access_denied",
}
_PLAIN = "plain"  # stream ts stand-in when streaming is unavailable


class BoltSlackPort:
    def __init__(self, client: AsyncWebClient):
        self._client = client
        self._plain = False
        self._plain_plan_ts: str | None = None

    async def _agent_call(
        self, method: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self._plain:
            return None
        try:
            response = await self._client.api_call(method, json=payload)
            return response.data  # type: ignore[return-value]
        except SlackApiError as err:
            if err.response.get("error") in _UNAVAILABLE:
                logger.info(
                    "agent APIs unavailable, using plain messages",
                    extra={"method": method},
                )
                self._plain = True
                return None
            raise

    async def set_status(
        self, facts: AgentFacts, status: str, title: str | None = None
    ) -> None:
        await self._agent_call(
            "agents.sessions.setStatus", slack_ui.status_payload(facts, status, title)
        )

    async def stream_start(self, facts: AgentFacts, text: str) -> str:
        data = await self._agent_call(
            "chat.startStream", slack_ui.stream_start_payload(facts, text)
        )
        return str(data["ts"]) if data and data.get("ts") else _PLAIN

    async def stream_tasks(
        self, facts: AgentFacts, ts: str, plan: list[dict[str, Any]]
    ) -> None:
        if ts != _PLAIN:
            await self._agent_call(
                "chat.appendStream", slack_ui.stream_append_payload(facts, ts, plan)
            )

    async def stream_stop(
        self,
        facts: AgentFacts,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None,
        session_status: str,
    ) -> None:
        if ts != _PLAIN and not self._plain:
            data = await self._agent_call(
                "chat.stopStream",
                slack_ui.stream_stop_payload(facts, ts, text, blocks, session_status),
            )
            if data is not None:
                return
        await self.post(facts, text, blocks)

    async def post(
        self, facts: AgentFacts, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> None:
        await self._client.chat_postMessage(
            channel=facts.channel_id,
            text=text,
            blocks=_with_text(text, blocks),
            thread_ts=_thread(facts),
        )

    async def post_private(
        self, facts: AgentFacts, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> None:
        await self._client.chat_postEphemeral(
            channel=facts.channel_id,
            user=facts.user_id,
            text=text,
            blocks=_with_text(text, blocks),
            thread_ts=_thread(facts),
        )


def _thread(facts: AgentFacts) -> str | None:
    # DMs outside the agent panel are flat today; channels reply in the thread.
    return facts.thread_ts if facts.surface != "dm" else None


def _with_text(
    text: str, blocks: list[dict[str, Any]] | None
) -> list[dict[str, Any]] | None:
    if not blocks:
        return None
    return (
        [{"type": "section", "text": {"type": "mrkdwn", "text": text}}, *blocks]
        if text
        else blocks
    )

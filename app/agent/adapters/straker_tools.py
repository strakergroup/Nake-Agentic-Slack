"""Bind the agent's tools to the app's existing functions.

This file holds the only calls from the agent into the rest of the app. It was
written from a reading of the source and has not been run: the app cannot start
outside Straker's network. docs/arbitr-agent-runbook.md lists each binding for
Wade's team to confirm.

Never bound, on purpose (tests/agent/adapters/test_straker_tools.py enforces it):
the functions that submit, accept or cancel paid work.
"""

from __future__ import annotations

import json
import re
from typing import Any

from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.core import copy, slack_ui
from app.agent.core.tools import ToolRegistry
from app.agent.core.types import AgentFacts, ToolOutcome

_TJ = re.compile(r"(?:TJ)?\s*(\d+)", re.I)
_JOB_FILTERS = {
    "open": "IN_PROGRESS",
    "waiting_on_me": "PENDING_QUOTES",
    "delivered": "COMPLETED",
    "all": None,
}
_THREAD_CHAR_LIMIT = 5000  # same ceiling the app applies before language detection
DIGEST_KEY = "slack-ray-translator:agent:digest:{team_id}:{user_id}"


def build_tools(
    context: AsyncBoltContext,
    client: AsyncWebClient,
    message: dict[str, Any],
    native_document_quotes: bool,
) -> ToolRegistry:
    registry = ToolRegistry(native_document_quotes=native_document_quotes)
    binder = _Bindings(context, client, message)
    registry.bind("get_job", binder.get_job)
    registry.bind("list_jobs", binder.list_jobs)
    registry.bind("account_status", binder.account_status)
    registry.bind("translate_text", binder.translate_text)
    registry.bind("post_translation_publicly", binder.post_translation_publicly)
    registry.bind("offer_form", binder.offer_form)
    registry.bind("set_digest", binder.set_digest)
    registry.bind("explain", binder.explain)
    if native_document_quotes:
        registry.bind("request_document_quote", binder.request_document_quote)
    return registry


def _job_view(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "source_language": getattr(getattr(job, "sl", None), "name", None),
        "target_languages": [
            getattr(t, "name", None) for t in (getattr(job, "tl", None) or [])
        ],
        "target_date": str(getattr(job, "target_date", "") or ""),
    }


class _Bindings:
    def __init__(
        self, context: AsyncBoltContext, client: AsyncWebClient, message: dict[str, Any]
    ):
        self._context, self._client, self._message = context, client, message

    def _ray_client(self) -> Any | None:
        ray = self._context.get("ray")
        return getattr(ray, "client", None)

    async def _not_connected(self) -> ToolOutcome:
        prompt = self._context.get("login_prompt")
        return ToolOutcome(
            content="The person's account is not connected. A connect prompt has been shown to them.",
            blocks=list(prompt.blocks)
            if prompt is not None
            else slack_ui.connect_blocks(),
        )

    # ------------------------------------------------------------------ jobs

    async def get_job(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        from app.ray.service import RayService

        ray_client = self._ray_client()
        if ray_client is None:
            return await self._not_connected()
        match = _TJ.fullmatch(str(tool_input["job_id"]).strip())
        if not match:
            return ToolOutcome(
                content="That is not a job reference. They look like TJ48213.",
                is_error=True,
            )
        jobs, _response = await RayService.get_service(ray_client).get_job(
            f"TJ{match.group(1)}"
        )
        if not jobs:
            return ToolOutcome(
                content="No job with that reference is visible to this person.",
                card_detail="Not found",
            )
        return ToolOutcome(
            content=json.dumps([_job_view(j) for j in jobs]),
            card_detail=f"{len(jobs)} found",
        )

    async def list_jobs(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        from app.ray.service import RayService

        ray_client = self._ray_client()
        if ray_client is None:
            return await self._not_connected()
        status = _JOB_FILTERS[tool_input["filter"]]
        response = await RayService.get_service(ray_client).get_job_list(
            status=status, page=1, page_size=10
        )
        jobs = response.data[0] if response.data else []
        return ToolOutcome(
            content=json.dumps([_job_view(j) for j in jobs]),
            card_detail=f"{len(jobs)} found",
        )

    async def account_status(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        if facts.is_ibm or self._ray_client() is not None:
            return ToolOutcome(content="ready", card_detail="Ready")
        return await self._not_connected()

    # ------------------------------------------------------------------ translation

    async def _request_translation(
        self, text: str, target_language: str, thread_ts: str | None
    ) -> ToolOutcome:
        from app.api.language_cloud import detect_language
        from app.slack.listener_actions import get_mt_translation
        from app.slack.middleware import require_mt_tokens, require_ray_client

        if not await require_ray_client(self._context, allow_org_billing=True):
            return ToolOutcome(
                content="The account check did not pass. The app has shown the person what to do."
            )
        if not await require_mt_tokens(self._context, len(text)):
            return ToolOutcome(
                content="There is not enough balance. The app has shown the person what to do."
            )
        detected = await detect_language(self._context, text[:_THREAD_CHAR_LIMIT])
        # "direct_machine_translation" is the app's existing usage type. A dedicated
        # "agent_translate" value needs the billing side to accept it first (run-book).
        await get_mt_translation(
            self._client,
            self._context,
            target_lang=target_language,
            source_lang=detected.language,
            sentence=text,
            thread_ts=thread_ts,
            usage_type="direct_machine_translation",
        )
        return ToolOutcome(
            content="Requested. The translation service will post the result in this conversation shortly.",
            card_detail=copy.CARD_DETAIL_REQUESTED,
        )

    async def _thread_text(self, channel_id: str, thread_ts: str) -> str:
        replies = await self._client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=50
        )
        bot_id = self._context.get("bot_user_id")
        lines = [
            m.get("text", "")
            for m in replies.get("messages", [])
            if m.get("user") != bot_id and m.get("text")
        ]
        return "\n".join(lines)[:_THREAD_CHAR_LIMIT]

    async def translate_text(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        text = tool_input.get("text")
        if tool_input.get("use_current_thread"):
            if not facts.thread_ts:
                return ToolOutcome(
                    content="There is no thread here to translate.", is_error=True
                )
            # The thread text goes to the translation service. It is never returned to the model.
            text = await self._thread_text(facts.channel_id, facts.thread_ts)
        if not text or not text.strip():
            return ToolOutcome(
                content="There is no text to translate. Ask the person for it.",
                is_error=True,
            )
        return await self._request_translation(
            text, tool_input["target_language"], facts.thread_ts
        )

    async def post_translation_publicly(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        """Runs only after the approval gate has verified the person's click."""
        history = await self._client.conversations_history(
            channel=facts.channel_id,
            latest=tool_input["message_ts"],
            inclusive=True,
            limit=1,
        )
        messages = history.get("messages", [])
        if not messages or not messages[0].get("text"):
            return ToolOutcome(
                content="That message could not be found.", is_error=True
            )
        return await self._request_translation(
            messages[0]["text"], tool_input["target_language"], tool_input["message_ts"]
        )

    # ------------------------------------------------------------------ documents

    async def request_document_quote(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        """Same steps, in the same order, as the document form's submit handler
        (app/slack/handlers/document_mt.py, handle_document_mt_job), up to the quote
        request. It stops there: the app's quote message and Accept button do the rest.
        Off unless AGENT_NATIVE_DOCUMENT_QUOTES is true."""
        from app.auth.connector import user_may_receive_quotes
        from app.saq_jobs import enqueue_document_mt_quote_preflight
        from app.slack.document_mt_quotes import new_document_mt_quote_id
        from app.slack.file_submissions import slack_file_submission_payload
        from app.slack.language_validation import get_same_family_target_codes
        from app.slack.listener_actions import get_accessible_slack_files
        from app.slack.middleware import require_ray_client
        from app.slack.select_options import get_language_options

        context = self._context
        if not await require_ray_client(
            context, prompt_login=False, allow_org_billing=True
        ):
            return ToolOutcome(
                content="The account check did not pass. The app has shown the person what to do."
            )
        if not await user_may_receive_quotes(context.get("ray")):
            return ToolOutcome(
                content="Not available for this person. Use offer_form with document_translation.",
                is_error=True,
            )

        source = tool_input.get("source_language")
        targets = [str(t) for t in tool_input.get("target_languages", [])]
        if not source:
            return ToolOutcome(
                content="Ask the person which language the document is written in.",
                is_error=True,
            )
        if not targets:
            return ToolOutcome(
                content="Ask the person which languages they want.", is_error=True
            )
        known = {option["value"] for option in await get_language_options()}
        unknown = [code for code in [source, *targets] if code not in known]
        if unknown:
            return ToolOutcome(
                content=f"These language codes are not available: {', '.join(unknown)}.",
                is_error=True,
            )
        if source in targets or get_same_family_target_codes(source, targets):
            return ToolOutcome(
                content="The source language cannot also be a target, including regional variants.",
                is_error=True,
            )

        files = [
            slack_file_submission_payload(
                file_id=f["id"],
                title=f.get("title") or f.get("name", ""),
                size=f.get("size"),
            )
            for f in self._message.get("files", [])
        ]
        if not files:
            return ToolOutcome(
                content="No files are attached. Ask the person to attach the document.",
                is_error=True,
            )
        accessible, _missing = await get_accessible_slack_files(self._client, files)
        accessible_ids = {str(f["id"]) for f in accessible if f.get("id")}
        files = [f for f in files if str(f["id"]) in accessible_ids]
        if not files:
            return ToolOutcome(
                content="Arbitr cannot access those files in this conversation.",
                is_error=True,
            )

        quote_id = new_document_mt_quote_id()
        await enqueue_document_mt_quote_preflight(
            quote_id=quote_id,
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.get("enterprise_id"),
            channel_id=context["channel_id"],
            files=files,
            source_language=source,
            target_languages=targets,
        )
        from app.agent.adapters.entry import get_store
        from app.agent.core.types import session_key

        await get_store().link_quote(quote_id, session_key(facts))
        return ToolOutcome(
            content="Quote requested. It will arrive as its own message with an Accept button. Do not state a price.",
            card_detail=copy.CARD_DETAIL_REQUESTED,
            waits_for_backend=True,
        )

    # ------------------------------------------------------------------ the rest

    async def offer_form(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        form = tool_input["form"]
        files = [
            {"id": f["id"], "title": f.get("title") or f.get("name", "")}
            for f in self._message.get("files", [])
        ]
        blocks = slack_ui.handoff_blocks(form, files, facts.channel_id, facts.thread_ts)
        shown = (
            "A button that opens the form has been shown."
            if len(blocks) > 1
            else "The person has been told where the form is."
        )
        return ToolOutcome(
            content=f"{shown} Do not describe the form's fields.", blocks=blocks
        )

    async def set_digest(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        from app.redis import redis_conn

        frequency = tool_input["frequency"]
        await redis_conn.set(
            DIGEST_KEY.format(team_id=facts.team_id, user_id=facts.user_id), frequency
        )
        return ToolOutcome(
            content=copy.DIGEST_OFF if frequency == "off" else copy.DIGEST_ON
        )

    async def explain(
        self, facts: AgentFacts, tool_input: dict[str, Any]
    ) -> ToolOutcome:
        topic = tool_input["topic"]
        if topic == "pricing":
            return ToolOutcome(
                content=copy.HELP_PRICING
                if facts.can_see_quotes
                else copy.HELP_NO_PRICE
            )
        if topic == "privacy":
            return ToolOutcome(content=copy.HELP_PRIVACY)
        if topic == "getting_started":
            return ToolOutcome(content=copy.HELP_GETTING_STARTED)
        return ToolOutcome(
            content=copy.HELP_IBM_GENERIC if facts.is_ibm else copy.HELP_GENERAL
        )

"""The agent's only orchestrator.

One turn: claim the event, lock the thread, load the session, let the model
reason, run look-ups straight away, turn gated proposals into pending approvals,
post the reply, save, audit. Everything outside is reached through four ports:
`LlmPort`, `SlackPort`, `SessionStore` and `ToolRegistry`.

Safety properties enforced here rather than by the model:
- a gated tool's handler runs only from `handle_approval`, after the gate has
  verified the click, with the input stored at proposal time;
- a Slack retry of the same event does nothing;
- replies are checked before posting: no connection or money talk in IBM
  workspaces, no amounts for people who cannot see quotes;
- failures produce fixed copy, never raw errors and never an invented answer.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Protocol

from . import copy, slack_ui
from .approvals import ApprovalError, ApprovalGate
from .audit import AuditSink
from .llm import LlmPort, LlmRequestError, LlmUnavailable
from .prompt import build_system_prompt
from .session import SessionStore
from .tools import ToolRegistry
from .types import AgentFacts, Session, ToolCall, ToolOutcome, TurnRecord, session_key
from .voice import check_copy

logger = logging.getLogger(__name__)

MAX_STEPS = 8
KEEPALIVE_SECONDS = 50 * 60

_IBM_FORBIDDEN = re.compile(
    r"\b(connect(ing)? (your|an|the) account|top[ -]?up|purchase|buy (more )?(tokens|credits)|your balance|AI tokens)\b",
    re.I,
)
_AMOUNT = re.compile(
    r"(\b(USD|EUR|GBP|NZD|AUD|JPY)\s?\d|[$€£¥]\s?\d|\b\d+(\.\d+)?\s?(credits|tokens)\b)",
    re.I,
)

# Tools that earn a task card. Simple look-ups show the working status only: a
# one-line plan for "where's my job?" is noise.
_CARDS: dict[str, str] = {
    "translate_text": copy.CARD_TRANSLATE_TEXT,
    "request_document_quote": copy.CARD_PRICE,
    "offer_form": copy.CARD_FORM,
    "post_translation_in_thread": copy.CARD_POST,
}
# The AI disclaimer goes on replies that accompany AI output, not on every message.
_DELIVERS_AI_OUTPUT = {
    "translate_text",
    "post_translation_publicly",
    "post_translation_in_thread",
}


class SlackPort(Protocol):
    async def set_status(
        self, facts: AgentFacts, status: str, title: str | None = None
    ) -> None: ...
    async def stream_start(self, facts: AgentFacts, text: str) -> str: ...
    async def stream_tasks(
        self, facts: AgentFacts, ts: str, plan: list[dict[str, Any]]
    ) -> None: ...
    async def stream_stop(
        self,
        facts: AgentFacts,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None,
        session_status: str,
    ) -> None: ...
    async def post(
        self, facts: AgentFacts, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> None: ...
    async def post_private(
        self, facts: AgentFacts, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> None: ...


class _Turn:
    """Book-keeping for one turn."""

    def __init__(self, started: float):
        self.started = started
        self.last_keepalive = started
        self.tools: list[str] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.model = ""
        self.outcome = "success"
        self.error_type: str | None = None
        self.violations: list[str] = []
        self.stream_ts: str | None = None
        self.tool_failures = 0
        self.extra_blocks: list[dict[str, Any]] = []


class AgentRunner:
    def __init__(
        self,
        llm: LlmPort,
        slack: SlackPort,
        store: SessionStore,
        tools: ToolRegistry,
        gate: ApprovalGate,
        clock: Callable[[], float],
        audit: AuditSink,
        max_steps: int = MAX_STEPS,
    ):
        self._llm, self._slack, self._store = llm, slack, store
        self._tools, self._gate, self._clock, self._audit = tools, gate, clock, audit
        self._max_steps = max_steps

    # ------------------------------------------------------------------ entry points

    async def handle_message(
        self,
        facts: AgentFacts,
        text: str,
        event_id: str,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        if not await self._store.claim_turn(event_id):
            return
        key = session_key(facts)
        async with self._store.lock(key):
            session = await self._store.load(key) or Session(key=key, facts=facts)
            is_new = not session.messages
            session.facts = facts
            session.stopped = False
            await self._store.clear_stop(key)
            session.messages.append(
                {"role": "user", "content": _user_content(text, files)}
            )
            await self._store.remember_open(
                facts.team_id, facts.channel_id, facts.user_id, key
            )
            turn = _Turn(self._clock())
            if is_new:
                session.title = _title(text)
            session.plan = []  # a new message starts a new plan
            await self._slack.set_status(
                facts, "processing", session.title if is_new else None
            )
            session.status = "processing"
            await self._reason(session, turn)
            await self._store.save(session)
            self._record(session, turn)

    async def handle_approval(
        self, facts: AgentFacts, approval_id: str, clicked_by: str, approved: bool
    ) -> None:
        key = session_key(facts)
        async with self._store.lock(key):
            session = await self._store.load(key)
            if session is None:
                await self._slack.post_private(facts, copy.APPROVAL_ERRORS["unknown"])
                return
            turn = _Turn(self._clock())
            try:
                if approved:
                    approval = self._gate.consume(session, approval_id, clicked_by)
                else:
                    approval = self._gate.decline(session, approval_id, clicked_by)
            except ApprovalError as err:
                await self._store.save(session)
                await self._slack.post_private(facts, copy.APPROVAL_ERRORS[err.reason])
                return

            if not approved:
                self._drop_card(session, _gated_card(approval.tool))
                self._note(
                    session, f"The person declined: {approval.summary}", copy.DECLINED
                )
                await self._slack.post(session.facts, copy.DECLINED)
                session.status = "suspended" if session.pending else "active"
                await self._slack.set_status(session.facts, session.status)
                await self._store.save(session)
                self._record(session, turn)
                return

            await self._slack.set_status(session.facts, "processing")
            session.status = "processing"
            session.plan = []  # execution is a second message with its own plan
            card = _gated_card(approval.tool)
            self._card(session, card, "in_progress", "")
            turn.tools.append(approval.tool)
            try:
                outcome = await self._tools.handler_for(approval.tool)(
                    session.facts, approval.tool_input
                )
            except Exception:
                logger.exception("approved tool failed", extra={"tool": approval.tool})
                outcome = ToolOutcome(content="failed", is_error=True)
            if outcome.is_error:
                self._card(session, card, "error", "")
                turn.outcome, turn.error_type = "failure", "tool_error"
                await self._finish_plain(session, copy.FALLBACK_TOOL_FAILED, None)
            else:
                self._card(session, card, "complete", outcome.card_detail or "")
                session.messages.append(
                    {
                        "role": "user",
                        "content": f"[app note] The person approved: {approval.summary}. Result: {outcome.content}",
                    }
                )
                await self._reason(session, turn)
            await self._store.save(session)
            self._record(session, turn)

    async def handle_stop(self, facts: AgentFacts) -> None:
        key = session_key(facts)
        await self._store.request_stop(key)  # seen by a running turn between steps
        async with self._store.lock(key):
            session = await self._store.load(key)
            if session is None:
                return
            session.stopped = True
            self._gate.cancel_all(session)
            session.plan = [c for c in session.plan if c["state"] == "complete"]
            session.status = "active"
            await self._slack.post(session.facts, copy.STOPPED)
            await self._slack.set_status(session.facts, "active")
            await self._store.save(session)

    async def handle_backend_event(
        self, key: str, kind: str, detail: str | None = None
    ) -> None:
        """The app's services reported back. `kind` is quote_ready or delivered."""
        async with self._store.lock(key):
            session = await self._store.load(key)
            if session is None:
                return
            if kind == "quote_ready":
                self._card(
                    session,
                    copy.CARD_PRICE,
                    "complete",
                    detail or copy.CARD_DETAIL_QUOTE_READY,
                )
                session.status = "suspended"
            elif kind == "delivered":
                self._card(
                    session,
                    copy.CARD_DELIVER,
                    "complete",
                    detail or copy.CARD_DETAIL_DELIVERED,
                )
                session.status = "active"
            else:
                return
            await self._slack.set_status(session.facts, session.status)
            await self._store.save(session)

    # ------------------------------------------------------------------ the loop

    async def _reason(self, session: Session, turn: _Turn) -> None:
        facts = session.facts
        system = build_system_prompt(facts)
        specs = self._tools.specs_for(facts.can_see_quotes, facts.surface)

        for _ in range(self._max_steps):
            if await self._store.stop_requested(session.key):
                turn.outcome = "partial"
                return
            await self._keepalive(session, turn)
            try:
                step = await self._llm.step(system, specs, session.messages)
            except LlmUnavailable:
                turn.outcome, turn.error_type = "failure", "llm_error"
                await self._finish_plain(
                    session, copy.FALLBACK_MODEL_DOWN, slack_ui.quick_action_blocks()
                )
                return
            except LlmRequestError:
                logger.exception(
                    "model rejected the request", extra={"session": session.key}
                )
                turn.outcome, turn.error_type = "failure", "validation_error"
                await self._finish_plain(
                    session, copy.FALLBACK_MODEL_DOWN, slack_ui.quick_action_blocks()
                )
                return

            turn.input_tokens += step.input_tokens
            turn.output_tokens += step.output_tokens
            turn.model = step.model
            session.messages.append({"role": "assistant", "content": step.content})

            if step.stop_reason == "refusal":
                turn.outcome = "partial"
                await self._finish(session, turn, copy.FALLBACK_REFUSED, disclaim=False)
                return
            if not step.tool_calls:
                await self._finish(session, turn, step.text, disclaim=True)
                return

            results = []
            for call in step.tool_calls:
                results.append(await self._run_call(session, turn, call))
                if turn.tool_failures >= 2:
                    session.messages.append(
                        {
                            "role": "user",
                            "content": results + _unanswered(step.tool_calls, results),
                        }
                    )
                    turn.outcome, turn.error_type = "failure", "tool_error"
                    _say(session, copy.FALLBACK_TOOL_FAILED)
                    await self._finish(
                        session, turn, copy.FALLBACK_TOOL_FAILED, disclaim=False
                    )
                    return
            session.messages.append({"role": "user", "content": results})

        turn.outcome, turn.error_type = "partial", "timeout"
        _say(session, copy.FALLBACK_TOO_MANY_STEPS)
        await self._finish(session, turn, copy.FALLBACK_TOO_MANY_STEPS, disclaim=False)

    async def _run_call(
        self, session: Session, turn: _Turn, call: ToolCall
    ) -> dict[str, Any]:
        facts = session.facts
        if not self._tools.is_known(call.name):
            return _result(call, f"Unknown tool: {call.name}", is_error=True)
        if call.name == "request_document_quote" and not facts.can_see_quotes:
            return _result(
                call,
                "Not available for this person. Use offer_form with document_translation.",
                is_error=True,
            )
        if call.name == "submit_document_translation" and facts.can_see_quotes:
            return _result(
                call,
                "This person sees quotes. Use request_document_quote.",
                is_error=True,
            )
        if call.name == "post_translation_in_thread" and facts.surface != "mention":
            return _result(
                call,
                "Only available when mentioned in a channel thread. Use post_translation_publicly.",
                is_error=True,
            )

        if self._tools.is_gated(call.name):
            summary, label, card = _gated_presentation(call)
            approval = self._gate.create(
                session,
                call.name,
                call.input,
                requested_by=facts.user_id,
                summary=summary,
                tool_use_id=call.id,
            )
            self._card(session, card, "pending", "")
            await self._show_plan(session, turn)
            turn.extra_blocks.extend(slack_ui.approval_blocks(approval, label))
            return _result(call, copy.WAITING_FOR_APPROVAL)

        turn.tools.append(call.name)
        card = _CARDS.get(call.name)
        if card:
            self._card(session, card, "in_progress", "")
            await self._show_plan(session, turn)
        try:
            outcome = await self._tools.handler_for(call.name)(facts, call.input)
        except Exception:
            logger.exception("tool failed", extra={"tool": call.name})
            outcome = ToolOutcome(
                content="The service did not respond. You may try once more.",
                is_error=True,
            )
        if outcome.is_error:
            turn.tool_failures += 1
            if card:
                self._card(session, card, "error", "")
        else:
            if card:
                waiting = outcome.waits_for_backend
                self._card(
                    session,
                    outcome.card or card,
                    "in_progress" if waiting else "complete",
                    outcome.card_detail or "",
                )
                if call.name == "request_document_quote":
                    self._card(session, copy.CARD_DELIVER, "pending", "")
            if outcome.blocks:
                turn.extra_blocks.extend(outcome.blocks)
        if card:
            await self._show_plan(session, turn)
        return _result(call, outcome.content, is_error=outcome.is_error)

    # ------------------------------------------------------------------ output

    async def _finish(
        self, session: Session, turn: _Turn, text: str, disclaim: bool
    ) -> None:
        facts = session.facts
        text, guarded = _guard(facts, text)
        if guarded:
            turn.outcome = "partial"
        turn.violations = [v.rule for v in check_copy(text)]
        if disclaim and not guarded and _DELIVERS_AI_OUTPUT & set(turn.tools):
            text = f"{text}\n\n_{copy.DISCLAIMER}_" if text else copy.DISCLAIMER
        session.status = "suspended" if session.pending else "active"
        blocks = turn.extra_blocks or None
        if turn.stream_ts is None:
            turn.stream_ts = await self._slack.stream_start(facts, "")
        await self._slack.stream_stop(
            facts, turn.stream_ts, text, blocks, session.status
        )

    async def _finish_plain(
        self, session: Session, text: str, blocks: list[dict[str, Any]] | None
    ) -> None:
        """Failure path: no streaming, fixed copy only."""
        session.status = "active"
        await self._slack.post(session.facts, text, blocks)
        await self._slack.set_status(session.facts, "active")

    async def _show_plan(self, session: Session, turn: _Turn) -> None:
        if turn.stream_ts is None:
            turn.stream_ts = await self._slack.stream_start(session.facts, "")
        await self._slack.stream_tasks(session.facts, turn.stream_ts, session.plan)

    async def _keepalive(self, session: Session, turn: _Turn) -> None:
        if self._clock() - turn.last_keepalive >= KEEPALIVE_SECONDS:
            await self._slack.set_status(session.facts, "processing")
            turn.last_keepalive = self._clock()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _card(session: Session, title: str, state: str, detail: str) -> None:
        for card in session.plan:
            if card["title"] == title:
                card["state"], card["detail"] = state, detail or card.get("detail", "")
                return
        session.plan.append(
            {
                "id": f"c{len(session.plan) + 1}",
                "title": title,
                "state": state,
                "detail": detail,
            }
        )

    @staticmethod
    def _drop_card(session: Session, title: str) -> None:
        session.plan = [c for c in session.plan if c["title"] != title]

    @staticmethod
    def _note(session: Session, note: str, reply: str) -> None:
        """Record an event in history while keeping user/assistant alternation."""
        session.messages.append({"role": "user", "content": f"[app note] {note}"})
        session.messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": reply}]}
        )

    def _record(self, session: Session, turn: _Turn) -> None:
        self._audit.record(
            TurnRecord(
                session_key=session.key,
                user_id=session.facts.user_id,
                team_id=session.facts.team_id,
                model=turn.model,
                tools_called=turn.tools,
                outcome=turn.outcome,  # type: ignore[arg-type]
                error_type=turn.error_type,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                total_latency_ms=int((self._clock() - turn.started) * 1000),
                voice_violations=turn.violations,
            )
        )


def _gated_card(tool: str) -> str:
    return (
        copy.CARD_DELIVER if tool == "submit_document_translation" else copy.CARD_POST
    )


def _gated_presentation(call: ToolCall) -> tuple[str, str, str]:
    """Summary text, approve-button label and task card for a gated proposal. No price, ever."""
    if call.name == "submit_document_translation":
        names = (
            call.input.get("target_language_names")
            or call.input.get("target_languages")
            or []
        )
        languages = ", ".join(str(n) for n in names)
        return (
            copy.SUBMIT_DOCUMENT_PROMPT.format(languages=languages),
            copy.SUBMIT_DOCUMENT_APPROVE,
            copy.CARD_DELIVER,
        )
    return copy.POST_PUBLICLY_PROMPT, copy.POST_PUBLICLY_APPROVE, copy.CARD_POST


def _say(session: Session, text: str) -> None:
    """Record a fixed reply so history keeps alternating user and assistant turns."""
    session.messages.append(
        {"role": "assistant", "content": [{"type": "text", "text": text}]}
    )


def _user_content(text: str, files: list[dict[str, Any]] | None) -> str:
    """The model sees file metadata only, never contents."""
    if not files:
        return text
    described = ", ".join(
        f"{f.get('title') or f.get('name', 'file')} ({f.get('filetype', 'unknown type')}, {_size(f.get('size'))})"
        for f in files
    )
    return f"{text}\n\n[attached files: {described}]".strip()


def _size(size: Any) -> str:
    if not isinstance(size, (int, float)) or size <= 0:
        return "size unknown"
    return (
        f"{size / 1_048_576:.1f} MB"
        if size >= 1_048_576
        else f"{max(1, round(size / 1024))} KB"
    )


def _title(text: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else "Conversation"
    return first[:60]


def _result(call: ToolCall, content: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": call.id,
        "content": content,
    }
    if is_error:
        result["is_error"] = True
    return result


def _unanswered(
    calls: list[ToolCall], results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Every tool_use needs a tool_result, even when the turn is cut short."""
    done = {r["tool_use_id"] for r in results}
    return [_result(c, "Not run.", is_error=True) for c in calls if c.id not in done]


def _guard(facts: AgentFacts, text: str) -> tuple[str, bool]:
    """Belt and braces behind the prompt rules."""
    if facts.is_ibm and _IBM_FORBIDDEN.search(text):
        return copy.HELP_IBM_GENERIC, True
    if not facts.can_see_quotes and _AMOUNT.search(text):
        return copy.HELP_NO_PRICE, True
    return text, False

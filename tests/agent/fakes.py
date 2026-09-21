"""Test doubles for the agent core. Kept out of a conftest on purpose: the core
tests run with --noconftest because the app's root conftest opens MySQL."""

from __future__ import annotations

from typing import Any

from app.agent.core.types import AgentFacts, Session, ToolOutcome, session_key


class FixedClock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_facts(**overrides: Any) -> AgentFacts:
    base: dict[str, Any] = dict(
        user_id="U_MIKA",
        team_id="T1",
        channel_id="D1",
        enterprise_id=None,
        thread_ts="111.222",
        locale="en-US",
        is_connected=True,
        can_see_quotes=True,
        is_ibm=False,
        is_workspace_admin=False,
        surface="dm",
        display_name="Mika Kato",
    )
    base.update(overrides)
    return AgentFacts(**base)


def make_session(**overrides: Any) -> Session:
    facts = overrides.pop("facts", make_facts())
    return Session(key=session_key(facts), facts=facts, **overrides)


class RecordingSlack:
    """Implements SlackPort and records every call in order."""

    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ts = 0

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def texts(self) -> list[str]:
        return [args.get("text", "") for _, args in self.calls]

    async def set_status(self, facts, status, title=None):
        self.calls.append(("set_status", {"status": status, "title": title}))

    async def stream_start(self, facts, text):
        self._ts += 1
        self.calls.append(("stream_start", {"text": text}))
        return f"ts-{self._ts}"

    async def stream_tasks(self, facts, ts, plan):
        self.calls.append(
            ("stream_tasks", {"ts": ts, "plan": [dict(card) for card in plan]})
        )

    async def stream_stop(self, facts, ts, text, blocks, session_status):
        self.calls.append(
            (
                "stream_stop",
                {
                    "ts": ts,
                    "text": text,
                    "blocks": blocks,
                    "session_status": session_status,
                },
            )
        )

    async def post(self, facts, text, blocks=None):
        self.calls.append(("post", {"text": text, "blocks": blocks}))

    async def post_private(self, facts, text, blocks=None):
        self.calls.append(("post_private", {"text": text, "blocks": blocks}))


class ScriptedLlm:
    """Returns queued LlmSteps and records what it was sent."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.requests: list[dict[str, Any]] = []

    async def step(self, system, tools, messages):
        import copy as _copy

        self.requests.append(
            {"system": system, "tools": tools, "messages": _copy.deepcopy(messages)}
        )
        if not self._steps:
            raise AssertionError("ScriptedLlm ran out of steps")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def ok(content: str = "done", **kwargs: Any) -> ToolOutcome:
    return ToolOutcome(content=content, **kwargs)

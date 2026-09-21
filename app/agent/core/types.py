"""Shared data shapes for the Arbitr agent core.

Nothing in this package imports the rest of the app (see
tests/agent/test_core_isolation.py). The adapters translate between these
shapes and the app's own objects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Literal

Surface = Literal["dm", "mention", "panel"]
SessionStatus = Literal["processing", "active", "suspended", "closed"]
ToolKind = Literal["lookup", "gated"]
Outcome = Literal["success", "partial", "failure"]


@dataclass(frozen=True)
class AgentFacts:
    """What the app already knows about the person before the model sees anything."""

    user_id: str
    team_id: str
    channel_id: str
    enterprise_id: str | None = None
    thread_ts: str | None = None
    locale: str = "en-US"
    is_connected: bool = False
    can_see_quotes: bool = False
    is_ibm: bool = False
    is_workspace_admin: bool = False
    surface: Surface = "dm"
    display_name: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    kind: ToolKind


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool hands back to the model. `content` is plain text or JSON text.

    `card` optionally names the task card to show for this work, and `blocks`
    lets a tool ask for Slack blocks (for example a hand-off button) to be posted.
    """

    content: str
    is_error: bool = False
    card: str | None = None
    card_detail: str | None = None
    blocks: list[dict[str, Any]] | None = None
    waits_for_backend: bool = False


@dataclass(frozen=True)
class PendingApproval:
    id: str
    session_key: str
    tool: str
    tool_input: dict[str, Any]
    requested_by: str
    summary: str
    show_amount: bool
    amount_text: str | None
    created_at: float
    expires_at: float
    tool_use_id: str | None = None


@dataclass
class Session:
    key: str
    facts: AgentFacts
    messages: list[dict[str, Any]] = field(default_factory=list)
    status: SessionStatus = "active"
    title: str | None = None
    pending: dict[str, PendingApproval] = field(default_factory=dict)
    used_approvals: list[str] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False
    plain_messages_only: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Session":
        data = dict(data)
        data["facts"] = AgentFacts(**data["facts"])
        data["pending"] = {
            k: PendingApproval(**v) for k, v in data.get("pending", {}).items()
        }
        return cls(**data)


def session_key(facts: AgentFacts) -> str:
    return f"{facts.team_id}:{facts.channel_id}:{facts.thread_ts or 'root'}"


@dataclass(frozen=True)
class TurnRecord:
    """One audit row per agent turn: the fields Slack's governance guide tells admins to expect."""

    session_key: str
    user_id: str
    team_id: str
    model: str
    tools_called: list[str]
    outcome: Outcome
    error_type: str | None
    input_tokens: int
    output_tokens: int
    total_latency_ms: int
    voice_violations: list[str] = field(default_factory=list)


ToolHandler = Callable[[AgentFacts, dict[str, Any]], Awaitable[ToolOutcome]]

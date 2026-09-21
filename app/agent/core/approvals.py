"""The approval gate: a click by the right person is the only way a gated tool runs.

Pure rules, no Slack and no model. The runner asks the gate to create a pending
approval when the model proposes a gated tool, and only runs the tool with what
`consume` returns. The button carries nothing but the approval id, so a tampered
payload cannot change what runs.
"""

from __future__ import annotations

import secrets
from typing import Any, Callable

from .types import PendingApproval, Session


class ApprovalError(Exception):
    """`reason` is one of: wrong_user, expired, unknown, already_used, stopped."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ApprovalGate:
    def __init__(
        self,
        clock: Callable[[], float],
        ttl_seconds: int = 900,
        id_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
        is_gated: Callable[[str], bool] = lambda name: True,
    ):
        self._clock = clock
        self._ttl = ttl_seconds
        self._new_id = id_factory
        self._is_gated = is_gated

    def create(
        self,
        session: Session,
        tool: str,
        tool_input: dict[str, Any],
        requested_by: str,
        summary: str,
        show_amount: bool = False,
        amount_text: str | None = None,
        tool_use_id: str | None = None,
    ) -> PendingApproval:
        if not self._is_gated(tool):
            raise ValueError(
                f"{tool} is not a gated tool; only gated tools get approvals"
            )
        now = self._clock()
        approval = PendingApproval(
            id=self._new_id(),
            session_key=session.key,
            tool=tool,
            tool_input=dict(tool_input),
            requested_by=requested_by,
            summary=summary,
            show_amount=show_amount,
            amount_text=amount_text if show_amount else None,
            created_at=now,
            expires_at=now + self._ttl,
            tool_use_id=tool_use_id,
        )
        session.pending[approval.id] = approval
        return approval

    def verify(
        self, session: Session, approval_id: str, clicked_by: str
    ) -> PendingApproval:
        if approval_id in session.used_approvals:
            raise ApprovalError("already_used")
        if session.stopped:
            raise ApprovalError("stopped")
        approval = session.pending.get(approval_id)
        if approval is None or approval.session_key != session.key:
            raise ApprovalError("unknown")
        if approval.requested_by != clicked_by:
            raise ApprovalError("wrong_user")
        if self._clock() > approval.expires_at:
            raise ApprovalError("expired")
        return approval

    def consume(
        self, session: Session, approval_id: str, clicked_by: str
    ) -> PendingApproval:
        """Verify, then mark used. The returned copy is the only source of tool input."""
        approval = self.verify(session, approval_id, clicked_by)
        del session.pending[approval_id]
        session.used_approvals.append(approval_id)
        return approval

    def decline(
        self, session: Session, approval_id: str, clicked_by: str
    ) -> PendingApproval:
        approval = self.verify(session, approval_id, clicked_by)
        del session.pending[approval_id]
        session.used_approvals.append(approval_id)
        return approval

    def cancel_all(self, session: Session) -> None:
        session.used_approvals.extend(session.pending)
        session.pending.clear()

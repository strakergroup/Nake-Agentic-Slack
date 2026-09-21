"""One audit record per agent turn."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Protocol

from .types import TurnRecord

logger = logging.getLogger(__name__)


class AuditSink(Protocol):
    def record(self, turn: TurnRecord) -> None: ...


class LoggingAuditSink:
    def record(self, turn: TurnRecord) -> None:
        logger.info("agent turn", extra={"agent_turn": asdict(turn)})


class ListAuditSink:
    def __init__(self) -> None:
        self.turns: list[TurnRecord] = []

    def record(self, turn: TurnRecord) -> None:
        self.turns.append(turn)

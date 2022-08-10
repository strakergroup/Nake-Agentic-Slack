from typing import Any
import datetime
from pydantic import BaseModel


class TextMessage:
    """A class representing a text-only Slack Message."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def text(self) -> str:
        return self._text


class SlackMessage(TextMessage):
    """A class representing a Slack Message with blocks."""

    def __init__(self, text: str, blocks: list[dict[str, Any]]) -> None:
        super().__init__(text)
        self._blocks = blocks

    @property
    def blocks(self) -> list[dict[str, Any]]:
        return self._blocks


class NewJobForm(BaseModel):
    """The model for a new job form."""

    reference: str | None = None
    target_date: datetime.date
    workflow: str
    category: str
    source_language: str
    target_languages: set[str]
    file_ids: list[str]

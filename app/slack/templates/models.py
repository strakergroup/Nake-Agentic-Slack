class TextMessage:
    """A class representing a text-only Slack Message."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def text(self) -> str:
        return self._text


class SlackMessage(TextMessage):
    """A class representing a Slack Message with blocks."""

    def __init__(self, text: str, blocks: list) -> None:
        super().__init__(text)
        self._blocks = blocks

    @property
    def blocks(self) -> list:
        return self._blocks

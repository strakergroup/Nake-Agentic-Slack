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


class RayLanguage(BaseModel):
    """The model for a RAY language."""

    code: str
    name: str

    @classmethod
    def parse_slack_option(cls, option: dict[str, Any]) -> "RayLanguage":
        """Parses a Slack option object (language) where the value is the language
        code and the text is the name.

        https://api.slack.com/reference/block-kit/composition-objects#option
        """
        return cls(code=option["value"], name=option["text"]["text"])


class SlackFile(BaseModel):
    """The model for a Slack file."""

    id: str
    title: str

    @classmethod
    def parse_slack_option(cls, option: dict[str, Any]) -> "SlackFile":
        """Parses a Slack option object (file) where the value is the file ID
        and the text is the title.

        https://api.slack.com/reference/block-kit/composition-objects#option
        """
        return cls(id=option["value"], title=option["text"]["text"])


class NewJobForm(BaseModel):
    """The model for a new job form."""

    reference: str | None = None
    target_date: datetime.date
    workflow: str
    category: str
    source_lang: RayLanguage
    target_langs: list[RayLanguage]
    files: list[SlackFile]

    @classmethod
    def parse_slack(cls, values: dict[str, dict[str:Any]]) -> "NewJobForm":
        """Parses a view submission payload from Slack.

        Args:
            values (dict): The input values payload from the Slack API
            (`view["state"]["values"]`).

        Returns:
            NewJobForm: An instance parsed and validated from the Slack payload.
        """
        try:
            return cls(
                reference=values["reference"]["reference"]["value"],
                target_date=values["target_date"]["target_date"]["selected_date"],
                workflow=values["workflow"]["workflow"]["selected_option"]["value"],
                category=values["category"]["category"]["selected_option"]["value"],
                source_lang=RayLanguage.parse_slack_option(
                    values["source"]["language_options"]["selected_option"]
                ),
                target_langs=[
                    RayLanguage.parse_slack_option(opt)
                    for opt in values["target"]["language_options"]["selected_options"]
                ],
                files=[
                    SlackFile.parse_slack_option(opt)
                    for opt in values["upload_files"]["file_options"][
                        "selected_options"
                    ]
                ],
            )
        except KeyError as e:
            raise ValueError("The Slack payload format is incorrect") from e

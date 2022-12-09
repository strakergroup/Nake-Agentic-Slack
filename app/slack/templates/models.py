from typing import Any
from pydantic import BaseModel, ValidationError, validator
from ray_sdk.api.v3.file import is_valid_file_ext


def convert_pydantic_to_slack_error(error: ValidationError) -> dict[str, str]:
    """Creates a Slack view error dict from pydantic's ValidationError.
    The view's input `block_id` needs to match the pydantic model's properties.

    https://api.slack.com/surfaces/modals/using#displaying_errors

    Args:
        error (ValidationError): The pydantic ValidationError to convert.

    Returns:
        dict[str, str]: The error dict for Slack's view_submission event response.
    """
    slack_errors = {}
    for e in error.errors():
        # Note: Errors for the same property will be overriden, including errors of
        # multiple items in a list.
        slack_errors[e["loc"][0]] = e["msg"]
    return slack_errors


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

    files: list[SlackFile]
    reference: str | None = None  # Max 100 chars, validated in view
    source_lang: RayLanguage
    target_langs: list[RayLanguage]
    # target_date: datetime.date
    service: str
    validation: bool
    notes: str | None = None
    # category: str

    @property
    def workflow(self) -> str:
        """The derived API workflow from the service and validation settings."""
        if self.service == "Translation":
            if self.validation:
                return "TRANSLATION_VALIDATION"
            else:
                return "TRANSLATION"
        elif self.service == "Translation + Edit":
            if self.validation:
                return "TRANSLATION_REVIEW_VALIDATION"
            else:
                return "TRANSLATION_REVIEW"
        else:
            raise ValueError(f"Cannot get workflow from service: {self.service}")

    @validator("target_langs")
    def validate_target_langs(cls, v):
        if not v:
            raise ValueError("At least one target language is required")
        return v

    @validator("target_langs", each_item=True)
    def validate_target_langs_item(cls, v, values):
        if v.code == values["source_lang"].code:
            raise ValueError("The source language cannot be a target language")
        return v

    # @validator("target_date")
    # def validate_target_date(cls, v):
    #     if v <= datetime.date.today():
    #         raise ValueError("The target date must be a future date")
    #     return v

    @validator("files")
    def validate_files(cls, v):
        if not v:
            raise ValueError("At least one file is required")
        return v

    @validator("files", each_item=True)
    def validate_files_item(cls, v: SlackFile):
        if not is_valid_file_ext(v.title):
            raise ValueError(f"File type is not allowed: {v.title}")
        return v

    @classmethod
    def parse_slack(cls, values: dict[str, dict[str, Any]]) -> "NewJobForm":
        """Parses a view submission payload from Slack.

        Args:
            values (dict): The input values payload from the Slack API
            (`view["state"]["values"]`).

        Returns:
            NewJobForm: An instance parsed and validated from the Slack payload.
        """
        try:
            return cls(
                files=[
                    SlackFile.parse_slack_option(opt)
                    for opt in values["files"]["file_options"]["selected_options"]
                ],
                reference=values["reference"]["reference"]["value"],
                source_lang=RayLanguage.parse_slack_option(
                    values["source_lang"]["language_options"]["selected_option"]
                ),
                target_langs=[
                    RayLanguage.parse_slack_option(opt)
                    for opt in values["target_langs"]["language_options"][
                        "selected_options"
                    ]
                ],
                # target_date=values["target_date"]["target_date"]["selected_date"],
                service=values["service"]["service"]["selected_option"]["value"],
                validation=bool(values["validation"]["validation"]["selected_options"]),
                notes=values["notes"]["notes"]["value"],
                # category=values["category"]["category"]["selected_option"]["value"],
            )
        except KeyError as e:
            raise ValueError("The Slack payload format is incorrect") from e

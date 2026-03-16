from typing import Any

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from ray_sdk.api.v3.file import is_valid_file_ext

from ...constants import HUMAN_EVALUATION_WORKFLOW_UUID
from ...models import SlackGroupSettingsTranslation


def convert_pydantic_to_slack_error(error: ValidationError) -> dict[str, str]:
    """Creates a Slack view error dict from pydantic's ValidationError.
    The view's input `block_id` needs to match the pydantic model's properties.

    https://api.slack.com/surfaces/modals/using#displaying_errors

    Args:
        error (ValidationError): The pydantic ValidationError to convert.

    Returns:
        dict[str, str]: The error dict for Slack's view_submission event response.
    """
    slack_errors: dict[str, str] = {}
    for e in error.errors():
        # Note: Errors for the same property will be overriden, including errors of
        # multiple items in a list.
        slack_errors[str(e["loc"][0])] = e["msg"].removeprefix("Value error, ")
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


class JobSearchForm(BaseModel):
    """The model for a job search form."""

    reference: str = ""  # Max 100 chars, validated in view

    @classmethod
    def parse_slack(cls, values: dict[str, dict[str, Any]]) -> "JobSearchForm":
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
            )
        except KeyError as e:
            raise ValueError("The Slack payload format is incorrect") from e


class NewJobForm(BaseModel):
    """The model for a new job form."""

    files: list[SlackFile]
    reference: str | None = None  # Max 100 chars, validated in view
    source_lang: RayLanguage
    target_langs: list[RayLanguage]
    group_id: str | None = None
    # target_date: datetime.date
    service: str
    timeframe: str
    validation: bool | None = None
    notes: str | None = None
    # translation_notes: str | None = None
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

    @field_validator("target_langs")
    @classmethod
    def validate_target_langs(cls, v: list[RayLanguage], info: ValidationInfo):
        if not v:
            raise ValueError("At least one target language is required")
        for lang in v:
            if lang.code == info.data["source_lang"].code:
                raise ValueError("The source language cannot be a target language")
        return v

    @field_validator("files")
    @classmethod
    def validate_files(cls, v: list[SlackFile]):
        if not v:
            raise ValueError("At least one file is required")
        for file in v:
            if not is_valid_file_ext(file.title):
                raise ValueError(f"File type is not allowed: {file.title}")
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
                    for key in values["files"]
                    if key.startswith("file_options")
                    for opt in values["files"][key]["selected_options"]
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
                group_id=(
                    values["group"]["group_options"]["selected_option"]["value"]
                    if values["group"]["group_options"]["selected_option"]
                    else None
                ),
                # target_date=values["target_date"]["target_date"]["selected_date"],
                service=values["service"]["service"]["selected_option"]["value"],
                timeframe=values["timeframe"]["timeframe"]["selected_option"]["value"],
                # validation=bool(values["validation"]["validation"]["selected_options"]),
                notes=values["notes"]["notes"]["value"],
                # translation_notes=values["translation_notes"]["translation_notes"][
                #     "value"
                # ],
                # category=values["category"]["category"]["selected_option"]["value"],
            )
        except KeyError as e:
            raise ValueError("The Slack payload format is incorrect") from e


class SsoLoginForm(BaseModel):
    """The model for a sso login form."""

    email: EmailStr
    firstName: str = Field(
        min_length=3, max_length=55, pattern='^[^*<>\\%$##!();}{\[\]&"]*$'
    )
    lastName: str = Field(
        min_length=3, max_length=55, pattern='^[^*<>\\%$##!();}{\[\]&"]*$'
    )

    @classmethod
    def parse_slack(cls, values: dict[str, dict[str, Any]]) -> "SsoLoginForm":
        """Parses a view submission payload from Slack.

        Args:
            values (dict): The input values payload from the Slack API
            (`view["state"]["values"]`).

        Returns:
            SsoLoginForm: An instance parsed and validated from the Slack payload.
        """
        try:
            return cls(
                email=values["email"]["email"]["value"],
                firstName=values["firstName"]["firstName"]["value"],
                lastName=values["lastName"]["lastName"]["value"],
            )
        except KeyError as e:
            raise ValueError("The Slack payload format is incorrect") from e


class AutoTranslationSettingsForm(BaseModel):
    """The model for the auto-translation settings form."""

    channels: list[str]
    languages: list[str]
    display_format: SlackGroupSettingsTranslation.DisplayFormatType

    @classmethod
    def parse_slack(
        cls, values: dict[str, dict[str, Any]]
    ) -> "AutoTranslationSettingsForm":
        """Parses a view submission payload from Slack.

        Args:
            values (dict): The input values payload from the Slack API
            (`view["state"]["values"]`).

        Returns:
            AutoTranslationSettingsForm: An instance parsed and validated from the Slack payload.
        """
        try:
            return cls(
                channels=[
                    c for c in values["channels"]["channels"]["selected_conversations"]
                ],
                languages=[
                    opt["value"]
                    for opt in values["languages"]["languages"]["selected_options"]
                ],
                display_format=values["display_format"]["display_format"][
                    "selected_option"
                ]["value"],
            )
        except KeyError as e:
            # TODO Better error handling
            raise ValueError("The Slack payload format is incorrect") from e


class EvaluateJobForm(BaseModel):
    """The model for an evaluation job form."""

    reference: str  # Max 100 chars, validated in view
    source_lang_uuid: str
    target_langs_uuid: list[str]
    workflow_options: str | None = None
    files: list[SlackFile]
    job_notes: str | None = None

    @classmethod
    def parse_human_job_form(
        cls, values: dict[str, dict[str, Any]], callback_id: str = "evaluate_job_human"
    ) -> "EvaluateJobForm":
        source_lang_uuid = values["source_lang"]["source_language_option_uuid"][
            "selected_option"
        ]["value"]
        target_langs_uuid = [
            opt["value"]
            for opt in values["target_langs"]["language_options_uuid"][
                "selected_options"
            ]
        ]
        job_notes = values.get("job_notes", {}).get("job_notes", {}).get("value", "")

        # Set workflow_options based on callback_id
        if callback_id == "evaluate_job":
            selected_option = (
                values.get("workflow_options", {})
                .get("workflow_options", {})
                .get("selected_option")
            )
            workflow_options = selected_option["value"] if selected_option else None
        else:
            workflow_options = HUMAN_EVALUATION_WORKFLOW_UUID
        reference = (
            values.get("reference", {}).get("reference", {}).get("value", "slack job")
        )
        return cls(
            reference=reference,
            source_lang_uuid=source_lang_uuid,
            target_langs_uuid=target_langs_uuid,
            workflow_options=workflow_options,
            files=[
                SlackFile.parse_slack_option(opt)
                for opt in values["files"]["files"]["selected_options"]
            ],
            job_notes=job_notes,
        )

    @classmethod
    def parse_slack(cls, values: dict[str, dict[str, Any]]) -> "EvaluateJobForm":
        reference = values["reference"]["reference"]["value"]
        source_lang_uuid = values["source_lang"]["source_language_option_uuid"][
            "selected_option"
        ]["value"]
        target_langs_uuid = [
            opt["value"]
            for opt in values["target_langs"]["language_options_uuid"][
                "selected_options"
            ]
        ]

        selected_option = (
            values.get("workflow_options", {})
            .get("workflow_options", {})
            .get("selected_option")
        )
        workflow_options = selected_option["value"] if selected_option else None

        return cls(
            reference=reference,
            source_lang_uuid=source_lang_uuid,
            target_langs_uuid=target_langs_uuid,
            workflow_options=workflow_options,
            files=[
                SlackFile.parse_slack_option(opt)
                for opt in values["files"]["files"]["selected_options"]
            ],
        )

from pydantic import BaseSettings, Field
from ibm_watson import AssistantV2

from .response import WatsonResponse


class WatsonConfig(BaseSettings):
    assistant_id: str = Field(env="WATSON_ASSISTANT_ID")
    environment_id: str = Field(env="WATSON_ENVIRONMENT_ID")

    class Config:
        min_anystr_length = 1
        allow_mutation = False
        error_msg_templates = {
            "value_error.missing": "The environment variable is missing",
            "value_error.any_str.min_length": "The environment variable is missing",
        }


watson_config = WatsonConfig()


# Automatically authenticated from the ibm-credentials.env file.
# https://github.com/watson-developer-cloud/python-sdk#credential-file
assistant = AssistantV2(version="2021-11-27")


def watson_message(text: str, user_id: str | None = None) -> WatsonResponse:
    # TODO: rate limits?
    response = assistant.message_stateless(
        watson_config.environment_id,
        input={"text": text},
        user_id=user_id,
    )

    return WatsonResponse.from_assistant_v2(text, response)

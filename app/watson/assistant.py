import os
from ibm_watson import AssistantV2
from .response import WatsonResponse


ASSISTANT_ID = os.getenv("WATSON_ASSISTANT_ID")
if not ASSISTANT_ID:
    raise ValueError("The WATSON_ASSISTANT_ID environment variable is not set")

# Automatically authenticated from the ibm-credentials.env file.
# https://github.com/watson-developer-cloud/python-sdk#credential-file
assistant = AssistantV2(version="2021-11-27")


def watson_message(text: str, user_id: str | None = None) -> WatsonResponse:
    response = assistant.message_stateless(
        ASSISTANT_ID,
        input={"text": text},
        user_id=user_id,
    ).get_result()

    return WatsonResponse(response["output"])

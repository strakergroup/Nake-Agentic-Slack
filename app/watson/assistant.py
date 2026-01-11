import asyncio

from ibm_watson import AssistantV2  # type: ignore

from ..config import config
from .response import WatsonResponse

# Automatically authenticated from the ibm-credentials.env file.
# https://github.com/watson-developer-cloud/python-sdk#credential-file
assistant = AssistantV2(version="2021-11-27")


async def watson_message(text: str, user_id: str | None = None) -> WatsonResponse:
    # TODO: rate limits?
    cleaned_text = text.replace("\r", " ").replace("\n", " ")
    response = await asyncio.to_thread(
        assistant.message_stateless,
        config.watson_environment_id,
        input={"text": cleaned_text},
        user_id=user_id,
    )

    return WatsonResponse.from_assistant_v2(text, response)

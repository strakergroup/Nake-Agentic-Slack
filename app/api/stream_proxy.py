"""
Functions to publish events to the stream proxy.
"""

from typing import List
from uuid import uuid4

import httpx

from app.api.models import MtTranslationExtraData
from app.config import domains


async def send_mt_translation_request(
    text: List[str],
    target_languages: List[str],
    source_language: str,
    extra_data: MtTranslationExtraData,
) -> None:
    """Send MT translation request to the stream proxy.

    Args:
        text: List of text strings to translate
        target_languages: Target language codes
        source_language: Source language code
        extra_data: Extra data for the translation request
    """
    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/mt-service:mt:translate:multi",
            json={
                "data": {
                    "app_id": "slack",
                    "task_id": str(uuid4()),
                    "text": text,
                    "target_languages": target_languages,
                    "source_language": source_language,
                    "output_stream": "slack:direct:mt:result",
                    "extra_data": extra_data.model_dump(),
                },
                "source": "Straker Translate for Slack",
            },
        )

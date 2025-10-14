"""
Functions to publish events to the stream proxy.
"""

from typing import List
from uuid import uuid4

import httpx

from app.api.models import MtTranslationExtraData
from app.config import domains


def validate_service_language_mapping(
    service_language_mapping: dict[str, List[str]],
) -> None:
    """Validate that the service language mapping is properly formatted.

    Args:
        service_language_mapping: The mapping to validate

    Raises:
        ValueError: If the mapping is invalid
    """
    if not service_language_mapping:
        raise ValueError("service_language_mapping cannot be empty")

    for service, languages in service_language_mapping.items():
        if not isinstance(service, str) or not service.strip():
            raise ValueError(f"Service name must be a non-empty string, got: {service}")
        if not isinstance(languages, list) or not languages:
            raise ValueError(
                f"Languages must be a non-empty list for service '{service}', got: {languages}"
            )
        for lang in languages:
            if not isinstance(lang, str) or not lang.strip():
                raise ValueError(
                    f"Language must be a non-empty string, got: {lang} in service '{service}'"
                )


async def send_mt_translation_request(
    text: List[str],
    service_language_mapping: dict[str, List[str]],
    source_language: str,
    extra_data: MtTranslationExtraData,
) -> None:
    """Send MT translation request to the stream proxy.

    Args:
        text: List of text strings to translate
        service_language_mapping: Mapping of services to their supported languages
        source_language: Source language code
        extra_data: Extra data for the translation request

    Raises:
        ValueError: If service_language_mapping is invalid
    """
    # Validate the service language mapping
    validate_service_language_mapping(service_language_mapping)

    # Prepare the request data
    request_data = {
        "data": {
            "app_id": "slack",
            "task_id": str(uuid4()),
            "text": text,
            "service_language_mapping": service_language_mapping,
            "source_language": source_language,
            "output_stream": "slack:direct:mt:result",
            "extra_data": extra_data.model_dump(),
        },
        "source": "Straker Translate for Slack",
    }

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/mt-service:mt:translate:multi",
            json=request_data,
        )

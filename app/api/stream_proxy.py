"""
Functions to publish events to the stream proxy.
"""

import logging
from typing import List
from uuid import uuid4

from app.api.http_client import get_shared_client, retry_on_timeout
from app.api.models import MtTranslationExtraData
from app.config import domains
from app.ray.events.models import MtFileRequestSchema
from app.slack_job import create_slack_job

logger = logging.getLogger(__name__)


def validate_service_language_mapping(
    service_language_mapping: dict[str, dict[str, str]],
) -> None:
    """Validate that the service language mapping is properly formatted.

    Args:
        service_language_mapping: The mapping to validate (service -> {lang: glossary_id})

    Raises:
        ValueError: If the mapping is invalid
    """
    if not service_language_mapping:
        raise ValueError("service_language_mapping cannot be empty")

    for service, lang_glossary_map in service_language_mapping.items():
        if not isinstance(service, str) or not service.strip():
            raise ValueError(f"Service name must be a non-empty string, got: {service}")
        if not isinstance(lang_glossary_map, dict) or not lang_glossary_map:
            raise ValueError(
                f"Languages must be a non-empty dict for service '{service}', got: {lang_glossary_map}"
            )
        for lang, glossary_id in lang_glossary_map.items():
            if not isinstance(lang, str) or not lang.strip():
                raise ValueError(
                    f"Language must be a non-empty string, got: {lang} in service '{service}'"
                )
            if not isinstance(glossary_id, str):
                raise ValueError(
                    f"Glossary ID must be a string, got: {glossary_id} for language '{lang}' in service '{service}'"
                )


async def send_mt_translation_request(
    text: List[str],
    service_language_mapping: dict[str, dict[str, str]],
    source_language: str,
    extra_data: MtTranslationExtraData,
) -> None:
    """Send MT translation request to the stream proxy.

    Args:
        text: List of text strings to translate
        service_language_mapping: Mapping of services to dictionaries of language codes to glossary IDs
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

    async def _post_request():
        client = await get_shared_client()
        response = await client.post(
            f"{domains.stream_proxy}/events/mt-service:mt:translate:multi",
            json=request_data,
        )
        response.raise_for_status()
        return response

    await retry_on_timeout(_post_request)


async def send_srt_translation_request(
    file_id: str,
    client_id: str,
    user_group_id: str | None,
    channel_id: str,
    target_language: str,
    ai_engine: str = "google",
) -> None:
    """Send SRT file translation request to the stream proxy.

    This is used by the transcribe+translate pipeline to auto-trigger
    translation after transcription completes.

    Args:
        file_id: The GridFS file ID of the SRT file
        client_id: The client/member UUID
        user_group_id: The user group UUID (for AI engine lookup)
        channel_id: The Slack channel ID for notifications
        target_language: The target language code to translate to
        ai_engine: The AI engine to use (default: google)
    """
    from app.auth.connector import get_group_mt_engine

    # Get the AI engine from group settings if we have user_group_id
    if user_group_id:
        try:
            ai_engine = await get_group_mt_engine(user_group_id, is_group=False)
        except Exception:
            ai_engine = "google"  # Fallback

    # French Canadian should use Microsoft
    if target_language.lower() == "fr-ca":
        ai_engine = "microsoft"

    task_data = MtFileRequestSchema(
        file_id=file_id,
        client_id=client_id,
        channel_id=channel_id,
        target_language=target_language,
        ai_engine=ai_engine,
        data_source="slack",
        submission_id=0,
    )

    task_uuid = await create_slack_job(task_data, status="pending")
    task_data.task_uuid = task_uuid

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/slack:job:machine:translate",
            json={
                "data": task_data.model_dump(),
                "source": "Straker Translate for Slack",
            },
        )

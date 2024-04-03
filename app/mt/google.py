"""
This file is a quick implementation of use Google Translate API v2.
Do not maintain this file as we will use a different method in the future.
"""

import asyncio
import httpx
from sqlalchemy.orm import Session
from buglog import notify_exception

from ..auth.connector import RayClient
from ..config import config
from ..models import GoogleApiLog
from ..database import engines


BASE_URL = "https://translation.googleapis.com/language/translate/v2"
API_KEY = config.google_mt_api_key.get_secret_value()


async def _translate(
    client: httpx.AsyncClient, text: str, target_lang: str, source_lang: str
) -> str:
    if source_lang == target_lang:
        return text

    response = await client.post(
        "",
        params={
            "key": API_KEY,
            "q": text,
            "target": target_lang,
            "format": "text",
            "source": source_lang,
        },
    )
    response.raise_for_status()
    return response.json()["data"]["translations"][0]["translatedText"]


async def get_machine_translations(
    text: str, target_lang: str | list[str]
) -> tuple[str, dict[str, str]]:
    """Get machine translation from Google Translate API v2.

    https://cloud.google.com/translate/docs/reference/rest/v2/translate
    https://cloud.google.com/translate/docs/reference/rest/v2/detect
    """
    text = text[:5000]  # Google translate v2 supports max 5000 characters
    if isinstance(target_lang, str):
        target_lang = [target_lang]

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        detect_response = await client.post(
            "/detect", params={"key": API_KEY, "q": text}
        )
        detect_response.raise_for_status()
        source_lang: str = detect_response.json()["data"]["detections"][0][0][
            "language"
        ]

        results = await asyncio.gather(
            *(_translate(client, text, tl, source_lang) for tl in target_lang),
            return_exceptions=True,
        )

    translations: dict[str, str] = {}
    for tl, result in zip(target_lang, results, strict=False):
        if isinstance(result, BaseException):
            notify_exception(result)
        else:
            translations[tl] = result
    return source_lang, translations


async def log_google_api_usage(
    user_uuid: str | None,  # LC UUID or Slack user_id
    text: str,
    source_lang: str,
    translations: dict[str, str],
) -> None:
    with Session(engines["ray_integration_log"]) as session:
        for target_lang, target_text in translations.items():
            session.add(
                # Need to rework this, so group IDs don't matter now.
                GoogleApiLog(
                    user_uuid=user_uuid or "",
                    group_uuid="",
                    super_group_uuid="",
                    app_name="slack",
                    sl=source_lang,
                    tl=target_lang,
                    source_text=text,
                    target_text=target_text,
                    response=None,
                    word_count=len(text.split()),
                    character_count=len(text),
                )
            )
        session.commit()

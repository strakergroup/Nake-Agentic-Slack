"""
This file is a quick implementation of use Microsoft Translator Text API.
Do not maintain this file as we will use a different method in the future.
"""

import asyncio
import httpx
import uuid
import requests
import time
import cachetools
from sqlalchemy.orm import Session
from buglog import notify_exception
from ..auth.connector import RayClient
from ..config import config
from ..models import MicrosoftApiLog
from ..database import engines


SUBSCRIPTION_KEY = config.microsoft_mt_api_key.get_secret_value()
URL = config.microsoft_mt_url
WEBSITE = config.microsoft_mt_website

headers = {
    'Content-type': 'application/json'
}

# Create a cache with a TTL of 9 minutes (540 seconds)
cache = cachetools.TTLCache(maxsize=100, ttl=540)

def get_credentials_for_access_token():
   return {
        "url": f"{WEBSITE}/sts/v1.0/issueToken",
        "key": SUBSCRIPTION_KEY
    }

def get_access_token(refresh=False):
    cache_key = "access-token"
    creds = get_credentials_for_access_token()
    if not refresh and cache_key in cache:
        return cache[cache_key]
    else:
        headers = {"Ocp-Apim-Subscription-Key": creds["key"]}
        response = requests.post(creds["url"], headers=headers)
        if response.status_code == 200:
            access_token = response.text
            cache[cache_key] = access_token
            return access_token
        else:
            # TODO: LOG FAILURES TO BUGLOG
            pass


async def _translate(
    client: httpx.AsyncClient, text: str, target_lang: str, source_lang: str
) -> str:
    if source_lang == target_lang:
        return text

    body = [{'text': text}]
    response = await client.post(
        f"/translate?api-version=3.0&from={source_lang}&to={target_lang}",
        headers=headers,
        json=body
    )
    response.raise_for_status()
    return response.json()[0]["translations"][0]["text"]


async def get_microsoft_machine_translations(
    text: str, target_lang: str | list[str]
) -> tuple[str, dict[str, str]]:
    """Get machine translation from Microsoft Translator Text API.

    https://docs.microsoft.com/en-us/azure/cognitive-services/translator/reference/v3-0-translate
    https://docs.microsoft.com/en-us/azure/cognitive-services/translator/reference/v3-0-detect
    """
    access_token = get_access_token()
    text = text[:5000]  # Microsoft translate supports max 5000 characters
    if isinstance(target_lang, str):
        target_lang = [target_lang]
    headers['Authorization'] = 'Bearer ' + access_token
    async with httpx.AsyncClient(base_url=URL, timeout=10.0) as client:
        detect_response = await client.post(
            "/detect?api-version=3.0",
            headers=headers,
            json=[{'Text': text}]
        )
        detect_response.raise_for_status()
        source_lang: str = detect_response.json()[0]["language"]
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


async def log_microsoft_api_usage(
    user_uuid: str | None,  # LC UUID or Slack user_id
    text: str,
    source_lang: str,
    translations: dict[str, str],
) -> None:
    with Session(engines["ray_integration_log"]) as session:
        for target_lang, target_text in translations.items():
            session.add(
                # Need to rework this, so group IDs don't matter now.
                MicrosoftApiLog(
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
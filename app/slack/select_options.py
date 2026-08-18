"""
Language selection and caching utilities for the Slack app.

This module provides both async and sync access to language data from the RAY API.
The languages are cached in Redis and also stored in a global variable for synchronous access.

Key functions:
- _get_languages_cached(): Async function that fetches and caches languages from Redis/API
- get_languages_sync(): Sync function that returns the globally cached languages
- initialize_languages_cache(): Called on app startup to populate the global cache

The global cache is automatically initialized on app startup.
"""

import asyncio
import json
from itertools import islice
from typing import Any, Iterable

from app.api.verify import get_verify_languages, get_verify_source_languages
from app.slack.buglog_notifier import notify_exception
from app.slack.file_submissions import format_slack_file_option_value
from app.translate import _

from ..models import SlackGroupSettingsTranslation
from ..ray.service import get_languages
from ..ray.settings import get_auto_translate_languages
from ..redis import redis_conn

# Global variable to store cached languages
_cached_languages: list[dict[str, str]] = []


async def _get_languages_cached():
    key = "slack-ray-translator:languages:v1"
    cached = ""
    try:
        cached = await redis_conn.get(key)
    except Exception as e:
        notify_exception(e)
    if cached:
        try:
            languages = json.loads(cached)
            assert isinstance(languages, list)
            return languages
        except Exception as e:
            notify_exception(e)

    languages = (await get_languages()).data
    languages = [
        {"code": lang.code, "name": lang.name, "uuid": lang.uuid} for lang in languages
    ]
    # Cache languages for 1 hour.
    try:
        await redis_conn.set(key, json.dumps(languages), ex=3600)
    except Exception as e:
        notify_exception(e)
    return languages


async def _update_global_cache():
    """Update the global cache with fresh language data."""
    global _cached_languages
    _cached_languages = await _get_languages_cached()


def get_languages_sync() -> list[dict[str, str]]:
    """Synchronous getter for cached languages. Returns the globally cached languages.
    If the global cache is empty, triggers _update_global_cache() in background to populate it.
    """
    global _cached_languages
    if not _cached_languages:
        # Start the async function in background without waiting
        try:
            asyncio.create_task(_update_global_cache())
        except RuntimeError:
            # If no event loop is running, we can't create a task
            # The cache will remain empty for this call
            pass
        except Exception as e:
            notify_exception(e)
    return _cached_languages


async def initialize_languages_cache():
    """Initialize the global languages cache on app startup."""
    global _cached_languages
    _cached_languages = await _get_languages_cached()


async def get_language_options(
    filter: str | None = None, format: str = "code", source_only: bool = False
) -> list[dict[str, Any]]:
    if format == "code":
        languages = await _get_languages_cached()
    elif source_only:
        languages = await get_verify_source_languages()
    else:
        languages = await get_verify_languages()
    # Filter language options from keyword filter.
    if filter:
        languages = (
            lang
            for lang in languages
            if filter.lower() in lang["name"].lower()
            or filter.lower() in lang["code"].lower()
        )  # type: ignore
    # Slack can show a maximum of 100 options.
    languages = islice(languages, 100)  # type: ignore

    # translated languages name and reorder by translated words
    languages = [{format: lang[format], "name": _(lang["name"])} for lang in languages]
    languages.sort(key=lambda lang: lang["name"].lower())
    return [
        {
            "text": {"type": "plain_text", "text": lang["name"], "emoji": False},
            "value": lang[format],
        }
        for lang in languages
    ]


async def get_file_options_cached(channel_id: str):
    key = f"slack-ray-translator:files:{channel_id}"
    cached = ""
    files = []
    try:
        cached = await redis_conn.get(key)
    except Exception as e:
        notify_exception(e)
    if cached:
        try:
            files = json.loads(cached)
            assert isinstance(files, list)
            return files
        except Exception as e:
            notify_exception(e)

    return files


def get_auto_translate_language_options():
    """Get the options block for the auto-translate language select input."""
    return [
        {"text": {"type": "plain_text", "text": name}, "value": code}
        for code, name in get_auto_translate_languages()
    ]


def filter_auto_translate_language_options(languages: Iterable[str]):
    """Get the auto-translate language options filtered by a list of languages
    (en, es, fr, etc.).
    """
    options_by_value = {
        option["value"]: option for option in get_auto_translate_language_options()
    }
    return [
        options_by_value[language]
        for language in languages
        if language in options_by_value
    ]


def map_file_options(
    files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Maps a list of file objects to a list of select options.

    - https://api.slack.com/types/file
    - https://api.slack.com/reference/block-kit/composition-objects#option.
    """
    max_title_length = 75
    max_option_value_length = 150
    # Keep only the latest 10 files
    files = files[:10]
    file_options = []
    for file in files:
        title = file.get("title", "")
        if not title:
            continue
        # Slack option text max is 75 characters. Display-only — SAQ must not
        # use this truncated label as the Verify upload filename (RAY-81396).
        if len(title) > max_title_length:
            title = f"{title[: max_title_length - 1]}…"
        id = file.get("id")
        if not id or len(id) > max_title_length:
            continue
        raw_size = file.get("size")
        size = raw_size if isinstance(raw_size, int) else None
        value = format_slack_file_option_value(id, size)
        if len(value) > max_option_value_length:
            continue
        option = {
            "text": {"type": "plain_text", "text": title, "emoji": False},
            "value": value,
        }
        file_options.append(option)
    return file_options, file_options


def translation_display_format_options() -> list[dict[str, Any]]:
    return [
        {
            "text": {
                "type": "plain_text",
                "text": _("In thread"),
            },
            "value": "thread",
        },
        {
            "text": {
                "type": "plain_text",
                "text": _("Message"),
            },
            "value": "message",
        },
    ]


def map_translation_display_format_option(
    value: SlackGroupSettingsTranslation.DisplayFormatType,
) -> dict[str, Any]:
    options = translation_display_format_options()
    for opt in options:
        if opt["value"] == value:
            return opt
    return {
        "text": {
            "type": "plain_text",
            "text": _("In thread"),
        },
        "value": "thread",
    }

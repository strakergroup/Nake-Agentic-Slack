import functools
from typing import Any, Iterable
from itertools import islice
import json

from buglog import notify_exception

from app.api.verify import get_verify_languages

from ..redis import redis_conn
from ..ray import get_languages
from ..ray.settings import get_auto_translate_languages
from ..models import SlackGroupSettingsTranslation
from app.translate import _


async def _get_languages_cached() -> list[dict[str, str]]:
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


async def get_language_options(
    filter: str | None = None, format: str = "code"
) -> list[dict[str, Any]]:
    languages = (
        await _get_languages_cached()
        if format == "code"
        else await get_verify_languages()
    )
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


async def get_file_options_cached(channel_id: str) -> list[dict[str, Any]]:
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
    languages = set(languages)
    options = get_auto_translate_language_options()
    return [opt for opt in options if opt["value"] in languages]


def map_file_options(
    files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Maps a list of file objects to a list of select options.

    - https://api.slack.com/types/file
    - https://api.slack.com/reference/block-kit/composition-objects#option.
    """
    max_title_length = 75
    # Keep only the latest 10 files
    files = files[:10]
    file_options = []
    for file in files:
        title = file.get("title", "")
        if not title:
            continue
        # Options text has max 75 characters.
        if len(title) > max_title_length:
            title = f"{title[:max_title_length - 1]}…"
        id = file.get("id")
        if not id or len(id) > max_title_length:
            continue
        option = {
            "text": {"type": "plain_text", "text": title, "emoji": False},
            "value": id,
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

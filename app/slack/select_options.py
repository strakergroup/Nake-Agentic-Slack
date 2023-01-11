from typing import Any
from itertools import islice
import json

from buglog import notify_exception

from ..redis import redis_conn
from ..ray import get_languages


async def _get_languages_cached() -> list[dict[str, str]]:
    key = "slack-ray-translator:languages"
    cached = await redis_conn.get(key)
    if cached:
        try:
            languages = json.loads(cached)
            assert isinstance(languages, list)
            return languages
        except Exception as e:
            notify_exception(e)

    languages = (await get_languages()).data
    languages = [{"code": lang.code, "name": lang.name} for lang in languages]
    # Cache languages for 1 hour.
    await redis_conn.set(key, json.dumps(languages), ex=3600)
    return languages


async def get_language_options(filter: str | None = None) -> list[dict[str, Any]]:
    languages = await _get_languages_cached()
    # Filter language options from keyword filter.
    if filter:
        languages = (
            lang
            for lang in languages
            if filter.lower() in lang["name"].lower()
            or filter.lower() in lang["code"].lower()
        )
    # Slack can show a maximum of 100 options.
    languages = islice(languages, 100)
    return [
        {
            "text": {"type": "plain_text", "text": lang["name"], "emoji": False},
            "value": lang["code"],
        }
        for lang in languages
    ]


def map_file_options(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Maps a list of file objects to a list of select options.

    - https://api.slack.com/types/file
    - https://api.slack.com/reference/block-kit/composition-objects#option.
    """
    max_title_length = 75
    file_options = []
    for file in files:
        title = file.get("title", "")
        # Options text has max 75 characters.
        if len(title) > max_title_length:
            title = f"{title[:max_title_length - 1]}…"
        file_options.append(
            {
                "text": {"type": "plain_text", "text": title, "emoji": False},
                "value": file.get("id"),
            }
        )
    return file_options

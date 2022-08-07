from typing import Any
from itertools import islice
from ..ray.methods import get_languages


async def get_language_options(filter: str | None) -> list[dict[str, Any]]:
    # TODO: cache
    languages = await get_languages()
    # Filter language options from keyword filter.
    if filter:
        languages = (
            lang
            for lang in languages
            if filter.lower() in lang.name.lower()
            or filter.lower() in lang.code.lower()
        )
    # Slack can show a maximum of 100 options.
    languages = islice(languages, 100)
    return [
        {
            "text": {"type": "plain_text", "text": lang.name, "emoji": False},
            "value": lang.code,
        }
        for lang in languages
    ]


def map_file_options(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Maps a list of file objects to a list of select options.

    - https://api.slack.com/types/file
    - https://api.slack.com/reference/block-kit/composition-objects#option.
    """
    return [
        {
            "text": {"type": "plain_text", "text": file.get("title"), "emoji": False},
            "value": file.get("id"),
        }
        for file in files
    ]

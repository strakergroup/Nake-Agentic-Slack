import functools
from typing import Iterable
from sqlalchemy import text  # type: ignore

from ..auth.connector import RayClient
from ..database import engines


@functools.cache
def get_auto_translate_languages(
    include_variations: bool = False,
) -> list[tuple[str, str]]:
    """Get the available languages for auto-translation (ISO code and name).

    Args:
        include_variations (bool, optional): Whether to include variations of
            languages, e.g. "zh" and "zh-CN". Defaults to False.

    Returns:
        list[tuple[str, str]]: The list of languages, tuples with code and label.
    """
    languages = [
        ("en", "English"),
        ("es", "Spanish"),
        ("fr", "French"),
        ("de", "German"),
        ("it", "Italian"),
        ("nl", "Dutch"),
        ("zh-CN", "Chinese (Simplified)"),
        ("zh-TW", "Chinese (Traditional)"),
        ("ja", "Japanese"),
        ("ko", "Korean"),
    ]
    if include_variations:
        languages.append(("zh", "Chinese (Simplified)"))
    return languages


@functools.cache
def get_auto_translate_language_codes(include_variations: bool = False) -> list[str]:
    """Get the available languages for auto-translation (ISO code only).

    Args:
        include_variations (bool, optional): Whether to include variations of
            languages, e.g. "zh" and "zh-CN". Defaults to False.

    Returns:
        list[str]: The list of language codes.
    """
    return [
        lang[0]
        for lang in get_auto_translate_languages(include_variations=include_variations)
    ]


@functools.cache
def is_valid_auto_translate_language(language: str) -> bool:
    """Check if a language code is valid for auto-translation.

    Args:
        language (str): A language code, e.g. "en", "es", etc.
    """
    return language in get_auto_translate_language_codes(include_variations=True)


@functools.cache
def get_auto_translate_language_name(language: str) -> str:
    """Get the name of a language for auto-translation.

    Args:
        language (str): A language code, e.g. "en", "es", etc.

    Returns:
        str: The name of the language if valid, else "Unknown".
    """
    language = language.casefold()
    for lang in get_auto_translate_languages(include_variations=True):
        if lang[0].casefold() == language or lang[1].casefold() == language:
            return lang[1]
    return "Unknown"


def filter_invalid_auto_translate_languages(languages: Iterable[str]) -> list[str]:
    """Filter and return a list of languages that are valid for auto-translation.

    Args:
        languages (Iterable[str]): The list of language codes to filter

    Returns:
        list[str]: The list with invalid languages removed.
    """
    return [lang for lang in languages if is_valid_auto_translate_language(lang)]


def get_auto_translate_settings_conversations(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (conversations) for a LanugageCloud user.

    Returns:
        list[str]: The list of conversation IDs to auto-translate.
    """
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT conversation_id
            FROM slack_settings_auto_translate_conversations
            WHERE member_uuid = :member_uuid
            """
        ).bindparams(member_uuid=ray_client.id)
        result = conn.execute(sql)
        conversation_ids = [row[0] for row in result]
    return conversation_ids


def get_auto_translate_settings_langs(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (languages) for a LanugageCloud user.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    with engines["ray_integration"].connect() as conn:
        sql = text(
            """
            SELECT lang
            FROM slack_settings_auto_translate_langs
            WHERE member_uuid = :member_uuid
            """
        ).bindparams(member_uuid=ray_client.id)
        result = conn.execute(sql)
        langs = [row[0] for row in result]
    return langs


def update_auto_translate_settings(
    ray_client: RayClient, conversations: list[str], languages: list[str]
) -> None:
    """Update the auto-translate settings for a LanugageCloud user.

    Args:
        ray_client (RayClient): The client to update the settings for
        conversations (list[str]): The IDs of the conversations to auto-translate.
        languages (list[str]): The languages to auto-translate to.
    """
    with engines["ray_integration"].begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM slack_settings_auto_translate_conversations
                WHERE member_uuid = :member_uuid
                """
            ).bindparams(member_uuid=ray_client.id)
        )
        conn.execute(
            text(
                """
                DELETE FROM slack_settings_auto_translate_langs
                WHERE member_uuid = :member_uuid
                """
            ).bindparams(member_uuid=ray_client.id)
        )
        for conversation in conversations:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_settings_auto_translate_conversations
                    (member_uuid, conversation_id)
                    VALUES
                    (:member_uuid, :conversation_id)
                    """
                ).bindparams(member_uuid=ray_client.id, conversation_id=conversation)
            )
        for lang in languages:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_settings_auto_translate_langs
                    (member_uuid, lang)
                    VALUES
                    (:member_uuid, :lang)
                    """
                ).bindparams(member_uuid=ray_client.id, lang=lang)
            )

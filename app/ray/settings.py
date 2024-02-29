import functools
from typing import Iterable
from sqlalchemy import text

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
        ("ar", "Arabic"),
        ("ru", "Russian"),
        ("uk", "Ukrainian"),
        ("be", "Belarusian"),
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


def get_auto_translate_settings_channels(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (channels) for a LanugageCloud user.

    Returns:
        list[str]: The list of channel IDs to auto-translate.
    """
    if ray_client.settings_id is None:
        return []
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT channel_id
            FROM slack_user_settings_auto_translate_channels
            WHERE settings_id = :settings_id
            """
        ).bindparams(settings_id=ray_client.settings_id)
        result = conn.execute(sql)
        channel_ids = [row[0] for row in result]
    return channel_ids


def get_auto_translate_settings_langs(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (languages) for a LanugageCloud user.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    if ray_client.settings_id is None:
        return []
    with engines["ray_integration_readonly"].connect() as conn:
        sql = text(
            """
            SELECT lang
            FROM slack_user_settings_auto_translate_langs
            WHERE settings_id = :settings_id
            """
        ).bindparams(settings_id=ray_client.settings_id)
        result = conn.execute(sql)
        langs = [row[0] for row in result]
    return langs


def update_auto_translate_settings(
    ray_client: RayClient, channels: list[str], languages: list[str]
) -> None:
    """Update the auto-translate settings for a LanugageCloud user.

    Args:
        ray_client (RayClient): The client to update the settings for
        channels (list[str]): The IDs of the channels (conversations) to auto-translate.
        languages (list[str]): The languages to auto-translate to.
    """
    with engines["ray_integration"].begin() as conn:
        if ray_client.settings_id is None:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_user_settings
                    (member_uuid)
                    VALUES
                    (:member_uuid)
                    """
                ).bindparams(member_uuid=ray_client.id)
            )
            settings_id_result = conn.execute(
                text(
                    """
                    SELECT id
                    FROM slack_user_settings
                    WHERE member_uuid = :member_uuid
                    """
                ).bindparams(member_uuid=ray_client.id)
            ).first()
            if settings_id_result:
                ray_client.settings_id = settings_id_result[0]
        else:
            conn.execute(
                text(
                    """
                    DELETE FROM slack_user_settings_auto_translate_channels
                    WHERE settings_id = :settings_id
                    """
                ).bindparams(settings_id=ray_client.settings_id)
            )
            conn.execute(
                text(
                    """
                    DELETE FROM slack_user_settings_auto_translate_langs
                    WHERE settings_id = :settings_id
                    """
                ).bindparams(settings_id=ray_client.settings_id)
            )

        for channel_id in channels:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_user_settings_auto_translate_channels
                    (settings_id, channel_id)
                    VALUES
                    (:settings_id, :channel_id)
                    """
                ).bindparams(settings_id=ray_client.settings_id, channel_id=channel_id)
            )
        for lang in languages:
            conn.execute(
                text(
                    """
                    INSERT INTO slack_user_settings_auto_translate_langs
                    (settings_id, lang)
                    VALUES
                    (:settings_id, :lang)
                    """
                ).bindparams(settings_id=ray_client.settings_id, lang=lang)
            )

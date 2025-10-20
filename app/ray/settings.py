import functools
from typing import Iterable

import langcodes
from slack_bolt.context.async_context import AsyncBoltContext
from sqlalchemy import text
from straker_utils.sql.async_engine import execute, fetch_all, fetch_one

from app.translate import _

from ..auth.connector import RayClient
from ..database import async_engines
from ..models import (
    SlackGroupSettings,
    SlackGroupSettingsTranslation,
)


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
        ("af", "Afrikaans"),
        ("sq", "Albanian"),
        ("am", "Amharic"),
        ("ar", "Arabic"),
        ("hy", "Armenian"),
        ("as", "Assamese"),
        ("eu", "Basque"),
        ("be", "Belarusian"),
        ("bn", "Bengali"),
        ("bs", "Bosnian"),
        ("bg", "Bulgarian"),
        ("ca", "Catalan"),
        ("ceb", "Cebuano"),
        ("ny", "Chichewa"),
        ("zh-CN", "Chinese (Simplified)"),
        ("zh-TW", "Chinese (Traditional)"),
        ("hr", "Croatian"),
        ("cs", "Czech"),
        ("da", "Danish"),
        ("dv", "Dhivehi"),
        ("nl", "Dutch"),
        ("en", "English"),
        ("eo", "Esperanto"),
        ("et", "Estonian"),
        ("fi", "Finnish"),
        ("fr", "French"),
        ("fr-CA", "French (Canadian)"),
        ("ka", "Georgian"),
        ("de", "German"),
        ("el", "Greek"),
        ("gn", "Guarani"),
        ("gu", "Gujarati"),
        ("ht", "Haitian Creole French"),
        ("iw", "Hebrew"),
        ("hi", "Hindi"),
        ("hu", "Hungarian"),
        ("is", "Icelandic"),
        ("id", "Indonesian"),
        ("ga", "Irish Gaelic"),
        ("it", "Italian"),
        ("ja", "Japanese"),
        ("jw", "Javanese"),
        ("kk", "Kazakh"),
        ("km", "Khmer"),
        ("ko", "Korean"),
        ("ky", "Kyrgyz"),
        ("lo", "Lao"),
        ("la", "Latin"),
        ("lv", "Latvian"),
        ("lt", "Lithuanian"),
        ("mk", "Macedonian"),
        ("mg", "Malagasy"),
        ("ms", "Malay"),
        ("ml", "Malayalam"),
        ("mt", "Maltese"),
        ("mi", "Maori"),
        ("mr", "Marathi"),
        ("mn", "Mongolian"),
        ("ne", "Nepali"),
        ("no", "Norwegian"),
        ("or", "Oriya"),
        ("ps", "Pashto"),
        ("fa", "Persian"),
        ("pl", "Polish"),
        ("pt", "Portuguese"),
        ("pa", "Punjabi"),
        ("ro", "Romanian"),
        ("ru", "Russian"),
        ("sa", "Sanskrit"),
        ("sr", "Serbian"),
        ("st", "Sesotho"),
        ("si", "Sinhala"),
        ("sk", "Slovak"),
        ("sl", "Slovenian"),
        ("so", "Somali"),
        ("es", "Spanish"),
        ("su", "Sundanese"),
        ("sw", "Swahili"),
        ("sv", "Swedish"),
        ("tl", "Tagalog"),
        ("tg", "Tajik"),
        ("ta", "Tamil"),
        ("th", "Thai"),
        ("tr", "Turkish"),
        ("uk", "Ukrainian"),
        ("ur", "Urdu"),
        ("uz", "Uzbek"),
        ("vi", "Vietnamese"),
        ("cy", "Welsh"),
        ("zu", "Zulu"),
    ]

    if include_variations:
        languages.append(("zh", "Chinese (Simplified)"))

    languages = [(lang[0], _(lang[1])) for lang in languages]
    languages = sorted(languages, key=lambda language: language[1])
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
    lang_info = langcodes.get(language)
    if lang_info:
        language = lang_info.language or language
    for lang in get_auto_translate_languages(include_variations=True):
        if lang[0].casefold() == language or lang[1].casefold() == language:
            return lang[1]
    return language


def filter_invalid_auto_translate_languages(languages: Iterable[str]) -> list[str]:
    """Filter and return a list of languages that are valid for auto-translation.

    Args:
        languages (Iterable[str]): The list of language codes to filter

    Returns:
        list[str]: The list with invalid languages removed.
    """
    return [lang for lang in languages if is_valid_auto_translate_language(lang)]


async def get_auto_translate_user_settings_channels(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (channels) for a LanugageCloud user.

    Returns:
        list[str]: The list of channel IDs to auto-translate.
    """
    if ray_client.settings_id is None:
        return []
    sql = text(
        """
        SELECT channel_id
        FROM slack_user_settings_auto_translate_channels
        WHERE settings_id = :settings_id
        """
    ).bindparams(settings_id=ray_client.settings_id)
    result = await fetch_all(sql, async_engines["ray_integration_readonly"])
    channel_ids = [row["channel_id"] for row in result]
    return channel_ids


async def get_auto_translate_user_settings_langs(ray_client: RayClient) -> list[str]:
    """Get the auto-translate settings (languages) for a LanugageCloud user.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    if ray_client.settings_id is None:
        return []
    sql = text(
        """
        SELECT lang
        FROM slack_user_settings_auto_translate_langs
        WHERE settings_id = :settings_id
        """
    ).bindparams(settings_id=ray_client.settings_id)
    result = await fetch_all(sql, async_engines["ray_integration_readonly"])
    langs = [row["lang"] for row in result]
    return langs


# Gets back all for enterprise. Creates entry for team if it doesn't exist
# TODO comeback to this
async def get_team_setting(
    context: AsyncBoltContext, team_id: str
) -> SlackGroupSettings:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    team_id = team_id or context.team_id

    # First, try to get existing settings
    sql = text(
        """
        SELECT id, slack_team_id, slack_enterprise_id, created_at, updated_at
        FROM slack_group_settings
        WHERE slack_team_id = :team_id
        """
    ).bindparams(team_id=team_id)

    result = await fetch_one(sql, async_engines["ray_integration"])

    if not result:
        # Create record if doesn't exist yet.
        insert_sql = text(
            """
            INSERT INTO slack_group_settings (slack_team_id, slack_enterprise_id, created_at, updated_at)
            VALUES (:team_id, :enterprise_id, NOW(), NOW())
            """
        ).bindparams(team_id=team_id, enterprise_id=context.enterprise_id)

        await execute(insert_sql, async_engines["ray_integration"], commit_after=True)

        # Get the newly created record
        result = await fetch_one(sql, async_engines["ray_integration"])

    if not result:
        raise ValueError("Failed to create or retrieve team setting")

    return SlackGroupSettings(
        id=result["id"],
        slack_team_id=result["slack_team_id"],
        slack_enterprise_id=result["slack_enterprise_id"],
        created_at=result["created_at"],
        updated_at=result["updated_at"],
    )


# Gets back all for enterprise. Creates entry for team if it doesn't exist
# TODO comeback to this
async def get_or_create_group_settings(
    context: AsyncBoltContext, team_id: str | None = None
) -> list[SlackGroupSettings]:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    team_id = team_id or context.team_id
    enterprise_id = context.enterprise_id

    # First, try to get existing settings for the team
    sql = text(
        """
        SELECT id, slack_team_id, slack_enterprise_id, created_at, updated_at
        FROM slack_group_settings
        WHERE slack_team_id = :team_id
        """
    ).bindparams(team_id=team_id)

    result = await fetch_one(sql, async_engines["ray_integration"])

    if not result:
        # Create record if doesn't exist yet.
        insert_sql = text(
            """
            INSERT INTO slack_group_settings (slack_team_id, slack_enterprise_id, created_at, updated_at)
            VALUES (:team_id, :enterprise_id, NOW(), NOW())
            """
        ).bindparams(team_id=team_id, enterprise_id=enterprise_id)

        await execute(insert_sql, async_engines["ray_integration"], commit_after=True)

    # Now get all settings for the team/enterprise
    if enterprise_id:
        sql = text(
            """
            SELECT id, slack_team_id, slack_enterprise_id, created_at, updated_at
            FROM slack_group_settings
            WHERE slack_team_id = :team_id OR slack_team_id = :enterprise_id
            """
        ).bindparams(team_id=team_id, enterprise_id=enterprise_id)
    else:
        sql = text(
            """
            SELECT id, slack_team_id, slack_enterprise_id, created_at, updated_at
            FROM slack_group_settings
            WHERE slack_team_id = :team_id
            """
        ).bindparams(team_id=team_id)

    results = await fetch_all(sql, async_engines["ray_integration"])

    return [
        SlackGroupSettings(
            id=row["id"],
            slack_team_id=row["slack_team_id"],
            slack_enterprise_id=row["slack_enterprise_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in results
    ]


async def get_or_create_setting_for_team(
    context: AsyncBoltContext,
    channel_id: str,
    team_id: str,
) -> SlackGroupSettingsTranslation:
    # Get setting for team and channel
    setting = await get_team_setting(context, team_id)
    # Extract the list of settings.id
    # settings_ids = [setting.id for setting in settings]

    sql = text(
        """
        SELECT id, settings_id, channel_id, display_format, created_at, updated_at
        FROM slack_group_settings_translation
        WHERE settings_id = :settings_id AND channel_id = :channel_id
        """
    ).bindparams(settings_id=setting.id, channel_id=channel_id)

    result = await fetch_one(sql, async_engines["ray_integration"])

    if result:
        return SlackGroupSettingsTranslation(
            id=result["id"],
            settings_id=result["settings_id"],
            channel_id=result["channel_id"],
            display_format=result["display_format"],
            created_at=result["created_at"],
            updated_at=result["updated_at"],
        )

    # Create record if doesn't exist yet.
    insert_sql = text(
        """
        INSERT INTO slack_group_settings_translation (settings_id, channel_id, display_format, created_at, updated_at)
        VALUES (:settings_id, :channel_id, 'inline', NOW(), NOW())
        """
    ).bindparams(settings_id=setting.id, channel_id=channel_id)

    await execute(insert_sql, async_engines["ray_integration"], commit_after=True)

    # Get the newly created record
    result = await fetch_one(sql, async_engines["ray_integration"])

    if not result:
        raise ValueError("Failed to create or retrieve translation setting")

    return SlackGroupSettingsTranslation(
        id=result["id"],
        settings_id=result["settings_id"],
        channel_id=result["channel_id"],
        display_format=result["display_format"],
        created_at=result["created_at"],
        updated_at=result["updated_at"],
    )


async def get_all_settings_for_channel(
    context: AsyncBoltContext, channel_id: str
) -> list[SlackGroupSettingsTranslation]:
    # TODO streamline this (join)
    settings = await get_or_create_group_settings(context)

    sql = text(
        """
        SELECT id, settings_id, channel_id, display_format, created_at, updated_at
        FROM slack_group_settings_translation
        WHERE channel_id = :channel_id
        """
    ).bindparams(channel_id=channel_id)

    results = await fetch_all(sql, async_engines["ray_integration"])

    if results:
        return [
            SlackGroupSettingsTranslation(
                id=row["id"],
                settings_id=row["settings_id"],
                channel_id=row["channel_id"],
                display_format=row["display_format"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in results
        ]

    # Create record if doesn't exist yet.
    insert_sql = text(
        """
        INSERT INTO slack_group_settings_translation (settings_id, channel_id, display_format, created_at, updated_at)
        VALUES (:settings_id, :channel_id, 'inline', NOW(), NOW())
        """
    ).bindparams(settings_id=settings[0].id, channel_id=channel_id)

    await execute(insert_sql, async_engines["ray_integration"], commit_after=True)

    # Get the newly created record
    result = await fetch_one(sql, async_engines["ray_integration"])

    if not result:
        raise ValueError("Failed to create or retrieve channel setting")

    return [
        SlackGroupSettingsTranslation(
            id=result["id"],
            settings_id=result["settings_id"],
            channel_id=result["channel_id"],
            display_format=result["display_format"],
            created_at=result["created_at"],
            updated_at=result["updated_at"],
        )
    ]


async def get_auto_translate_settings_and_langs(
    context: AsyncBoltContext, channel_id: str | None = None, team_id: str | None = None
) -> list[dict[str, str]]:
    """Get the auto-translate languages for a channel for a LanugageCloud group.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    # TODO: Combine with above function
    if not channel_id:
        return []  # Modal triggers do not have channel_id
    team_id = team_id or context.team_id

    # Use async engine for database operations
    from sqlalchemy import text

    sql = text(
        """
        SELECT DISTINCT langslang.lang as target_lang, sgt.display_format
        FROM slack_group_settings_translation_langs langslang
        JOIN slack_group_settings_translation sgt
        ON sgt.id = langslang.translation_settings_id
        WHERE sgt.channel_id = :channel_id
        """
    ).bindparams(channel_id=channel_id)

    results = await fetch_all(sql, async_engines["ray_integration"])

    return [
        {"target_lang": row["target_lang"], "display_format": row["display_format"]}
        for row in results
    ]


async def update_auto_translate_group_settings(
    context: AsyncBoltContext,
    channels: list[dict[str, bool | str | None]],
    languages: list[str],
    display_format: SlackGroupSettingsTranslation.DisplayFormatType,
) -> None:
    """Update the auto-translate settings for a channel for a LanugageCloud group.

    Args:
        channels (list[str]): The IDs of the channels (conversations) to auto-translate.
        languages (list[str]): The languages to auto-translate to.
        display_format: The display format setting.
    """
    for channel in channels:
        # insert for team
        channel_setting = await get_or_create_setting_for_team(
            context,
            str(channel["channel_id"]) if channel.get("channel_id") is not None else "",
            str(channel["team_id"]) if channel.get("team_id") is not None else "",
        )

        # Update display format
        update_sql = text(
            """
            UPDATE slack_group_settings_translation
            SET display_format = :display_format, updated_at = NOW()
            WHERE id = :setting_id
            """
        ).bindparams(display_format=display_format, setting_id=channel_setting.id)

        await execute(update_sql, async_engines["ray_integration"], commit_after=True)

        # Get current settings for the channel
        current_settings_sql = text(
            """
            SELECT id FROM slack_group_settings_translation
            WHERE channel_id = :channel_id
            """
        ).bindparams(channel_id=channel["channel_id"])

        current_settings = await fetch_all(
            current_settings_sql, async_engines["ray_integration"]
        )
        current_setting_ids = [setting["id"] for setting in current_settings]

        # Delete existing language settings
        if current_setting_ids:
            delete_sql = text(
                """
                DELETE FROM slack_group_settings_translation_langs
                WHERE translation_settings_id IN :setting_ids
                """
            ).bindparams(setting_ids=tuple(current_setting_ids))

            await execute(
                delete_sql, async_engines["ray_integration"], commit_after=True
            )

        # Insert new language settings
        for lang in languages:
            insert_lang_sql = text(
                """
                INSERT INTO slack_group_settings_translation_langs (translation_settings_id, lang, created_at, updated_at)
                VALUES (:translation_settings_id, :lang, NOW(), NOW())
                """
            ).bindparams(translation_settings_id=channel_setting.id, lang=lang)

            await execute(
                insert_lang_sql, async_engines["ray_integration"], commit_after=True
            )


async def disable_auto_translate_group_settings(
    context: AsyncBoltContext, channel_id: str
) -> None:
    """Disable the auto-translate settings for a channel for a LanugageCloud group
    by deleting the languages for that channel.

    Args:
        channel_id str: The ID of the channel (conversations) to auto-translate.
    """
    channel_settings = await get_all_settings_for_channel(context, channel_id)
    settings_ids = [setting.id for setting in channel_settings]

    if settings_ids:
        delete_sql = text(
            """
            DELETE FROM slack_group_settings_translation_langs
            WHERE translation_settings_id IN :setting_ids
            """
        ).bindparams(setting_ids=tuple(settings_ids))

        await execute(delete_sql, async_engines["ray_integration"], commit_after=True)


async def get_full_group_translation_settings(
    context: AsyncBoltContext, page: int = 1, rows_per_page: int = 5
) -> list[tuple[SlackGroupSettingsTranslation, list[str]]]:
    """Get the group translation settings for all channels.

    Returns:
        list[tuple[SlackGroupSettingsTranslation, list[str]]]: A list of tuples with
            the channel settings and the languages to translate to.
    """
    # TODO Refactor
    settings = await get_or_create_group_settings(context, context.team_id)
    settings_id = [setting.id for setting in settings]

    # Get channel settings with pagination
    channel_settings_sql = text(
        """
        SELECT DISTINCT sgt.id, sgt.settings_id, sgt.channel_id, sgt.display_format, sgt.created_at, sgt.updated_at
        FROM slack_group_settings_translation sgt
        JOIN slack_group_settings_translation_langs langslang
        ON sgt.id = langslang.translation_settings_id
        WHERE sgt.settings_id IN :settings_id
        GROUP BY sgt.id
        ORDER BY sgt.id DESC
        LIMIT :limit OFFSET :offset
        """
    ).bindparams(
        settings_id=tuple(settings_id),
        limit=rows_per_page,
        offset=(page - 1) * rows_per_page,
    )

    channel_settings_results = await fetch_all(
        channel_settings_sql, async_engines["ray_integration"]
    )

    if not channel_settings_results:
        return []

    channel_settings = [
        SlackGroupSettingsTranslation(
            id=row["id"],
            settings_id=row["settings_id"],
            channel_id=row["channel_id"],
            display_format=row["display_format"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        for row in channel_settings_results
    ]

    # Get languages for these channel settings
    channel_setting_ids = [setting.id for setting in channel_settings]

    channel_langs_sql = text(
        """
        SELECT translation_settings_id, lang
        FROM slack_group_settings_translation_langs
        WHERE translation_settings_id IN :setting_ids
        ORDER BY id
        """
    ).bindparams(setting_ids=tuple(channel_setting_ids))

    channel_langs_results = await fetch_all(
        channel_langs_sql, async_engines["ray_integration"]
    )

    # Map channels to languages.
    settings_lang_map: dict[int, tuple[SlackGroupSettingsTranslation, list[str]]] = {
        setting.id: (setting, []) for setting in channel_settings
    }

    for lang_row in channel_langs_results:
        translation_settings_id = lang_row["translation_settings_id"]
        if translation_settings_id in settings_lang_map:
            settings_lang_map[translation_settings_id][1].append(lang_row["lang"])

    # Remove channels with no languages.
    return [(channel, langs) for channel, langs in settings_lang_map.values()]


# pagination - get number of pages based on rows per page and number of records
async def get_pagination(context: AsyncBoltContext, rows_per_page: int) -> int:
    """Get the number of pages based on the number of rows per page and total rows.

    Args:
        rows_per_page (int): The number of rows per page.

    Returns:
        int: The number of pages.
    """
    settings = await get_or_create_group_settings(context)
    settings_id = [setting.id for setting in settings]

    total_rows_sql = text(
        """
        SELECT COUNT(DISTINCT sgt.id) as total
        FROM slack_group_settings_translation sgt
        JOIN slack_group_settings_translation_langs langslang
        ON sgt.id = langslang.translation_settings_id
        WHERE sgt.settings_id IN :settings_id
        """
    ).bindparams(settings_id=tuple(settings_id))

    result = await fetch_one(total_rows_sql, async_engines["ray_integration"])
    total_rows = result["total"] if result else 0

    if not total_rows:
        return 0

    return (total_rows + rows_per_page - 1) // rows_per_page


async def update_channel_id(old_channel_id: str, new_channel_id: str) -> None:
    sql = text(
        """
        UPDATE slack_group_settings_translation
        SET channel_id = :new_channel_id
        WHERE channel_id = :old_channel_id
        """
    ).bindparams(new_channel_id=new_channel_id, old_channel_id=old_channel_id)

    await execute(sql, async_engines["ray_integration"], commit_after=True)

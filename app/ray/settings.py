import functools
from typing import Iterable

import langcodes
from slack_bolt.context.async_context import AsyncBoltContext
from sqlalchemy import delete, distinct, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from straker_utils.sql.async_engine import execute, fetch_all, fetch_one

from app.translate import _

from ..auth.connector import RayClient
from ..database import async_engines
from ..models import (
    SlackGroupSettings,
    SlackGroupSettingsTranslation,
    SlackGroupSettingsTranslationLangs,
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


async def get_auto_translate_user_settings_channels(ray_client: RayClient):
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


async def get_auto_translate_user_settings_langs(ray_client: RayClient):
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


async def update_auto_translate_user_settings(
    ray_client: RayClient, channels: list[str], languages: list[str]
) -> None:
    """Update the auto-translate settings for a LanugageCloud user.

    Args:
        ray_client (RayClient): The client to update the settings for
        channels (list[str]): The IDs of the channels (conversations) to auto-translate.
        languages (list[str]): The languages to auto-translate to.
    """
    if ray_client.settings_id is None:
        await execute(
            text(
                """
                INSERT INTO slack_user_settings
                (member_uuid)
                VALUES
                (:member_uuid)
                """
            ).bindparams(member_uuid=ray_client.id),
            async_engines["ray_integration"],
            commit_after=True,
        )
        settings_id_result = await fetch_one(
            text(
                """
                SELECT id
                FROM slack_user_settings
                WHERE member_uuid = :member_uuid
                """
            ).bindparams(member_uuid=ray_client.id),
            async_engines["ray_integration"],
        )
        if settings_id_result:
            ray_client.settings_id = settings_id_result["id"]
    else:
        await execute(
            text(
                """
                DELETE FROM slack_user_settings_auto_translate_channels
                WHERE settings_id = :settings_id
                """
            ).bindparams(settings_id=ray_client.settings_id),
            async_engines["ray_integration"],
            commit_after=True,
        )
        await execute(
            text(
                """
                DELETE FROM slack_user_settings_auto_translate_langs
                WHERE settings_id = :settings_id
                """
            ).bindparams(settings_id=ray_client.settings_id),
            async_engines["ray_integration"],
            commit_after=True,
        )

    for channel_id in channels:
        await execute(
            text(
                """
                INSERT INTO slack_user_settings_auto_translate_channels
                (settings_id, channel_id)
                VALUES
                (:settings_id, :channel_id)
                """
            ).bindparams(settings_id=ray_client.settings_id, channel_id=channel_id),
            async_engines["ray_integration"],
            commit_after=True,
        )
    for lang in languages:
        await execute(
            text(
                """
                INSERT INTO slack_user_settings_auto_translate_langs
                (settings_id, lang)
                VALUES
                (:settings_id, :lang)
                """
            ).bindparams(settings_id=ray_client.settings_id, lang=lang),
            async_engines["ray_integration"],
            commit_after=True,
        )


# Gets back all for enterprise. Creates entry for team if it doesn't exist
# TODO comeback to this
async def get_team_setting(
    context: AsyncBoltContext, team_id: str
) -> SlackGroupSettings:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    async with AsyncSession(async_engines["ray_integration"]) as session:
        team_id = team_id or context.team_id or ""
        query = select(SlackGroupSettings).where(
            SlackGroupSettings.slack_team_id == team_id
        )
        result = await session.execute(query)
        settings = result.scalars().all()

        if not settings:
            # Create record if doesn't exist yet.
            new_settings = SlackGroupSettings(
                slack_team_id=team_id, slack_enterprise_id=context.enterprise_id
            )
            session.add(new_settings)
            await session.commit()
            await session.refresh(new_settings)
            return new_settings

        return settings[0]


# Gets back all for enterprise. Creates entry for team if it doesn't exist
# TODO comeback to this
async def get_or_create_group_settings(
    context: AsyncBoltContext, team_id: str | None = None
) -> list[SlackGroupSettings]:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    async with AsyncSession(async_engines["ray_integration"]) as session:
        team_id = team_id or context.team_id
        query = select(SlackGroupSettings).where(
            SlackGroupSettings.slack_team_id == team_id
        )
        result = await session.execute(query)
        settings = result.scalars().all()

        if not settings:
            # Create record if doesn't exist yet.
            new_settings = SlackGroupSettings(
                slack_team_id=team_id, slack_enterprise_id=context.enterprise_id
            )
            session.add(new_settings)
            await session.commit()
            await session.refresh(new_settings)

        # Query for all relevant settings
        team_id = team_id or context.team_id
        enterprise_id = context.enterprise_id
        if enterprise_id:
            query = select(SlackGroupSettings).where(
                or_(
                    SlackGroupSettings.slack_team_id == team_id,
                    SlackGroupSettings.slack_team_id == enterprise_id,
                )
            )
        else:
            query = select(SlackGroupSettings).where(
                SlackGroupSettings.slack_team_id == team_id
            )

        result = await session.execute(query)
        settings = result.scalars().all()
        return list(settings)


async def get_or_create_setting_for_team(
    context: AsyncBoltContext, channel_id: str, team_id: str
) -> SlackGroupSettingsTranslation:
    # Get setting for team and channel
    setting = await get_team_setting(context, team_id)

    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = select(SlackGroupSettingsTranslation).where(
            SlackGroupSettingsTranslation.settings_id == setting.id,
            SlackGroupSettingsTranslation.channel_id == channel_id,
        )
        result = await session.execute(query)
        auto_translate_settings = result.scalars().first()

        if auto_translate_settings:
            return auto_translate_settings

        # Create record if doesn't exist yet.
        new_settings = SlackGroupSettingsTranslation(
            settings_id=setting.id, channel_id=channel_id
        )
        session.add(new_settings)
        await session.commit()
        await session.refresh(new_settings)
        return new_settings


async def get_all_settings_for_channel(
    context: AsyncBoltContext, channel_id: str
) -> list[SlackGroupSettingsTranslation]:
    # TODO streamline this (join)
    settings = await get_or_create_group_settings(context)

    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = select(SlackGroupSettingsTranslation).where(
            SlackGroupSettingsTranslation.channel_id == channel_id
        )
        result = await session.execute(query)
        auto_translate_settings = result.scalars().all()

        if auto_translate_settings:
            return list(auto_translate_settings)

        # Create record if doesn't exist yet.
        new_settings = SlackGroupSettingsTranslation(
            settings_id=settings[0].id, channel_id=channel_id
        )
        session.add(new_settings)
        await session.commit()
        await session.refresh(new_settings)
        return [new_settings]


async def get_or_create_auto_translate_group_settings(
    context: AsyncBoltContext, channel_id: str
) -> list[SlackGroupSettingsTranslation]:
    # TODO streamline this (join)
    settings = await get_or_create_group_settings(context)
    # Extract the list of settings.id
    settings_ids = [setting.id for setting in settings]

    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = select(SlackGroupSettingsTranslation).where(
            SlackGroupSettingsTranslation.settings_id.in_(settings_ids),
            SlackGroupSettingsTranslation.channel_id == channel_id,
        )
        result = await session.execute(query)
        auto_translate_settings = result.scalars().all()

        if auto_translate_settings:
            return list(auto_translate_settings)

        # Create record if doesn't exist yet.
        new_settings = SlackGroupSettingsTranslation(
            settings_id=settings[0].id, channel_id=channel_id
        )
        session.add(new_settings)
        await session.commit()
        await session.refresh(new_settings)
        return [new_settings]


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
    await get_all_settings_for_channel(context, channel_id)

    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = (
            select(
                distinct(SlackGroupSettingsTranslationLangs.lang).label("target_lang"),
                SlackGroupSettingsTranslation.display_format,
            )
            .join(
                SlackGroupSettingsTranslation,
                SlackGroupSettingsTranslation.id
                == SlackGroupSettingsTranslationLangs.translation_settings_id,
            )
            .where(
                SlackGroupSettingsTranslation.channel_id == channel_id,
            )
        )
        result = await session.execute(query)
        results = result.all()

        return [
            {"target_lang": row.target_lang, "display_format": row.display_format}
            for row in results
        ]


async def update_auto_translate_group_settings(
    context: AsyncBoltContext,
    channels: list[dict[str, str | bool | None]],
    languages: list[str],
    display_format: SlackGroupSettingsTranslation.DisplayFormatType,
) -> None:
    """Update the auto-translate settings for a channel for a LanugageCloud group.

    Args:
        channels: List of channel dicts with channel_id, team_id, and optionally
            channel_name and is_private.
        languages (list[str]): The languages to auto-translate to.
        display_format: The display format setting.
    """
    for channel in channels:
        # insert for team
        channel_setting = await get_or_create_setting_for_team(
            context, str(channel["channel_id"]), str(channel["team_id"])
        )

        async with AsyncSession(async_engines["ray_integration"]) as session:
            # Update display format
            # Merge the detached object from the previous session into this session
            channel_setting = await session.merge(channel_setting)
            channel_setting.display_format = display_format

            # Update channel_name and is_private if provided
            if "name" in channel and channel["name"] is not None:
                channel_setting.channel_name = str(channel["name"])
            if "is_private" in channel:
                channel_setting.is_private = bool(channel["is_private"])

            # Get current settings for this channel
            query = select(SlackGroupSettingsTranslation).where(
                SlackGroupSettingsTranslation.channel_id == channel["channel_id"]
            )
            result = await session.execute(query)
            current_settings = result.scalars().all()
            current_setting_ids = [setting.id for setting in current_settings]

            # Delete existing language settings
            if current_setting_ids:
                await session.execute(
                    text(
                        """
                        DELETE FROM slack_group_settings_translation_langs
                        WHERE translation_settings_id IN :current_setting_ids
                        """
                    ).bindparams(current_setting_ids=current_setting_ids)
                )

            # Add new language settings
            for language_code in languages:
                new_lang = SlackGroupSettingsTranslationLangs(
                    translation_settings_id=channel_setting.id, lang=language_code
                )
                session.add(new_lang)

            await session.commit()


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

    async with AsyncSession(async_engines["ray_integration"]) as session:
        if settings_ids:
            query = select(SlackGroupSettingsTranslationLangs).where(
                SlackGroupSettingsTranslationLangs.translation_settings_id.in_(
                    settings_ids
                )
            )
            result = await session.execute(query)
            existing_langs = result.scalars().all()
            for lang in existing_langs:
                await session.delete(lang)
            await session.commit()


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

    async with AsyncSession(async_engines["ray_integration"]) as session:
        # Get channel settings
        query = (
            select(SlackGroupSettingsTranslation)
            .join(
                SlackGroupSettingsTranslationLangs,
                SlackGroupSettingsTranslation.id
                == SlackGroupSettingsTranslationLangs.translation_settings_id,
            )
            .where(SlackGroupSettingsTranslation.settings_id.in_(settings_id))
            .group_by(SlackGroupSettingsTranslation.id)
            .limit(rows_per_page)
            .offset((page - 1) * rows_per_page)
            .order_by(SlackGroupSettingsTranslation.id.desc())
        )

        result = await session.execute(query)
        channel_settings = result.scalars().all()

        if not channel_settings:
            return []

        # Get language settings for these channels
        channel_ids = [setting.id for setting in channel_settings]
        lang_query = (
            select(SlackGroupSettingsTranslationLangs)
            .where(
                SlackGroupSettingsTranslationLangs.translation_settings_id.in_(
                    channel_ids
                )
            )
            .order_by(SlackGroupSettingsTranslationLangs.id)
        )

        lang_result = await session.execute(lang_query)
        channel_langs = lang_result.scalars().all()

        # Map channels to languages.
        settings_lang_map: dict[
            int, tuple[SlackGroupSettingsTranslation, list[str]]
        ] = {setting.id: (setting, []) for setting in channel_settings}
        for lang in channel_langs:
            if lang.translation_settings_id in settings_lang_map:
                settings_lang_map[lang.translation_settings_id][1].append(lang.lang)

        # Remove channels with no languages.
        return [(channel, langs) for channel, langs in settings_lang_map.values()]


# pagination - get number of pages based on rows per page and number of records
async def get_pagination(context: AsyncBoltContext, rows_per_page: int):
    """Get the number of pages based on the number of rows per page and total rows.

    Args:
        rows_per_page (int): The number of rows per page.

    Returns:
        int: The number of pages.
    """
    settings = await get_or_create_group_settings(context)
    settings_id = [setting.id for setting in settings]

    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = (
            select(func.count(distinct(SlackGroupSettingsTranslation.id)))
            .join(
                SlackGroupSettingsTranslationLangs,
                SlackGroupSettingsTranslation.id
                == SlackGroupSettingsTranslationLangs.translation_settings_id,
            )
            .where(SlackGroupSettingsTranslation.settings_id.in_(settings_id))
        )

        result = await session.execute(query)
        total_rows = result.scalar()

        if not total_rows:
            return 0
        return (total_rows + rows_per_page - 1) // rows_per_page


async def update_channel_id(old_channel_id, new_channel_id):
    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = select(SlackGroupSettingsTranslation).where(
            SlackGroupSettingsTranslation.channel_id == old_channel_id
        )
        result = await session.execute(query)
        settings = result.scalars().all()

        for setting in settings:
            setting.channel_id = new_channel_id
            session.add(setting)

        await session.commit()


async def delete_channel_id(channel_id: str | None):
    if not channel_id:
        return
    async with AsyncSession(async_engines["ray_integration"]) as session:
        await session.execute(
            delete(SlackGroupSettingsTranslation).where(
                SlackGroupSettingsTranslation.channel_id == channel_id
            )
        )
        await session.commit()


async def update_channel_info(
    setting_id: int,
    channel_name: str | None,
    is_private: bool,
) -> None:
    """Update channel_name and is_private for a translation setting.

    Args:
        setting_id: The ID of the SlackGroupSettingsTranslation record.
        channel_name: The channel name to store.
        is_private: Whether the channel is private.
    """
    async with AsyncSession(async_engines["ray_integration"]) as session:
        query = select(SlackGroupSettingsTranslation).where(
            SlackGroupSettingsTranslation.id == setting_id
        )
        result = await session.execute(query)
        setting = result.scalars().first()

        if setting:
            setting.channel_name = channel_name
            setting.is_private = is_private
            await session.commit()

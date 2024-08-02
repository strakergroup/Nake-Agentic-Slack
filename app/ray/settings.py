import functools
from typing import Iterable
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session
from slack_bolt.context.async_context import AsyncBoltContext

from ..auth.connector import RayClient
from ..database import engines
from ..models import (
    SlackGroupSettings,
    SlackGroupSettingsTranslationLangs,
    SlackGroupSettingsTranslation,
)
from app.translate import _


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
        ("fr-CA", "French (Canadian)"),
        ("de", "German"),
        ("it", "Italian"),
        ("pt", "Portuguese"),
        ("nl", "Dutch"),
        ("da", "Danish"),
        ("sv", "Swedish"),
        ("fi", "Finnish"),
        ("no", "Norwegian"),
        ("zh-CN", "Chinese (Simplified)"),
        ("zh-TW", "Chinese (Traditional)"),
        ("ja", "Japanese"),
        ("ko", "Korean"),
        ("vi", "Vietnamese"),
        ("ar", "Arabic"),
        ("pl", "Polish"),
        ("ru", "Russian"),
        ("uk", "Ukrainian"),
        ("be", "Belarusian"),
        ("mi", "Maori"),
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
    return "Unknown"


def filter_invalid_auto_translate_languages(languages: Iterable[str]) -> list[str]:
    """Filter and return a list of languages that are valid for auto-translation.

    Args:
        languages (Iterable[str]): The list of language codes to filter

    Returns:
        list[str]: The list with invalid languages removed.
    """
    return [lang for lang in languages if is_valid_auto_translate_language(lang)]


def get_auto_translate_user_settings_channels(ray_client: RayClient) -> list[str]:
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


def get_auto_translate_user_settings_langs(ray_client: RayClient) -> list[str]:
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


def update_auto_translate_user_settings(
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


def get_or_create_group_settings(
    session: Session, context: AsyncBoltContext
) -> SlackGroupSettings:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    query = select(SlackGroupSettings)
    if context.enterprise_id:
        query = query.where(
            # TODO delete team_id filter after enterprise refactor
            (SlackGroupSettings.slack_enterprise_id == context.enterprise_id)
            | (SlackGroupSettings.slack_team_id == context.team_id)
        )
    else:
        query = query.where(SlackGroupSettings.slack_team_id == context.team_id)
    settings = session.scalar(query)
    if settings:
        return settings
    # Create record if doesn't exist yet.
    new_settings = SlackGroupSettings(
        slack_team_id=context.team_id, slack_enterprise_id=context.enterprise_id
    )
    session.add(new_settings)
    session.commit()
    session.refresh(new_settings)
    return new_settings


def get_or_create_auto_translate_group_settings(
    session: Session, context: AsyncBoltContext, channel_id: str
) -> SlackGroupSettingsTranslation:
    # TODO streamline this (join)
    settings = get_or_create_group_settings(session, context)
    auto_translate_settings = session.scalar(
        select(SlackGroupSettingsTranslation)
        .where(SlackGroupSettingsTranslation.settings_id == settings.id)
        .where(SlackGroupSettingsTranslation.channel_id == channel_id)
    )
    if auto_translate_settings:
        return auto_translate_settings
    # Create record if doesn't exist yet.
    new_settings = SlackGroupSettingsTranslation(
        settings_id=settings.id, channel_id=channel_id
    )
    session.add(new_settings)
    session.commit()
    session.refresh(new_settings)
    return new_settings


def get_auto_translate_settings_and_langs(
    context: AsyncBoltContext, channel_id: str | None = None
) -> tuple[SlackGroupSettingsTranslation | None, list[str]]:
    """Get the auto-translate languages for a channel for a LanugageCloud group.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    # TODO: Combine with above function
    if not channel_id:
        return None, []  # Modal triggers do not have channel_id
    with Session(engines["ray_integration"]) as session:
        channel_settings = get_or_create_auto_translate_group_settings(
            session, context, channel_id
        )
        results = session.scalars(
            select(SlackGroupSettingsTranslationLangs.lang).where(
                SlackGroupSettingsTranslationLangs.translation_settings_id
                == channel_settings.id
            )
        ).all()
    return channel_settings, list(results)


def update_auto_translate_group_settings(
    context: AsyncBoltContext,
    channels: list[dict[str, str]],
    languages: list[str],
    display_format: SlackGroupSettingsTranslation.DisplayFormatType,
) -> None:
    """Update the auto-translate settings for a channel for a LanugageCloud group.

    Args:
        channels (list[str]): The IDs of the channels (conversations) to auto-translate.
        languages (list[str]): The languages to auto-translate to.
        display_format: The display format setting.
    """
    with Session(engines["ray_integration"]) as session:
        for channel in channels:
            channel_settings = get_or_create_auto_translate_group_settings(
                session, context, channel["channel_id"]
            )
            channel_settings.display_format = display_format
            session.execute(
                delete(SlackGroupSettingsTranslationLangs).where(
                    SlackGroupSettingsTranslationLangs.translation_settings_id
                    == channel_settings.id
                )
            )
            for lang in languages:
                session.add(
                    SlackGroupSettingsTranslationLangs(
                        translation_settings_id=channel_settings.id, lang=lang
                    )
                )
        session.commit()


def disable_auto_translate_group_settings(
    context: AsyncBoltContext, channel_id: str
) -> None:
    """Disable the auto-translate settings for a channel for a LanugageCloud group
    by deleting the languages for that channel.

    Args:
        channel_id str: The ID of the channel (conversations) to auto-translate.
    """
    with Session(engines["ray_integration"]) as session:
        channel_settings = get_or_create_auto_translate_group_settings(
            session, context, channel_id
        )
        session.execute(
            delete(SlackGroupSettingsTranslationLangs).where(
                SlackGroupSettingsTranslationLangs.translation_settings_id
                == channel_settings.id
            )
        )
        session.commit()


def get_full_group_translation_settings(
    context: AsyncBoltContext, page: int = 1, rows_per_page: int = 5
) -> list[tuple[SlackGroupSettingsTranslation, list[str]]]:
    """Get the group translation settings for all channels.

    Returns:
        list[tuple[SlackGroupSettingsTranslation, list[str]]]: A list of tuples with
            the channel settings and the languages to translate to.
    """
    # TODO Refactor
    with Session(engines["ray_integration"]) as session:
        settings = get_or_create_group_settings(session, context)
        channel_settings = session.scalars(
            select(SlackGroupSettingsTranslation)
            .join(
                SlackGroupSettingsTranslationLangs,
                SlackGroupSettingsTranslation.id
                == SlackGroupSettingsTranslationLangs.translation_settings_id,
            )
            .where(SlackGroupSettingsTranslation.settings_id == settings.id)
            .group_by(SlackGroupSettingsTranslation.id)
            .limit(rows_per_page)
            .offset((page - 1) * rows_per_page)
            .order_by(SlackGroupSettingsTranslation.id)
        ).all()
        channel_langs = session.scalars(
            select(SlackGroupSettingsTranslationLangs)
            .where(
                SlackGroupSettingsTranslationLangs.translation_settings_id.in_(
                    [setting.id for setting in channel_settings]
                )
            )
            .order_by(SlackGroupSettingsTranslationLangs.id)
        ).all()
        # Map channels to languages.
        settings_lang_map: dict[
            int, tuple[SlackGroupSettingsTranslation, list[str]]
        ] = {setting.id: (setting, []) for setting in channel_settings}
        for lang in channel_langs:
            if lang.translation_settings_id in settings_lang_map:
                settings_lang_map[lang.translation_settings_id][1].append(lang.lang)
    # Remove channels with no languages.
    return [
        (channel, langs) for channel, langs in settings_lang_map.values() if len(langs)
    ]


# pagination - get number of pages based on rows per page and number of records
def get_pagination(context: AsyncBoltContext, rows_per_page: int) -> int:
    """Get the number of pages based on the number of rows per page and total rows.

    Args:
        rows_per_page (int): The number of rows per page.

    Returns:
        int: The number of pages.
    """
    with Session(engines["ray_integration"]) as session:
        settings = get_or_create_group_settings(session, context)
        total_rows = session.scalar(
            select(func.count(SlackGroupSettingsTranslation.id))
            .join(
                SlackGroupSettingsTranslationLangs,
                SlackGroupSettingsTranslation.id
                == SlackGroupSettingsTranslationLangs.translation_settings_id,
            )
            .where(SlackGroupSettingsTranslation.settings_id == settings.id)
        )
        if total_rows == 0:
            return 0
    return (total_rows + rows_per_page - 1) // rows_per_page


def update_channel_id(old_channel_id, new_channel_id):
    with Session(engines["ray_integration"]) as session:
        session.execute(
            update(SlackGroupSettingsTranslation)
            .where(SlackGroupSettingsTranslation.channel_id == old_channel_id)
            .values(channel_id=new_channel_id)
        )
        session.commit()

import functools
from typing import Iterable
import langcodes
from sqlalchemy import delete, distinct, func, select, text, update, or_
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


# Gets back all for enterprise. Creates entry for team if it doesn't exist
# TODO comeback to this
def get_team_setting(
    session: Session, context: AsyncBoltContext, team_id: str
) -> SlackGroupSettings:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    query = select(SlackGroupSettings)
    team_id = team_id or context.team_id
    query = query.where(SlackGroupSettings.slack_team_id == team_id)
    settings = session.scalars(query).all()
    if not settings:
        # Create record if doesn't exist yet.
        new_settings = SlackGroupSettings(
            slack_team_id=team_id, slack_enterprise_id=context.enterprise_id
        )
        session.add(new_settings)
        session.commit()
        session.refresh(new_settings)
        return new_settings
    return settings[0]


# Gets back all for enterprise. Creates entry for team if it doesn't exist
# TODO comeback to this
def get_or_create_group_settings(
    session: Session, context: AsyncBoltContext, team_id: str | None = None
) -> list[SlackGroupSettings]:
    # NOTE: The logic for getting group settings is not stable and WILL change in the future.
    # Please don't touch this file yet
    query = select(SlackGroupSettings)
    team_id = team_id or context.team_id
    query = query.where(SlackGroupSettings.slack_team_id == team_id)
    settings = session.scalars(query).all()
    if not settings:
        # Create record if doesn't exist yet.
        new_settings = SlackGroupSettings(
            slack_team_id=team_id, slack_enterprise_id=context.enterprise_id
        )
        session.add(new_settings)
        session.commit()
        session.refresh(new_settings)
    query = select(SlackGroupSettings)
    team_id = team_id or context.team_id
    enterprise_id = context.enterprise_id
    if enterprise_id:
        query = query.where(
            or_(
                SlackGroupSettings.slack_team_id == team_id,
                SlackGroupSettings.slack_team_id == enterprise_id,
            )
        )
    else:
        query = query.where(SlackGroupSettings.slack_team_id == team_id)
    settings = session.scalars(query).all()
    return settings


def get_or_create_setting_for_team(
    session: Session, context: AsyncBoltContext, channel_id: str, team_id: str
) -> SlackGroupSettingsTranslation:
    # Get setting for team and channel
    setting = get_team_setting(session, context, team_id)
    # Extract the list of settings.id
    # settings_ids = [setting.id for setting in settings]

    auto_translate_settings = session.scalars(
        select(SlackGroupSettingsTranslation)
        .where(SlackGroupSettingsTranslation.settings_id == setting.id)
        .where(SlackGroupSettingsTranslation.channel_id == channel_id)
    ).first()
    if auto_translate_settings:
        return auto_translate_settings
    # Create record if doesn't exist yet.
    new_settings = SlackGroupSettingsTranslation(
        settings_id=setting.id, channel_id=channel_id
    )
    session.add(new_settings)
    session.commit()
    session.refresh(new_settings)
    return new_settings


def get_all_settings_for_channel(
    session: Session, context: AsyncBoltContext, channel_id: str
) -> list[SlackGroupSettingsTranslation]:
    # TODO streamline this (join)
    settings = get_or_create_group_settings(session, context)

    auto_translate_settings = session.scalars(
        select(SlackGroupSettingsTranslation).where(
            SlackGroupSettingsTranslation.channel_id == channel_id
        )
    ).all()
    if auto_translate_settings:
        return auto_translate_settings
    # Create record if doesn't exist yet.
    new_settings = SlackGroupSettingsTranslation(
        settings_id=settings[0].id, channel_id=channel_id
    )
    session.add(new_settings)
    session.commit()
    session.refresh(new_settings)
    return [new_settings]


def get_or_create_auto_translate_group_settings(
    session: Session, context: AsyncBoltContext, channel_id: str
) -> list[SlackGroupSettingsTranslation]:
    # TODO streamline this (join)
    settings = get_or_create_group_settings(session, context)
    # Extract the list of settings.id
    settings_ids = [setting.id for setting in settings]

    auto_translate_settings = session.scalars(
        select(SlackGroupSettingsTranslation)
        .where(SlackGroupSettingsTranslation.settings_id.in_(settings_ids))
        .where(SlackGroupSettingsTranslation.channel_id == channel_id)
    ).all()
    if auto_translate_settings:
        return auto_translate_settings
    # Create record if doesn't exist yet.
    new_settings = SlackGroupSettingsTranslation(
        settings_id=settings[0].id, channel_id=channel_id
    )
    session.add(new_settings)
    session.commit()
    session.refresh(new_settings)
    return [new_settings]


def get_auto_translate_settings_and_langs(
    context: AsyncBoltContext, channel_id: str | None = None, team_id: str | None = None
) -> tuple[SlackGroupSettingsTranslation | None, list[str]]:
    """Get the auto-translate languages for a channel for a LanugageCloud group.

    Returns:
        list[str]: The list of languages to auto-translate to.
    """
    # TODO: Combine with above function
    if not channel_id:
        return None, []  # Modal triggers do not have channel_id
    team_id = team_id or context.team_id
    with Session(engines["ray_integration"]) as session:
        channel_settings = get_all_settings_for_channel(session, context, channel_id)
        channel_ids = [channel_settings.id for channel_settings in channel_settings]
        results = session.scalars(
            select(distinct(SlackGroupSettingsTranslationLangs.lang)).where(
                SlackGroupSettingsTranslationLangs.translation_settings_id.in_(
                    channel_ids
                )
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
            # insert for team
            channel_setting = get_or_create_setting_for_team(
                session, context, channel["channel_id"], channel["team_id"]
            )
            channel_setting.display_format = display_format
            current_settings = session.scalars(
                select(SlackGroupSettingsTranslation).where(
                    SlackGroupSettingsTranslation.channel_id == channel["channel_id"]
                )
            ).all()
            current_setting_ids = [setting.id for setting in current_settings]
            session.execute(
                delete(SlackGroupSettingsTranslationLangs).where(
                    SlackGroupSettingsTranslationLangs.translation_settings_id.in_(
                        current_setting_ids
                    )
                )
            )
            for lang in languages:
                session.add(
                    SlackGroupSettingsTranslationLangs(
                        translation_settings_id=channel_setting.id, lang=lang
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
        channel_settings = get_all_settings_for_channel(session, context, channel_id)
        settings_ids = [setting.id for setting in channel_settings]
        session.execute(
            delete(SlackGroupSettingsTranslationLangs).where(
                SlackGroupSettingsTranslationLangs.translation_settings_id.in_(
                    settings_ids
                )
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
        settings = get_or_create_group_settings(session, context, context.team_id)
        settings_id = [setting.id for setting in settings]
        channel_settings = session.scalars(
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
    return [(channel, langs) for channel, langs in settings_lang_map.values()]


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
        settings_id = [setting.id for setting in settings]
        total_rows = session.scalar(
            select(func.count(distinct(SlackGroupSettingsTranslation.id)))
            .join(
                SlackGroupSettingsTranslationLangs,
                SlackGroupSettingsTranslation.id
                == SlackGroupSettingsTranslationLangs.translation_settings_id,
            )
            .where(SlackGroupSettingsTranslation.settings_id.in_(settings_id))
        )
        if not total_rows:
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

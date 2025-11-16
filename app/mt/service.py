import langcodes
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from straker_utils.sql.async_engine import fetch_all, fetch_one

from app.auth.connector import RayClient
from app.database import async_engines
from app.models import Language

microsoft_languages = {
    "fr-ca": "fr-ca",
    "french-canada": "fr-ca",
    "french-canadian": "fr-ca",
}


async def resolve_language_code(lang: str | None) -> Language | None:
    """Resolve the language code to a Google language code."""

    if not lang:
        return None
    async with AsyncSession(async_engines["translators_readonly"]) as session:
        query = select(Language).where(
            or_(
                func.lower(Language.bcp_47) == (lang.lower()),
                Language.label == (lang),
                Language.code == (lang),
                Language.google_code == (lang),
            )
        )
        result = await session.execute(query)
        language = result.scalars().first()

    if language:
        return language
    else:
        if "-" in lang:
            if lang == "french-canada":
                like_lang = "french (canada)"
            else:
                like_lang = lang.replace("-", " ")
        else:
            like_lang = f"{lang}%"

        query = select(Language).where(
            or_(
                Language.bcp_47.ilike(like_lang),
                Language.google_code.ilike(like_lang),
                Language.label.ilike(like_lang),
                Language.code.ilike(like_lang),
                # Language.site_shortname.ilike(like_lang),
                # Language.parent_lang.ilike(like_lang),
            )
        )
        async with AsyncSession(async_engines["translators_readonly"]) as session:
            result = await session.execute(query)
            language = result.scalars().first()

    return language


async def evaluate_get_org_groups(organization_uuid: str):
    sql = text(
        "SELECT obj_uuid FROM obj_m_group WHERE organization_id = :org_uuid"
    ).bindparams(org_uuid=organization_uuid)
    result = await fetch_all(sql, async_engines["sitemanager"])
    return [row["obj_uuid"] for row in result]


async def get_client_groups(client_uuid: str):
    sql = text(
        "SELECT groupid FROM obj_m_mglink WHERE memberid = :client_uuid and is_active = 1"
    ).bindparams(client_uuid=client_uuid)
    result = await fetch_all(sql, async_engines["sitemanager"])
    return [row["groupid"] for row in result]


async def get_group_id(verify_organization_uuid: str) -> str | None:
    """Get group ID (obj_uuid) from obj_m_group by organization_id.

    Args:
        verify_organization_uuid: The verify organization UUID (organization_id)

    Returns:
        The group ID (obj_uuid) if found, None otherwise
    """
    sql = text(
        "SELECT obj_uuid FROM obj_m_group WHERE organization_id = :id and active = 1"
    ).bindparams(id=verify_organization_uuid)
    result = await fetch_one(sql, async_engines["sitemanager"])
    if not result:
        return None
    return result["obj_uuid"]


async def evaluate_get_glossary_resource(
    org_uuid: str, client: RayClient | None, sl: str | None, tl: str | None, engine: str
) -> str:
    groups = (
        await evaluate_get_org_groups(org_uuid)
        if not client
        else await get_client_groups(client.id)
    )

    if not groups:
        return ""

    sql = text("""
        SELECT terminology_id
        FROM terminology_third_party_info
        WHERE group_id IN :groups
        AND sl = :sl
        AND tl = :tl
        AND terminology_engine = :engine
        ORDER BY created_at DESC
        LIMIT 1
    """).bindparams(groups=groups, sl=sl, tl=tl, engine=engine)
    result = await fetch_one(sql, async_engines["machine_translation_readonly"])
    if not result:
        return ""

    return result["terminology_id"]


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
        ("fr-ca", "French (Canadian)"),
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

    languages = [(lang[0], lang[1]) for lang in languages]
    languages = sorted(languages, key=lambda language: language[1])
    return languages


async def resolve_language(target_langs: list[str], engine: str) -> list[str]:
    """Resolve language code from language name."""
    # Resolve language code from language name
    langs_dict = dict(get_auto_translate_languages(True)) | microsoft_languages
    mapped_lang = []
    for lang in target_langs:
        if engine != "microsoft" and lang.lower() in langs_dict:
            mapped_lang.append(lang)
        else:
            db_lang = await resolve_language_code(lang)
            if db_lang:
                if db_lang and engine == "microsoft":
                    if db_lang.bcp_47:
                        mapped_lang.append(db_lang.bcp_47)
                    else:
                        lang_code = langcodes.get(db_lang.shortname).language
                        if lang_code:
                            mapped_lang.append(lang_code)
                else:
                    if db_lang.google_code:
                        mapped_lang.append(db_lang.google_code)
    # If language code is not found
    if not mapped_lang:
        return ["en"]
    return mapped_lang

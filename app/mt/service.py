import langcodes
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from straker_utils.sql.async_engine import fetch_all, fetch_one

from app.auth.connector import RayClient
from app.database import async_engines
from app.models import Language
from app.slack.language_validation import get_language_base_code

microsoft_languages = {
    "fr-ca": "fr-ca",
    "french-canada": "fr-ca",
    "french-canadian": "fr-ca",
}


def is_no_op_translation_pair(source: str | None, target: str | None) -> bool:
    """Return True when translating ``source`` → ``target`` is effectively a no-op.

    Used to short-circuit MT requests so customers are not charged for
    translations that would return the input unchanged.

    Rules (case-insensitive, also tolerates ``_`` separators via
    :func:`get_language_base_code`):

    - Empty / missing input on either side → False (let the normal flow handle it).
    - Exact match → True (e.g. ``en`` ↔ ``en``).
    - Same ISO-639 base AND at least one side is the bare base code → True.
      Examples that skip: ``zh`` ↔ ``zh-CN``, ``fr`` ↔ ``fr-ca``, ``pt`` ↔ ``pt-BR``.
    - Distinct dialects sharing a base (no bare side) → False, so the
      translation still runs. Examples that translate: ``zh-CN`` ↔ ``zh-TW``,
      ``pt-BR`` ↔ ``pt-PT``.
    """
    if not source or not target:
        return False
    s_lower = source.strip().lower()
    t_lower = target.strip().lower()
    if not s_lower or not t_lower:
        return False
    if s_lower == t_lower:
        return True
    s_base = get_language_base_code(s_lower)
    t_base = get_language_base_code(t_lower)
    if not s_base or s_base != t_base:
        return False
    return s_lower == s_base or t_lower == t_base


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
    """Get group IDs (obj_uuid) from obj_m_group by organization_id.

    Args:
        verify_organization_uuid: The verify organization UUID (organization_id)

    Returns:
        All group IDs (obj_uuid) joined by colons if found, None otherwise
    """
    sql = text(
        "SELECT obj_uuid FROM obj_m_group WHERE organization_id = :id"
    ).bindparams(id=verify_organization_uuid)
    results = await fetch_all(sql, async_engines["sitemanager"])
    if not results:
        return None
    return ":".join(row["obj_uuid"] for row in results)


def glossary_language_candidates(lang: str | None) -> list[str]:
    """Return ordered language candidates for glossary lookup.

    Slack direct MT often resolves English to the generic `en`, while the
    terminology record may have been created for `en-us` or `en-gb`. Try the
    exact value first, then the common English variants.
    """
    if not lang:
        return []

    normalized = lang.strip().lower()
    if not normalized:
        return []

    candidates = [normalized]
    if normalized.startswith("en"):
        for variant in ("en", "en-us", "en-gb"):
            if variant not in candidates:
                candidates.append(variant)
    return candidates


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

    source_candidates = glossary_language_candidates(sl)
    target_candidates = glossary_language_candidates(tl)
    if not source_candidates:
        source_candidates = [sl or ""]
    if not target_candidates:
        target_candidates = [tl or ""]

    sql = text("""
        SELECT terminology_id
        FROM terminology_third_party_info
        WHERE group_id IN :groups
        AND sl = :sl
        AND tl = :tl
        AND terminology_engine = :engine
        ORDER BY created_at DESC
        LIMIT 1
    """)

    for source_candidate in source_candidates:
        for target_candidate in target_candidates:
            result = await fetch_one(
                sql.bindparams(
                    groups=groups,
                    sl=source_candidate,
                    tl=target_candidate,
                    engine=engine,
                ),
                async_engines["machine_translation_readonly"],
            )
            if result:
                return result["terminology_id"]

    return ""


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
        ("pt-BR", "Portuguese (Brazil)"),
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
            if lang.lower() in ["fr-ca", "french-canada", "french-canadian"]:
                mapped_lang.append("fr-ca")
            else:
                mapped_lang.append(lang.lower())
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

import langcodes
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.ray.settings import get_auto_translate_language_code_map

from ..database import engines
from ..models import Language


# TODO: get microsoft code
def resolve_language_code(lang: str | None) -> Language | None:
    """Resolve the language code to a Google language code."""

    if not lang:
        return None
    with Session(engines["translators_readonly"]) as session:
        language = (
            session.query(Language)
            .filter(
                or_(
                    func.lower(Language.bcp_47) == (lang),
                    Language.label == (lang),
                    Language.code == (lang),
                    Language.google_code == (lang),
                    # Language.site_shortname == (lang),
                    # Language.parent_lang == (lang),
                )
            )
            .first()
        )

    session.close()

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

        language = (
            session.query(Language)
            .filter(
                or_(
                    Language.bcp_47.ilike(like_lang),
                    Language.google_code.ilike(like_lang),
                    Language.label.ilike(like_lang),
                    Language.code.ilike(like_lang),
                    # Language.site_shortname.ilike(like_lang),
                    # Language.parent_lang.ilike(like_lang),
                )
            )
            .first()
        )

    return language


# TODO: Clean this. Create static mapping for microsoft api/google api rather than our db
def resolve_language(target_langs: list[str], engine: str) -> list[str]:
    """Resolve language code from language name."""
    # Resolve language code from language name
    langs_dict = get_auto_translate_language_code_map()
    mapped_lang = []
    for lang in target_langs:
        normalized = lang.lower().replace("_", "-")
        if engine != "microsoft" and normalized in langs_dict:
            mapped_lang.append(langs_dict[normalized].lower())
        else:
            db_lang = resolve_language_code(lang)
            if db_lang:
                if db_lang and engine == "microsoft":
                    if db_lang.bcp_47:
                        mapped_lang.append(db_lang.bcp_47)
                    else:
                        lng_str = langcodes.get(db_lang.shortname).language
                        if lng_str:
                            mapped_lang.append(lng_str)
                        else:
                            mapped_lang.append(lang)
                else:
                    mapped_lang.append(db_lang.google_code)
    # If language code is not found
    if not mapped_lang:
        return ["en"]
    return mapped_lang

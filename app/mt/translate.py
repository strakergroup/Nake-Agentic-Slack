import httpx
import langcodes
from slack_bolt.context.async_context import AsyncBoltContext

from app.mt.schemas import TranslationRequest, TranslationResponse
from ..config import domains
from app.auth.connector import get_group_mt_engine
from app.slack.utils import escape_slack_emoji, unescape_slack_emoji
from ..models import Language
from ..database import engines
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from straker_auth.languagecloud import create_languagecloud_group_token

from app.ray.settings import get_auto_translate_languages
from app.slack.middleware import require_mt_tokens

from ..config import config


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
    langs_dict = get_auto_translate_languages(True)
    mapped_lang = []
    for lang in target_langs:
        if engine != "microsoft" and lang in langs_dict:
            mapped_lang.append(lang)
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


# TODO: this should check if lang is supported by api. Should return array of google_langs and mircosoft_langs
def get_mt_engine(target_langs: list[str], mt_id: str, is_gropid: bool) -> str:
    """Gets engine that should be used based on group setting and language."""
    microsoft_languages = {
        "fr-ca": "fr-ca",
        "french-canada": "fr-ca",
        "french-canadian": "fr-ca",
    }
    if any(lang.lower() in microsoft_languages for lang in target_langs):
        return "microsoft"
    ai_engine = get_group_mt_engine(mt_id, is_gropid)
    return ai_engine


# TODO: Add tests
async def get_ai_translation(
    context: AsyncBoltContext, text: str, target_langs: list[str], usage_type: str
) -> tuple[str | None, list[tuple[str, str]]]:
    """Get google or microsoft machine translation for sentence by correct language pair.

    Args:
        context (AsyncBoltContext): The context from the listener.
        ray_client (RayClient): The RAY client details.
        target_lang (str): The target language use for translation.
        source_lang (str): The source language use for detect sentence.
        sentence (str): The sentence post on RAY need to be translated.
        thread_ts (str | None, optional): The message thread to reply to.
    """
    if not context.channel_id and not context.user_id and not context.response_url:
        raise AssertionError("No channel to post to")
    required_tokens = len(text) * len(target_langs)
    if not required_tokens or not await require_mt_tokens(context, required_tokens):
        return None, []
    escaped_text = escape_slack_emoji(text)
    url = f"{domains.languagecloud_api}/mt/translate"
    token = (
        context["ray"].client.id_token
        if context["ray"].client
        else create_languagecloud_group_token(
            context["ray"].super_group[0].verify_organization_uuid,
            aud="languagecloud-api",
            secret=config.languagecloud_api_key.get_secret_value(),
        )
    )
    headers = {
        "Authorization": f"Bearer {token}",
    }
    task_data = TranslationRequest(
        text=escaped_text,
        target_languages=target_langs,
        app_name="slack",
        usage_type=usage_type,
        email=context.get("user_info", {}).get("profile", {}).get("email", "unknown"),
        group_uuid=context["ray"].super_group[0].id,
    )
    async with httpx.AsyncClient() as http:
        response = await http.post(
            url,
            headers=headers,
            json=task_data.model_dump(),
        )
        response.raise_for_status()
        data = TranslationResponse(**response.json())
    translations = [
        (tl, unescape_slack_emoji(target_text, text))
        for tl, target_text in data.translations.items()
    ]
    source_lang = data.source_language
    return (source_lang, translations)

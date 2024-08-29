import asyncio
import langcodes
from slack_bolt.context.async_context import AsyncBoltContext

from app.auth.connector import get_group_mt_engine, spend_mt_tokens
from app.mt.google import get_machine_translations, log_google_api_usage
from app.slack.utils import escape_slack_emoji, unescape_slack_emoji
from ..models import Language
from ..database import engines
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from ..mt.microsoft import get_microsoft_machine_translations, log_microsoft_api_usage

from app.ray.settings import get_auto_translate_languages
from app.slack.middleware import require_mt_tokens


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
                    Language.site_shortname == (lang),
                    Language.google_code == (lang),
                    Language.parent_lang == (lang),
                )
            )
            .first()
        )

    session.close()

    if language:
        return language
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
                    Language.site_shortname.ilike(like_lang),
                    Language.parent_lang.ilike(like_lang),
                )
            )
            .first()
        )

    return language


# TODO: Clean this. Create static mapping for microsoft api/google api rather than our db
def resolve_language(target_langs: list[str], engine: str) -> str:
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
                        mapped_lang.append(langcodes.get(db_lang.shortname).language)
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
    context: AsyncBoltContext, text: str, target_langs: list[str]
) -> tuple[str, list[tuple[str, str]]]:
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
        return
    is_gropid = False
    escaped_text = escape_slack_emoji(text)
    if context["ray"].client is None:
        user_group_id = context["ray"].super_group[0].id
        is_gropid = True
    else:
        user_group_id = context["ray"].client.user_group_id
    engine = get_mt_engine(target_langs, user_group_id, is_gropid)
    target_langs = resolve_language(target_langs, engine)
    if engine == "microsoft":
        source_lang, translations = await get_microsoft_machine_translations(
            escaped_text, target_langs
        )
    else:
        source_lang, translations = await get_machine_translations(
            escaped_text, target_langs
        )
    # TODO: update logging to include transaction id
    if engine == "microsoft":
        ray_client = context["ray"]
        asyncio.create_task(
            log_microsoft_api_usage(
                ray_client.client.id if ray_client.client else context.user_id,
                escaped_text,
                source_lang,
                translations,
            )
        )
    else:
        ray_client = context["ray"]
        asyncio.create_task(
            log_google_api_usage(
                ray_client.client.id if ray_client.client else context.user_id,
                escaped_text,
                source_lang,
                translations,
            )
        )
    translations = [
        (tl, unescape_slack_emoji(target_text, text))
        for tl, target_text in translations.items()
    ]
    await spend_mt_tokens(credits=required_tokens, ray_connection=context["ray"])
    return (source_lang, translations)

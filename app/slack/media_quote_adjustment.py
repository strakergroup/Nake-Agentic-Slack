"""Media Quote2 (AI translation) Adjust Request — same contract as Document MT.

Quote2 stores a Document-MT-shaped ``session["quote"]`` so the shared
``evaluation_ai_quote_adjust_modal`` / ``document_mt_*`` helpers can price and
render per-file/per-language rows. Pair keys are ``file_id:language_code``.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.slack.document_mt_quote_adjustment import (
    document_mt_language_costs,
    document_mt_language_names,
    document_mt_pair_key,
)
from app.slack.media_quotes import (
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_TRANSLATING,
    media_translation_tokens,
    source_embed_tokens_for_session,
    translated_embed_tokens_for_session,
)

MEDIA_TRANSLATION_QUOTE_ADJUST_ACTION_ID = "media_translation_quote_adjust"
MEDIA_TRANSLATION_QUOTE_KIND = "media_translation"


def media_translation_file_id(session: dict[str, Any]) -> str:
    """Stable file identity for Quote2 pair keys (Slack/source media file id)."""
    return str(session.get("file_id") or session.get("task_uuid") or "media")


def media_translation_quote_from_session(session: dict[str, Any]) -> dict[str, Any]:
    """Build the Document-MT-shaped quote payload used by shared Adjust Request."""
    existing = session.get("quote")
    if isinstance(existing, dict) and existing.get("files"):
        return existing

    file_id = media_translation_file_id(session)
    file_name = str(session.get("file_name") or file_id)
    character_count = int(session.get("source_text_length") or 0)
    languages = [
        str(code) for code in (session.get("target_languages") or []) if str(code)
    ]
    targets = [
        {
            "target_language": code,
            "tokens": media_translation_tokens(character_count, 1),
        }
        for code in languages
    ]
    return {
        "files": [
            {
                "file_id": file_id,
                "file_name": file_name,
                "character_count": character_count,
                "target_languages": targets,
            }
        ],
        "total_tokens": media_translation_tokens(character_count, len(languages)),
        "pdf_conversion_tokens": 0,
    }


def media_selected_target_languages(
    session: dict[str, Any],
    selected_pairs: Iterable[str] | None = None,
) -> list[str]:
    """Return target language codes still selected after Adjust Request.

    When ``selected_pairs`` is omitted, uses the session value when present,
    otherwise the original ``target_languages`` list.
    """
    quote = media_translation_quote_from_session(session)
    if selected_pairs is None and "selected_pairs" not in session:
        return [str(code) for code in (session.get("target_languages") or []) if code]
    pairs = {
        str(pair)
        for pair in (
            selected_pairs
            if selected_pairs is not None
            else session.get("selected_pairs") or []
        )
    }
    selected: list[str] = []
    for file in quote.get("files") or []:
        file_id = str(file.get("file_id") or "")
        for target in file.get("target_languages") or []:
            code = str(target.get("target_language") or "")
            if code and document_mt_pair_key(file_id, code) in pairs:
                selected.append(code)
    return selected


def media_selected_target_language_names(
    session: dict[str, Any],
    selected_languages: Iterable[str],
) -> list[str]:
    """Keep display names aligned with the original language order."""
    original_codes = [str(code) for code in (session.get("target_languages") or [])]
    original_names = [
        str(name) for name in (session.get("target_language_names") or [])
    ]
    name_by_code = {
        code: original_names[index]
        for index, code in enumerate(original_codes)
        if index < len(original_names)
    }
    return [name_by_code.get(code, code) for code in selected_languages]


def media_translation_language_names(session: dict[str, Any]) -> dict[str, str]:
    """Auto-translate names, overlaid with names stored on the media session."""
    names = document_mt_language_names()
    codes = [str(code) for code in (session.get("target_languages") or [])]
    display = [str(name) for name in (session.get("target_language_names") or [])]
    for index, code in enumerate(codes):
        if index < len(display) and display[index]:
            names[code] = display[index]
    return names


def media_translation_language_costs(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-file/per-language cost rows for Quote2 Adjust Request."""
    return document_mt_language_costs(
        media_translation_quote_from_session(session),
        language_names=media_translation_language_names(session),
    )


def media_quote2_required_tokens(
    session: dict[str, Any], pairs: list[str]
) -> int:
    """Tokens charged at Quote2 accept for the selected pairs plus embedding."""
    from app.slack.document_mt_quote_adjustment import document_mt_tokens_for_pairs

    return (
        document_mt_tokens_for_pairs(
            media_translation_quote_from_session(session), pairs
        )
        + translated_embed_tokens_for_session(session, language_count=len(pairs))
        + source_embed_tokens_for_session(session)
    )


def media_translation_quote_has_rows(session: dict[str, Any]) -> bool:
    """True when Quote2 can render the shared AI Translate file/language grid."""
    return bool(media_translation_language_costs(session))

def media_translation_quote_uses_adjust_layout(session: dict[str, Any]) -> bool:
    """Quote2 (not Quote1) uses the shared AI Translate Accept/Adjust grid.

    In-flight Quote2 sessions without a stored ``quote`` payload still qualify
    when the stage is translation accept/translating and languages are present.
    Quote1 never stores ``quote`` and is not in those stages.
    """
    stage = session.get("stage")
    has_quote_files = isinstance(session.get("quote"), dict) and bool(
        session["quote"].get("files")
    )
    if (
        stage not in (STAGE_AWAITING_TRANSLATION_ACCEPT, STAGE_TRANSLATING)
        and not has_quote_files
    ):
        return False
    return media_translation_quote_has_rows(session)


def media_quote_message_ts(session: dict[str, Any]) -> str | None:
    """Slack message ts for the Quote2 Service Quote (session field name differs)."""
    return (
        str(session.get("quote_message_ts") or session.get("message_ts") or "") or None
    )

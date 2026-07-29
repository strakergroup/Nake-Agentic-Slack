"""Selection and pricing helpers for Document MT quote adjustment.

Mirrors the staged-evaluate AI quote adjustment contract
(``evaluation_ai_adjustment``) but sources its rows from the Document MT
quote response (``session["quote"]["files"]``) instead of Verify quote
details. Kept free of template/service imports so ``templates/blocks.py``
can build quote rows without an import cycle.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.ray.settings import get_auto_translate_languages

DOCUMENT_MT_QUOTE_ADJUST_ACTION_ID = "document_mt_quote_adjust"
DOCUMENT_MT_QUOTE_KIND = "document_mt"


def document_mt_pair_key(file_id: str, target_language: str) -> str:
    """Build the file/language selection key shared by quotes, modals and MT events."""
    return f"{file_id}:{target_language}"


def document_mt_language_names() -> dict[str, str]:
    """Map MT language codes to their display names."""
    return {code: name for code, name in get_auto_translate_languages()}


def _quote_files(quote: dict[str, Any]) -> list[dict[str, Any]]:
    return [file for file in quote.get("files") or [] if file.get("file_id")]


def document_mt_quote_details(quote: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapt quote files to the ``quote_tokens_for_pairs`` details contract."""
    details: list[dict[str, Any]] = []
    for file in _quote_files(quote):
        for target in file.get("target_languages") or []:
            details.append(
                {
                    "file_uuid": str(file["file_id"]),
                    "target_language_uuid": str(target.get("target_language") or ""),
                    "token": int(target.get("tokens") or 0),
                }
            )
    return details


def document_mt_language_costs(
    quote: dict[str, Any],
    language_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return per-file/per-language cost rows ordered by file then target."""
    names = (
        language_names if language_names is not None else document_mt_language_names()
    )
    rows: list[dict[str, Any]] = []
    for file in _quote_files(quote):
        file_uuid = str(file["file_id"])
        file_label = str(file.get("file_name") or file_uuid)
        for target in file.get("target_languages") or []:
            language_code = str(target.get("target_language") or "")
            if not language_code:
                continue
            rows.append(
                {
                    "file_uuid": file_uuid,
                    "file_label": file_label,
                    "value": language_code,
                    "label": names.get(language_code, language_code),
                    "token": int(target.get("tokens") or 0),
                }
            )
    return rows


def document_mt_all_pairs(quote: dict[str, Any]) -> list[str]:
    """Return the full file/language pair set implied by the quote."""
    return sorted(
        {
            document_mt_pair_key(str(file["file_id"]), str(target["target_language"]))
            for file in _quote_files(quote)
            for target in file.get("target_languages") or []
            if target.get("target_language")
        }
    )


def document_mt_filter_rows(
    language_costs: Iterable[dict[str, Any]],
    selected_pairs: Iterable[str],
) -> list[dict[str, Any]]:
    """Keep only cost rows whose file/language pair remains selected."""
    selected = set(selected_pairs)
    return [
        row
        for row in language_costs
        if document_mt_pair_key(
            str(row.get("file_uuid") or ""), str(row.get("value") or "")
        )
        in selected
    ]


def document_mt_language_costs_with_cancelled(
    language_costs: Iterable[dict[str, Any]],
    selected_pairs: Iterable[str],
) -> list[dict[str, Any]]:
    """Return all cost rows, marking deselected pairs as cancelled for display.

    Mirrors the staged-evaluate AI quote behaviour so Adjust Request keeps the
    full file/language grid and labels opted-out pairs instead of hiding them.
    """
    selected = set(selected_pairs)
    marked: list[dict[str, Any]] = []
    for language_cost in language_costs:
        row = dict(language_cost)
        key = document_mt_pair_key(
            str(row.get("file_uuid") or ""),
            str(row.get("value") or ""),
        )
        row["cancelled"] = bool(selected) and key not in selected
        marked.append(row)
    return marked


def _selected_file_ids(
    quote: dict[str, Any], selected_pairs: Iterable[str]
) -> set[str]:
    known_file_ids = {str(file["file_id"]) for file in _quote_files(quote)}
    selected_file_ids: set[str] = set()
    for pair in selected_pairs:
        file_id, _, _ = str(pair).partition(":")
        if file_id in known_file_ids:
            selected_file_ids.add(file_id)
    return selected_file_ids


def document_mt_tokens_for_pairs(
    quote: dict[str, Any], selected_pairs: Iterable[str]
) -> int:
    """Sum the per-row translation tokens for the selected pairs.

    Display total only: each row carries its own ceil, so this can exceed the
    aggregate quoted total by up to (targets - 1) tokens per file — the same
    presentation drift the staged evaluate AI quote already accepts.
    """
    selected = set(selected_pairs)
    return sum(
        int(detail["token"])
        for detail in document_mt_quote_details(quote)
        if document_mt_pair_key(detail["file_uuid"], detail["target_language_uuid"])
        in selected
    )


def document_mt_pdf_tokens_for_pairs(
    quote: dict[str, Any], selected_pairs: Iterable[str]
) -> int:
    """Sum PDF conversion tokens for files with at least one selected pair."""
    selected_file_ids = _selected_file_ids(quote, selected_pairs)
    return sum(
        int(file.get("pdf_conversion_tokens") or 0)
        for file in _quote_files(quote)
        if str(file["file_id"]) in selected_file_ids
    )


def document_mt_pdf_pages_for_pairs(
    quote: dict[str, Any], selected_pairs: Iterable[str]
) -> int:
    """Sum PDF conversion page counts for files with at least one selected pair."""
    selected_file_ids = _selected_file_ids(quote, selected_pairs)
    return sum(
        int(file.get("pdf_conversion_page_count") or 0)
        for file in _quote_files(quote)
        if str(file["file_id"]) in selected_file_ids
    )

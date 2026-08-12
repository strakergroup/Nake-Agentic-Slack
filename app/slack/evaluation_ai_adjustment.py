"""Selection and pricing helpers for staged AI Translation quotes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from app.slack.ai_quote_display import ai_language_cost_display_amounts
from app.slack.media_quotes import media_translation_tokens
from app.slack.templates.blocks import (
    _format_evaluate_quote_cost,
    _format_evaluate_quote_usd,
)
from app.translate import _

AI_QUOTE_ADJUST_ACTION_ID = "evaluation_ai_quote_adjust"
AI_QUOTE_ADJUST_CALLBACK_ID = "evaluation_ai_quote_adjust_submit"
AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID = "evaluation_ai_quote_language_selection"


def pair_key(file_uuid: str, language_uuid: str) -> str:
    """Build the durable file/language selection key used in quotes and modals."""
    return f"{file_uuid}:{language_uuid}"


def evaluate_upload_filename(file_data: dict[str, Any]) -> str:
    """Return the filename Verify will see after PDF-to-DOCX conversion."""
    title = str(file_data.get("title") or file_data.get("name") or "")
    if title.lower().endswith(".pdf"):
        return f"{title[:-4]}.docx"
    return title


def colliding_evaluate_upload_filenames(
    files: Iterable[dict[str, Any]],
) -> dict[str, list[str]]:
    """Group original titles that share a post-convert Verify upload name.

    PDF titles are rewritten to ``.docx``. Two files that collapse onto the
    same name (``report.pdf`` + ``report.docx``) cannot be selected
    unambiguously, and Verify rejects the create with 400.
    """
    grouped: dict[str, list[str]] = {}
    for file_data in files:
        original = str(file_data.get("title") or file_data.get("name") or "")
        if not original:
            continue
        grouped.setdefault(evaluate_upload_filename(file_data), []).append(original)
    return {
        upload_name: originals
        for upload_name, originals in grouped.items()
        if len(originals) > 1
    }


def _join_display_filenames(names: list[str]) -> str:
    bold = [f"*{name}*" for name in names]
    if len(bold) <= 1:
        return bold[0] if bold else ""
    if len(bold) == 2:
        return f"{bold[0]} and {bold[1]}"
    return ", ".join(bold[:-1]) + f", and {bold[-1]}"


def post_convert_filename_collision_message(
    collisions: dict[str, list[str]],
) -> str:
    """User-facing reason to rename files that would share an upload name."""
    lines: list[str] = []
    for upload_name, originals in collisions.items():
        lines.append(
            _(
                "We couldn't start this evaluation because {filenames} would "
                "be uploaded as {upload_name}. Rename the duplicates so each "
                "file is unique, then try again."
            ).format(
                filenames=_join_display_filenames(originals),
                upload_name=f"*{upload_name}*",
            )
        )
    return "\n".join(lines)


def filename_language_pairs_from_selection(
    files: Iterable[dict[str, Any]],
    selected_pairs: Iterable[str],
) -> list[str]:
    """Map Slack ``file_id:language`` pairs onto upload ``filename:language`` pairs."""
    files_by_id = {
        str(file_data.get("id")): evaluate_upload_filename(file_data)
        for file_data in files
        if file_data.get("id")
    }
    mapped: set[str] = set()
    for value in selected_pairs:
        split = split_pair_key(str(value))
        if not split:
            continue
        file_id, language_uuid = split
        filename = files_by_id.get(file_id)
        if filename:
            mapped.add(pair_key(filename, language_uuid))
    return sorted(mapped)


def split_pair_key(value: str) -> tuple[str, str] | None:
    """Split a `file_uuid:language_uuid` selection value."""
    if ":" not in value:
        return None
    file_uuid, language_uuid = value.split(":", 1)
    if not file_uuid or not language_uuid:
        return None
    return file_uuid, language_uuid


def file_language_pairs(
    file_uuids: Iterable[str], language_uuids: Iterable[str]
) -> list[str]:
    """Return a stable cross-product selection for the backend contract."""
    return sorted(
        {
            pair_key(file_uuid, language_uuid)
            for file_uuid in file_uuids
            for language_uuid in language_uuids
            if file_uuid and language_uuid
        }
    )


def selected_pairs_from_values(values: Iterable[str]) -> list[str]:
    """Normalize checkbox values into sorted pair keys."""
    pairs: set[str] = set()
    for value in values:
        split = split_pair_key(str(value))
        if split:
            pairs.add(pair_key(*split))
    return sorted(pairs)


def files_and_languages_from_pairs(
    pairs: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Derive selected file and language UUID lists from pair keys."""
    file_uuids: set[str] = set()
    language_uuids: set[str] = set()
    for value in pairs:
        split = split_pair_key(str(value))
        if not split:
            continue
        file_uuid, language_uuid = split
        file_uuids.add(file_uuid)
        language_uuids.add(language_uuid)
    return sorted(file_uuids), sorted(language_uuids)


def filter_language_costs_by_pairs(
    language_costs: Iterable[dict[str, Any]],
    selected_pairs: Iterable[str],
) -> list[dict[str, Any]]:
    """Keep only file/language cost rows that remain selected."""
    selected = set(selected_pairs)
    return [
        language_cost
        for language_cost in language_costs
        if pair_key(
            str(language_cost.get("file_uuid") or ""),
            str(language_cost.get("value") or ""),
        )
        in selected
    ]


def language_costs_with_cancelled_status(
    language_costs: Iterable[dict[str, Any]],
    selected_pairs: Iterable[str],
) -> list[dict[str, Any]]:
    """Return all cost rows, marking deselected pairs as cancelled for display.

    An empty selection marks every row cancelled so Adjust Request can submit a
    full opt-out as a cancelled quote.
    """
    selected = set(selected_pairs)
    marked: list[dict[str, Any]] = []
    for language_cost in language_costs:
        row = dict(language_cost)
        key = pair_key(
            str(row.get("file_uuid") or ""),
            str(row.get("value") or ""),
        )
        row["cancelled"] = key not in selected
        marked.append(row)
    return marked


def tokens_from_language_cost_rows(
    language_costs: Iterable[dict[str, Any]],
) -> int:
    """Sum token estimates from visible file/language quote rows."""
    return sum(int(row.get("token") or 0) for row in language_costs)


def quote_tokens_for_pairs(quote: dict[str, Any], pairs: Iterable[str]) -> int:
    """Sum per-pair quote tokens without trusting the aggregate quote total."""
    selected = set(pairs)
    return sum(
        int(detail.get("token") or 0)
        for detail in quote.get("details") or []
        if (
            f"{detail.get('file_uuid')}:{detail.get('target_language_uuid')}"
            in selected
        )
    )


def quote_language_costs(
    quote: dict[str, Any],
    languages: Iterable[dict[str, Any]],
    selected_language_uuids: Iterable[str] | None = None,
    language_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate pair-level AI quote tokens into target-language rows."""
    selected = (
        {str(value) for value in selected_language_uuids}
        if selected_language_uuids is not None
        else None
    )
    tokens_by_language: dict[str, int] = {}
    for detail in quote.get("details") or []:
        language_uuid = str(detail.get("target_language_uuid") or "")
        if language_uuid:
            tokens_by_language[language_uuid] = tokens_by_language.get(
                language_uuid, 0
            ) + int(detail.get("token") or 0)

    return [
        {
            "value": str(language["uuid"]),
            "label": str(
                language.get("name")
                or (language_names or {}).get(str(language["uuid"]))
                or language["uuid"]
            ),
            "token": tokens_by_language.get(str(language["uuid"]), 0),
        }
        for language in languages
        if selected is None or str(language["uuid"]) in selected
    ]


def quote_file_language_costs(
    quote: dict[str, Any],
    job_data: dict[str, Any],
    language_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return pair-level quote rows ordered by source file then target language."""
    details = {
        (
            str(detail.get("file_uuid") or ""),
            str(detail.get("target_language_uuid") or ""),
        ): int(detail.get("token") or 0)
        for detail in quote.get("details") or []
    }
    rows: list[dict[str, Any]] = []
    for source_file in job_data.get("source_files") or []:
        file_uuid = str(source_file.get("file_uuid") or "")
        if not file_uuid:
            continue
        for language in job_data.get("target_languages") or []:
            language_uuid = str(language.get("uuid") or "")
            pair = (file_uuid, language_uuid)
            if pair not in details:
                continue
            rows.append(
                {
                    "file_uuid": file_uuid,
                    "file_label": str(
                        source_file.get("filename")
                        or source_file.get("title")
                        or file_uuid
                    ),
                    "value": language_uuid,
                    "label": str(
                        language.get("name")
                        or (language_names or {}).get(language_uuid)
                        or language_uuid
                    ),
                    "token": details[pair],
                }
            )
    return rows


def estimated_pdf_language_costs(
    files: list[dict[str, Any]],
    languages: Iterable[dict[str, Any]],
    selected_language_uuids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return one extract-priced row per selected target language."""
    selected = (
        {str(value) for value in selected_language_uuids}
        if selected_language_uuids is not None
        else None
    )
    character_count = sum(int(file.get("character_count") or 0) for file in files)
    tokens_per_language = media_translation_tokens(character_count, 1)
    return [
        {
            "value": str(language["value"]),
            "label": str(language["label"]),
            "token": tokens_per_language,
        }
        for language in languages
        if selected is None or str(language["value"]) in selected
    ]


def estimated_pdf_file_language_costs(
    files: list[dict[str, Any]],
    languages: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return extract-priced rows grouped by file and language."""
    language_list = list(languages)
    rows: list[dict[str, Any]] = []
    for file_data in files:
        tokens_per_language = media_translation_tokens(
            int(file_data.get("character_count") or 0),
            1,
        )
        for language in language_list:
            rows.append(
                {
                    "file_uuid": str(file_data["id"]),
                    "file_label": str(
                        file_data.get("title")
                        or file_data.get("name")
                        or file_data["id"]
                    ),
                    "value": str(language["value"]),
                    "label": str(language["label"]),
                    "token": tokens_per_language,
                }
            )
    return rows


def ai_scope_from_job(job_data: dict[str, Any]) -> list[str]:
    """Resolve the durable AI scope, falling back to the full job cross-product."""
    extra_info = job_data.get("extra_info") or {}
    stored = extra_info.get("ai_translation_file_and_languages")
    if isinstance(stored, list) and stored:
        return sorted({str(value) for value in stored if value})
    return file_language_pairs(
        [
            str(source_file["file_uuid"])
            for source_file in job_data.get("source_files") or []
        ],
        [str(language["uuid"]) for language in job_data.get("target_languages") or []],
    )


def filter_job_to_pairs(
    job_data: dict[str, Any], selected_pairs: Iterable[str]
) -> dict[str, Any]:
    """Copy a job while hiding files/languages outside the selected pair scope."""
    selected = set(selected_pairs)
    filtered = deepcopy(job_data)
    selected_file_uuids = {pair.split(":", 1)[0] for pair in selected}
    selected_language_uuids = {pair.split(":", 1)[1] for pair in selected}
    filtered["target_languages"] = [
        language
        for language in filtered.get("target_languages") or []
        if str(language.get("uuid")) in selected_language_uuids
    ]
    filtered_files: list[dict[str, Any]] = []
    for source_file in filtered.get("source_files") or []:
        file_uuid = str(source_file.get("file_uuid"))
        if file_uuid not in selected_file_uuids:
            continue
        if "target_files" in source_file:
            source_file["target_files"] = [
                target_file
                for target_file in source_file.get("target_files") or []
                if f"{file_uuid}:{target_file.get('language_uuid')}" in selected
            ]
        filtered_files.append(source_file)
    filtered["source_files"] = filtered_files
    return filtered


def mark_out_of_scope_pairs_cancelled(
    job_data: dict[str, Any],
    selected_pairs: Iterable[str],
    *,
    cancelled_status: str = "Cancelled",
) -> dict[str, Any]:
    """Copy a job and mark file/language pairs outside the scope as Cancelled.

    Unlike ``filter_job_to_pairs``, keeps the full target-language list so quote
    blocks can show Cancelled rows instead of re-expanding a file×language grid.
    """
    selected = {str(pair) for pair in selected_pairs if pair}
    marked = deepcopy(job_data)
    language_uuids = [
        str(language.get("uuid"))
        for language in marked.get("target_languages") or []
        if language.get("uuid")
    ]
    for source_file in marked.get("source_files") or []:
        file_uuid = str(source_file.get("file_uuid") or "")
        if not file_uuid:
            continue
        by_language = {
            str(target.get("language_uuid")): dict(target)
            for target in source_file.get("target_files") or []
            if target.get("language_uuid")
        }
        target_files: list[dict[str, Any]] = []
        for language_uuid in language_uuids:
            target = by_language.get(language_uuid, {"language_uuid": language_uuid})
            pair = f"{file_uuid}:{language_uuid}"
            if pair in selected:
                target.pop("human_job_status", None)
            else:
                target["human_job_status"] = cancelled_status
            target_files.append(target)
        source_file["target_files"] = target_files
    return marked


def pdf_adjusted_costs(
    session: dict[str, Any],
    selected_file_ids: Iterable[str],
    selected_language_uuids: Iterable[str],
) -> tuple[int, int]:
    """Return AI tokens and PDF page count for a pre-job selection."""
    selected_files = set(selected_file_ids)
    selected_languages = list(selected_language_uuids)
    files = [
        file_data
        for file_data in session.get("files") or []
        if str(file_data.get("id")) in selected_files
    ]
    pdf_page_count = sum(
        int(file_data.get("pdf_page_count") or 0) for file_data in files
    )
    target_count = len(selected_languages)
    ai_token_estimate = sum(
        media_translation_tokens(
            int(file_data.get("character_count") or 0),
            target_count,
        )
        for file_data in files
        if target_count
    )
    return ai_token_estimate, pdf_page_count


def pdf_costs_for_pairs(
    session: dict[str, Any],
    selected_pairs: Iterable[str],
) -> tuple[int, int]:
    """Return AI tokens and PDF page count for selected file/language pairs."""
    pairs = selected_pairs_from_values(selected_pairs)
    selected_file_ids, selected_language_uuids = files_and_languages_from_pairs(pairs)
    files = [
        file_data
        for file_data in session.get("files") or []
        if str(file_data.get("id")) in selected_file_ids
    ]
    pdf_page_count = sum(
        int(file_data.get("pdf_page_count") or 0) for file_data in files
    )
    language_costs = filter_language_costs_by_pairs(
        session.get("all_language_costs") or session.get("language_costs") or [],
        pairs,
    )
    if language_costs:
        return tokens_from_language_cost_rows(language_costs), pdf_page_count
    if any("character_count" in file_data for file_data in files):
        langs_by_file: dict[str, list[str]] = {}
        for pair in pairs:
            file_id, _, language_uuid = str(pair).partition(":")
            if not file_id or not language_uuid:
                continue
            langs_by_file.setdefault(file_id, []).append(language_uuid)
        ai_token_estimate = sum(
            media_translation_tokens(
                int(file_data.get("character_count") or 0),
                len(langs_by_file.get(str(file_data.get("id")), [])),
            )
            for file_data in files
            if langs_by_file.get(str(file_data.get("id")))
        )
        return ai_token_estimate, pdf_page_count
    return pdf_adjusted_costs(
        session,
        selected_file_ids,
        selected_language_uuids,
    )


def selected_values(view: dict[str, Any], action_id: str) -> list[str]:
    """Collect checkbox values for one action from a Slack modal state."""
    values: list[str] = []
    for block_data in (view.get("state") or {}).get("values", {}).values():
        action_data = block_data.get(action_id)
        if not action_data:
            continue
        values.extend(
            str(option["value"])
            for option in action_data.get("selected_options") or []
            if option.get("value")
        )
    return sorted(set(values))


def selected_pairs_from_view(view: dict[str, Any]) -> list[str]:
    """Read currently selected file/language pairs from a modal view."""
    return selected_pairs_from_values(
        selected_values(view, AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID)
    )


def quote_message_context_from_body(
    body: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Resolve the original quote channel and message ts from a Slack payload."""
    message = body.get("message") or {}
    container = body.get("container") or {}
    channel = body.get("channel") or {}
    message_ts = message.get("ts") or container.get("message_ts")
    channel_id = channel.get("id") or container.get("channel_id")
    return (
        str(channel_id) if channel_id else None,
        str(message_ts) if message_ts else None,
    )


def job_adjustment_options(
    job_data: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    files = [
        {
            "value": str(source_file["file_uuid"]),
            "label": str(source_file.get("filename") or source_file["file_uuid"]),
        }
        for source_file in job_data.get("source_files") or []
    ]
    languages = [
        {
            "value": str(language["uuid"]),
            "label": str(language.get("name") or language["uuid"]),
        }
        for language in job_data.get("target_languages") or []
    ]
    return files, languages


def sync_checkbox_initial_options_from_state(view: dict[str, Any]) -> None:
    """Rewrite checkbox ``initial_options`` from the live modal state.

    Slack ``views.update`` replays block ``initial_options``. If those still
    reflect the originally opened selection, a cost refresh after deselection
    re-checks every file/language and Accept Quote submits the full set.
    """
    selected = set(selected_pairs_from_view(view))
    for block in view.get("blocks") or []:
        for element in block.get("elements") or []:
            if element.get("type") != "checkboxes":
                continue
            if element.get("action_id") != AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID:
                continue
            options = element.get("options") or []
            initial_options = [
                option
                for option in options
                if str(option.get("value") or "") in selected
            ]
            if initial_options:
                element["initial_options"] = initial_options
            else:
                element.pop("initial_options", None)


def update_modal_cost_blocks(
    view: dict[str, Any],
    *,
    ai_tokens: int,
    pdf_tokens: int,
    language_costs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Update cost blocks and keep checkbox initial options in sync with state.

    When ``language_costs`` is provided, checkbox labels redistribute the
    charged AI total across the currently selected pairs.
    """
    sync_checkbox_initial_options_from_state(view)
    selected = set(selected_pairs_from_view(view))
    display_by_pair: dict[str, float] = {}
    if language_costs is not None:
        for row, amount in zip(
            language_costs,
            ai_language_cost_display_amounts(
                ai_tokens,
                language_costs,
                selected_pairs=selected,
            ),
            strict=True,
        ):
            file_uuid = str(row.get("file_uuid") or "")
            language_uuid = str(row.get("value") or "")
            key = (
                f"{file_uuid}:{language_uuid}"
                if file_uuid and language_uuid
                else language_uuid
            )
            display_by_pair[key] = 0.0 if amount is None else amount
    for block in view.get("blocks") or []:
        block_id = block.get("block_id")
        if block_id == "ai_quote_pdf_cost_block":
            block["text"]["text"] = (
                f"*{_('PDF conversion')}:* {_format_evaluate_quote_cost(pdf_tokens)}"
            )
        elif block_id == "total_cost_block":
            block["text"]["text"] = (
                f"*{_('Total cost')}:* "
                f"{_format_evaluate_quote_cost(ai_tokens + pdf_tokens)}"
            )
        if not display_by_pair:
            continue
        for element in block.get("elements") or []:
            if element.get("type") != "checkboxes":
                continue
            if element.get("action_id") != AI_QUOTE_LANGUAGE_SELECTION_ACTION_ID:
                continue
            for option in element.get("options") or []:
                value = str(option.get("value") or "")
                if value not in display_by_pair:
                    continue
                label = str((option.get("text") or {}).get("text") or "")
                # Preserve "*Label*: …" prefix when rewriting the amount.
                prefix = label.split(":", 1)[0] if ":" in label else label
                option["text"] = {
                    "type": "mrkdwn",
                    "text": (
                        f"{prefix}: {_format_evaluate_quote_usd(display_by_pair[value])}"
                    ),
                }
            if element.get("initial_options"):
                option_by_value = {
                    str(option.get("value") or ""): option
                    for option in element.get("options") or []
                }
                element["initial_options"] = [
                    option_by_value[str(option.get("value") or "")]
                    for option in element["initial_options"]
                    if str(option.get("value") or "") in option_by_value
                ]
    return {
        key: view[key]
        for key in (
            "type",
            "title",
            "blocks",
            "close",
            "submit",
            "private_metadata",
            "callback_id",
        )
        if key in view
    }

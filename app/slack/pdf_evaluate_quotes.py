"""Pre-job Slack PDF evaluate quote sessions.

These sessions exist before cloud-verify-api has an evaluation job UUID. They
let Slack show an AI Translation + PDF Conversion quote without paying the
Adobe PDF-to-DOCX conversion cost or creating evaluate segments.

AI tokens come from the same consumer extract path as Document MT
(``slack:job:machine:translate:quote`` with ``quote_mode`` / M48 extract on the
original PDF). The priced callback uses
``verify:slack:evaluate:pdf:quote`` so Slack renders the evaluate/HT Service
Quote UI rather than the Document MT quote message.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.config import config
from app.constants import EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
from app.redis import redis_conn
from app.slack.templates.messages import EvaluationCreditsQuoteMessage
from app.translate import _

PDF_EVALUATE_QUOTE_KEY_PREFIX = "slack-ray-translator:pdf-evaluate-quote:"
PDF_EVALUATE_QUOTE_ACTION_ID = "evaluation_pdf_prequote_accept"
PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID = "evaluation_pdf_prequote_adjust"

STAGE_QUOTE_PENDING = "quote_pending"
STAGE_AWAITING_ACCEPT = "awaiting_accept"
STAGE_PROCESSING_ACCEPT = "processing_accept"
STAGE_ACCEPTED = "accepted"


def _quote_key(quote_id: str) -> str:
    return f"{PDF_EVALUATE_QUOTE_KEY_PREFIX}{quote_id}"


async def save_pdf_evaluate_quote_session(
    *,
    quote_id: str | None = None,
    channel_id: str,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    files: list[dict[str, Any]],
    target_langs_uuid: list[str],
    reference: str,
    source_lang_uuid: str,
    workflow_uuid: str | None,
    job_notes: str,
    ai_token_estimate: int = 0,
    pdf_page_count: int = 0,
    language_costs: list[dict[str, Any]] | None = None,
    language_uuid_by_code: dict[str, str] | None = None,
    language_names: dict[str, str] | None = None,
    message_ts: str | None = None,
    stage: str = STAGE_QUOTE_PENDING,
) -> str:
    quote_id = quote_id or str(uuid.uuid4())
    payload = {
        "quote_id": quote_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "team_id": team_id,
        "enterprise_id": enterprise_id,
        "files": files,
        "target_langs_uuid": target_langs_uuid,
        "selected_file_ids": [str(file_data["id"]) for file_data in files],
        "selected_target_langs_uuid": list(target_langs_uuid),
        "reference": reference,
        "source_lang_uuid": source_lang_uuid,
        "workflow_uuid": workflow_uuid,
        "job_notes": job_notes,
        "ai_token_estimate": ai_token_estimate,
        "pdf_page_count": pdf_page_count,
        "pdf_tokens": pdf_page_count * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
        "language_costs": language_costs or [],
        "all_language_costs": language_costs or [],
        "language_uuid_by_code": language_uuid_by_code or {},
        "language_names": language_names or {},
        "message_ts": message_ts,
        "stage": stage,
    }
    await redis_conn.set(
        _quote_key(quote_id),
        json.dumps(payload),
        ex=config.evaluate_quote_ttl_seconds,
    )
    return quote_id


async def get_pdf_evaluate_quote_session(quote_id: str) -> dict[str, Any] | None:
    raw = await redis_conn.get(_quote_key(quote_id))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


async def update_pdf_evaluate_quote_session(
    quote_id: str,
    **updates: Any,
) -> None:
    session = await get_pdf_evaluate_quote_session(quote_id)
    if not session:
        return
    session.update(updates)
    if "pdf_page_count" in updates and "pdf_tokens" not in updates:
        session["pdf_tokens"] = (
            int(session.get("pdf_page_count") or 0)
            * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
        )
    await redis_conn.set(
        _quote_key(quote_id),
        json.dumps(session),
        ex=config.evaluate_quote_ttl_seconds,
    )


def _resolve_language_uuid(
    language_uuid_by_code: dict[str, str],
    target_language: str,
) -> str | None:
    code = str(target_language or "").strip()
    if not code:
        return None
    if code in language_uuid_by_code:
        return language_uuid_by_code[code]
    lower = code.lower()
    for key, value in language_uuid_by_code.items():
        if str(key).lower() == lower:
            return value
    return None


def build_pdf_evaluate_language_costs(
    *,
    session_files: list[dict[str, Any]],
    quote_files: list[dict[str, Any]],
    language_uuid_by_code: dict[str, str],
    language_names: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Map consumer extract quote files onto Slack file/language UUID rows."""
    files_by_gridfs = {
        str(
            file_data.get("gridfs_file_id") or file_data.get("file_id") or ""
        ): file_data
        for file_data in session_files
        if file_data.get("gridfs_file_id") or file_data.get("file_id")
    }
    language_costs: list[dict[str, Any]] = []
    pdf_page_count = 0
    updated_files: list[dict[str, Any]] = []
    seen_slack_ids: set[str] = set()

    for quote_file in quote_files:
        gridfs_id = str(quote_file.get("file_id") or "")
        file_meta = dict(files_by_gridfs.get(gridfs_id) or {})
        if not file_meta:
            continue
        pages = int(quote_file.get("pdf_conversion_page_count") or 0)
        file_meta["pdf_page_count"] = pages
        file_meta["character_count"] = int(quote_file.get("character_count") or 0)
        pdf_page_count += pages
        seen_slack_ids.add(str(file_meta.get("id") or ""))
        updated_files.append(file_meta)
        slack_file_id = str(file_meta.get("id") or "")
        file_label = str(
            file_meta.get("title") or file_meta.get("file_name") or slack_file_id
        )
        for target in quote_file.get("target_languages") or []:
            target_code = str(target.get("target_language") or "")
            language_uuid = _resolve_language_uuid(language_uuid_by_code, target_code)
            if not language_uuid:
                continue
            language_costs.append(
                {
                    "file_uuid": slack_file_id,
                    "file_label": file_label,
                    "value": language_uuid,
                    "label": language_names.get(language_uuid, target_code),
                    "token": int(target.get("tokens") or 0),
                }
            )

    # Preserve session files that the quote response omitted.
    for file_meta in session_files:
        if str(file_meta.get("id") or "") not in seen_slack_ids:
            updated_files.append(file_meta)

    return language_costs, updated_files, pdf_page_count


async def apply_pdf_evaluate_quote_result(
    quote_id: str,
    quote_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Persist extract-priced totals onto the evaluate PDF quote session."""
    session = await get_pdf_evaluate_quote_session(quote_id)
    if not session:
        return None

    language_costs, updated_files, pdf_page_count = build_pdf_evaluate_language_costs(
        session_files=list(session.get("files") or []),
        quote_files=list(quote_data.get("files") or []),
        language_uuid_by_code={
            str(key): str(value)
            for key, value in (session.get("language_uuid_by_code") or {}).items()
        },
        language_names={
            str(key): str(value)
            for key, value in (session.get("language_names") or {}).items()
        },
    )
    pdf_tokens = int(quote_data.get("pdf_conversion_tokens") or 0)
    total_tokens = int(quote_data.get("total_tokens") or 0)
    ai_token_estimate = max(total_tokens - pdf_tokens, 0)
    if ai_token_estimate == 0 and language_costs:
        # Fallback when an older consumer omits aggregate totals.
        ai_token_estimate = sum(int(row.get("token") or 0) for row in language_costs)

    await update_pdf_evaluate_quote_session(
        quote_id,
        files=updated_files,
        language_costs=language_costs,
        all_language_costs=language_costs,
        ai_token_estimate=ai_token_estimate,
        pdf_page_count=pdf_page_count,
        pdf_tokens=pdf_tokens
        or (pdf_page_count * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE),
        quote=quote_data,
        preflight_task_uuid=quote_data.get("preflight_task_uuid"),
        stage=STAGE_AWAITING_ACCEPT,
    )
    return await get_pdf_evaluate_quote_session(quote_id)


def evaluation_pdf_quote_message(
    session: dict[str, Any],
    *,
    actions: bool = True,
    status_message: str | None = None,
    is_ibm: bool = False,
) -> EvaluationCreditsQuoteMessage:
    return EvaluationCreditsQuoteMessage(
        service_label=_("AI Translation"),
        token_cost=int(session.get("ai_token_estimate") or 0),
        job_uuid=str(session.get("quote_id") or ""),
        accept_action_id=PDF_EVALUATE_QUOTE_ACTION_ID,
        adjust_action_id=PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID,
        pdf_page_count=int(session.get("pdf_page_count") or 0) or None,
        pdf_tokens=int(session.get("pdf_tokens") or 0) or None,
        actions=actions,
        status_message=status_message,
        is_ibm=is_ibm,
        language_costs=list(session.get("language_costs") or []),
    )


async def post_pdf_evaluate_quote_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    session: dict[str, Any],
    is_ibm: bool = False,
) -> str | None:
    """Post or update the priced PDF Service Quote and remember its timestamp."""
    message = evaluation_pdf_quote_message(session, actions=True, is_ibm=is_ibm)
    existing_message_ts = str(session.get("message_ts") or "")
    if existing_message_ts:
        await client.chat_update(
            channel=channel_id,
            ts=existing_message_ts,
            text=message.text,
            blocks=message.blocks,
        )
        return existing_message_ts
    response = await client.chat_postMessage(
        channel=channel_id,
        text=message.text,
        blocks=message.blocks,
    )
    message_ts = response.get("ts")
    quote_id = str(session.get("quote_id") or "")
    if message_ts and quote_id:
        await update_pdf_evaluate_quote_session(quote_id, message_ts=message_ts)
    return message_ts


async def update_pdf_evaluate_quote_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str,
    quote_id: str,
    ai_token_estimate: int,
    pdf_page_count: int,
    status_message: str | None,
    actions: bool,
    is_ibm: bool = False,
) -> None:
    session = await get_pdf_evaluate_quote_session(quote_id)
    has_selected_pairs = "selected_pairs" in (session or {})
    selected_pairs = {
        str(value) for value in (session or {}).get("selected_pairs") or [] if value
    }
    selected_languages = {
        str(value) for value in (session or {}).get("selected_target_langs_uuid") or []
    }
    language_costs = []
    for language_cost in (session or {}).get("language_costs") or []:
        row = dict(language_cost)
        pair = (
            f"{row.get('file_uuid')}:{row.get('value')}"
            if row.get("file_uuid")
            else None
        )
        if actions:
            if selected_pairs:
                if pair not in selected_pairs:
                    continue
            elif selected_languages and str(row.get("value")) not in selected_languages:
                continue
        elif has_selected_pairs:
            # Explicit empty selection (full opt-out) marks every row cancelled.
            row["cancelled"] = bool(pair) and pair not in selected_pairs
        elif selected_languages:
            row["cancelled"] = str(row.get("value")) not in selected_languages
        language_costs.append(row)
    message = EvaluationCreditsQuoteMessage(
        service_label=_("AI Translation"),
        token_cost=ai_token_estimate,
        job_uuid=quote_id,
        accept_action_id=PDF_EVALUATE_QUOTE_ACTION_ID,
        adjust_action_id=PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID,
        pdf_page_count=pdf_page_count,
        pdf_tokens=pdf_page_count * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
        actions=actions,
        status_message=status_message,
        is_ibm=is_ibm,
        language_costs=language_costs,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.text,
        blocks=message.blocks,
    )


async def restore_pdf_evaluate_quote_for_retry(
    client: AsyncWebClient,
    *,
    quote_id: str | None,
    channel_id: str | None,
    message_ts: str | None,
    is_ibm: bool = False,
    status_message: str | None = None,
) -> bool:
    """Return an accepted PDF quote to an acceptable state after a failed submit.

    The accept handler stages the quote and strips its buttons before handing
    off to the durable submission job. If that job cannot submit anything, the
    message otherwise sits on "Converting PDF..." forever with no way to retry.
    """
    if not quote_id:
        return False
    session = await get_pdf_evaluate_quote_session(quote_id)
    if not session:
        return False

    await update_pdf_evaluate_quote_session(quote_id, stage=STAGE_AWAITING_ACCEPT)
    if not channel_id or not message_ts:
        return False

    await update_pdf_evaluate_quote_message(
        client,
        channel_id=channel_id,
        message_ts=message_ts,
        quote_id=quote_id,
        ai_token_estimate=int(session.get("ai_token_estimate") or 0),
        pdf_page_count=int(session.get("pdf_page_count") or 0),
        actions=True,
        status_message=status_message
        or _("We could not start this translation. Please try again."),
        is_ibm=is_ibm,
    )
    return True

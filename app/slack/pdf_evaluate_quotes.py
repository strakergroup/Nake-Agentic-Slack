"""Pre-job Slack PDF evaluate quote sessions.

These sessions exist before cloud-verify-api has an evaluation job UUID. They
let Slack show an AI Translation + PDF Conversion quote without
paying the Adobe PDF-to-DOCX conversion cost or creating evaluate segments.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any

import PyPDF2
from slack_sdk.web.async_client import AsyncWebClient

from app.config import config
from app.constants import EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
from app.redis import redis_conn
from app.slack.templates.messages import EvaluationCreditsQuoteMessage
from app.translate import _

PDF_EVALUATE_QUOTE_KEY_PREFIX = "slack-ray-translator:pdf-evaluate-quote:"
PDF_EVALUATE_QUOTE_ACTION_ID = "evaluation_pdf_prequote_accept"
PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID = "evaluation_pdf_prequote_adjust"

STAGE_AWAITING_ACCEPT = "awaiting_accept"
STAGE_PROCESSING_ACCEPT = "processing_accept"
STAGE_ACCEPTED = "accepted"

# Match the current Slack MT balance gate estimate. The exact charge is still
# calculated from extracted reports after the user accepts.
ESTIMATED_AI_TRANSLATION_TOKENS_PER_BYTE = 0.002


def _quote_key(quote_id: str) -> str:
    return f"{PDF_EVALUATE_QUOTE_KEY_PREFIX}{quote_id}"


def pdf_page_count_from_file(file_path: str) -> int:
    reader = PyPDF2.PdfReader(file_path)
    return len(reader.pages)


def estimate_pdf_evaluate_ai_tokens(
    files: list[dict[str, Any]],
    target_language_count: int,
) -> int:
    tokens_per_language = sum(
        math.ceil(int(file.get("size") or 0) * ESTIMATED_AI_TRANSLATION_TOKENS_PER_BYTE)
        for file in files
        if isinstance(file.get("size"), int) or str(file.get("size") or "").isdigit()
    )
    return tokens_per_language * max(target_language_count, 1)


async def save_pdf_evaluate_quote_session(
    *,
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
    ai_token_estimate: int,
    pdf_page_count: int,
    language_costs: list[dict[str, Any]] | None = None,
    message_ts: str | None = None,
    stage: str = STAGE_AWAITING_ACCEPT,
) -> str:
    quote_id = str(uuid.uuid4())
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
    await redis_conn.set(
        _quote_key(quote_id),
        json.dumps(session),
        ex=config.evaluate_quote_ttl_seconds,
    )


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

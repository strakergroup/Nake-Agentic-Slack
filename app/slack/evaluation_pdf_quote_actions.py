from __future__ import annotations

import json
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.saq_jobs import enqueue_evaluation_submission
from app.slack.buglog_notifier import notify_exception
from app.slack.evaluation_ai_adjustment import (
    filename_language_pairs_from_selection,
    selected_pairs_from_values,
)
from app.slack.pdf_evaluate_quotes import (
    STAGE_ACCEPTED as PDF_PREQUOTE_STAGE_ACCEPTED,
)
from app.slack.pdf_evaluate_quotes import (
    STAGE_AWAITING_ACCEPT as PDF_PREQUOTE_STAGE_AWAITING_ACCEPT,
)
from app.slack.pdf_evaluate_quotes import (
    STAGE_PROCESSING_ACCEPT as PDF_PREQUOTE_STAGE_PROCESSING_ACCEPT,
)
from app.slack.pdf_evaluate_quotes import (
    get_pdf_evaluate_quote_session,
    update_pdf_evaluate_quote_message,
    update_pdf_evaluate_quote_session,
)
from app.translate import _


async def accept_pdf_evaluate_quote(
    client: AsyncWebClient,
    body: dict[str, Any],
    quote_id: str,
    context: RayContext,
    selected_pairs_override: list[str] | None = None,
) -> None:
    """Accept a pre-job PDF evaluate quote and start real processing."""
    session = await get_pdf_evaluate_quote_session(quote_id)
    if not session:
        return
    if session.get("stage") in {
        PDF_PREQUOTE_STAGE_PROCESSING_ACCEPT,
        PDF_PREQUOTE_STAGE_ACCEPTED,
    }:
        return

    lock_key = f"evaluate_pdf_prequote_accept_{quote_id}"
    lock_acquired = await redis_conn.set(lock_key, "1", ex=300, nx=True)
    if not lock_acquired:
        return

    channel_id = str(session.get("channel_id") or context.get("channel_id") or "")
    message_ts = session.get("message_ts") or body.get("message", {}).get("ts")
    private_metadata_raw = (body.get("view") or {}).get("private_metadata")
    if isinstance(private_metadata_raw, str) and private_metadata_raw:
        try:
            metadata = json.loads(private_metadata_raw)
        except json.JSONDecodeError:
            metadata = {}
        if isinstance(metadata, dict):
            if metadata.get("message_ts"):
                message_ts = str(metadata["message_ts"])
            if metadata.get("channel_id"):
                channel_id = str(metadata["channel_id"])
    ai_token_estimate = int(session.get("ai_token_estimate") or 0)
    pdf_page_count = int(session.get("pdf_page_count") or 0)
    selected_pairs = selected_pairs_from_values(selected_pairs_override or []) or [
        str(value) for value in session.get("selected_pairs") or [] if value
    ]
    if selected_pairs:
        selected_file_ids = {pair.split(":", 1)[0] for pair in selected_pairs}
        selected_target_langs_uuid = sorted(
            {pair.split(":", 1)[1] for pair in selected_pairs}
        )
    else:
        selected_file_ids = {
            str(value)
            for value in session.get("selected_file_ids")
            or [file_data["id"] for file_data in session.get("files") or []]
        }
        selected_target_langs_uuid = [
            str(value)
            for value in session.get("selected_target_langs_uuid")
            or session.get("target_langs_uuid")
            or []
        ]
    selected_files = [
        file_data
        for file_data in session.get("files") or []
        if str(file_data.get("id")) in selected_file_ids
    ]
    if not selected_files or not selected_target_langs_uuid:
        await redis_conn.delete(lock_key)
        return
    context_enterprise_id = (
        context.get("enterprise_id") if hasattr(context, "get") else None
    ) or getattr(context, "enterprise_id", None)
    is_ibm = is_ibm_enterprise(session.get("enterprise_id") or context_enterprise_id)

    try:
        await update_pdf_evaluate_quote_session(
            quote_id,
            stage=PDF_PREQUOTE_STAGE_PROCESSING_ACCEPT,
        )
        if channel_id and message_ts:
            await update_pdf_evaluate_quote_message(
                client,
                channel_id=channel_id,
                message_ts=message_ts,
                quote_id=quote_id,
                ai_token_estimate=ai_token_estimate,
                pdf_page_count=pdf_page_count,
                actions=False,
                status_message=_(
                    "Quote accepted. Converting PDF and running AI translation..."
                ),
                is_ibm=is_ibm,
            )

        await enqueue_evaluation_submission(
            user_id=str(session["user_id"]),
            team_id=str(session["team_id"]),
            enterprise_id=session.get("enterprise_id"),
            channel_id=channel_id,
            files=selected_files,
            target_langs_uuid=selected_target_langs_uuid,
            reference=str(session["reference"]),
            source_lang_uuid=str(session["source_lang_uuid"]),
            workflow_uuid=session.get("workflow_uuid"),
            job_notes=str(session.get("job_notes") or ""),
            preaccepted_ai_translation_quote=True,
            prequote_message_ts=str(message_ts) if message_ts else None,
            ai_translation_filename_and_languages=filename_language_pairs_from_selection(
                selected_files,
                selected_pairs,
            ),
        )
        await update_pdf_evaluate_quote_session(
            quote_id,
            stage=PDF_PREQUOTE_STAGE_ACCEPTED,
        )
    except Exception as e:
        notify_exception(e)
        await update_pdf_evaluate_quote_session(
            quote_id,
            stage=PDF_PREQUOTE_STAGE_AWAITING_ACCEPT,
        )
        if channel_id and message_ts:
            await update_pdf_evaluate_quote_message(
                client,
                channel_id=channel_id,
                message_ts=message_ts,
                quote_id=quote_id,
                ai_token_estimate=ai_token_estimate,
                pdf_page_count=pdf_page_count,
                actions=True,
                status_message=_(
                    "There was an error accepting your quote. Please try again."
                ),
                is_ibm=is_ibm,
            )
        await redis_conn.delete(lock_key)

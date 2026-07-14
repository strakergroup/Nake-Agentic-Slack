"""Persistence for staged AI quote adjustment modal submissions."""

from __future__ import annotations

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.constants import EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
from app.ray.utils import is_ibm_enterprise
from app.slack.evaluation_ai_adjustment import (
    AI_QUOTE_ADJUST_ACTION_ID,
    files_and_languages_from_pairs,
    filter_language_costs_by_pairs,
    pdf_costs_for_pairs,
    quote_tokens_for_pairs,
    selected_pairs_from_values,
)
from app.slack.evaluation_quotes import (
    STAGE_AWAITING_AI,
    get_evaluate_quote_session,
    update_evaluate_quote_session,
    update_evaluate_quote_slack_message,
)
from app.slack.pdf_evaluate_quotes import (
    STAGE_AWAITING_ACCEPT as PDF_PREQUOTE_STAGE_AWAITING_ACCEPT,
)
from app.slack.pdf_evaluate_quotes import (
    get_pdf_evaluate_quote_session,
    update_pdf_evaluate_quote_message,
    update_pdf_evaluate_quote_session,
)
from app.translate import _


async def persist_ai_quote_adjustment(
    client: AsyncWebClient,
    *,
    quote_id: str,
    quote_kind: str,
    selected_pairs: list[str],
    user_id: str,
    context: RayContext,
    channel_id: str | None = None,
    message_ts: str | None = None,
) -> bool:
    """Persist selection and refresh the original quote if it is still adjustable."""
    pairs = selected_pairs_from_values(selected_pairs)
    if quote_kind == "pdf_prequote":
        session = await get_pdf_evaluate_quote_session(quote_id)
        if (
            not session
            or session.get("user_id") != user_id
            or session.get("stage") != PDF_PREQUOTE_STAGE_AWAITING_ACCEPT
        ):
            return False
        selected_file_ids, selected_language_uuids = files_and_languages_from_pairs(
            pairs
        )
        ai_tokens, pdf_page_count = pdf_costs_for_pairs(session, pairs)
        pdf_tokens = pdf_page_count * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
        resolved_channel_id = str(
            channel_id or session.get("channel_id") or context.get("channel_id") or ""
        )
        resolved_message_ts = str(message_ts or session.get("message_ts") or "") or None
        await update_pdf_evaluate_quote_session(
            quote_id,
            selected_file_ids=selected_file_ids,
            selected_target_langs_uuid=selected_language_uuids,
            selected_pairs=pairs,
            ai_token_estimate=ai_tokens,
            pdf_page_count=pdf_page_count,
            pdf_tokens=pdf_tokens,
            channel_id=resolved_channel_id or session.get("channel_id"),
            message_ts=resolved_message_ts or session.get("message_ts"),
        )
        if resolved_channel_id and resolved_message_ts:
            await update_pdf_evaluate_quote_message(
                client,
                channel_id=resolved_channel_id,
                message_ts=resolved_message_ts,
                quote_id=quote_id,
                ai_token_estimate=ai_tokens,
                pdf_page_count=pdf_page_count,
                actions=True,
                status_message=None,
                is_ibm=is_ibm_enterprise(session.get("enterprise_id")),
            )
        return True

    session = await get_evaluate_quote_session(quote_id)
    if (
        not session
        or session.get("user_id") != user_id
        or session.get("stage") != STAGE_AWAITING_AI
    ):
        return False
    quote_snapshot = dict(session.get("quote_snapshot") or {})
    ai_tokens = quote_tokens_for_pairs(
        {"details": quote_snapshot.get("ai_quote_details") or []},
        pairs,
    )
    all_language_costs = (
        quote_snapshot.get("all_language_costs")
        or quote_snapshot.get("language_costs")
        or []
    )
    language_costs = filter_language_costs_by_pairs(all_language_costs, pairs)
    quote_snapshot.update(
        {
            "token_cost": ai_tokens,
            "ai_translation_file_and_languages": pairs,
            "all_language_costs": all_language_costs,
            "language_costs": language_costs,
        }
    )
    resolved_channel_id = str(
        channel_id or session.get("channel_id") or context.get("channel_id") or ""
    )
    resolved_message_ts = str(message_ts or session.get("message_ts") or "") or None
    session_updates: dict = {"quote_snapshot": quote_snapshot}
    if resolved_channel_id:
        session_updates["channel_id"] = resolved_channel_id
    if resolved_message_ts:
        session_updates["message_ts"] = resolved_message_ts
    await update_evaluate_quote_session(quote_id, session_updates)
    if resolved_channel_id and resolved_message_ts:
        await update_evaluate_quote_slack_message(
            client,
            channel_id=resolved_channel_id,
            message_ts=resolved_message_ts,
            service_label=str(
                quote_snapshot.get("service_label") or _("AI Translation")
            ),
            token_cost=ai_tokens,
            job_uuid=quote_id,
            accept_action_id=str(
                quote_snapshot.get("accept_action_id") or "evaluation_ai_quote_accept"
            ),
            adjust_action_id=AI_QUOTE_ADJUST_ACTION_ID,
            pdf_page_count=session.get("pdf_page_count"),
            pdf_tokens=quote_snapshot.get("pdf_tokens"),
            actions=True,
            status_message=None,
            is_ibm=is_ibm_enterprise(context.get("enterprise_id")),
            language_costs=language_costs,
        )
    return True

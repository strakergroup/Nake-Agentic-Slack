"""Slack modal orchestration for staged AI quote adjustment."""

from __future__ import annotations

from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import get_verify_languages
from app.auth.connector import RayContext
from app.constants import EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
from app.slack.document_mt_quote_adjustment import (
    DOCUMENT_MT_QUOTE_KIND,
    document_mt_all_pairs,
    document_mt_language_costs,
    document_mt_pdf_tokens_for_pairs,
    document_mt_tokens_for_pairs,
)
from app.slack.document_mt_quotes import (
    QUOTE_STATUS_QUOTED,
    get_document_mt_quote_session,
    update_document_mt_quote_session,
)
from app.slack.evaluation_ai_adjustment import (
    estimated_pdf_file_language_costs,
    file_language_pairs,
    media_embed_toggles_from_view,
    pdf_costs_for_pairs,
    quote_tokens_for_pairs,
    selected_pairs_from_view,
    update_modal_cost_blocks,
)
from app.slack.evaluation_quotes import (
    STAGE_AWAITING_AI,
    evaluate_quote_expired_message,
    get_evaluate_quote_session,
    update_evaluate_quote_session,
)
from app.slack.media_quote_adjustment import (
    MEDIA_TRANSLATION_QUOTE_KIND,
    media_quote_message_ts,
    media_translation_language_costs,
    media_translation_quote_from_session,
)
from app.slack.media_quotes import (
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    get_media_quote_session,
    source_embed_tokens_for_session,
    translated_embed_tokens_for_session,
    update_media_quote_session,
)
from app.slack.middleware import populate_ray_connection, require_ray_client
from app.slack.modal_trigger import request_error_modal, safe_views_update, status_modal
from app.slack.pdf_evaluate_quotes import (
    get_pdf_evaluate_quote_session,
    update_pdf_evaluate_quote_session,
)
from app.slack.templates.views import evaluation_ai_quote_adjust_modal
from app.translate import _


def _quote_expired_modal() -> dict[str, Any]:
    return status_modal(_("Quote expired"), evaluate_quote_expired_message())


async def populate_ai_quote_adjustment_modal(
    client: AsyncWebClient,
    *,
    view_id: str,
    quote_id: str,
    quote_kind: str,
    user_id: str,
    context: RayContext,
    channel_id: str | None = None,
    message_ts: str | None = None,
) -> None:
    """Populate the already-opened loading modal for PDF or extracted quotes."""
    if quote_kind == DOCUMENT_MT_QUOTE_KIND:
        session = await get_document_mt_quote_session(quote_id)
        if not session:
            await safe_views_update(client, view_id, _quote_expired_modal())
            return
        if (
            session.get("user_id") != user_id
            or session.get("status") != QUOTE_STATUS_QUOTED
        ):
            await safe_views_update(client, view_id, request_error_modal())
            return
        resolved_channel_id = str(
            channel_id or session.get("channel_id") or context.get("channel_id") or ""
        )
        resolved_message_ts = str(message_ts or session.get("message_ts") or "") or None
        session_updates: dict[str, Any] = {}
        if resolved_channel_id and resolved_channel_id != session.get("channel_id"):
            session_updates["channel_id"] = resolved_channel_id
        if resolved_message_ts and resolved_message_ts != session.get("message_ts"):
            session_updates["message_ts"] = resolved_message_ts
        if session_updates:
            await update_document_mt_quote_session(quote_id, session_updates)
        quote = session.get("quote") or {}
        selected_pairs = [
            str(value) for value in session.get("selected_pairs") or []
        ] or document_mt_all_pairs(quote)
        await safe_views_update(
            client,
            view_id,
            evaluation_ai_quote_adjust_modal(
                quote_id=quote_id,
                quote_kind=quote_kind,
                language_costs=document_mt_language_costs(quote),
                selected_pairs=selected_pairs,
                ai_tokens=document_mt_tokens_for_pairs(quote, selected_pairs),
                pdf_tokens=document_mt_pdf_tokens_for_pairs(quote, selected_pairs),
                channel_id=resolved_channel_id or None,
                message_ts=resolved_message_ts,
            ),
        )
        return

    if quote_kind == MEDIA_TRANSLATION_QUOTE_KIND:
        session = await get_media_quote_session(quote_id)
        if not session:
            await safe_views_update(client, view_id, _quote_expired_modal())
            return
        if (
            session.get("user_id") != user_id
            or session.get("stage") != STAGE_AWAITING_TRANSLATION_ACCEPT
        ):
            await safe_views_update(client, view_id, request_error_modal())
            return
        resolved_channel_id = str(
            channel_id or session.get("channel_id") or context.get("channel_id") or ""
        )
        resolved_message_ts = (
            str(message_ts or media_quote_message_ts(session) or "") or None
        )
        session_updates: dict[str, Any] = {}
        if resolved_channel_id and resolved_channel_id != session.get("channel_id"):
            session_updates["channel_id"] = resolved_channel_id
        if resolved_message_ts and resolved_message_ts != session.get(
            "quote_message_ts"
        ):
            session_updates["quote_message_ts"] = resolved_message_ts
        if session_updates:
            await update_media_quote_session(quote_id, session_updates)
        quote = media_translation_quote_from_session(session)
        selected_pairs = [
            str(value) for value in session.get("selected_pairs") or []
        ] or document_mt_all_pairs(quote)
        embed_tokens = translated_embed_tokens_for_session(
            session, language_count=len(selected_pairs)
        )
        await safe_views_update(
            client,
            view_id,
            evaluation_ai_quote_adjust_modal(
                quote_id=quote_id,
                quote_kind=quote_kind,
                language_costs=media_translation_language_costs(session),
                selected_pairs=selected_pairs,
                ai_tokens=document_mt_tokens_for_pairs(quote, selected_pairs),
                pdf_tokens=0,
                embed_tokens=embed_tokens,
                embed_language_count=len(selected_pairs) if embed_tokens else 0,
                source_embed_tokens=source_embed_tokens_for_session(session),
                show_embed_toggles=True,
                embed_source=bool(session.get("embed_source")),
                embed_translated=bool(session.get("embed_translated")),
                channel_id=resolved_channel_id or None,
                message_ts=resolved_message_ts,
            ),
        )
        return

    if quote_kind == "pdf_prequote":
        session = await get_pdf_evaluate_quote_session(quote_id)
        if not session:
            await safe_views_update(client, view_id, _quote_expired_modal())
            return
        if session.get("user_id") != user_id:
            await safe_views_update(client, view_id, request_error_modal())
            return
        resolved_channel_id = str(
            channel_id or session.get("channel_id") or context.get("channel_id") or ""
        )
        resolved_message_ts = str(message_ts or session.get("message_ts") or "") or None
        session_updates: dict[str, Any] = {}
        if resolved_channel_id and resolved_channel_id != session.get("channel_id"):
            session_updates["channel_id"] = resolved_channel_id
        if resolved_message_ts and resolved_message_ts != session.get("message_ts"):
            session_updates["message_ts"] = resolved_message_ts
        target_language_uuids = [
            str(value) for value in session.get("target_langs_uuid") or []
        ]
        language_names = {
            str(language["uuid"]): str(language.get("name") or language["uuid"])
            for language in await get_verify_languages()
        }
        languages = [
            {
                "value": language_uuid,
                "label": language_names.get(language_uuid, language_uuid),
            }
            for language_uuid in target_language_uuids
        ]
        language_costs = list(
            session.get("all_language_costs") or session.get("language_costs") or []
        )
        if not language_costs:
            # Extract-priced rows should already be on the session; rebuild from
            # character counts only as a safety net for older sessions.
            language_costs = estimated_pdf_file_language_costs(
                session.get("files") or [],
                languages,
            )
            session_updates["language_costs"] = language_costs
            session_updates["all_language_costs"] = language_costs
        selected_pairs = [
            str(value) for value in session.get("selected_pairs") or []
        ] or file_language_pairs(
            [str(file_data["id"]) for file_data in session.get("files") or []],
            [
                str(value)
                for value in session.get("selected_target_langs_uuid")
                or target_language_uuids
                or []
            ],
        )
        if session_updates:
            await update_pdf_evaluate_quote_session(quote_id, **session_updates)
        await safe_views_update(
            client,
            view_id,
            evaluation_ai_quote_adjust_modal(
                quote_id=quote_id,
                quote_kind=quote_kind,
                language_costs=language_costs,
                selected_pairs=selected_pairs,
                ai_tokens=int(session.get("ai_token_estimate") or 0),
                pdf_tokens=int(session.get("pdf_tokens") or 0),
                channel_id=resolved_channel_id or None,
                message_ts=resolved_message_ts,
            ),
        )
        return

    await populate_ray_connection(context)
    if not await require_ray_client(context, prompt_login=True):
        await safe_views_update(
            client,
            view_id,
            status_modal(
                _("Sign in required"),
                _("Please sign in to continue."),
            ),
        )
        return
    session = await get_evaluate_quote_session(quote_id)
    if not session:
        await safe_views_update(client, view_id, _quote_expired_modal())
        return
    if session.get("user_id") != user_id or session.get("stage") != STAGE_AWAITING_AI:
        await safe_views_update(client, view_id, request_error_modal())
        return
    resolved_channel_id = str(
        channel_id or session.get("channel_id") or context.get("channel_id") or ""
    )
    resolved_message_ts = str(message_ts or session.get("message_ts") or "") or None
    session_updates: dict[str, Any] = {}
    if resolved_channel_id and resolved_channel_id != session.get("channel_id"):
        session_updates["channel_id"] = resolved_channel_id
    if resolved_message_ts and resolved_message_ts != session.get("message_ts"):
        session_updates["message_ts"] = resolved_message_ts
    # Freeze amounts from the staged quote snapshot — never re-quote AI
    # Translation here (TM/memory changes after MT must not rewrite the view).
    quote_snapshot = dict(session.get("quote_snapshot") or {})
    language_costs = list(
        quote_snapshot.get("all_language_costs")
        or quote_snapshot.get("language_costs")
        or []
    )
    quote_details = list(quote_snapshot.get("ai_quote_details") or [])
    if not language_costs:
        await safe_views_update(client, view_id, request_error_modal())
        return
    selected_pairs = [
        str(value)
        for value in quote_snapshot.get("ai_translation_file_and_languages") or []
    ] or file_language_pairs(
        [
            str(file_uuid)
            for file_uuid in quote_snapshot.get("file_uuids") or []
            if file_uuid
        ]
        or sorted(
            {
                str(row.get("file_uuid"))
                for row in language_costs
                if row.get("file_uuid")
            }
        ),
        sorted({str(row.get("value")) for row in language_costs if row.get("value")}),
    )
    if session_updates:
        await update_evaluate_quote_session(quote_id, session_updates)
    all_pair_keys = {
        f"{row.get('file_uuid')}:{row.get('value')}"
        for row in language_costs
        if row.get("file_uuid") and row.get("value")
    }
    aggregate_tokens = int(quote_snapshot.get("token_cost") or 0)
    if set(selected_pairs) == all_pair_keys and aggregate_tokens:
        ai_tokens = aggregate_tokens
    elif quote_details:
        ai_tokens = quote_tokens_for_pairs({"details": quote_details}, selected_pairs)
    else:
        ai_tokens = sum(
            int(row.get("token") or 0)
            for row in language_costs
            if f"{row.get('file_uuid')}:{row.get('value')}" in set(selected_pairs)
        )
    await safe_views_update(
        client,
        view_id,
        evaluation_ai_quote_adjust_modal(
            quote_id=quote_id,
            quote_kind=quote_kind,
            language_costs=language_costs,
            selected_pairs=selected_pairs,
            ai_tokens=ai_tokens,
            pdf_tokens=int(quote_snapshot.get("pdf_tokens") or 0),
            channel_id=resolved_channel_id or None,
            message_ts=resolved_message_ts,
        ),
    )


async def refresh_ai_quote_adjustment_cost(
    client: AsyncWebClient,
    *,
    view: dict[str, Any],
    quote_id: str,
    quote_kind: str,
) -> None:
    """Refresh modal cost blocks from its current checkbox state."""
    selected_pairs = selected_pairs_from_view(view)
    language_costs: list[dict[str, Any]] = []
    embed_tokens = 0
    embed_language_count = 0
    source_embed_tokens = 0
    if quote_kind == DOCUMENT_MT_QUOTE_KIND:
        session = await get_document_mt_quote_session(quote_id)
        if not session:
            return
        quote = session.get("quote") or {}
        language_costs = document_mt_language_costs(quote)
        ai_tokens = document_mt_tokens_for_pairs(quote, selected_pairs)
        pdf_tokens = document_mt_pdf_tokens_for_pairs(quote, selected_pairs)
    elif quote_kind == MEDIA_TRANSLATION_QUOTE_KIND:
        session = await get_media_quote_session(quote_id)
        if not session:
            return
        quote = media_translation_quote_from_session(session)
        language_costs = media_translation_language_costs(session)
        ai_tokens = document_mt_tokens_for_pairs(quote, selected_pairs)
        pdf_tokens = 0
        toggles = media_embed_toggles_from_view(view)
        merged = dict(session)
        if toggles is not None:
            merged["embed_source"] = toggles[0]
            merged["embed_translated"] = toggles[1]
        embed_tokens = translated_embed_tokens_for_session(
            merged, language_count=len(selected_pairs)
        )
        embed_language_count = len(selected_pairs) if embed_tokens else 0
        source_embed_tokens = source_embed_tokens_for_session(merged)
    elif quote_kind == "pdf_prequote":
        session = await get_pdf_evaluate_quote_session(quote_id)
        if not session:
            return
        language_costs = list(
            session.get("all_language_costs") or session.get("language_costs") or []
        )
        all_pair_keys = {
            f"{row.get('file_uuid')}:{row.get('value')}"
            for row in language_costs
            if row.get("file_uuid") and row.get("value")
        }
        if selected_pairs and set(selected_pairs) == all_pair_keys:
            ai_tokens = int(session.get("ai_token_estimate") or 0)
            pdf_tokens = int(session.get("pdf_tokens") or 0)
        else:
            ai_tokens, pdf_page_count = pdf_costs_for_pairs(session, selected_pairs)
            pdf_tokens = pdf_page_count * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
    else:
        session = await get_evaluate_quote_session(quote_id)
        if not session:
            return
        quote_snapshot = session.get("quote_snapshot") or {}
        language_costs = list(
            quote_snapshot.get("all_language_costs")
            or quote_snapshot.get("language_costs")
            or []
        )
        all_pair_keys = {
            f"{row.get('file_uuid')}:{row.get('value')}"
            for row in language_costs
            if row.get("file_uuid") and row.get("value")
        }
        aggregate_tokens = int(quote_snapshot.get("token_cost") or 0)
        if selected_pairs and set(selected_pairs) == all_pair_keys and aggregate_tokens:
            ai_tokens = aggregate_tokens
        else:
            ai_tokens = quote_tokens_for_pairs(
                {"details": quote_snapshot.get("ai_quote_details") or []},
                selected_pairs,
            )
        pdf_tokens = int(quote_snapshot.get("pdf_tokens") or 0)
    await client.views_update(
        view_id=view["id"],
        view=update_modal_cost_blocks(
            view,
            ai_tokens=ai_tokens,
            pdf_tokens=pdf_tokens,
            language_costs=language_costs or None,
            embed_tokens=embed_tokens,
            embed_language_count=embed_language_count,
            source_embed_tokens=source_embed_tokens,
        ),
    )

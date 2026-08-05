from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_client_evaluation_job,
    get_evaluation_job_quote,
    get_job_pricing,
    is_ambiguous_api_failure,
    proceed_evaluation_job,
    proceed_quality_evaluation,
)
from app.auth.connector import RayContext
from app.constants import (
    EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
    EVALUATE_SERVICE_AI_TRANSLATION,
    EVALUATE_SERVICE_QUALITY_EVALUATION,
)
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
from app.slack.evaluation_ai_adjustment import (
    AI_QUOTE_ADJUST_ACTION_ID,
    ai_scope_from_job,
    filter_job_to_pairs,
    language_costs_with_cancelled_status,
    mark_out_of_scope_pairs_cancelled,
    selected_pairs_from_values,
)
from app.slack.evaluation_combined_quotes import (
    HT_SUBMITTED_QUOTE_DISPLAY,
    PRE_QE_QUOTE_DISPLAY,
    WORST_CASE_QE_QUALITY_TIER,
    active_quote_cost_rows,
    combined_human_job_quote_message,
    job_file_uuids,
    job_target_language_uuids,
    qe_additional_cost,
    qe_additional_costs_from_quote,
    resolve_selected_human_translation_targets,
)
from app.slack.evaluation_quotes import (
    AI_TERMINAL_STAGES,
    QE_TERMINAL_STAGES,
    STAGE_ACCEPTED_AI,
    STAGE_ACCEPTED_QE,
    STAGE_AWAITING_AI,
    STAGE_AWAITING_QE,
    STAGE_PROCESSING_AI,
    STAGE_PROCESSING_QE,
    evaluate_quote_expired_message,
    get_evaluate_quote_session,
    update_evaluate_quote_slack_message,
    update_evaluate_quote_stage,
)
from app.slack.middleware import require_ray_client
from app.translate import _


def _pending_confirmation_status() -> str:
    return _(
        "We could not confirm whether your acceptance went through. "
        "We are checking on it — please do not accept this quote again."
    )


def _clicking_user_id(body: dict[str, Any], context: RayContext) -> str:
    return str(body.get("user", {}).get("id") or context.get("user_id") or "")


async def _notify_evaluate_quote_expired(
    client: AsyncWebClient,
    *,
    body: dict[str, Any],
    context: RayContext,
    channel_id: str | None = None,
    message_ts: str | None = None,
) -> None:
    """DM the clicker and strip Accept/Adjust from the quote message when possible."""
    user_id = _clicking_user_id(body, context)
    expired = evaluate_quote_expired_message()
    if user_id:
        await client.chat_postMessage(channel=user_id, text=expired)
    resolved_channel = str(
        channel_id
        or body.get("channel", {}).get("id")
        or context.get("channel_id")
        or ""
    )
    resolved_ts = message_ts or body.get("message", {}).get("ts")
    if resolved_channel and resolved_ts:
        try:
            await client.chat_update(
                channel=resolved_channel,
                ts=str(resolved_ts),
                text=expired,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": expired},
                    }
                ],
            )
        except Exception as e:
            notify_exception(e)


async def _reject_foreign_quote_click(
    client: AsyncWebClient,
    *,
    session: dict[str, Any] | None,
    clicking_user_id: str,
    message: str,
) -> bool:
    """Reject an accept click from anyone other than the quote owner.

    Quote sessions live in Redis for hours, and the Slack message carrying the
    Accept button is visible to the whole channel, so the button value alone is
    not authorisation to spend the owner's tokens.
    """
    owner_id = (session or {}).get("user_id")
    if not owner_id or not clicking_user_id or owner_id == clicking_user_id:
        return False
    if clicking_user_id:
        await client.chat_postMessage(channel=clicking_user_id, text=message)
    return True


async def _resolve_evaluate_quote_message_ts(
    body: dict[str, Any], job_uuid: str
) -> str | None:
    """Resolve the original quote message timestamp from the action or session.

    Slack action bodies carry a ``message.ts`` for message-button clicks. When
    the accept originates from a modal submission the timestamp is stored in the
    modal ``private_metadata`` and, as a last resort, on the persisted quote
    session.
    """
    message_ts = body.get("message", {}).get("ts")
    if message_ts:
        return message_ts
    private_metadata_raw = (body.get("view") or {}).get("private_metadata")
    if isinstance(private_metadata_raw, str) and private_metadata_raw:
        try:
            metadata = json.loads(private_metadata_raw)
        except json.JSONDecodeError:
            metadata = {}
        stored_from_modal = (
            metadata.get("message_ts") if isinstance(metadata, dict) else None
        )
        if isinstance(stored_from_modal, str) and stored_from_modal:
            return stored_from_modal
    session = await get_evaluate_quote_session(job_uuid)
    if session:
        stored_ts = session.get("message_ts")
        if isinstance(stored_ts, str):
            return stored_ts
    return None


def _display_evaluate_language_costs(
    service: str,
    quote_snapshot: dict[str, Any],
    selected_pairs: list[str],
) -> list[dict[str, Any]] | None:
    """Build the per-language cost rows shown on the quote message.

    For AI Translation the deselected file/language pairs are kept but flagged
    as cancelled so the message shows what the user opted out of.
    """
    if service != EVALUATE_SERVICE_AI_TRANSLATION:
        return quote_snapshot.get("language_costs")
    all_language_costs = (
        quote_snapshot.get("all_language_costs")
        or quote_snapshot.get("language_costs")
        or []
    )
    if not all_language_costs:
        return None
    return language_costs_with_cancelled_status(
        all_language_costs,
        selected_pairs,
    )


async def _refresh_evaluate_quote_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str | None,
    service: str,
    service_label: str,
    token_cost: int,
    job_uuid: str,
    accept_action_id: str,
    pdf_page_count: int | None,
    pdf_tokens: int | None,
    is_ibm: bool,
    quote_snapshot: dict[str, Any],
    selected_pairs: list[str],
    actions: bool,
    status_message: str | None,
) -> None:
    if not message_ts:
        return
    await update_evaluate_quote_slack_message(
        client,
        channel_id=channel_id,
        message_ts=message_ts,
        service_label=service_label,
        token_cost=token_cost,
        job_uuid=job_uuid,
        accept_action_id=accept_action_id,
        adjust_action_id=AI_QUOTE_ADJUST_ACTION_ID
        if service == EVALUATE_SERVICE_AI_TRANSLATION
        else None,
        pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
        pdf_tokens=pdf_tokens,
        actions=actions,
        status_message=status_message,
        is_ibm=is_ibm,
        language_costs=_display_evaluate_language_costs(
            service, quote_snapshot, selected_pairs
        ),
    )


async def _accept_evaluation_service_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
    lock_key_prefix: str,
    service: str,
    service_label: str,
    accept_action_id: str,
    terminal_stages: frozenset[str],
    awaiting_stage: str,
    processing_stage: str,
    accepted_stage: str,
    include_pdf_fee: bool,
    accepted_message: str,
    insufficient_balance_message: str,
    generic_error_message: str,
    proceed: Callable[..., Awaitable[None]],
    selected_pairs_override: list[str] | None = None,
) -> None:
    """Accept a staged evaluate quote, updating the original Slack message in place."""
    job_uuid = action["value"]
    session = await get_evaluate_quote_session(job_uuid)
    if session is None:
        await _notify_evaluate_quote_expired(client, body=body, context=context)
        return
    if await _reject_foreign_quote_click(
        client,
        session=session,
        clicking_user_id=_clicking_user_id(body, context),
        message=_("You do not have permission to accept this quote."),
    ):
        return
    channel_id = str(session.get("channel_id") or context.get("channel_id") or "")
    private_metadata_raw = (body.get("view") or {}).get("private_metadata")
    if isinstance(private_metadata_raw, str) and private_metadata_raw:
        try:
            metadata = json.loads(private_metadata_raw)
        except json.JSONDecodeError:
            metadata = {}
        if isinstance(metadata, dict) and metadata.get("channel_id"):
            channel_id = str(metadata["channel_id"])
    message_ts = await _resolve_evaluate_quote_message_ts(body, job_uuid)
    context_enterprise_id = (
        context.get("enterprise_id") if hasattr(context, "get") else None
    ) or getattr(context, "enterprise_id", None)
    is_ibm = is_ibm_enterprise(context_enterprise_id)

    quote_snapshot = (session or {}).get("quote_snapshot") or {}
    if service == EVALUATE_SERVICE_AI_TRANSLATION:
        selected_pairs = selected_pairs_from_values(selected_pairs_override or []) or [
            str(value)
            for value in quote_snapshot.get("ai_translation_file_and_languages") or []
        ]
    else:
        selected_pairs = []
    if session and session.get("stage") in terminal_stages:
        return

    lock_key = f"{lock_key_prefix}_{job_uuid}"
    lock_acquired = await redis_conn.set(lock_key, "1", ex=300, nx=True)
    if not lock_acquired:
        if message_ts:
            quote_snapshot = (session or {}).get("quote_snapshot") or {}
            await update_evaluate_quote_slack_message(
                client,
                channel_id=channel_id,
                message_ts=message_ts,
                service_label=quote_snapshot.get("service_label", service_label),
                token_cost=int(quote_snapshot.get("token_cost", 0)),
                job_uuid=job_uuid,
                accept_action_id=accept_action_id,
                adjust_action_id=AI_QUOTE_ADJUST_ACTION_ID
                if service == EVALUATE_SERVICE_AI_TRANSLATION
                else None,
                pdf_page_count=(session or {}).get("pdf_page_count"),
                pdf_tokens=quote_snapshot.get("pdf_tokens"),
                actions=False,
                status_message=_(
                    "A request is already in progress. Please wait a moment."
                ),
                is_ibm=is_ibm,
                language_costs=quote_snapshot.get("language_costs"),
            )
        return

    base_token_cost = 0
    pdf_page_count: int | None = None
    pdf_tokens: int | None = None
    proceed_attempted = False

    try:
        if not await require_ray_client(context) or context["ray"].client is None:
            await redis_conn.delete(lock_key)
            return

        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [service],
            file_and_languages=selected_pairs or None,
        )
        services_costs = quote.get("services_costs") or {}
        base_token_cost = int(services_costs.get(service, quote.get("token", 0)))
        total_token_cost = base_token_cost

        if include_pdf_fee:
            job = await get_client_evaluation_job(context["ray"].client, job_uuid)
            extra_info = job["data"].get("extra_info") or {}
            pdf_page_count = extra_info.get("pdf_page_count")
            if pdf_page_count:
                pdf_tokens = (
                    int(pdf_page_count) * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE
                )
                total_token_cost += pdf_tokens

        await update_evaluate_quote_stage(job_uuid, processing_stage)
        await _refresh_evaluate_quote_message(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            service=service,
            service_label=service_label,
            token_cost=base_token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            pdf_page_count=pdf_page_count,
            pdf_tokens=pdf_tokens,
            is_ibm=is_ibm,
            quote_snapshot=quote_snapshot,
            selected_pairs=selected_pairs,
            actions=False,
            status_message=_("Accepting quote..."),
        )

        proceed_attempted = True
        await proceed(
            context["ray"].client,
            job_uuid,
            total_token_cost,
            selected_pairs,
        )

        await update_evaluate_quote_stage(job_uuid, accepted_stage)
        await _refresh_evaluate_quote_message(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            service=service,
            service_label=service_label,
            token_cost=base_token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            pdf_page_count=pdf_page_count,
            pdf_tokens=pdf_tokens,
            is_ibm=is_ibm,
            quote_snapshot=quote_snapshot,
            selected_pairs=selected_pairs,
            actions=False,
            status_message=accepted_message,
        )
    except VerifyAPIError as e:
        await update_evaluate_quote_stage(job_uuid, awaiting_stage)
        await _refresh_evaluate_quote_message(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            service=service,
            service_label=service_label,
            token_cost=base_token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            pdf_page_count=pdf_page_count,
            pdf_tokens=pdf_tokens,
            is_ibm=is_ibm,
            quote_snapshot=quote_snapshot,
            selected_pairs=selected_pairs,
            actions=e.status_code != 402,
            status_message=insufficient_balance_message
            if e.status_code == 402
            else generic_error_message,
        )
        await redis_conn.delete(lock_key)
    except Exception as e:
        notify_exception(e)
        # A timeout or 5xx after the debit was issued may still have charged the
        # client. Keep the stage at ``processing_*`` and leave Accept off rather
        # than inviting a second click.
        ambiguous = proceed_attempted and is_ambiguous_api_failure(e)
        if not ambiguous:
            await update_evaluate_quote_stage(job_uuid, awaiting_stage)
        await _refresh_evaluate_quote_message(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            service=service,
            service_label=service_label,
            token_cost=base_token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            pdf_page_count=pdf_page_count,
            pdf_tokens=pdf_tokens,
            is_ibm=is_ibm,
            quote_snapshot=quote_snapshot,
            selected_pairs=selected_pairs,
            actions=not ambiguous,
            status_message=_pending_confirmation_status()
            if ambiguous
            else _("There was an error processing your request. Please try again."),
        )
        if not ambiguous:
            await redis_conn.delete(lock_key)


async def _proceed_ai_translation(
    ray_client,
    job_uuid: str,
    token_cost: int,
    selected_pairs: list[str],
) -> None:
    await proceed_evaluation_job(
        ray_client,
        job_uuid,
        token_cost=token_cost,
        skip_quality_evaluation=True,
        ai_translation_file_and_languages=selected_pairs or None,
    )


async def accept_ai_translation_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
    selected_pairs_override: list[str] | None = None,
) -> None:
    """Accept the AI Translation quote for a staged evaluate submission."""
    await _accept_evaluation_service_quote(
        client=client,
        body=body,
        action=action,
        context=context,
        lock_key_prefix="evaluate_quote_accept",
        service=EVALUATE_SERVICE_AI_TRANSLATION,
        service_label=_("AI Translation"),
        accept_action_id="evaluation_ai_quote_accept",
        terminal_stages=AI_TERMINAL_STAGES,
        awaiting_stage=STAGE_AWAITING_AI,
        processing_stage=STAGE_PROCESSING_AI,
        accepted_stage=STAGE_ACCEPTED_AI,
        include_pdf_fee=True,
        accepted_message=_(
            "Your AI Translation quote has been accepted. Processing will begin shortly."
        ),
        insufficient_balance_message=_(
            "Insufficient AI token balance to accept this quote."
        ),
        generic_error_message=_(
            "There was an error accepting your quote. Please try again or contact your administrator."
        ),
        proceed=_proceed_ai_translation,
        selected_pairs_override=selected_pairs_override,
    )


async def _refresh_combined_quote(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str | None,
    display_job_data: dict[str, Any] | None,
    costs: list[dict[str, Any]],
    qe_token_cost: int,
    qe_costs: list[dict[str, Any]],
    actions: bool,
    status_message: str | None,
    submitted: bool = False,
) -> None:
    if not message_ts or display_job_data is None:
        return
    display = HT_SUBMITTED_QUOTE_DISPLAY if submitted else PRE_QE_QUOTE_DISPLAY
    message = combined_human_job_quote_message(
        display_job_data,
        costs,
        qe_token_cost=qe_token_cost,
        qe_additional_costs=qe_costs,
        actions=actions,
        status_message=status_message,
        allow_adjust=actions,
        download_translations_job_uuid=(
            None if submitted else display_job_data["uuid"]
        ),
        **display,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.text,
        blocks=message.blocks,
    )


async def accept_combined_qe_human_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> None:
    """Accept the combined Quality Evaluation + Human Translation quote."""
    job_uuid = action["value"]
    message_ts = body.get("message", {}).get("ts")
    session = await get_evaluate_quote_session(job_uuid)
    if session is None:
        await _notify_evaluate_quote_expired(
            client,
            body=body,
            context=context,
            message_ts=message_ts,
        )
        return
    message_ts = await _resolve_evaluate_quote_message_ts(body, job_uuid)
    if await _reject_foreign_quote_click(
        client,
        session=session,
        clicking_user_id=_clicking_user_id(body, context),
        message=_("You do not have permission to accept this quote."),
    ):
        return
    channel_id = str(session.get("channel_id") or context.get("channel_id") or "")
    if session.get("stage") in QE_TERMINAL_STAGES:
        return

    lock_key = f"evaluate_qe_human_quote_accept_{job_uuid}"
    lock_acquired = await redis_conn.set(lock_key, "1", ex=300, nx=True)
    if not lock_acquired:
        return

    qe_token_cost = 0
    display_job_data: dict[str, Any] | None = None
    costs: list[dict[str, Any]] = []
    qe_costs: list[dict[str, Any]] = []
    proceed_attempted = False

    try:
        if not await require_ray_client(context) or context["ray"].client is None:
            await redis_conn.delete(lock_key)
            return

        job = await get_client_evaluation_job(context["ray"].client, job_uuid)
        job_data = job["data"]
        quote_snapshot = (session or {}).get("quote_snapshot") or {}
        ai_scope = [
            str(value)
            for value in quote_snapshot.get("ai_translation_file_and_languages")
            or ai_scope_from_job(job_data)
        ]
        selected_targets = [
            str(value)
            for value in quote_snapshot.get("quality_evaluation_file_and_languages")
            or ai_scope
        ]
        scoped_job = filter_job_to_pairs(job_data, ai_scope)
        # Display must keep Cancelled placeholders for asymmetric file×language
        # scope; filter_job_to_pairs drops those rows and yields USD 0.00 ghosts.
        display_job_data = mark_out_of_scope_pairs_cancelled(
            job_data,
            selected_targets,
        )
        resolve_selected_human_translation_targets(
            display_job_data,
            selected_languages=selected_targets,
        )
        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [EVALUATE_SERVICE_QUALITY_EVALUATION],
            file_and_languages=selected_targets,
        )
        services_costs = quote.get("services_costs") or {}
        qe_token_cost = int(
            services_costs.get(
                EVALUATE_SERVICE_QUALITY_EVALUATION, quote.get("token", 0)
            )
        )
        qe_costs = qe_additional_costs_from_quote(
            quote,
            selected_pairs=selected_targets,
        )
        pricing = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            job_file_uuids(scoped_job),
            job_target_language_uuids(scoped_job),
            assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER,
        )
        costs = active_quote_cost_rows(pricing["data"], selected_targets)
        if not qe_costs:
            qe_costs = qe_additional_cost(qe_token_cost, costs)

        await update_evaluate_quote_stage(job_uuid, STAGE_PROCESSING_QE)
        await _refresh_combined_quote(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            display_job_data=display_job_data,
            costs=costs,
            qe_token_cost=qe_token_cost,
            qe_costs=qe_costs,
            actions=False,
            status_message=_("Accepting quote..."),
            submitted=True,
        )

        proceed_attempted = True
        await proceed_quality_evaluation(
            context["ray"].client,
            job_uuid,
            token_cost=qe_token_cost,
            human_translation_file_and_languages=selected_targets,
            quality_evaluation_file_and_languages=selected_targets,
        )

        await update_evaluate_quote_stage(job_uuid, STAGE_ACCEPTED_QE)
        await _refresh_combined_quote(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            display_job_data=display_job_data,
            costs=costs,
            qe_token_cost=qe_token_cost,
            qe_costs=qe_costs,
            actions=False,
            status_message=_(
                "Quote accepted! Submitting for human translation and "
                "calculating your final discount based on AI quality..."
            ),
            submitted=True,
        )
    except VerifyAPIError as e:
        await update_evaluate_quote_stage(job_uuid, STAGE_AWAITING_QE)
        await _refresh_combined_quote(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            display_job_data=display_job_data,
            costs=costs,
            qe_token_cost=qe_token_cost,
            qe_costs=qe_costs,
            actions=e.status_code != 402,
            status_message=_("Insufficient AI token balance to accept this quote.")
            if e.status_code == 402
            else _(
                "There was an error accepting your quote. Please try again or contact your administrator."
            ),
        )
        await redis_conn.delete(lock_key)
    except Exception as e:
        notify_exception(e)
        ambiguous = proceed_attempted and is_ambiguous_api_failure(e)
        if not ambiguous:
            await update_evaluate_quote_stage(job_uuid, STAGE_AWAITING_QE)
        await _refresh_combined_quote(
            client,
            channel_id=channel_id,
            message_ts=message_ts,
            display_job_data=display_job_data,
            costs=costs,
            qe_token_cost=qe_token_cost,
            qe_costs=qe_costs,
            actions=not ambiguous,
            status_message=_pending_confirmation_status()
            if ambiguous
            else _("There was an error processing your request. Please try again."),
            submitted=ambiguous,
        )
        if not ambiguous:
            await redis_conn.delete(lock_key)

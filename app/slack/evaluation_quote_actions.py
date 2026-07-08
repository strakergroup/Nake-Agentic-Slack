from __future__ import annotations

from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_client_evaluation_job,
    get_evaluation_job_quote,
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
from app.slack.evaluation_quotes import (
    AI_TERMINAL_STAGES,
    QE_TERMINAL_STAGES,
    STAGE_ACCEPTED_AI,
    STAGE_ACCEPTED_QE,
    STAGE_AWAITING_AI,
    STAGE_AWAITING_QE,
    STAGE_PROCESSING_AI,
    STAGE_PROCESSING_QE,
    get_evaluate_quote_session,
    update_evaluate_quote_slack_message,
    update_evaluate_quote_stage,
)
from app.translate import _


async def _resolve_evaluate_quote_message_ts(
    body: dict[str, Any], job_uuid: str
) -> str | None:
    message_ts = body.get("message", {}).get("ts")
    if message_ts:
        return message_ts
    session = await get_evaluate_quote_session(job_uuid)
    if session:
        stored_ts = session.get("message_ts")
        if isinstance(stored_ts, str):
            return stored_ts
    return None


async def _proceed_evaluation_service_quote(
    ray_client,
    *,
    service: str,
    job_uuid: str,
    token_cost: int,
) -> None:
    if service == EVALUATE_SERVICE_AI_TRANSLATION:
        await proceed_evaluation_job(
            ray_client,
            job_uuid,
            token_cost=token_cost,
            skip_quality_evaluation=True,
        )
        return

    if service == EVALUATE_SERVICE_QUALITY_EVALUATION:
        await proceed_quality_evaluation(
            ray_client,
            job_uuid,
            token_cost=token_cost,
        )
        return

    raise ValueError(f"Unsupported evaluation service quote: {service}")


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
    download_translations_after_accept: bool = False,
) -> None:
    """Accept a staged evaluate quote, updating the original Slack message in place."""
    job_uuid = action["value"]
    channel_id = context["channel_id"]
    message_ts = await _resolve_evaluate_quote_message_ts(body, job_uuid)
    context_enterprise_id = (
        context.get("enterprise_id") if hasattr(context, "get") else None
    ) or getattr(context, "enterprise_id", None)
    is_ibm = is_ibm_enterprise(context_enterprise_id)

    session = await get_evaluate_quote_session(job_uuid)
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
                pdf_page_count=(session or {}).get("pdf_page_count"),
                pdf_tokens=quote_snapshot.get("pdf_tokens"),
                actions=False,
                status_message=_(
                    "A request is already in progress. Please wait a moment."
                ),
                is_ibm=is_ibm,
            )
        return

    base_token_cost = 0
    pdf_page_count: int | None = None
    pdf_tokens: int | None = None

    async def _refresh_quote_message(
        *,
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
            token_cost=base_token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
            pdf_tokens=pdf_tokens,
            actions=actions,
            status_message=status_message,
            download_translations_job_uuid=(
                job_uuid if download_translations_after_accept else None
            ),
            is_ibm=is_ibm,
        )

    try:
        assert context["ray"] is not None
        assert context["ray"].client is not None

        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [service],
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
        await _refresh_quote_message(
            actions=False,
            status_message=_("Accepting quote..."),
        )

        await _proceed_evaluation_service_quote(
            context["ray"].client,
            service=service,
            job_uuid=job_uuid,
            token_cost=total_token_cost,
        )

        await update_evaluate_quote_stage(job_uuid, accepted_stage)
        await _refresh_quote_message(
            actions=False,
            status_message=accepted_message,
        )
    except VerifyAPIError as e:
        await update_evaluate_quote_stage(job_uuid, awaiting_stage)
        if e.status_code == 402:
            await _refresh_quote_message(
                actions=False,
                status_message=insufficient_balance_message,
            )
        else:
            await _refresh_quote_message(
                actions=True,
                status_message=generic_error_message,
            )
        await redis_conn.delete(lock_key)
    except Exception as e:
        notify_exception(e)
        await update_evaluate_quote_stage(job_uuid, awaiting_stage)
        await _refresh_quote_message(
            actions=True,
            status_message=_(
                "There was an error processing your request. Please try again."
            ),
        )
        await redis_conn.delete(lock_key)


async def accept_ai_translation_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> None:
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
    )


async def accept_quality_evaluation_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> None:
    await _accept_evaluation_service_quote(
        client=client,
        body=body,
        action=action,
        context=context,
        lock_key_prefix="evaluate_qe_quote_accept",
        service=EVALUATE_SERVICE_QUALITY_EVALUATION,
        service_label=_("Quality Evaluation"),
        accept_action_id="evaluation_qe_quote_accept",
        terminal_stages=QE_TERMINAL_STAGES,
        awaiting_stage=STAGE_AWAITING_QE,
        processing_stage=STAGE_PROCESSING_QE,
        accepted_stage=STAGE_ACCEPTED_QE,
        include_pdf_fee=False,
        accepted_message=_(
            "Your Quality Evaluation quote has been accepted. Scoring will begin shortly."
        ),
        insufficient_balance_message=_(
            "Insufficient AI token balance to accept this quote."
        ),
        generic_error_message=_(
            "There was an error accepting your quote. Please try again or contact your administrator."
        ),
        download_translations_after_accept=True,
    )

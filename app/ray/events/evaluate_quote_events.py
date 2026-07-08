from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_evaluation_job,
    get_evaluation_job_quote,
    proceed_evaluation_job,
)
from app.auth.connector import get_ray_client
from app.constants import (
    EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
    EVALUATE_SERVICE_AI_TRANSLATION,
)
from app.dependencies import RayEvent, RayEventAuth
from app.ray.events.logging import post_notification
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.slack.evaluation_quotes import (
    STAGE_ACCEPTED_AI,
    get_evaluate_quote_session,
    save_evaluate_quote_session,
    update_evaluate_quote_slack_message,
)
from app.slack.templates.messages import EvaluationCreditsQuoteMessage
from app.translate import _

logger = logging.getLogger(__name__)

RAY_EVENT_DEDUPE_TTL_SECONDS = 7 * 24 * 60 * 60


async def claim_evaluate_complete_notification(event: RayEvent) -> bool:
    """Atomically claim a user-facing evaluate-complete notification."""
    client_id = event.data.get("client_id")
    job_uuid = event.data.get("job_uuid")
    if not client_id or not job_uuid:
        return True

    key = f"ray_event:{event.event}:{client_id}:{job_uuid}"
    try:
        was_set = await redis_conn.set(
            key,
            "1",
            ex=RAY_EVENT_DEDUPE_TTL_SECONDS,
            nx=True,
        )
    except Exception:
        logger.warning(
            "Failed to claim evaluate-complete notification idempotency key",
            exc_info=True,
        )
        return True

    if isinstance(was_set, bool):
        return was_set
    if was_set is None:
        return False
    if isinstance(was_set, str):
        return was_set.upper() == "OK"
    return bool(was_set)


def pdf_tokens_from_page_count(pdf_page_count: int | None) -> int | None:
    if not pdf_page_count:
        return None
    return int(pdf_page_count) * EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE


def resolve_evaluate_channel_id(
    event: RayEvent, job_data: dict[str, Any] | None = None
) -> str | None:
    channel_id = event.data.get("channel_id")
    if channel_id:
        return str(channel_id)
    if job_data:
        extra_info = job_data.get("extra_info") or {}
        slack_channel_id = extra_info.get("slack_channel_id")
        if slack_channel_id:
            return str(slack_channel_id)
    return None


async def post_evaluate_service_quote(
    client: AsyncWebClient,
    event: RayEvent,
    auth: RayEventAuth,
    *,
    job_uuid: str,
    service: str,
    service_label: str,
    accept_action_id: str,
    include_pdf_fee: bool,
) -> None:
    if auth.slack_user is None:
        raise ValueError("Slack user is required for evaluate quote notifications")

    ray_client = await get_ray_client(
        auth.slack_user.user_id,
        auth.slack_user.team_id,
        auth.slack_user.enterprise_id,
    )
    if ray_client is None:
        raise ValueError("Could not get ray client for evaluate quote")

    quote = await get_evaluation_job_quote(ray_client, job_uuid, [service])
    services_costs = quote.get("services_costs") or {}
    token_cost = int(services_costs.get(service, quote.get("token", 0)))

    job = await get_evaluation_job(auth.slack_user, job_uuid)
    job_data = job["data"]
    extra_info = job_data.get("extra_info") or {}
    pdf_page_count = extra_info.get("pdf_page_count")
    pdf_tokens = None
    if include_pdf_fee and pdf_page_count:
        pdf_tokens = pdf_tokens_from_page_count(int(pdf_page_count))

    is_qe_quote = accept_action_id == "evaluation_qe_quote_accept"
    channel_id = resolve_evaluate_channel_id(event, job_data)
    prequote_message_ts = extra_info.get("prequote_message_ts")
    is_ibm = is_ibm_enterprise(auth.slack_user.enterprise_id)
    if service == EVALUATE_SERVICE_AI_TRANSLATION and extra_info.get(
        "preaccepted_ai_translation_quote"
    ):
        total_token_cost = token_cost + (pdf_tokens or 0)
        try:
            if prequote_message_ts:
                await update_evaluate_quote_slack_message(
                    client,
                    channel_id=channel_id
                    or auth.slack_user.channel_id
                    or auth.slack_user.user_id,
                    message_ts=str(prequote_message_ts),
                    service_label=service_label,
                    token_cost=token_cost,
                    job_uuid=job_uuid,
                    accept_action_id=accept_action_id,
                    pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
                    pdf_tokens=pdf_tokens,
                    actions=False,
                    status_message=_(
                        "Your AI Translation quote has been accepted. Processing will begin shortly."
                    ),
                    is_ibm=is_ibm,
                )
            await proceed_evaluation_job(
                ray_client,
                job_uuid,
                token_cost=total_token_cost,
                skip_quality_evaluation=True,
            )
            await save_evaluate_quote_session(
                job_uuid,
                channel_id=channel_id
                or auth.slack_user.channel_id
                or auth.slack_user.user_id,
                user_id=auth.slack_user.user_id,
                team_id=auth.slack_user.team_id,
                stage=STAGE_ACCEPTED_AI,
                pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
                quote_snapshot={
                    "service": service,
                    "token_cost": token_cost,
                    "pdf_tokens": pdf_tokens,
                    "service_label": service_label,
                    "accept_action_id": accept_action_id,
                },
                message_ts=str(prequote_message_ts) if prequote_message_ts else None,
            )
        except VerifyAPIError as e:
            status = (
                _("Insufficient AI token balance to accept this quote.")
                if e.status_code == 402
                else _(
                    "There was an error accepting your quote. Please try again or contact your administrator."
                )
            )
            if prequote_message_ts:
                await update_evaluate_quote_slack_message(
                    client,
                    channel_id=channel_id
                    or auth.slack_user.channel_id
                    or auth.slack_user.user_id,
                    message_ts=str(prequote_message_ts),
                    service_label=service_label,
                    token_cost=token_cost,
                    job_uuid=job_uuid,
                    accept_action_id=accept_action_id,
                    pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
                    pdf_tokens=pdf_tokens,
                    actions=False,
                    status_message=status,
                    is_ibm=is_ibm,
                )
        return

    message = EvaluationCreditsQuoteMessage(
        service_label=service_label,
        token_cost=token_cost,
        job_uuid=job_uuid,
        accept_action_id=accept_action_id,
        pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
        pdf_tokens=pdf_tokens,
        download_translations_job_uuid=job_uuid if is_qe_quote else None,
        is_ibm=is_ibm,
    )
    session = await get_evaluate_quote_session(job_uuid) if is_qe_quote else None
    session_channel_id = session.get("channel_id") if session else None
    session_message_ts = session.get("message_ts") if session else None

    if is_qe_quote and session_channel_id and session_message_ts:
        await update_evaluate_quote_slack_message(
            client,
            channel_id=session_channel_id,
            message_ts=session_message_ts,
            service_label=service_label,
            token_cost=token_cost,
            job_uuid=job_uuid,
            accept_action_id=accept_action_id,
            actions=True,
            download_translations_job_uuid=job_uuid,
            is_ibm=is_ibm,
        )
        channel_id = session_channel_id
        message_ts = session_message_ts
    else:
        response = await post_notification(
            client,
            event,
            auth.slack_user,
            message,
            channel_id=channel_id,
        )
        message_ts = response.get("ts") if isinstance(response, dict) else None
    stage = "awaiting_ai" if not is_qe_quote else "awaiting_qe"
    await save_evaluate_quote_session(
        job_uuid,
        channel_id=channel_id or auth.slack_user.channel_id or auth.slack_user.user_id,
        user_id=auth.slack_user.user_id,
        team_id=auth.slack_user.team_id,
        stage=stage,
        pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
        quote_snapshot={
            "service": service,
            "token_cost": token_cost,
            "pdf_tokens": pdf_tokens,
            "service_label": service_label,
            "accept_action_id": accept_action_id,
        },
        message_ts=message_ts,
    )


def evaluate_quote_event_error(event_name: str, error: Exception) -> HTTPException:
    return HTTPException(
        422,
        {
            "message": f"Error processing {event_name} event: {str(error)}",
        },
    )

"""Redis session storage for sequential evaluate quote confirmation."""

from __future__ import annotations

import json
import logging
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_evaluation_job,
    get_evaluation_job_quote,
    proceed_evaluation_job,
)
from app.auth.connector import get_ray_client
from app.config import config
from app.constants import (
    EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
    EVALUATE_SERVICE_AI_TRANSLATION,
)
from app.dependencies import RayEvent, RayEventAuth
from app.ray.events.logging import post_notification
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.slack.templates.messages import EvaluationCreditsQuoteMessage
from app.translate import _

logger = logging.getLogger(__name__)

EVALUATE_QUOTE_KEY_PREFIX = "slack-ray-translator:evaluate-quote:"
RAY_EVENT_DEDUPE_TTL_SECONDS = 7 * 24 * 60 * 60

STAGE_AWAITING_AI = "awaiting_ai"
STAGE_PROCESSING_AI = "processing_ai"
STAGE_ACCEPTED_AI = "accepted_ai"
STAGE_AWAITING_QE = "awaiting_qe"
STAGE_PROCESSING_QE = "processing_qe"
STAGE_ACCEPTED_QE = "accepted_qe"

AI_TERMINAL_STAGES = frozenset({STAGE_PROCESSING_AI, STAGE_ACCEPTED_AI})
QE_TERMINAL_STAGES = frozenset({STAGE_PROCESSING_QE, STAGE_ACCEPTED_QE})


def _quote_key(job_uuid: str) -> str:
    return f"{EVALUATE_QUOTE_KEY_PREFIX}{job_uuid}"


async def save_evaluate_quote_session(
    job_uuid: str,
    *,
    channel_id: str,
    user_id: str,
    team_id: str,
    stage: str,
    pdf_page_count: int | None = None,
    quote_snapshot: dict[str, Any] | None = None,
    message_ts: str | None = None,
) -> None:
    payload = {
        "channel_id": channel_id,
        "user_id": user_id,
        "team_id": team_id,
        "stage": stage,
        "pdf_page_count": pdf_page_count,
        "quote_snapshot": quote_snapshot or {},
        "message_ts": message_ts,
    }
    await redis_conn.set(
        _quote_key(job_uuid),
        json.dumps(payload),
        ex=config.evaluate_quote_ttl_seconds,
    )


async def get_evaluate_quote_session(job_uuid: str) -> dict[str, Any] | None:
    raw = await redis_conn.get(_quote_key(job_uuid))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


async def update_evaluate_quote_stage(job_uuid: str, stage: str) -> None:
    session = await get_evaluate_quote_session(job_uuid)
    if not session:
        return
    session["stage"] = stage
    await redis_conn.set(
        _quote_key(job_uuid),
        json.dumps(session),
        ex=config.evaluate_quote_ttl_seconds,
    )


async def update_evaluate_quote_slack_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str,
    service_label: str,
    token_cost: int,
    job_uuid: str,
    accept_action_id: str,
    pdf_page_count: int | None = None,
    pdf_tokens: int | None = None,
    actions: bool = False,
    status_message: str | None = None,
    download_translations_job_uuid: str | None = None,
    is_ibm: bool = False,
) -> None:
    """Replace the original evaluate quote message in place."""
    message = EvaluationCreditsQuoteMessage(
        service_label=service_label,
        token_cost=token_cost,
        job_uuid=job_uuid,
        accept_action_id=accept_action_id,
        pdf_page_count=pdf_page_count,
        pdf_tokens=pdf_tokens,
        actions=actions,
        status_message=status_message,
        download_translations_job_uuid=download_translations_job_uuid,
        is_ibm=is_ibm,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.text,
        blocks=message.blocks,
    )


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
        is_ibm=is_ibm,
    )
    response = await post_notification(
        client,
        event,
        auth.slack_user,
        message,
        channel_id=channel_id,
    )
    message_ts = response.get("ts") if isinstance(response, dict) else None
    await save_evaluate_quote_session(
        job_uuid,
        channel_id=channel_id or auth.slack_user.channel_id or auth.slack_user.user_id,
        user_id=auth.slack_user.user_id,
        team_id=auth.slack_user.team_id,
        stage=STAGE_AWAITING_AI,
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

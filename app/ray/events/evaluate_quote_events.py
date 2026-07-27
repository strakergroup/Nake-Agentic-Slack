from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_evaluation_job,
    get_evaluation_job_quote,
    get_verify_languages,
    proceed_evaluation_job,
)
from app.auth.connector import get_ray_client
from app.constants import (
    EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE,
    EVALUATE_SERVICE_AI_TRANSLATION,
    SLACK_HT_QUOTE_AFTER_QE_KEY,
)
from app.dependencies import RayEvent, RayEventAuth
from app.ray.events.logging import post_notification, slack_response_message_ts
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
from app.slack.evaluation_ai_adjustment import (
    AI_QUOTE_ADJUST_ACTION_ID,
    filter_language_costs_by_pairs,
    quote_file_language_costs,
)
from app.slack.evaluation_quotes import (
    STAGE_ACCEPTED_AI,
    STAGE_AWAITING_AI,
    save_evaluate_quote_session,
)
from app.slack.templates.messages import EvaluationCreditsQuoteMessage
from app.translate import _

logger = logging.getLogger(__name__)

RAY_EVENT_DEDUPE_TTL_SECONDS = 7 * 24 * 60 * 60


def _ray_event_dedupe_key(event: RayEvent) -> str | None:
    client_id = event.data.get("client_id")
    job_uuid = event.data.get("job_uuid")
    if not client_id or not job_uuid:
        return None
    return f"ray_event:{event.event}:{client_id}:{job_uuid}"


async def claim_ray_event_notification(event: RayEvent) -> bool:
    """Atomically claim a single delivery of a RAY event for one job.

    RAY streams are at-least-once, so any handler with a user-visible or
    billable side effect must claim before acting. The key is namespaced by
    event name, so each event type claims independently for the same job.
    """
    key = _ray_event_dedupe_key(event)
    if key is None:
        return True

    try:
        was_set = await redis_conn.set(
            key,
            "1",
            ex=RAY_EVENT_DEDUPE_TTL_SECONDS,
            nx=True,
        )
    except Exception:
        logger.warning(
            "Failed to claim RAY event idempotency key",
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


async def release_ray_event_notification(event: RayEvent) -> None:
    """Drop a claim so a later redelivery can retry a non-billable failure.

    Call this only when no debit/purchase could have landed (definite 401/402/403
    before side effects, or failures before the billable API call).
    """
    key = _ray_event_dedupe_key(event)
    if key is None:
        return
    try:
        await redis_conn.delete(key)
    except Exception:
        logger.warning(
            "Failed to release RAY event idempotency key",
            exc_info=True,
        )


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


def _source_file_uuids(job_data: dict[str, Any]) -> list[str]:
    return [
        str(source_file["file_uuid"])
        for source_file in job_data.get("source_files") or []
        if source_file.get("file_uuid")
    ]


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
    ai_translation_file_and_languages = sorted(
        {
            f"{detail['file_uuid']}:{detail['target_language_uuid']}"
            for detail in quote.get("details") or []
            if detail.get("file_uuid") and detail.get("target_language_uuid")
        }
    )

    job = await get_evaluation_job(auth.slack_user, job_uuid)
    job_data = job["data"]
    language_names = {
        str(language["uuid"]): str(language.get("name") or language["uuid"])
        for language in await get_verify_languages()
    }
    language_costs = (
        quote_file_language_costs(quote, job_data, language_names)
        if service == EVALUATE_SERVICE_AI_TRANSLATION
        else None
    )
    extra_info = job_data.get("extra_info") or {}
    pdf_page_count = extra_info.get("pdf_page_count")
    pdf_tokens = None
    if include_pdf_fee and pdf_page_count:
        pdf_tokens = pdf_tokens_from_page_count(int(pdf_page_count))

    channel_id = resolve_evaluate_channel_id(event, job_data)
    prequote_message_ts = extra_info.get("prequote_message_ts")
    is_ibm = is_ibm_enterprise(auth.slack_user.enterprise_id)
    if service == EVALUATE_SERVICE_AI_TRANSLATION and extra_info.get(
        SLACK_HT_QUOTE_AFTER_QE_KEY
    ):
        # Non-admin: auto-run AI (no Accept UI); HT is quoted after QE.
        await _proceed_preaccepted_ai_quote(
            client,
            event,
            auth,
            job_uuid=job_uuid,
            service=service,
            service_label=service_label,
            accept_action_id=accept_action_id,
            ray_client=ray_client,
            quote=quote,
            job_data=job_data,
            extra_info=extra_info,
            channel_id=channel_id,
            token_cost=token_cost,
            pdf_tokens=pdf_tokens,
            pdf_page_count=pdf_page_count,
            language_costs=language_costs,
            quoted_ai_pairs=ai_translation_file_and_languages,
            prequote_message_ts=prequote_message_ts,
            status_on_success=_(
                "AI translation started. You will receive a human translation "
                "quote after quality evaluation."
            ),
        )
        return

    if service == EVALUATE_SERVICE_AI_TRANSLATION and extra_info.get(
        "preaccepted_ai_translation_quote"
    ):
        await _proceed_preaccepted_ai_quote(
            client,
            event,
            auth,
            job_uuid=job_uuid,
            service=service,
            service_label=service_label,
            accept_action_id=accept_action_id,
            ray_client=ray_client,
            quote=quote,
            job_data=job_data,
            extra_info=extra_info,
            channel_id=channel_id,
            token_cost=token_cost,
            pdf_tokens=pdf_tokens,
            pdf_page_count=pdf_page_count,
            language_costs=language_costs,
            quoted_ai_pairs=ai_translation_file_and_languages,
            prequote_message_ts=prequote_message_ts,
            status_on_success=None,
        )
        return

    message = EvaluationCreditsQuoteMessage(
        service_label=service_label,
        token_cost=token_cost,
        job_uuid=job_uuid,
        accept_action_id=accept_action_id,
        adjust_action_id=AI_QUOTE_ADJUST_ACTION_ID
        if service == EVALUATE_SERVICE_AI_TRANSLATION
        else None,
        pdf_page_count=int(pdf_page_count) if pdf_page_count else None,
        pdf_tokens=pdf_tokens,
        is_ibm=is_ibm,
        language_costs=language_costs,
    )
    response = await post_notification(
        client,
        event,
        auth.slack_user,
        message,
        channel_id=channel_id,
    )
    message_ts = slack_response_message_ts(response)
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
            "ai_translation_file_and_languages": ai_translation_file_and_languages,
            "ai_quote_details": quote.get("details") or [],
            "all_language_costs": language_costs,
            "language_costs": language_costs,
            "file_uuids": _source_file_uuids(job_data),
        },
        message_ts=message_ts,
    )


async def _proceed_preaccepted_ai_quote(
    client: AsyncWebClient,
    event: RayEvent,
    auth: RayEventAuth,
    *,
    job_uuid: str,
    service: str,
    service_label: str,
    accept_action_id: str,
    ray_client: Any,
    quote: dict[str, Any],
    job_data: dict[str, Any],
    extra_info: dict[str, Any],
    channel_id: str | None,
    token_cost: int,
    pdf_tokens: int | None,
    pdf_page_count: Any,
    language_costs: list[dict[str, Any]] | None,
    quoted_ai_pairs: list[str],
    prequote_message_ts: Any,
    status_on_success: str | None = None,
) -> None:
    """Charge and proceed AI translation without an Accept click.

    Used for PDF pre-quotes the user already accepted, and for non-admin
    auto-proceed (``slack_ht_quote_after_qe``). Runs off an at-least-once RAY
    stream and issues a real token debit, so the delivery is claimed before the
    charge. Once the claim is held nothing may escape to the router: a raised
    exception becomes an HTTP 422 and the producer redelivers, which would
    debit the client a second time.
    """
    assert auth.slack_user is not None
    notify_channel_id = (
        channel_id or auth.slack_user.channel_id or auth.slack_user.user_id
    )

    if not await claim_ray_event_notification(event):
        logger.info(
            "Skipping duplicate preaccepted AI quote acceptance for job %s",
            job_uuid,
        )
        return

    try:
        persisted_ai_pairs = extra_info.get("ai_translation_file_and_languages")
        selected_ai_pairs = (
            [str(value) for value in persisted_ai_pairs if value]
            if isinstance(persisted_ai_pairs, list) and persisted_ai_pairs
            else quoted_ai_pairs
        )
        await proceed_evaluation_job(
            ray_client,
            job_uuid,
            token_cost=token_cost + (pdf_tokens or 0),
            skip_quality_evaluation=True,
            ai_translation_file_and_languages=selected_ai_pairs or None,
        )
    except VerifyAPIError as e:
        # Definite non-billable rejection (401/402/403): release so a later
        # redelivery or balance top-up can retry. Non-admins have no Accept UI.
        await release_ray_event_notification(event)
        status = (
            _("Insufficient AI token balance to accept this quote.")
            if e.status_code == 402
            else _(
                "There was an error accepting your quote. Please try again or contact your administrator."
            )
        )
        await client.chat_postMessage(channel=notify_channel_id, text=status)
        return
    except Exception as e:
        # Ambiguous failure (timeout, 5xx, connection drop): the debit may have
        # landed, so never let the producer redeliver this event.
        notify_exception(e, "Preaccepted AI quote acceptance failed")
        logger.error(
            "Preaccepted AI quote acceptance failed for job %s", job_uuid, exc_info=True
        )
        await client.chat_postMessage(
            channel=notify_channel_id,
            text=_(
                "There was an error accepting your quote. Please try again or contact your administrator."
            ),
        )
        return

    try:
        selected_language_costs = (
            filter_language_costs_by_pairs(language_costs, selected_ai_pairs)
            if language_costs
            else None
        )
        await save_evaluate_quote_session(
            job_uuid,
            channel_id=notify_channel_id,
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
                "ai_translation_file_and_languages": selected_ai_pairs,
                "ai_quote_details": quote.get("details") or [],
                "all_language_costs": language_costs,
                "language_costs": selected_language_costs or language_costs,
                "file_uuids": _source_file_uuids(job_data),
            },
            message_ts=str(prequote_message_ts) if prequote_message_ts else None,
        )
        if status_on_success:
            await client.chat_postMessage(
                channel=notify_channel_id, text=status_on_success
            )
    except Exception as e:
        # The tokens are already spent; a retry would double-charge. Alert and
        # let the job continue without its Slack session snapshot.
        notify_exception(e, "Failed to persist accepted AI quote session after debit")
        logger.error(
            "Failed to persist accepted AI quote session for job %s after debit",
            job_uuid,
            exc_info=True,
        )


def evaluate_quote_event_error(event_name: str, error: Exception) -> HTTPException:
    return HTTPException(
        422,
        {
            "message": f"Error processing {event_name} event: {str(error)}",
        },
    )

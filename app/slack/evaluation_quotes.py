"""Redis session storage for sequential evaluate quote confirmation."""

from __future__ import annotations

import json
import logging
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.config import config
from app.redis import redis_conn
from app.slack.templates.messages import EvaluationCreditsQuoteMessage

logger = logging.getLogger(__name__)

EVALUATE_QUOTE_KEY_PREFIX = "slack-ray-translator:evaluate-quote:"

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
    ai_message_ts: str | None = None,
) -> None:
    existing = await get_evaluate_quote_session(job_uuid)
    # Preserve Slack timestamps when callers omit them so a later save cannot
    # wipe the HT quote ts needed for in-place post-QE updates. Only keep
    # message_ts when it is already distinct from ai_message_ts (the HT quote).
    if existing:
        existing_ai_message_ts = existing.get("ai_message_ts")
        existing_message_ts = existing.get("message_ts")
        if ai_message_ts is None and isinstance(existing_ai_message_ts, str):
            if existing_ai_message_ts:
                ai_message_ts = existing_ai_message_ts
        if (
            message_ts is None
            and isinstance(existing_message_ts, str)
            and existing_message_ts
            and isinstance(existing_ai_message_ts, str)
            and existing_ai_message_ts
            and existing_message_ts != existing_ai_message_ts
        ):
            message_ts = existing_message_ts
    payload = {
        "channel_id": channel_id,
        "user_id": user_id,
        "team_id": team_id,
        "stage": stage,
        "pdf_page_count": pdf_page_count,
        "quote_snapshot": quote_snapshot or {},
        "message_ts": message_ts,
    }
    if ai_message_ts:
        payload["ai_message_ts"] = ai_message_ts
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


async def update_evaluate_quote_session(
    job_uuid: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    """Update selected quote fields while preserving the current session."""
    session = await get_evaluate_quote_session(job_uuid)
    if not session:
        return None
    session.update(updates)
    await redis_conn.set(
        _quote_key(job_uuid),
        json.dumps(session),
        ex=config.evaluate_quote_ttl_seconds,
    )
    return session


async def update_evaluate_quote_stage(job_uuid: str, stage: str) -> None:
    await update_evaluate_quote_session(job_uuid, {"stage": stage})


async def update_evaluate_quote_slack_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str,
    service_label: str,
    token_cost: int,
    job_uuid: str,
    accept_action_id: str,
    adjust_action_id: str | None = None,
    pdf_page_count: int | None = None,
    pdf_tokens: int | None = None,
    actions: bool = False,
    status_message: str | None = None,
    download_translations_job_uuid: str | None = None,
    is_ibm: bool = False,
    language_costs: list[dict[str, Any]] | None = None,
) -> None:
    """Replace the original evaluate quote message in place."""
    message = EvaluationCreditsQuoteMessage(
        service_label=service_label,
        token_cost=token_cost,
        job_uuid=job_uuid,
        accept_action_id=accept_action_id,
        adjust_action_id=adjust_action_id,
        pdf_page_count=pdf_page_count,
        pdf_tokens=pdf_tokens,
        actions=actions,
        status_message=status_message,
        download_translations_job_uuid=download_translations_job_uuid,
        is_ibm=is_ibm,
        language_costs=language_costs,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.text,
        blocks=message.blocks,
    )

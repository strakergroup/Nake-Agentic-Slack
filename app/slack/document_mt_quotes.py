from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from slack_sdk.web.async_client import AsyncWebClient

from app.config import config
from app.redis import redis_conn
from app.slack.templates.messages import DocumentMtQuoteMessage

QUOTE_STATUS_PENDING = "pending"
QUOTE_STATUS_QUOTED = "quoted"
QUOTE_STATUS_ACCEPTED = "accepted"
QUOTE_STATUS_CANCELLED = "cancelled"

DOCUMENT_MT_QUOTE_KEY_PREFIX = "slack-ray-translator:document-mt-quote"
DOCUMENT_MT_QUOTE_LOCK_PREFIX = "slack-ray-translator:document-mt-quote-lock"


def new_document_mt_quote_id() -> str:
    return str(uuid4())


def document_mt_quote_key(quote_id: str) -> str:
    return f"{DOCUMENT_MT_QUOTE_KEY_PREFIX}:{quote_id}"


def document_mt_quote_lock_key(quote_id: str) -> str:
    return f"{DOCUMENT_MT_QUOTE_LOCK_PREFIX}:{quote_id}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_cached_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode()
    if not isinstance(value, str):
        return None
    return json.loads(value)


async def save_document_mt_quote_session(session: dict[str, Any]) -> None:
    session = dict(session)
    session.setdefault("created_at", _utc_now_iso())
    session.setdefault("updated_at", _utc_now_iso())
    await redis_conn.set(
        document_mt_quote_key(session["quote_id"]),
        json.dumps(session),
        ex=config.document_mt_quote_ttl_seconds,
    )


async def get_document_mt_quote_session(quote_id: str) -> dict[str, Any] | None:
    return _decode_cached_json(await redis_conn.get(document_mt_quote_key(quote_id)))


async def update_document_mt_quote_session(
    quote_id: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    session = await get_document_mt_quote_session(quote_id)
    if session is None:
        return None
    session.update(updates)
    session["updated_at"] = _utc_now_iso()
    await save_document_mt_quote_session(session)
    return session


async def apply_document_mt_quote_result(
    quote_id: str,
    quote_data: dict[str, Any],
) -> dict[str, Any] | None:
    return await update_document_mt_quote_session(
        quote_id,
        {
            "status": QUOTE_STATUS_QUOTED,
            "quote": quote_data,
            "preflight_task_uuid": quote_data.get("preflight_task_uuid"),
        },
    )


async def mark_document_mt_quote_accepted(
    quote_id: str,
) -> dict[str, Any] | None:
    return await update_document_mt_quote_session(
        quote_id,
        {"status": QUOTE_STATUS_ACCEPTED, "accepted_at": _utc_now_iso()},
    )


async def delete_document_mt_quote_session(quote_id: str) -> None:
    await redis_conn.delete(document_mt_quote_key(quote_id))


async def update_document_mt_quote_slack_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str,
    session: dict[str, Any],
    actions: bool = True,
    status_message: str | None = None,
) -> None:
    """Replace the original document MT quote message in place."""
    message = DocumentMtQuoteMessage(
        session,
        actions=actions,
        status_message=status_message,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.text,
        blocks=message.blocks,
    )

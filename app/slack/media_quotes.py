"""Media transcription / embedding / translation quote sessions (Redis)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.auth.connector import duration_to_subtitling_tokens, duration_to_tokens
from app.config import config
from app.ray.utils import format_currency, is_ibm_enterprise
from app.redis import redis_conn
from app.translate import _

# Align with int-slack-verify-consumer / LanguageCloud SOW MT rate.
SOW_TOKENS_PER_CHARACTER = 0.002
AI_TOKEN_USD_RATE = 0.02

MEDIA_QUOTE_KEY_PREFIX = "slack-ray-translator:media-quote"
MEDIA_QUOTE_LOCK_PREFIX = "slack-ray-translator:media-quote-lock"

STAGE_AWAITING_TRANSCRIPTION_ACCEPT = "awaiting_transcription_accept"
STAGE_TRANSCRIBING = "transcribing"
STAGE_AWAITING_TRANSLATION_ACCEPT = "awaiting_translation_accept"
STAGE_TRANSLATING = "translating"
STAGE_EMBEDDING = "embedding"
STAGE_DONE = "done"
STAGE_CANCELLED = "cancelled"

PIPELINE_TRANSCRIBE = "transcribe"
PIPELINE_TRANSCRIBE_TRANSLATE = "transcribe_translate"
PIPELINE_TRANSCRIBE_TRANSLATE_EMBED = "transcribe_translate_embed"
PIPELINE_EMBED = "embed"

ACTION_MEDIA_QUOTE_ACCEPT = "media_quote_accept"
ACTION_MEDIA_QUOTE_CANCEL = "media_quote_cancel"
ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT = "media_translation_quote_accept"
ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL = "media_translation_quote_cancel"


def new_media_quote_id() -> str:
    return str(uuid4())


def media_quote_key(quote_id: str) -> str:
    return f"{MEDIA_QUOTE_KEY_PREFIX}:{quote_id}"


def media_quote_lock_key(quote_id: str) -> str:
    return f"{MEDIA_QUOTE_LOCK_PREFIX}:{quote_id}"


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


def media_translation_tokens(source_text_length: int, target_count: int) -> int:
    """``ceil(source_text_length × target_count × 0.002)`` — same as document SRT MT."""
    if source_text_length <= 0 or target_count <= 0:
        return 0
    return math.ceil(source_text_length * target_count * SOW_TOKENS_PER_CHARACTER)


def transcription_tokens_for_duration(duration_ms: int) -> int:
    return duration_to_tokens(max(duration_ms, 0))


def embedding_tokens_for_duration(duration_ms: int, target_count: int) -> int:
    if duration_ms <= 0 or target_count <= 0:
        return 0
    return duration_to_subtitling_tokens(duration_ms) * target_count


def build_quote1_line_items(
    *,
    pipeline_kind: str,
    duration_ms: int,
    target_count: int,
) -> list[dict[str, Any]]:
    """Priced line items for Quote1 (transcription and/or embedding)."""
    items: list[dict[str, Any]] = []
    if pipeline_kind in (
        PIPELINE_TRANSCRIBE,
        PIPELINE_TRANSCRIBE_TRANSLATE,
        PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    ):
        items.append(
            {
                "label": _("Transcription"),
                "tokens": transcription_tokens_for_duration(duration_ms),
            }
        )
    if pipeline_kind in (PIPELINE_TRANSCRIBE_TRANSLATE_EMBED, PIPELINE_EMBED):
        items.append(
            {
                "label": _("Subtitle embedding"),
                "tokens": embedding_tokens_for_duration(
                    duration_ms, max(target_count, 1)
                ),
            }
        )
    return items


def total_tokens_from_line_items(line_items: list[dict[str, Any]]) -> int:
    return sum(int(item.get("tokens") or 0) for item in line_items)


def _format_quote_cost(token_count: int, *, is_ibm: bool) -> str:
    if is_ibm:
        return f"{token_count:,} {_('tokens')}"
    return format_currency(token_count * AI_TOKEN_USD_RATE, "USD")


def media_quote_blocks(
    session: dict[str, Any],
    *,
    accept_action_id: str,
    cancel_action_id: str,
    actions: bool = True,
    status_message: str | None = None,
) -> list[dict[str, Any]]:
    """Service Quote blocks for media Quote1 / Quote2."""
    line_items = session.get("line_items") or []
    total_tokens = int(
        session.get("total_tokens") or total_tokens_from_line_items(line_items)
    )
    is_ibm = is_ibm_enterprise(session.get("enterprise_id"))
    cost_label = _("Cost")
    total_label = _("Total cost")

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": _("Service Quote"), "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _(
                    "Review the quote below and click *Accept Quote* to continue."
                ),
            },
        },
        {"type": "divider"},
    ]

    file_name = session.get("file_name")
    if file_name:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{_('File')}:* `{file_name}`",
                },
            }
        )

    for item in line_items:
        label = str(item.get("label") or _("Service"))
        tokens = int(item.get("tokens") or 0)
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*{_('Service')}:*\n{label}"},
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*{cost_label}:*\n"
                            f"{_format_quote_cost(tokens, is_ibm=is_ibm)}"
                        ),
                    },
                ],
            }
        )

    blocks.extend(
        [
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{total_label}:* "
                        f"{_format_quote_cost(total_tokens, is_ibm=is_ibm)}"
                    ),
                },
            },
        ]
    )

    if status_message:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": status_message},
            }
        )

    if actions:
        quote_id = str(session["quote_id"])
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Accept Quote"),
                        },
                        "style": "primary",
                        "value": quote_id,
                        "action_id": accept_action_id,
                    },
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _("Cancel"),
                        },
                        "style": "danger",
                        "value": quote_id,
                        "action_id": cancel_action_id,
                    },
                ],
            }
        )
    return blocks


async def save_media_quote_session(session: dict[str, Any]) -> None:
    session = dict(session)
    session.setdefault("created_at", _utc_now_iso())
    session["updated_at"] = _utc_now_iso()
    await redis_conn.set(
        media_quote_key(session["quote_id"]),
        json.dumps(session),
        ex=config.media_quote_ttl_seconds,
    )


async def get_media_quote_session(quote_id: str) -> dict[str, Any] | None:
    return _decode_cached_json(await redis_conn.get(media_quote_key(quote_id)))


async def update_media_quote_session(
    quote_id: str,
    updates: dict[str, Any],
) -> dict[str, Any] | None:
    session = await get_media_quote_session(quote_id)
    if session is None:
        return None
    session.update(updates)
    session["updated_at"] = _utc_now_iso()
    await save_media_quote_session(session)
    return session


async def delete_media_quote_session(quote_id: str) -> None:
    await redis_conn.delete(media_quote_key(quote_id))


async def create_media_quote_session(
    *,
    pipeline_kind: str,
    stage: str,
    user_id: str,
    team_id: str,
    enterprise_id: str | None,
    channel_id: str,
    thread_ts: str | None,
    file_info: dict[str, Any],
    download_url: str,
    target_languages: list[str] | None = None,
    target_language_names: list[str] | None = None,
    submission_id: int | None = None,
    submission_ids: list[int] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create and persist a Quote1 (or Quote2) media session."""
    duration_ms = int(file_info.get("duration_ms") or 0)
    target_languages = target_languages or []
    target_count = len(target_languages) if target_languages else 1

    if stage == STAGE_AWAITING_TRANSLATION_ACCEPT:
        source_text_length = int((extra or {}).get("source_text_length") or 0)
        line_items = [
            {
                "label": _("AI Translation"),
                "tokens": media_translation_tokens(
                    source_text_length, len(target_languages) or 1
                ),
            }
        ]
    else:
        line_items = build_quote1_line_items(
            pipeline_kind=pipeline_kind,
            duration_ms=duration_ms,
            target_count=target_count,
        )

    session: dict[str, Any] = {
        "quote_id": new_media_quote_id(),
        "pipeline_kind": pipeline_kind,
        "stage": stage,
        "user_id": user_id,
        "team_id": team_id,
        "enterprise_id": enterprise_id,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "file_id": file_info["file_id"],
        "file_name": file_info["file_name"],
        "duration_ms": duration_ms,
        "download_url": download_url,
        "target_languages": target_languages,
        "target_language_names": target_language_names or [],
        "submission_id": submission_id,
        "submission_ids": submission_ids or [],
        "line_items": line_items,
        "total_tokens": total_tokens_from_line_items(line_items),
        "task_uuid": None,
        "quote_message_ts": None,
    }
    if extra:
        session.update(extra)
    await save_media_quote_session(session)
    return session

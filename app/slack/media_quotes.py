"""Media transcription / embedding / translation quote sessions (Redis)."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.auth.connector import (
    RayConnection,
    duration_to_subtitling_tokens,
    duration_to_tokens,
    user_may_receive_quotes,
)
from app.config import config
from app.ray.utils import format_slack_usd
from app.redis import redis_conn
from app.translate import _

# Align with int-slack-verify-consumer / LanguageCloud SOW MT rate.
SOW_TOKENS_PER_CHARACTER = 0.002
AI_TOKEN_USD_RATE = 0.02

MEDIA_QUOTE_KEY_PREFIX = "slack-ray-translator:media-quote"
MEDIA_QUOTE_LOCK_PREFIX = "slack-ray-translator:media-quote-lock"
MEDIA_QUOTE_THREAD_PREFIX = "slack-ray-translator:media-quote-thread"

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


def translate_resume_pipeline_type(session: dict[str, Any]) -> str:
    """Quote 2 resume job type. Configure never auto-chains embed after MT."""
    if session.get("workflow_type"):
        return "translate_only"
    if session.get("pipeline_kind") == PIPELINE_TRANSCRIBE_TRANSLATE_EMBED:
        return "translate_embed"
    return "translate_only"


def new_media_quote_id() -> str:
    return str(uuid4())


def media_quote_key(quote_id: str) -> str:
    return f"{MEDIA_QUOTE_KEY_PREFIX}:{quote_id}"


def media_quote_lock_key(quote_id: str) -> str:
    return f"{MEDIA_QUOTE_LOCK_PREFIX}:{quote_id}"


def media_quote_thread_key(channel_id: str, thread_ts: str) -> str:
    return f"{MEDIA_QUOTE_THREAD_PREFIX}:{channel_id}:{thread_ts}"


def _thread_quote_ids_from_raw(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str) or not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if item]
        return []
    return [raw]


async def thread_quote_ids(channel_id: str, thread_ts: str) -> list[str]:
    return _thread_quote_ids_from_raw(
        await redis_conn.get(media_quote_thread_key(channel_id, thread_ts))
    )


def _ray_from_context(context: Any) -> RayConnection | None:
    getter = getattr(context, "get", None)
    value = getter("ray") if callable(getter) else None
    return value if isinstance(value, RayConnection) else None


async def media_quote_actor_may_continue(session: dict[str, Any], context: Any) -> bool:
    if session.get("user_id") == context["user_id"]:
        return True
    ray = _ray_from_context(context)
    if ray is None:
        from app.slack.middleware import populate_ray_connection

        await populate_ray_connection(context)
        ray = _ray_from_context(context)
    return await user_may_receive_quotes(ray)


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


def translated_embed_tokens_for_session(
    session: dict[str, Any], *, language_count: int
) -> int:
    """Mux tokens for Quote2 when translated embedding is selected."""
    if not session.get("embed_translated"):
        return 0
    return embedding_tokens_for_duration(
        int(session.get("duration_ms") or 0), language_count
    )


def translated_embed_language_detail(language_count: int) -> str:
    """Singular or plural language copy for Quote2 translated embed rows."""
    if language_count == 1:
        return _("1 language")
    return f"{language_count} {_('languages')}"


def build_quote1_line_items(
    *,
    pipeline_kind: str,
    duration_ms: int,
    target_count: int,
    embed_source: bool | None = None,
) -> list[dict[str, Any]]:
    """Priced line items for Quote1 (transcription and/or source embedding)."""
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
    include_source_embed = (
        embed_source
        if embed_source is not None
        else pipeline_kind in (PIPELINE_TRANSCRIBE_TRANSLATE_EMBED, PIPELINE_EMBED)
    )
    if include_source_embed:
        source_embed_langs = 1 if embed_source is True else max(target_count, 1)
        items.append(
            {
                "label": (
                    _("Source subtitle embedding")
                    if embed_source is True
                    else _("Subtitle embedding")
                ),
                "tokens": embedding_tokens_for_duration(
                    duration_ms, source_embed_langs
                ),
            }
        )
    return items


def build_quote2_line_items(
    *,
    source_text_length: int,
    target_count: int,
    duration_ms: int,
    embed_translated: bool = False,
) -> list[dict[str, Any]]:
    """Priced line items for Quote2 (AI translation and optional translated embed)."""
    items: list[dict[str, Any]] = [
        {
            "label": _("AI Translation"),
            "tokens": media_translation_tokens(source_text_length, target_count or 1),
        }
    ]
    if embed_translated:
        items.append(
            {
                "label": _("Translated subtitle embedding"),
                "tokens": embedding_tokens_for_duration(
                    duration_ms, max(target_count, 1)
                ),
            }
        )
    return items


def total_tokens_from_line_items(line_items: list[dict[str, Any]]) -> int:
    return sum(int(item.get("tokens") or 0) for item in line_items)


def _format_quote_cost(token_count: int) -> str:
    """Always display media quote costs in USD ($0.02 per AI token)."""
    return format_slack_usd(token_count * AI_TOKEN_USD_RATE)


def media_quote_intro_text(session: dict[str, Any]) -> str:
    """Intro copy under the Service Quote header for Quote1 / Quote2."""
    stage = session.get("stage")
    pipeline_kind = session.get("pipeline_kind")
    if stage == STAGE_AWAITING_TRANSLATION_ACCEPT:
        return _("Running the AI translation will incur the following cost:")
    if pipeline_kind in (
        PIPELINE_TRANSCRIBE_TRANSLATE,
        PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    ):
        return _(
            "To estimate the cost of AI translation, your source file(s) must first "
            "be transcribed. The following transcription service charges will apply:"
        )
    return _("Review the quote below and click *Accept Quote* to continue.")


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
                "text": media_quote_intro_text(session),
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
                        "text": f"*{cost_label}:*\n{_format_quote_cost(tokens)}",
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
                    "text": f"*{total_label}:* {_format_quote_cost(total_tokens)}",
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
    ttl = config.media_quote_ttl_seconds
    await redis_conn.set(
        media_quote_key(session["quote_id"]),
        json.dumps(session),
        ex=ttl,
    )
    channel_id = session.get("channel_id")
    thread_ts = session.get("thread_ts")
    if channel_id and thread_ts:
        quote_ids = await thread_quote_ids(str(channel_id), str(thread_ts))
        quote_id = str(session["quote_id"])
        if quote_id not in quote_ids:
            quote_ids.append(quote_id)
        await redis_conn.set(
            media_quote_thread_key(str(channel_id), str(thread_ts)),
            json.dumps(quote_ids),
            ex=ttl,
        )


async def get_media_quote_session(quote_id: str) -> dict[str, Any] | None:
    return _decode_cached_json(await redis_conn.get(media_quote_key(quote_id)))


async def get_media_quote_session_for_thread(
    channel_id: str, thread_ts: str
) -> dict[str, Any] | None:
    sessions = await get_media_quote_sessions_for_thread(channel_id, thread_ts)
    return sessions[-1] if sessions else None


async def get_media_quote_sessions_for_thread(
    channel_id: str, thread_ts: str
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    for quote_id in await thread_quote_ids(channel_id, thread_ts):
        session = await get_media_quote_session(quote_id)
        if session is not None:
            sessions.append(session)
    return sessions


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

    extra = extra or {}
    embed_source = extra.get("embed_source")
    embed_translated = bool(extra.get("embed_translated"))

    if stage == STAGE_AWAITING_TRANSLATION_ACCEPT:
        source_text_length = int(extra.get("source_text_length") or 0)
        line_items = build_quote2_line_items(
            source_text_length=source_text_length,
            target_count=len(target_languages) or 1,
            duration_ms=duration_ms,
            embed_translated=embed_translated,
        )
    else:
        line_items = build_quote1_line_items(
            pipeline_kind=pipeline_kind,
            duration_ms=duration_ms,
            target_count=target_count,
            embed_source=embed_source if isinstance(embed_source, bool) else None,
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

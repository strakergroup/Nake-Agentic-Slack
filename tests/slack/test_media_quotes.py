"""Tests for media quote session helpers and pricing."""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import pytest

from app.slack.media_quotes import (
    PIPELINE_EMBED,
    PIPELINE_TRANSCRIBE,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    SOW_TOKENS_PER_CHARACTER,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    build_quote1_line_items,
    embedding_tokens_for_duration,
    media_quote_blocks,
    media_quote_key,
    media_translation_tokens,
    transcription_tokens_for_duration,
)


def test_media_quote_key():
    assert media_quote_key("abc") == "slack-ray-translator:media-quote:abc"


def test_transcription_tokens_for_one_minute():
    # $2/min at $0.02/token => 100 tokens/min
    assert transcription_tokens_for_duration(60_000) == 100


def test_embedding_tokens_for_one_minute_two_langs():
    # $0.60/min at $0.02/token => 30 tokens/min/lang
    assert embedding_tokens_for_duration(60_000, 2) == 60


def test_media_translation_tokens_matches_sow_rate():
    assert media_translation_tokens(500, 1) == math.ceil(500 * SOW_TOKENS_PER_CHARACTER)
    assert media_translation_tokens(200, 2) == math.ceil(
        200 * 2 * SOW_TOKENS_PER_CHARACTER
    )
    assert media_translation_tokens(0, 1) == 0
    assert media_translation_tokens(100, 0) == 0


def test_quote1_line_items_transcribe_only():
    items = build_quote1_line_items(
        pipeline_kind=PIPELINE_TRANSCRIBE,
        duration_ms=60_000,
        target_count=1,
    )
    assert len(items) == 1
    assert items[0]["tokens"] == 100


def test_quote1_line_items_transcribe_translate_no_translation_yet():
    items = build_quote1_line_items(
        pipeline_kind=PIPELINE_TRANSCRIBE_TRANSLATE,
        duration_ms=60_000,
        target_count=3,
    )
    assert len(items) == 1
    assert items[0]["tokens"] == 100


def test_quote1_line_items_full_embed():
    items = build_quote1_line_items(
        pipeline_kind=PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
        duration_ms=60_000,
        target_count=2,
    )
    assert len(items) == 2
    assert items[0]["tokens"] == 100
    assert items[1]["tokens"] == 60


def test_quote1_line_items_embed_only():
    items = build_quote1_line_items(
        pipeline_kind=PIPELINE_EMBED,
        duration_ms=60_000,
        target_count=1,
    )
    assert len(items) == 1
    assert items[0]["tokens"] == 30


def test_media_quote_blocks_include_accept_cancel():
    session = {
        "quote_id": "q-1",
        "file_name": "clip.mp4",
        "enterprise_id": None,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "line_items": [{"label": "Transcription", "tokens": 100}],
        "total_tokens": 100,
    }
    blocks = media_quote_blocks(
        session,
        accept_action_id="media_quote_accept",
        cancel_action_id="media_quote_cancel",
        actions=True,
    )
    assert blocks[0]["type"] == "header"
    assert (
        blocks[1]["text"]["text"]
        == "Review the quote below and click *Accept Quote* to continue."
    )
    actions = [b for b in blocks if b.get("type") == "actions"]
    assert len(actions) == 1
    action_ids = [el["action_id"] for el in actions[0]["elements"]]
    assert action_ids == ["media_quote_accept", "media_quote_cancel"]


def test_media_quote1_intro_explains_transcription_before_ai_translate():
    session = {
        "quote_id": "q-tt",
        "file_name": "clip.mp4",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "line_items": [{"label": "Transcription", "tokens": 100}],
        "total_tokens": 100,
    }
    blocks = media_quote_blocks(
        session,
        accept_action_id="media_quote_accept",
        cancel_action_id="media_quote_cancel",
        actions=False,
    )
    assert "must first be transcribed" in blocks[1]["text"]["text"]
    assert "transcription service charges will apply" in blocks[1]["text"]["text"]


def test_media_quote2_intro_matches_document_ai_copy():
    session = {
        "quote_id": "q-2",
        "file_name": "clip.mp4",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "line_items": [{"label": "AI Translation", "tokens": 50}],
        "total_tokens": 50,
    }
    blocks = media_quote_blocks(
        session,
        accept_action_id="media_translation_quote_accept",
        cancel_action_id="media_translation_quote_cancel",
        actions=False,
    )
    assert (
        blocks[1]["text"]["text"]
        == "Running the AI translation will incur the following cost:"
    )


def test_media_quote_blocks_always_show_usd():
    """Media quotes display USD for all workspaces, including IBM."""
    session = {
        "quote_id": "q-ibm",
        "file_name": "clip.mp4",
        "enterprise_id": "EIBM",
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "line_items": [{"label": "Transcription", "tokens": 100}],
        "total_tokens": 100,
    }
    with patch(
        "app.slack.media_quotes.format_slack_usd",
        return_value="USD 2.00",
    ) as mock_format:
        blocks = media_quote_blocks(
            session,
            accept_action_id="media_quote_accept",
            cancel_action_id="media_quote_cancel",
            actions=False,
        )

    mock_format.assert_called()
    # 100 tokens × $0.02
    assert mock_format.call_args_list[0].args[0] == pytest.approx(2.0)
    block_text = " ".join(
        str(block.get("text", {}).get("text", ""))
        + " ".join(field.get("text", "") for field in block.get("fields", []) or [])
        for block in blocks
    )
    assert "USD 2.00" in block_text
    assert "tokens" not in block_text.lower()


@pytest.mark.asyncio
async def test_create_media_quote_session_persists():
    from app.slack.media_quotes import create_media_quote_session

    with patch("app.slack.media_quotes.redis_conn") as mock_redis:
        mock_redis.set = AsyncMock()
        session = await create_media_quote_session(
            pipeline_kind=PIPELINE_TRANSCRIBE,
            stage=STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            thread_ts="123.456",
            file_info={
                "file_id": "F1",
                "file_name": "a.mp4",
                "duration_ms": 60_000,
            },
            download_url="https://example.com/a.mp4",
            submission_id=42,
        )
        assert session["stage"] == STAGE_AWAITING_TRANSCRIPTION_ACCEPT
        assert session["total_tokens"] == 100
        assert session["submission_id"] == 42
        mock_redis.set.assert_awaited()


@pytest.mark.asyncio
async def test_create_translation_quote_session():
    from app.slack.media_quotes import create_media_quote_session

    with patch("app.slack.media_quotes.redis_conn") as mock_redis:
        mock_redis.set = AsyncMock()
        session = await create_media_quote_session(
            pipeline_kind=PIPELINE_TRANSCRIBE_TRANSLATE,
            stage=STAGE_AWAITING_TRANSLATION_ACCEPT,
            user_id="U1",
            team_id="T1",
            enterprise_id=None,
            channel_id="C1",
            thread_ts=None,
            file_info={
                "file_id": "F1",
                "file_name": "a.mp4",
                "duration_ms": 60_000,
            },
            download_url="https://example.com/a.mp4",
            target_languages=["es", "fr"],
            extra={"source_text_length": 1000},
        )
        assert session["stage"] == STAGE_AWAITING_TRANSLATION_ACCEPT
        assert session["total_tokens"] == media_translation_tokens(1000, 2)
        assert session["line_items"][0]["label"]

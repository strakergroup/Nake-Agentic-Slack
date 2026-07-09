"""Tests for media quote accept / cancel action helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.slack.media_quotes import (
    PIPELINE_TRANSCRIBE,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_TRANSCRIBING,
)


@pytest.mark.asyncio
async def test_accept_media_quote_requires_awaiting_stage():
    from app.slack.media_quote_actions import accept_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U1",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.get = lambda key, default=None: None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_TRANSCRIBING,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
        "channel_id": "C1",
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.delete = AsyncMock()
            await accept_media_quote(
                client=client,
                body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
                action={"value": "q1"},
                context=context,
            )

    client.chat_postMessage.assert_awaited()
    assert "not ready" in client.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_accept_media_quote_lock_contention():
    from app.slack.media_quote_actions import accept_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {
        "user_id": "U1",
        "team_id": "T1",
    }.get(key)
    context.enterprise_id = None
    context.get = lambda key, default=None: None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
        "channel_id": "C1",
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=False)
            await accept_media_quote(
                client=client,
                body={},
                action={"value": "q1"},
                context=context,
            )

    assert "already in progress" in client.chat_postMessage.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_accept_translation_quote_resumes_phase():
    from app.slack.media_quote_actions import accept_media_translation_quote

    client = AsyncMock()
    context = {
        "user_id": "U1",
        "team_id": "T1",
        "ray": MagicMock(),
    }
    context_obj = MagicMock()
    context_obj.__getitem__ = lambda self, key: context[key]
    context_obj.get = lambda key, default=None: context.get(key, default)
    context_obj.enterprise_id = None
    context_obj["ray"].client = MagicMock()
    context_obj["ray"].client.id_token = "tok"
    context_obj["ray"].super_group = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "total_tokens": 2,
        "channel_id": "C1",
        "task_uuid": "task-1",
        "target_languages": ["es"],
        "thread_ts": None,
    }

    with patch(
        "app.slack.media_quote_actions.get_media_quote_session",
        new=AsyncMock(return_value=session),
    ):
        with patch("app.slack.media_quote_actions.redis_conn") as mock_redis:
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.delete = AsyncMock()
            with patch(
                "app.slack.media_quote_actions._require_ai_token_balance",
                new=AsyncMock(return_value=True),
            ):
                with patch(
                    "app.slack.media_quote_actions._resume_translate_phase",
                    new=AsyncMock(),
                ) as mock_resume:
                    with patch(
                        "app.slack.media_quote_actions.update_media_quote_session",
                        new=AsyncMock(return_value={**session, "stage": "translating"}),
                    ):
                        with patch(
                            "app.slack.media_quote_actions._update_quote_message",
                            new=AsyncMock(),
                        ):
                            await accept_media_translation_quote(
                                client=client,
                                body={
                                    "channel": {"id": "C1"},
                                    "message": {"ts": "1.2"},
                                },
                                action={"value": "q1"},
                                context=context_obj,
                            )

    mock_resume.assert_awaited_once()
    assert mock_resume.await_args.kwargs["task_uuid"] == "task-1"

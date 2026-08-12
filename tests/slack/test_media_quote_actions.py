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


@pytest.mark.asyncio
async def test_accept_translation_quote_reprices_selected_pairs():
    from app.slack.media_quote_actions import accept_media_translation_quote
    from app.slack.media_quotes import media_translation_tokens

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
        "total_tokens": media_translation_tokens(1000, 2),
        "channel_id": "C1",
        "task_uuid": "task-1",
        "file_id": "F1",
        "file_name": "clip.mp4",
        "source_text_length": 1000,
        "target_languages": ["es", "fr"],
        "target_language_names": ["Spanish", "French"],
        "selected_pairs": ["F1:es"],
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
            ) as mock_balance:
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

    assert mock_balance.await_args.args[2] == media_translation_tokens(1000, 1)
    assert mock_resume.await_args.kwargs["session"]["selected_pairs"] == ["F1:es"]


@pytest.mark.asyncio
async def test_cancel_media_quote_fails_submissions():
    from app.slack.media_quote_actions import cancel_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1"}.get(key)
    context.enterprise_id = None

    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "submission_id": 55,
        "channel_id": "C1",
    }

    with (
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_quote_actions.update_media_quote_session",
            new=AsyncMock(),
        ),
        patch(
            "app.slack.media_quote_actions.delete_media_quote_session",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.fail_media_submissions",
            new=AsyncMock(),
        ) as mock_fail,
    ):
        await cancel_media_quote(
            client=client,
            body={"channel": {"id": "C1"}, "message": {"ts": "1.2"}},
            action={"value": "q1"},
            context=context,
        )

    mock_fail.assert_awaited_once_with(session)
    client.chat_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_or_auto_start_falls_back_to_quote_when_balance_fails():
    """Non-admin auto-start must not strand the submission without Accept UI."""
    from app.slack.media_quote_actions import post_or_auto_start_media_quote

    client = AsyncMock()
    context = MagicMock()
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "channel_id": "C1",
        "stage": STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "total_tokens": 100,
    }

    with (
        patch(
            "app.slack.media_quote_actions.user_may_receive_quotes",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.slack.media_quote_actions.update_media_quote_session",
            new=AsyncMock(return_value={**session, "auto_proceed": True}),
        ),
        patch(
            "app.slack.media_quote_actions.get_media_quote_session",
            new=AsyncMock(return_value={**session, "auto_proceed": True}),
        ),
        patch(
            "app.slack.media_quote_actions.accept_media_quote",
            new=AsyncMock(return_value=False),
        ) as mock_accept,
        patch(
            "app.slack.media_quote_actions.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_post,
    ):
        await post_or_auto_start_media_quote(client, context, session)

    mock_accept.assert_awaited_once()
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_accept_translation_returns_false_without_ray_client():
    from app.slack.media_quote_actions import (
        auto_accept_media_translation_quote_if_needed,
    )

    client = AsyncMock()
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "team_id": "T1",
        "auto_proceed": True,
        "channel_id": "C1",
    }

    with (
        patch(
            "app.auth.connector.get_ray_client",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.auth.connector.get_ray_super_group",
            new=AsyncMock(return_value=[]),
        ),
    ):
        handled = await auto_accept_media_translation_quote_if_needed(client, session)

    assert handled is False

"""Tests for Configure media workflow adapters (review, replace, Quote 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.media.media_workflow import MediaWorkflowStage
from app.slack.media_quotes import (
    PIPELINE_TRANSCRIBE_TRANSLATE,
    PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    translate_resume_pipeline_type,
)


def test_translate_resume_pipeline_type_configure_never_auto_embeds():
    assert (
        translate_resume_pipeline_type(
            {
                "workflow_type": "transcribe_translate",
                "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
                "embed_translated": True,
            }
        )
        == "translate_only"
    )


def test_translate_resume_pipeline_type_legacy_full_embed_still_chains():
    assert (
        translate_resume_pipeline_type(
            {"pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE_EMBED}
        )
        == "translate_embed"
    )
    assert (
        translate_resume_pipeline_type({"pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE})
        == "translate_only"
    )


def test_review_srt_filename_matches_source_stem():
    from app.slack.media_workflow_actions import thread_srt_matches_review_file

    assert thread_srt_matches_review_file(
        uploaded_name="clip.srt",
        original_file_name="clip.mp4",
        stage=MediaWorkflowStage.AWAITING_SOURCE_REVIEW,
        target_languages=(),
    )
    assert not thread_srt_matches_review_file(
        uploaded_name="other.srt",
        original_file_name="clip.mp4",
        stage=MediaWorkflowStage.AWAITING_SOURCE_REVIEW,
        target_languages=(),
    )


@pytest.mark.asyncio
async def test_thread_srt_during_review_replaces_instead_of_embed_quote():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    client.conversations_history.return_value = {
        "messages": [{"files": [{"id": "V123", "name": "clip.mp4", "filetype": "mp4"}]}]
    }
    context = MagicMock()
    context.get.return_value = "C123"
    message = {
        "thread_ts": "123.456",
        "files": [{"id": "F9", "name": "clip.srt", "filetype": "srt"}],
    }
    session = {
        "quote_id": "q1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "file_name": "clip.mp4",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C123",
        "thread_ts": "123.456",
    }

    with (
        patch(
            "app.slack.listener_actions.require_ray_client",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.listener_actions.get_media_quote_session_for_thread",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.listener_actions.apply_thread_srt_review_replace",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_replace,
        patch(
            "app.slack.listener_actions.quote_existing_srt_embed_task",
            new_callable=AsyncMock,
        ) as mock_quote,
    ):
        handled = await maybe_show_thread_media_embed_option(client, context, message)

    assert handled is True
    mock_replace.assert_awaited_once()
    mock_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_srt_during_configure_does_not_open_leftover_embed_quote():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    client.conversations_history.return_value = {
        "messages": [{"files": [{"id": "V123", "name": "clip.mp4", "filetype": "mp4"}]}]
    }
    context = MagicMock()
    context.get.return_value = "C123"
    message = {
        "thread_ts": "123.456",
        "files": [{"id": "F9", "name": "unrelated.srt", "filetype": "srt"}],
    }
    session = {
        "quote_id": "q1",
        "stage": "awaiting_translation_accept",
        "workflow_type": "transcribe_translate",
        "file_name": "clip.mp4",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C123",
        "thread_ts": "123.456",
    }

    with (
        patch(
            "app.slack.listener_actions.require_ray_client",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.listener_actions.get_media_quote_session_for_thread",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.listener_actions.quote_existing_srt_embed_task",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_quote,
    ):
        handled = await maybe_show_thread_media_embed_option(client, context, message)

    assert handled is True
    mock_quote.assert_not_awaited()
    client.conversations_history.assert_not_called()


@pytest.mark.asyncio
async def test_approve_source_srt_posts_quote2_for_translate_workflow():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "target_language_names": ["Spanish"],
        "source_text_length": 500,
        "duration_ms": 60_000,
        "file_id": "F1",
        "file_name": "clip.mp4",
        "channel_id": "C1",
        "thread_ts": "1.2",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
    }
    with (
        patch(
            "app.slack.media_workflow_actions.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ) as mock_update,
        patch(
            "app.slack.media_workflow_actions.post_media_quote_message",
            new_callable=AsyncMock,
        ) as mock_post,
        patch(
            "app.slack.media_workflow_actions.auto_accept_media_translation_quote_if_needed",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_approve_continue(
            client=client,
            action={"value": "q1"},
            context=context,
        )

    assert mock_update.await_args.args[1]["stage"] == "awaiting_translation_accept"
    mock_post.assert_awaited_once()
    line_items = mock_update.await_args.args[1]["line_items"]
    labels = [item["label"] for item in line_items]
    assert "AI Translation" in labels
    assert "Translated subtitle embedding" in labels


@pytest.mark.asyncio
async def test_approve_source_srt_skips_when_quote_lock_held():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "1.2",
    }

    with (
        patch(
            "app.slack.media_workflow_actions.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.media_workflow_actions.execute_media_workflow_decision",
            new_callable=AsyncMock,
        ) as mock_execute,
        patch("app.slack.media_workflow_actions.redis_conn", create=True) as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=None)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_approve_continue(
            client=client,
            action={"value": "q1"},
            context=context,
        )

    mock_execute.assert_not_awaited()
    assert "already in progress" in client.chat_postMessage.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_approve_source_srt_transcribe_only_completes_submissions():
    from app.media.media_workflow import (
        MediaWorkflowEvent,
        advance_media_workflow,
        media_workflow_session_from_quote,
    )
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    session = {
        "quote_id": "q1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "submission_ids": [42],
    }
    decision = advance_media_workflow(
        media_workflow_session_from_quote(session),
        MediaWorkflowEvent.SOURCE_SRT_APPROVED,
    )
    client = AsyncMock()
    with (
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ) as mock_update,
        patch(
            "app.ray.events.media_pipeline_events.update_submission_status",
            new_callable=AsyncMock,
        ) as mock_complete,
    ):
        await execute_media_workflow_decision(
            client=client, session=session, decision=decision
        )

    assert mock_update.await_args.args[1]["stage"] == "done"
    mock_complete.assert_awaited_once()
    assert mock_complete.await_args.args[0]["submission_ids"] == [42]

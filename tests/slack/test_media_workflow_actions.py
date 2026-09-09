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


def test_translation_review_filename_matches_language_code_as_token():
    from app.slack.media_workflow_actions import thread_srt_matches_review_file

    assert thread_srt_matches_review_file(
        uploaded_name="clip_es.srt",
        original_file_name="clip.mp4",
        stage=MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
        target_languages=("es",),
    )
    assert thread_srt_matches_review_file(
        uploaded_name="es.srt",
        original_file_name="clip.mp4",
        stage=MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
        target_languages=("es",),
    )
    assert not thread_srt_matches_review_file(
        uploaded_name="files.srt",
        original_file_name="clip.mp4",
        stage=MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
        target_languages=("es",),
    )
    assert not thread_srt_matches_review_file(
        uploaded_name="attendance.srt",
        original_file_name="clip.mp4",
        stage=MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
        target_languages=("en",),
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
async def test_thread_srt_unrelated_name_opens_leftover_embed_quote():
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
    mock_quote.assert_awaited_once()


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
async def test_stale_srt_approve_tells_user():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "transcribing",
        "workflow_type": "transcribe_only",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
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
        patch("app.slack.media_workflow_actions.notify_exception"),
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_approve_continue(
            client=client,
            action={"value": "q1"},
            context=context,
        )

    mock_execute.assert_not_awaited()
    posted = client.chat_postMessage.await_args.kwargs
    assert posted["channel"] == "U1"
    assert "no longer" in posted["text"].lower() or "cannot" in posted["text"].lower()


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


@pytest.mark.asyncio
async def test_failed_source_embed_does_not_persist_embedding_stage():
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
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "task_uuid": "asr-task",
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
            "app.slack.media_configure_embed.resume_configure_embed_phase",
            new_callable=AsyncMock,
            side_effect=RuntimeError("embed enqueue failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="embed enqueue failed"):
            await execute_media_workflow_decision(
                client=client, session=session, decision=decision
            )

    persisted_stages = [
        call.args[1].get("stage") for call in mock_update.await_args_list
    ]
    assert "embedding_source" not in persisted_stages


@pytest.mark.asyncio
async def test_successful_source_embed_persists_embedding_stage():
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
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "task_uuid": "asr-task",
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
            "app.slack.media_configure_embed.resume_configure_embed_phase",
            new_callable=AsyncMock,
        ) as mock_embed,
    ):
        await execute_media_workflow_decision(
            client=client, session=session, decision=decision
        )

    mock_embed.assert_awaited_once()
    persisted_stages = [
        call.args[1].get("stage") for call in mock_update.await_args_list
    ]
    assert persisted_stages[-1] == "embedding_source"


@pytest.mark.asyncio
async def test_replace_modal_accepts_differently_named_srt():
    from app.slack.media_workflow_actions import handle_media_srt_replace_submit

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "file_name": "clip.mp4",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
    }
    view = {
        "private_metadata": "q1",
        "state": {
            "values": {
                "srt_file": {
                    "srt_file_input": {
                        "files": [{"id": "F9", "name": "edited.srt"}],
                    }
                }
            }
        },
    }

    with (
        patch(
            "app.slack.media_workflow_actions.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new_callable=AsyncMock,
            return_value="fs-replaced",
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ) as mock_update,
    ):
        await handle_media_srt_replace_submit(view=view, client=client, context=context)

    assert (
        mock_update.await_args.args[1]["approved_source_srt_file_id"] == "fs-replaced"
    )


@pytest.mark.asyncio
async def test_thread_srt_replace_upload_failure_tells_user_and_skips_embed_quote():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    context = MagicMock()
    context.get.return_value = "C123"
    message = {
        "thread_ts": "1.2",
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
        "thread_ts": "1.2",
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
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new_callable=AsyncMock,
            side_effect=RuntimeError("file server down"),
        ),
        patch("app.slack.media_workflow_actions.notify_exception"),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "app.slack.listener_actions.quote_existing_srt_embed_task",
            new_callable=AsyncMock,
        ) as mock_quote,
    ):
        handled = await maybe_show_thread_media_embed_option(client, context, message)

    assert handled is True
    mock_quote.assert_not_awaited()
    mock_update.assert_not_awaited()
    posted = client.chat_postMessage.await_args.kwargs
    assert posted["channel"] == "C123"
    assert posted["thread_ts"] == "1.2"
    assert "Could not replace" in posted["text"]

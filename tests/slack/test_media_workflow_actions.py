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


def test_translated_replace_language_reads_pipeline_language_name():
    from app.slack.media_workflow_actions import _translated_replace_language

    targets = ("fi", "fr")
    assert _translated_replace_language("tst (3) (1)_French.srt", targets) == "fr"
    assert _translated_replace_language("tst (3) (1)_Finnish.srt", targets) == "fi"
    assert _translated_replace_language("clip_es.srt", ("es", "fr")) == "es"
    assert _translated_replace_language("edited.srt", targets) is None


@pytest.mark.asyncio
async def test_unmatched_translated_thread_srt_is_not_a_replacement():
    from app.slack.media_workflow_actions import apply_thread_srt_review_replace

    client = AsyncMock()
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_translation_review",
        "workflow_type": "transcribe_translate",
        "file_name": "tst (3) (1).mp4",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["fi", "fr"],
        "channel_id": "C1",
        "thread_ts": "1.2",
    }

    with (
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new_callable=AsyncMock,
        ) as mock_upload,
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
        ) as mock_update,
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        handled = await apply_thread_srt_review_replace(
            client=client,
            session=session,
            slack_file_id="F9",
            uploaded_name="tst (3) (1)_notes.srt",
            acting_user_id="U1",
        )

    assert handled is False
    mock_upload.assert_not_awaited()
    mock_update.assert_not_awaited()
    client.chat_postMessage.assert_not_awaited()


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
            "app.slack.listener_actions.get_media_quote_sessions_for_thread",
            new_callable=AsyncMock,
            return_value=[session],
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
async def test_thread_srt_unrelated_name_during_configure_does_not_open_leftover_embed_quote():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    context = MagicMock()
    context.get.return_value = "C123"
    context.__getitem__ = lambda self, key: {"user_id": "U1", "channel_id": "C123"}[key]
    message = {
        "thread_ts": "123.456",
        "files": [{"id": "F9", "name": "unrelated.srt", "filetype": "srt"}],
    }
    session = {
        "quote_id": "q1",
        "user_id": "U1",
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
            "app.slack.listener_actions.get_media_quote_sessions_for_thread",
            new_callable=AsyncMock,
            return_value=[session],
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
    posted = client.chat_postMessage.await_args.kwargs
    assert "finish" in posted["text"].lower() or "current" in posted["text"].lower()


@pytest.mark.asyncio
async def test_unmatched_thread_srt_during_translation_review_does_not_open_embed_quote():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    client.conversations_history.return_value = {
        "messages": [
            {"files": [{"id": "V123", "name": "tst (3) (1).mp4", "filetype": "mp4"}]}
        ]
    }
    context = MagicMock()
    context.get.return_value = "C123"
    context.__getitem__ = lambda self, key: {"user_id": "U1", "channel_id": "C123"}[key]
    message = {
        "thread_ts": "123.456",
        "files": [{"id": "F9", "name": "tst (3) (1)_notes.srt", "filetype": "srt"}],
    }
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_translation_review",
        "workflow_type": "transcribe_translate",
        "file_name": "tst (3) (1).mp4",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["fi", "fr"],
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
            "app.slack.listener_actions.get_media_quote_sessions_for_thread",
            new_callable=AsyncMock,
            return_value=[session],
        ),
        patch(
            "app.slack.listener_actions.quote_existing_srt_embed_task",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_quote,
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new_callable=AsyncMock,
        ) as mock_upload,
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        handled = await maybe_show_thread_media_embed_option(client, context, message)

    assert handled is True
    mock_quote.assert_not_awaited()
    mock_upload.assert_not_awaited()
    posted = client.chat_postMessage.await_args.kwargs["text"].lower()
    assert "edit and reupload" in posted


@pytest.mark.asyncio
async def test_thread_srt_matches_second_file_when_first_review_name_differs():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    context = MagicMock()
    context.get.return_value = "C123"
    context.__getitem__ = lambda self, key: {"user_id": "U1", "channel_id": "C123"}[key]
    message = {
        "thread_ts": "123.456",
        "files": [{"id": "F9", "name": "b.srt", "filetype": "srt"}],
    }
    first = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "file_name": "a.mp4",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C123",
        "thread_ts": "123.456",
    }
    second = {
        **first,
        "quote_id": "q2",
        "file_name": "b.mp4",
    }

    with (
        patch(
            "app.slack.listener_actions.require_ray_client",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.listener_actions.get_media_quote_sessions_for_thread",
            new_callable=AsyncMock,
            return_value=[first, second],
        ),
        patch(
            "app.slack.listener_actions.apply_thread_srt_review_replace",
            new_callable=AsyncMock,
            side_effect=[False, True],
        ) as mock_replace,
        patch(
            "app.slack.listener_actions.quote_existing_srt_embed_task",
            new_callable=AsyncMock,
        ) as mock_quote,
    ):
        handled = await maybe_show_thread_media_embed_option(client, context, message)

    assert handled is True
    assert mock_replace.await_count == 2
    assert mock_replace.await_args_list[1].kwargs["session"]["quote_id"] == "q2"
    mock_quote.assert_not_awaited()


@pytest.mark.asyncio
async def test_thread_srt_leftover_embed_quote_after_configure_done():
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
        "stage": "done",
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
            "app.slack.listener_actions.get_media_quote_sessions_for_thread",
            new_callable=AsyncMock,
            return_value=[session],
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
async def test_quote_admin_can_approve_another_users_review():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U-admin", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U-owner",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "submission_ids": [42],
        "srt_review_message_ts": [],
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
        ),
        patch(
            "app.slack.media_quotes.user_may_receive_quotes",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.middleware.populate_ray_connection",
            new_callable=AsyncMock,
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_submission_status",
            new_callable=AsyncMock,
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

    texts = [
        str(call.kwargs.get("text") or "")
        for call in client.chat_postMessage.await_args_list
    ]
    assert all("permission" not in text.lower() for text in texts)


@pytest.mark.asyncio
async def test_non_admin_cannot_approve_another_users_review():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U-other", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U-owner",
        "stage": "awaiting_source_review",
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
            "app.slack.media_quotes.user_may_receive_quotes",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.slack.middleware.populate_ray_connection",
            new_callable=AsyncMock,
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

    posted = client.chat_postMessage.await_args.kwargs["text"].lower()
    assert "permission" in posted
    mock_redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_quote_admin_can_replace_another_users_review_file():
    from app.slack.media_workflow_actions import handle_media_srt_replace_submit

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U-admin", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U-owner",
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
                        "files": [{"id": "F9", "name": "clip.srt"}],
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
            "app.slack.media_quotes.user_may_receive_quotes",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.slack.middleware.populate_ray_connection",
            new_callable=AsyncMock,
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
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_replace_submit(view=view, client=client, context=context)

    stored = {}
    for call in mock_update.await_args_list:
        stored.update(call.args[1])
    assert stored["approved_source_srt_file_id"] == "fs-replaced"
    assert stored["stage"] == "done"


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
async def test_approve_source_srt_quote2_includes_source_embed_when_selected():
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
        "embed_source": True,
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
        ),
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

    line_items = mock_update.await_args.args[1]["line_items"]
    labels = [item["label"] for item in line_items]
    assert labels.index("Source subtitle embedding") < labels.index(
        "Translated subtitle embedding"
    )


@pytest.mark.asyncio
async def test_approve_source_srt_posts_deferred_word_transcript():
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
        "deferred_word_file_id": "docx-1",
        "deferred_word_file_name": "clip.docx",
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
        ),
        patch(
            "app.slack.media_workflow_actions.auto_accept_media_translation_quote_if_needed",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.slack.media_workflow_actions.post_deferred_word_transcript",
            new_callable=AsyncMock,
        ) as mock_word,
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_approve_continue(
            client=client,
            action={"value": "q1"},
            context=context,
        )

    mock_word.assert_awaited_once()
    assert mock_word.await_args.kwargs["word_file_id"] == "docx-1"
    assert mock_word.await_args.kwargs["word_file_name"] == "clip.docx"
    cleared = {}
    for call in mock_update.await_args_list:
        cleared.update(call.args[1])
    assert cleared.get("deferred_word_file_id") is None
    assert cleared.get("deferred_word_file_name") is None


@pytest.mark.asyncio
async def test_source_replace_auto_advances_without_approve_click():
    from app.slack.media_workflow_actions import apply_thread_srt_review_replace

    client = AsyncMock()
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
        "task_uuid": "task-1",
    }
    store = dict(session)

    async def _update(quote_id, updates):
        store.update(updates)
        return dict(store)

    context = MagicMock()
    with (
        patch(
            "app.slack.media_workflow_actions.media_quote_actor_may_continue",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new=AsyncMock(return_value="fs-new"),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(side_effect=_update),
        ),
        patch(
            "app.slack.media_workflow_actions.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_quote2,
        patch(
            "app.slack.media_workflow_actions.auto_accept_media_translation_quote_if_needed",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.slack.media_workflow_actions.post_deferred_word_transcript",
            new=AsyncMock(),
        ),
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        replaced = await apply_thread_srt_review_replace(
            client=client,
            session=session,
            slack_file_id="F9",
            uploaded_name="clip.srt",
            acting_user_id="U1",
            context=context,
        )

    assert replaced is True
    assert store["approved_source_srt_file_id"] == "fs-new"
    assert store["stage"] == "awaiting_translation_accept"
    mock_quote2.assert_awaited_once()
    posted = str(client.chat_postMessage.await_args_list)
    assert "Continuing with your updated subtitles" in posted
    assert "Click *Approve & Continue* when you are ready" not in posted


@pytest.mark.asyncio
async def test_translated_replace_still_waits_for_approve_click():
    from app.slack.media_workflow_actions import apply_thread_srt_review_replace

    client = AsyncMock()
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_translation_review",
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
        "task_uuid": "task-1",
    }
    store = dict(session)

    async def _update(quote_id, updates):
        store.update(updates)
        return dict(store)

    context = MagicMock()
    with (
        patch(
            "app.slack.media_workflow_actions.media_quote_actor_may_continue",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new=AsyncMock(return_value="fs-new-es"),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(side_effect=_update),
        ),
        patch(
            "app.slack.media_workflow_actions.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_quote2,
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        replaced = await apply_thread_srt_review_replace(
            client=client,
            session=session,
            slack_file_id="F9",
            uploaded_name="clip_Spanish.srt",
            acting_user_id="U1",
            language="es",
            context=context,
        )

    assert replaced is True
    assert store["approved_translated_srt_file_id"] == "fs-new-es"
    assert store["stage"] == "awaiting_translation_review"
    mock_quote2.assert_not_awaited()
    posted = str(client.chat_postMessage.await_args_list)
    assert "Click *Approve & Continue* when you are ready" in posted


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
async def test_source_embed_only_translate_approve_starts_embed_before_quote2():
    from app.media.media_workflow import (
        MediaWorkflowEvent,
        advance_media_workflow,
        media_workflow_session_from_quote,
    )
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    session = {
        "quote_id": "q1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "source_text_length": 500,
        "duration_ms": 60_000,
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
        patch(
            "app.slack.media_workflow_actions.post_media_quote_message",
            new_callable=AsyncMock,
        ) as mock_post,
        patch(
            "app.slack.media_workflow_actions.auto_accept_media_translation_quote_if_needed",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        await execute_media_workflow_decision(
            client=client, session=session, decision=decision
        )

    mock_embed.assert_awaited_once()
    mock_post.assert_not_awaited()
    persisted_stages = [
        call.args[1].get("stage") for call in mock_update.await_args_list
    ]
    assert "embedding_source" in persisted_stages
    assert "awaiting_translation_accept" not in persisted_stages


@pytest.mark.asyncio
async def test_auto_proceed_skips_source_review_and_starts_embed():
    from app.media.media_workflow import (
        MediaWorkflowCommand,
        MediaWorkflowEvent,
        advance_media_workflow,
        media_workflow_session_from_quote,
    )
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    session = {
        "quote_id": "q1",
        "stage": "transcribing",
        "workflow_type": "transcribe_only",
        "embed_source": True,
        "embed_translated": False,
        "auto_proceed": True,
        "channel_id": "C1",
        "thread_ts": "1.2",
        "task_uuid": "asr-task",
    }
    decision = advance_media_workflow(
        media_workflow_session_from_quote(session),
        MediaWorkflowEvent.TRANSCRIPTION_COMPLETED,
    )
    assert decision.commands == (MediaWorkflowCommand.POST_SOURCE_REVIEW,)
    client = AsyncMock()
    with (
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ),
        patch(
            "app.slack.media_configure_embed.resume_configure_embed_phase",
            new_callable=AsyncMock,
        ) as mock_embed,
    ):
        await execute_media_workflow_decision(
            client=client, session=session, decision=decision
        )

    mock_embed.assert_awaited_once()
    client.chat_postMessage.assert_not_awaited()


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
        "transcript_zip_entries": [
            {"file_id": "srt-old", "filename": "clip.srt"},
            {"file_id": "docx-1", "filename": "clip.docx"},
        ],
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
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_replace_submit(view=view, client=client, context=context)

    updates = {}
    for call in mock_update.await_args_list:
        updates.update(call.args[1])
    assert updates["approved_source_srt_file_id"] == "fs-replaced"
    assert "transcript_zip_entries" not in updates


@pytest.mark.asyncio
async def test_thread_srt_replace_upload_failure_tells_user_and_skips_embed_quote():
    from app.slack.listener_actions import maybe_show_thread_media_embed_option

    client = AsyncMock()
    context = MagicMock()
    context.get.return_value = "C123"
    context.__getitem__ = lambda self, key: {"user_id": "U1", "channel_id": "C123"}[key]
    message = {
        "thread_ts": "1.2",
        "files": [{"id": "F9", "name": "clip.srt", "filetype": "srt"}],
    }
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
            "app.slack.listener_actions.get_media_quote_sessions_for_thread",
            new_callable=AsyncMock,
            return_value=[session],
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
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
        patch(
            "app.slack.listener_actions.quote_existing_srt_embed_task",
            new_callable=AsyncMock,
        ) as mock_quote,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        handled = await maybe_show_thread_media_embed_option(client, context, message)

    assert handled is True
    mock_quote.assert_not_awaited()
    mock_update.assert_not_awaited()
    posted = client.chat_postMessage.await_args.kwargs
    assert posted["channel"] == "C123"
    assert posted["thread_ts"] == "1.2"
    assert "Could not replace" in posted["text"]


@pytest.mark.asyncio
async def test_noop_source_embed_completed_does_not_write_stage():
    from app.media.media_workflow import (
        MediaWorkflowEvent,
        advance_media_workflow,
        media_workflow_session_from_quote,
    )
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    session = {
        "quote_id": "q1",
        "stage": "awaiting_translation_accept",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "1.2",
    }
    decision = advance_media_workflow(
        media_workflow_session_from_quote(session),
        MediaWorkflowEvent.SOURCE_EMBED_COMPLETED,
    )
    assert decision.commands == ()
    client = AsyncMock()
    with patch(
        "app.slack.media_workflow_actions.update_media_quote_session",
        new_callable=AsyncMock,
    ) as mock_update:
        await execute_media_workflow_decision(
            client=client, session=session, decision=decision
        )

    mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_submit_rejects_other_user():
    from app.slack.media_workflow_actions import handle_media_srt_replace_submit

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U2", "team_id": "T1"}[key]
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
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new_callable=AsyncMock,
            return_value="fs-replaced",
        ) as mock_upload,
    ):
        await handle_media_srt_replace_submit(view=view, client=client, context=context)

    mock_upload.assert_not_awaited()
    mock_update.assert_not_awaited()
    assert "permission" in client.chat_postMessage.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_approve_reloads_session_after_lock_so_replacement_is_used():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    stale = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "task_uuid": "asr-task",
        "file_id": "F1",
        "download_url": "https://files.example/clip.mp4",
        "file_name": "clip.mp4",
    }
    fresh = {**stale, "approved_source_srt_file_id": "srt-replaced"}

    with (
        patch(
            "app.slack.media_workflow_actions.get_media_quote_session",
            new_callable=AsyncMock,
            side_effect=[stale, fresh],
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**fresh, **updates},
        ),
        patch(
            "app.slack.media_configure_embed.resume_configure_embed_phase",
            new_callable=AsyncMock,
        ) as mock_embed,
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_approve_continue(
            client=client,
            action={"value": "q1"},
            context=context,
        )

    assert mock_embed.await_args.kwargs["session"]["approved_source_srt_file_id"] == (
        "srt-replaced"
    )


@pytest.mark.asyncio
async def test_approve_translated_srt_ignores_replacement_without_language():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_translation_review",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es", "fr"],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "task_uuid": "asr-task",
        "approved_translated_srt_file_id": "srt-replaced",
        "srt_review_message_ts": [],
        "submission_ids": [42],
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
        ),
        patch(
            "app.slack.media_configure_embed.resume_configure_embed_phase",
            new_callable=AsyncMock,
        ) as mock_embed,
        patch(
            "app.ray.events.media_pipeline_events.update_submission_status",
            new_callable=AsyncMock,
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

    mock_embed.assert_awaited_once()
    assert not any(
        "replacement" in str(call.kwargs.get("text") or "").lower()
        for call in client.chat_postMessage.await_args_list
    )


@pytest.mark.asyncio
async def test_post_srt_review_stores_message_timestamp():
    from app.slack.media_workflow_actions import _post_srt_review

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(side_effect=[{"ts": "9.9"}, {"ts": "9.10"}])
    session = {
        "quote_id": "q1",
        "channel_id": "C1",
        "thread_ts": "1.2",
    }
    with patch(
        "app.slack.media_workflow_actions.update_media_quote_session",
        new_callable=AsyncMock,
        side_effect=lambda quote_id, updates: {**session, **updates},
    ) as mock_update:
        await _post_srt_review(client, session)

    assert mock_update.await_args.args[1]["srt_review_message_ts"] == ["9.9", "9.10"]


@pytest.mark.asyncio
async def test_approve_removes_review_buttons_after_submit():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    client.chat_update = AsyncMock()
    client.chat_delete = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "srt_review_message_ts": ["10.1", "10.2"],
        "submission_ids": [42],
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
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_submission_status",
            new_callable=AsyncMock,
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

    client.chat_delete.assert_awaited_once_with(channel="C1", ts="10.1")
    client.chat_update.assert_awaited_once()
    updated = client.chat_update.await_args.kwargs
    assert updated["ts"] == "10.2"
    assert updated["channel"] == "C1"
    assert updated["text"] == "Transcript approved."
    action_ids = [
        el.get("action_id")
        for block in updated.get("blocks") or []
        for el in block.get("elements", [])
    ]
    assert "media_srt_approve_continue" not in action_ids
    assert "media_srt_replace" not in action_ids


@pytest.mark.asyncio
async def test_approve_translated_review_says_subtitles_approved():
    from app.slack.media_workflow_actions import handle_media_srt_approve_continue

    client = AsyncMock()
    client.chat_update = AsyncMock()
    client.chat_delete = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    context.get = lambda key, default=None: None
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_translation_review",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "srt_review_message_ts": ["10.2"],
        "submission_ids": [42],
        "approved_translated_srt_file_id": "srt-es",
        "approved_translated_srt_language": "es",
        "task_uuid": "asr-task",
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
        ),
        patch(
            "app.slack.media_configure_embed.resume_configure_embed_phase",
            new_callable=AsyncMock,
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_submission_status",
            new_callable=AsyncMock,
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

    updated = client.chat_update.await_args.kwargs
    assert updated["text"] == "Subtitles approved."


@pytest.mark.asyncio
async def test_replace_submit_rejects_non_srt_file():
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
                        "files": [{"id": "F9", "name": "notes.txt"}],
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
        ) as mock_upload,
    ):
        await handle_media_srt_replace_submit(view=view, client=client, context=context)

    mock_upload.assert_not_awaited()
    assert (
        client.chat_postMessage.await_args.kwargs["text"]
        == "Please upload a transcript or subtitle file."
    )


@pytest.mark.asyncio
async def test_replace_submit_uses_language_from_private_metadata():
    from app.slack.media_workflow_actions import handle_media_srt_replace_submit

    client = AsyncMock()
    context = MagicMock()
    context.__getitem__ = lambda self, key: {"user_id": "U1", "team_id": "T1"}[key]
    session = {
        "quote_id": "q1",
        "user_id": "U1",
        "stage": "awaiting_translation_review",
        "workflow_type": "transcribe_translate",
        "file_name": "clip.mp4",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["fi", "es"],
        "channel_id": "C1",
        "thread_ts": "1.2",
    }
    view = {
        "private_metadata": '{"quote_id": "q1", "language": "fi"}',
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
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "app.slack.media_workflow_actions._upload_slack_srt_to_file_server",
            new_callable=AsyncMock,
            return_value="fs-replaced",
        ),
        patch("app.slack.media_workflow_actions.redis_conn") as mock_redis,
    ):
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock()
        await handle_media_srt_replace_submit(view=view, client=client, context=context)

    updates = mock_update.await_args.args[1]
    assert updates["approved_translated_srt_file_id"] == "fs-replaced"
    assert updates["approved_translated_srt_language"] == "fi"


@pytest.mark.asyncio
async def test_replace_open_loads_then_shows_file_picker():
    from app.slack.media_workflow_actions import handle_media_srt_replace_open

    client = AsyncMock()
    client.views_open.return_value = {"view": {"id": "V1"}}

    await handle_media_srt_replace_open(
        client=client,
        body={"trigger_id": "trig-1"},
        action={"value": "q1"},
    )

    client.views_open.assert_awaited_once()
    assert client.views_open.await_args.kwargs["trigger_id"] == "trig-1"
    opened = client.views_open.await_args.kwargs["view"]
    assert opened.get("callback_id") != "media_srt_replace_submit"
    client.views_update.assert_awaited_once()
    updated = client.views_update.await_args.kwargs["view"]
    assert updated["callback_id"] == "media_srt_replace_submit"
    assert any(
        block.get("element", {}).get("type") == "file_input"
        for block in updated["blocks"]
    )


@pytest.mark.asyncio
async def test_replace_open_falls_back_when_file_picker_rejected():
    from slack_sdk.errors import SlackApiError

    from app.slack.media_workflow_actions import handle_media_srt_replace_open

    client = AsyncMock()
    client.views_open.return_value = {"view": {"id": "V1"}}
    client.views_update.side_effect = [
        SlackApiError("invalid", {"error": "invalid_blocks"}),
        {"ok": True},
    ]

    with patch("app.slack.media_workflow_actions.notify_exception"):
        await handle_media_srt_replace_open(
            client=client,
            body={"trigger_id": "trig-1"},
            action={"value": "q1"},
        )

    assert client.views_update.await_count == 2
    fallback = client.views_update.await_args_list[1].kwargs["view"]
    assert fallback.get("callback_id") != "media_srt_replace_submit"
    assert "thread" in fallback["blocks"][0]["text"]["text"].lower()

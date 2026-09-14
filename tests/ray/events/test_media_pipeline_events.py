"""Tests for media transcription / translation Slack callback helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.slack.media_quotes import (
    PIPELINE_TRANSCRIBE,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_TRANSCRIBING,
)


@pytest.mark.asyncio
async def test_handle_transcription_complete_skips_ai_translation_follow_up():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe",
        extra_data={
            "slack_team_id": "T1",
            "slack_user_id": "U1",
            "slack_enterprise_id": "E1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ) as mock_enqueue,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    mock_enqueue.assert_awaited_once()
    assert "follow_up_message" not in mock_enqueue.await_args.kwargs
    assert mock_enqueue.await_args.kwargs["team_id"] == "T1"
    assert mock_enqueue.await_args.kwargs["slack_user_id"] == "U1"
    assert mock_enqueue.await_args.kwargs["enterprise_id"] == "E1"
    # No word_source_file_id in extra_data → SRT-only enqueue, same as today.
    assert mock_enqueue.await_args.kwargs.get("word_file_id") is None
    assert mock_enqueue.await_args.kwargs.get("word_file_name") is None


@pytest.mark.asyncio
async def test_handle_transcription_complete_passes_word_source_file():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe",
        extra_data={
            "slack_team_id": "T1",
            "slack_user_id": "U1",
            "word_source_file_id": "docx-1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ) as mock_enqueue,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    mock_enqueue.assert_awaited_once()
    assert mock_enqueue.await_args.kwargs["word_file_id"] == "docx-1"
    assert mock_enqueue.await_args.kwargs["word_file_name"] == "clip.docx"


@pytest.mark.asyncio
async def test_handle_transcription_complete_withholds_word_when_review_pending():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe",
        extra_data={
            "media_quote_id": "q1",
            "workflow_type": "transcribe_translate",
            "embed_source": True,
            "word_source_file_id": "docx-1",
            "slack_team_id": "T1",
            "slack_user_id": "U1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ) as mock_enqueue,
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value={"workflow_type": "transcribe_translate"}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    mock_enqueue.assert_awaited_once()
    assert mock_enqueue.await_args.kwargs.get("word_file_id") is None
    assert mock_enqueue.await_args.kwargs.get("word_file_name") is None
    deferred = {}
    for call in mock_update.await_args_list:
        deferred.update(call.args[1])
    assert deferred.get("deferred_word_file_id") == "docx-1"
    assert deferred.get("deferred_word_file_name") == "clip.docx"


@pytest.mark.asyncio
async def test_handle_transcription_complete_records_transcript_zip_entries():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe",
        extra_data={
            "media_quote_id": "q1",
            "workflow_type": "transcribe_only",
            "word_source_file_id": "docx-1",
            "slack_team_id": "T1",
            "slack_user_id": "U1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value={"workflow_type": "transcribe_only"}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    zip_updates = [
        call.args[1]
        for call in mock_update.await_args_list
        if "transcript_zip_entries" in call.args[1]
    ]
    assert zip_updates[-1]["transcript_zip_entries"] == [
        {"file_id": "file-1", "filename": "clip.srt"},
        {"file_id": "docx-1", "filename": "clip.docx"},
    ]


@pytest.mark.asyncio
async def test_handle_transcription_complete_defers_configure_srt_review():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe",
        extra_data={
            "media_quote_id": "q1",
            "workflow_type": "transcribe_only",
            "embed_source": True,
            "review_gate": True,
            "slack_team_id": "T1",
            "slack_user_id": "U1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ) as mock_enqueue,
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    assert mock_enqueue.await_args.kwargs["srt_review_quote_id"] == "q1"
    assert any(
        call.args[1].get("defer_source_review") is True
        for call in mock_update.await_args_list
    )


@pytest.mark.asyncio
async def test_handle_transcription_complete_defers_source_review_for_translated_embed():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe_translate",
        extra_data={
            "media_quote_id": "q1",
            "workflow_type": "transcribe_translate",
            "embed_source": False,
            "embed_translated": True,
            "slack_team_id": "T1",
            "slack_user_id": "U1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ) as mock_enqueue,
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    assert mock_enqueue.await_args.kwargs["srt_review_quote_id"] == "q1"
    assert any(
        call.args[1].get("defer_source_review") is True
        for call in mock_update.await_args_list
    )


@pytest.mark.asyncio
async def test_handle_transcription_complete_skips_review_when_auto_proceed():
    from app.ray.events.media_pipeline_events import handle_transcription_complete

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="transcribe",
        extra_data={
            "media_quote_id": "q1",
            "workflow_type": "transcribe_only",
            "embed_source": True,
            "slack_team_id": "T1",
            "slack_user_id": "U1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(ray_client_id="client-1"))
    auth_slack_user = SimpleNamespace(channel_id="C1")

    with (
        patch(
            "app.ray.events.media_pipeline_events.post_notification",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.ray.events.media_pipeline_events.enqueue_transcription_upload",
            new=AsyncMock(),
        ) as mock_enqueue,
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value={"auto_proceed": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await handle_transcription_complete(
            client,
            "file-1",
            "clip.srt",
            task_info,
            False,
            "C1",
            "123.456",
            MagicMock(),
            auth,
            auth_slack_user,
        )

    assert mock_enqueue.await_args.kwargs["srt_review_quote_id"] is None
    zip_updates = [
        call.args[1]
        for call in mock_update.await_args_list
        if isinstance(call.args[1], dict) and "transcript_zip_entries" in call.args[1]
    ]
    assert zip_updates[-1]["transcript_zip_entries"] == [
        {"file_id": "file-1", "filename": "clip.srt"},
    ]
    assert all("defer_source_review" not in update for update in zip_updates)


@pytest.mark.asyncio
async def test_maybe_post_media_translation_quote_skips_transcribe_only():
    from app.ray.events.media_pipeline_events import maybe_post_media_translation_quote

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        source_text_length=500,
        duration_ms=60_000,
        extra_data={"media_quote_id": "q1", "pipeline_kind": PIPELINE_TRANSCRIBE},
    )
    session = {
        "quote_id": "q1",
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "stage": STAGE_TRANSCRIBING,
        "target_languages": [],
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(return_value=session),
        ) as mock_update,
        patch(
            "app.ray.events.media_pipeline_events.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_post,
    ):
        posted = await maybe_post_media_translation_quote(
            client, task_info, "C1", "123.456"
        )

    assert posted is False
    mock_post.assert_not_awaited()
    mock_update.assert_awaited_once()
    assert mock_update.await_args.args[1]["stage"] == "done"


@pytest.mark.asyncio
async def test_maybe_post_media_translation_quote_posts_for_translate_pipeline():
    from app.ray.events.media_pipeline_events import maybe_post_media_translation_quote

    client = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        source_text_length=500,
        duration_ms=60_000,
        extra_data={
            "media_quote_id": "q1",
            "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
            "target_languages": ["es"],
        },
    )
    session = {
        "quote_id": "q1",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_TRANSCRIBING,
        "target_languages": ["es"],
        "file_id": "F1",
        "file_name": "clip.mp4",
        "channel_id": "C1",
        "thread_ts": "123.456",
    }
    updated = {**session, "stage": STAGE_AWAITING_TRANSLATION_ACCEPT}

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(return_value=updated),
        ) as mock_update,
        patch(
            "app.ray.events.media_pipeline_events.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_post,
    ):
        posted = await maybe_post_media_translation_quote(
            client, task_info, "C1", "123.456"
        )

    assert posted is True
    mock_post.assert_awaited_once()
    quote = mock_update.await_args.args[1]["quote"]
    assert quote["files"][0]["file_id"] == "F1"
    assert quote["files"][0]["character_count"] == 500
    assert quote["files"][0]["target_languages"][0]["target_language"] == "es"


@pytest.mark.asyncio
async def test_maybe_post_media_translation_quote_posts_review_when_configure_gate_on():
    from app.ray.events.media_pipeline_events import maybe_post_media_translation_quote
    from app.slack.media_quotes import PIPELINE_TRANSCRIBE_TRANSLATE, STAGE_TRANSCRIBING

    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        source_text_length=500,
        duration_ms=60_000,
        extra_data={"media_quote_id": "q1"},
    )
    session = {
        "quote_id": "q1",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_TRANSCRIBING,
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(return_value=session),
        ) as mock_update,
        patch(
            "app.ray.events.media_pipeline_events.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_post,
    ):
        posted = await maybe_post_media_translation_quote(
            client, task_info, "C1", "123.456"
        )

    assert posted is True
    mock_post.assert_not_awaited()
    assert mock_update.await_args.args[1]["stage"] == "awaiting_source_review"
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any("Approve & Continue" in text or "Review" in text for text in texts)
    posted_action_ids = [
        [
            el.get("action_id")
            for block in call.kwargs.get("blocks") or []
            for el in block.get("elements", [])
        ]
        for call in client.chat_postMessage.await_args_list
    ]
    assert any("media_srt_replace" in ids for ids in posted_action_ids)
    assert any("media_srt_approve_continue" in ids for ids in posted_action_ids)
    assert all(
        not ("media_srt_replace" in ids and "media_srt_approve_continue" in ids)
        for ids in posted_action_ids
    )


@pytest.mark.asyncio
async def test_maybe_post_media_translation_quote_posts_source_review_for_translated_embed():
    from app.ray.events.media_pipeline_events import maybe_post_media_translation_quote
    from app.slack.media_quotes import PIPELINE_TRANSCRIBE_TRANSLATE, STAGE_TRANSCRIBING

    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        source_text_length=500,
        duration_ms=60_000,
        extra_data={"media_quote_id": "q1"},
    )
    session = {
        "quote_id": "q1",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_TRANSCRIBING,
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(return_value=session),
        ) as mock_update,
        patch(
            "app.ray.events.media_pipeline_events.post_media_quote_message",
            new=AsyncMock(),
        ) as mock_post,
    ):
        posted = await maybe_post_media_translation_quote(
            client, task_info, "C1", "123.456"
        )

    assert posted is True
    mock_post.assert_not_awaited()
    assert mock_update.await_args.args[1]["stage"] == "awaiting_source_review"
    posted_action_ids = [
        [
            el.get("action_id")
            for block in call.kwargs.get("blocks") or []
            for el in block.get("elements", [])
        ]
        for call in client.chat_postMessage.await_args_list
    ]
    assert any("media_srt_replace" in ids for ids in posted_action_ids)
    assert any("media_srt_approve_continue" in ids for ids in posted_action_ids)


@pytest.mark.asyncio
async def test_maybe_post_media_translation_quote_skips_review_when_not_embedding():
    from app.ray.events.media_pipeline_events import maybe_post_media_translation_quote
    from app.slack.media_quotes import PIPELINE_TRANSCRIBE_TRANSLATE, STAGE_TRANSCRIBING

    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        source_text_length=500,
        duration_ms=60_000,
        extra_data={"media_quote_id": "q1"},
    )
    session = {
        "quote_id": "q1",
        "pipeline_kind": PIPELINE_TRANSCRIBE_TRANSLATE,
        "stage": STAGE_TRANSCRIBING,
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "source_text_length": 500,
        "duration_ms": 60_000,
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ),
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
        posted = await maybe_post_media_translation_quote(
            client, task_info, "C1", "123.456"
        )

    assert posted is True
    mock_post.assert_awaited_once()
    client.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_post_media_translation_quote_skips_review_when_deferred():
    from app.ray.events.media_pipeline_events import maybe_post_media_translation_quote
    from app.slack.media_quotes import PIPELINE_TRANSCRIBE, STAGE_TRANSCRIBING

    client = AsyncMock()
    client.chat_postMessage = AsyncMock()
    task_info = SimpleNamespace(
        task_uuid="task-1",
        source_text_length=500,
        duration_ms=60_000,
        extra_data={"media_quote_id": "q1"},
    )
    session = {
        "quote_id": "q1",
        "pipeline_kind": PIPELINE_TRANSCRIBE,
        "stage": STAGE_TRANSCRIBING,
        "workflow_type": "transcribe_only",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "defer_source_review": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "123.456",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(side_effect=lambda quote_id, updates: {**session, **updates}),
        ),
    ):
        posted = await maybe_post_media_translation_quote(
            client, task_info, "C1", "123.456"
        )

    assert posted is True
    client.chat_postMessage.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_translation_complete_posts_failure_when_undelivered():
    from app.ray.events.media_pipeline_events import handle_translation_complete

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with patch(
        "app.ray.events.media_pipeline_events.show_tokens_message",
        new=AsyncMock(),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    # No translated files and an existing thread → no status spam
    assert uploaded == 0
    assert client.chat_postMessage.await_count == 0

    task_info.translated_file_ids = {"es": "file-es"}
    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": None}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 0
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any("could not be delivered" in text for text in texts)
    assert not any("reupload the edited files" in text for text in texts)
    assert not any("downloaded above" in text for text in texts)


@pytest.mark.asyncio
async def test_handle_translation_complete_cancels_configure_when_undelivered():
    from app.ray.events.media_pipeline_events import handle_translation_complete

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-1",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
        extra_data={
            "workflow_type": "transcribe_translate",
            "media_quote_id": "q1",
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": None}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "app.slack.media_workflow_actions.execute_media_workflow_decision",
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 0
    mock_update.assert_awaited_once()
    assert mock_update.await_args.args == ("q1", {"stage": "cancelled"})
    mock_execute.assert_not_awaited()
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any("could not be delivered" in text for text in texts)
    assert not any("Approve & Continue" in text or "Review" in text for text in texts)


@pytest.mark.asyncio
async def test_handle_translation_complete_posts_success_after_upload(tmp_path):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(srt)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            return_value="Spanish",
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ) as mock_tokens,
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 1
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any("provided above" in text for text in texts)
    assert any("subtitle files (SRT)" in text for text in texts)
    assert any("reupload the edited subtitle files" in text for text in texts)
    mock_tokens.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_translation_complete_uploads_srt_only_without_word(tmp_path):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    async def _download(file_id: str) -> dict[str, str]:
        return {"file": str(srt)}

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
        extra_data={"word_translated_file_ids": {"es": "docx-es"}},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(side_effect=_download),
        ) as mock_download,
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            return_value="Spanish",
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_upload,
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 1
    downloaded_ids = [c.args[0] for c in mock_download.await_args_list]
    assert downloaded_ids == ["file-es"]
    filenames = [c.kwargs["filename"] for c in mock_upload.await_args_list]
    assert filenames == ["clip_Spanish.srt"]


@pytest.mark.asyncio
async def test_handle_translation_complete_records_transcript_zip_entries(tmp_path):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")
    docx = tmp_path / "es.docx"
    docx.write_bytes(b"fake-docx")

    async def _download(file_id: str) -> dict[str, str]:
        return {"file": str(srt) if file_id == "file-es" else str(docx)}

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
        extra_data={
            "media_quote_id": "q1",
            "workflow_type": "transcribe_translate",
            "word_translated_file_ids": {"es": "docx-es"},
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))
    session = {
        "workflow_type": "transcribe_translate",
        "transcript_zip_entries": [
            {"file_id": "file-1", "filename": "clip.srt"},
        ],
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(side_effect=_download),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            return_value="Spanish",
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await handle_translation_complete(client, "C1", "123.456", task_info, auth)

    zip_updates = [
        call.args[1]
        for call in mock_update.await_args_list
        if isinstance(call.args[1], dict) and "transcript_zip_entries" in call.args[1]
    ]
    assert zip_updates[-1]["transcript_zip_entries"] == [
        {"file_id": "file-1", "filename": "clip.srt"},
        {"file_id": "file-es", "filename": "clip_Spanish.srt"},
    ]


@pytest.mark.asyncio
async def test_handle_translation_complete_ignores_translated_word_ids(tmp_path):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
        extra_data={"word_translated_file_ids": {"es": "docx-es"}},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(srt)}),
        ) as mock_download,
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            return_value="Spanish",
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ) as mock_upload,
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    # SRT delivery counts as success; translated Word ids are ignored.
    assert uploaded == 1
    assert mock_upload.await_count == 1
    assert [c.args[0] for c in mock_download.await_args_list] == ["file-es"]
    assert [c.kwargs["filename"] for c in mock_upload.await_args_list] == [
        "clip_Spanish.srt"
    ]
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any(
        text == "Your file is AI translated and can be downloaded above."
        for text in texts
    )
    assert not any("could not be delivered" in text for text in texts)


@pytest.mark.asyncio
async def test_handle_translation_complete_skips_mandatory_reupload_for_configure(
    tmp_path,
):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
        extra_data={"workflow_type": "transcribe_translate", "media_quote_id": "q1"},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))
    session = {
        "quote_id": "q1",
        "stage": "translating",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "user_id": "U1",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(srt)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            return_value="Spanish",
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 1
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert not any("reupload the edited subtitle files" in text for text in texts)
    translated_lines = [
        text
        for text in texts
        if "Your file is AI translated and can be downloaded above." in text
    ]
    assert len(translated_lines) == 1
    assert "Approve & Continue" in translated_lines[0]
    assert not any("Review *" in text for text in texts)


@pytest.mark.asyncio
async def test_handle_translation_complete_posts_replace_for_each_language(tmp_path):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "clip.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    async def _download(_file_id: str) -> dict[str, str]:
        copy = tmp_path / f"{_file_id}.srt"
        copy.write_text(srt.read_text())
        return {"file": str(copy)}

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"fi": "file-fi", "es": "file-es"},
        extra_data={
            "workflow_type": "transcribe_translate",
            "media_quote_id": "q1",
            "review_gate": True,
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))
    session = {
        "quote_id": "q1",
        "stage": "translating",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["fi", "es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "user_id": "U1",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(side_effect=_download),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            side_effect=lambda code: {"fi": "Finnish", "es": "Spanish"}[code],
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(
                side_effect=lambda quote_id, updates: session.update(updates) or session
            ),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(
                side_effect=lambda quote_id, updates: session.update(updates) or session
            ),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 2
    replace_values = []
    approve_count = 0
    for call in client.chat_postMessage.await_args_list:
        action_ids = [
            el.get("action_id")
            for block in call.kwargs.get("blocks") or []
            for el in block.get("elements", [])
        ]
        if "media_srt_replace" in action_ids:
            assert "media_srt_approve_continue" not in action_ids
            dumped = json.dumps(call.kwargs.get("blocks") or [])
            assert "Review" not in dumped
            for block in call.kwargs.get("blocks") or []:
                for el in block.get("elements", []):
                    if el.get("action_id") == "media_srt_replace":
                        replace_values.append(el.get("value"))
        if "media_srt_approve_continue" in action_ids:
            assert "media_srt_replace" not in action_ids
            assert "Your file is AI translated and can be downloaded above." in (
                call.kwargs.get("text") or ""
            )
            approve_count += 1
    parsed = [json.loads(value) for value in replace_values]
    assert {"quote_id": "q1", "language": "fi"} in parsed
    assert {"quote_id": "q1", "language": "es"} in parsed
    assert approve_count == 1


@pytest.mark.asyncio
async def test_handle_translation_complete_persists_review_stage_before_review_buttons(
    tmp_path,
):
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "clip.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    async def _download(_file_id: str) -> dict[str, str]:
        copy = tmp_path / f"{_file_id}.srt"
        copy.write_text(srt.read_text())
        return {"file": str(copy)}

    timeline: list[str] = []
    client = AsyncMock()

    async def _post_message(**kwargs):
        for block in kwargs.get("blocks") or []:
            for el in block.get("elements", []):
                if el.get("action_id") == "media_srt_approve_continue":
                    timeline.append("approve")
                    return {"ts": "999.001"}
                if el.get("action_id") == "media_srt_replace":
                    timeline.append("replace")
                    return {"ts": "999.001"}
        timeline.append("message")
        return {"ts": "999.001"}

    client.chat_postMessage = AsyncMock(side_effect=_post_message)
    task_info = SimpleNamespace(
        task_uuid="task-2",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
        extra_data={
            "workflow_type": "transcribe_translate",
            "media_quote_id": "q1",
            "review_gate": True,
        },
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))
    session = {
        "quote_id": "q1",
        "stage": "translating",
        "workflow_type": "transcribe_translate",
        "embed_source": False,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "user_id": "U1",
    }

    async def _update_session(_quote_id, updates):
        if updates.get("stage") == "awaiting_translation_review":
            timeline.append("stage")
        session.update(updates)
        return session

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(side_effect=_download),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_auto_translate_language_name",
            return_value="Spanish",
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_media_quote_session",
            new=AsyncMock(side_effect=_update_session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(side_effect=_update_session),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 1
    assert "stage" in timeline
    assert "approve" in timeline
    assert timeline.index("stage") < timeline.index("approve")


def test_job_transcribed_event_carries_failed_languages():
    """`failed_languages` is optional and survives both payload formats."""
    from app.ray.events.models import JobTranscribedEvent

    new_format = JobTranscribedEvent.model_validate(
        {"task_uuid": "task-1", "client_id": "client-1", "failed_languages": ["fr"]}
    )
    assert new_format.failed_languages == ["fr"]

    # Older sup-subtitle-ai-cons deploys omit the field entirely.
    without_field = JobTranscribedEvent.model_validate(
        {"task_uuid": "task-1", "client_id": "client-1"}
    )
    assert without_field.failed_languages is None

    legacy = JobTranscribedEvent.model_validate(
        {
            "result": {
                "task_uuid": "task-2",
                "client_id": "client-2",
                "error": None,
                "failed_languages": ["de", "ja"],
            }
        }
    )
    assert legacy.task_uuid == "task-2"
    assert legacy.failed_languages == ["de", "ja"]

    legacy_without_field = JobTranscribedEvent.model_validate(
        {"result": {"task_uuid": "task-3", "client_id": "client-3"}}
    )
    assert legacy_without_field.failed_languages is None


@pytest.mark.asyncio
async def test_handle_translation_complete_names_failed_languages(tmp_path):
    """Partial success: deliver what arrived and name what did not."""
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-partial",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(srt)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
    ):
        uploaded = await handle_translation_complete(
            client,
            "C1",
            "123.456",
            task_info,
            auth,
            failed_languages=["fr"],
        )

    assert uploaded == 1
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    # Exactly one message names the missing language, by name and not by code.
    naming_failure = [text for text in texts if "French" in text]
    assert len(naming_failure) == 1
    assert "could not translate" in naming_failure[0]
    assert "fr." not in naming_failure[0]
    # The plain success line is replaced, not duplicated alongside the warning.
    assert not any(
        text == "Your file is AI translated and can be downloaded above."
        for text in texts
    )
    # Delivered files still get the edit/reupload guidance.
    assert any("reupload the edited subtitle files" in text for text in texts)


@pytest.mark.asyncio
async def test_handle_translation_complete_without_failed_languages_unchanged(tmp_path):
    """No failed_languages (older sup-subtitle deploy) keeps the previous wording."""
    from app.ray.events.media_pipeline_events import handle_translation_complete

    srt = tmp_path / "es.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhola\n")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "999.001"})
    task_info = SimpleNamespace(
        task_uuid="task-full",
        file_name="clip.mp4",
        pipeline_type="translate_only",
        translated_file_ids={"es": "file-es"},
    )
    auth = SimpleNamespace(slack_user=SimpleNamespace(enterprise_id=None))

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(srt)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.show_tokens_message",
            new=AsyncMock(),
        ),
    ):
        uploaded = await handle_translation_complete(
            client, "C1", "123.456", task_info, auth
        )

    assert uploaded == 1
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any(
        text == "Your file is AI translated and can be downloaded above."
        for text in texts
    )
    assert not any("could not translate" in text for text in texts)


@pytest.mark.asyncio
async def test_handle_transcribe_embed_pipeline_names_failed_languages(tmp_path):
    """Embedded video is still delivered, with the un-embedded languages named."""
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    media = tmp_path / "out.mp4"
    media.write_bytes(b"fake-video")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-embed",
        file_name="clip.mp4",
        pipeline_type="embed",
    )

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(media)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        ok = await handle_transcribe_embed_pipeline(
            client,
            "file-1",
            "out.mp4",
            task_info,
            "C1",
            "123.456",
            auth=None,
            failed_languages=["de"],
        )

    assert ok is True
    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    naming_failure = [text for text in texts if "German" in text]
    assert len(naming_failure) == 1
    assert "could not embed subtitles" in naming_failure[0]


@pytest.mark.asyncio
async def test_resolve_language_labels_handles_codes_uuids_and_unknowns():
    from app.ray.events.media_pipeline_events import resolve_language_labels

    uuid = "3f1c9d2e-4b5a-4c6d-8e7f-0a1b2c3d4e5f"
    with patch(
        "app.ray.events.media_pipeline_events.get_language_name_by_uuid",
        new=AsyncMock(return_value="Japanese"),
    ) as mock_by_uuid:
        labels = await resolve_language_labels(["fr", uuid, "", None])

    assert labels == ["French", "Japanese"]
    mock_by_uuid.assert_awaited_once_with(uuid)
    assert await resolve_language_labels(None) == []


@pytest.mark.asyncio
async def test_fail_media_submissions_list_and_dict_ids():
    from app.ray.events.media_pipeline_events import fail_media_submissions
    from app.ray.submissions import SubmissionStatus

    with patch(
        "app.ray.events.media_pipeline_events.updated_submission_status"
    ) as mock_update:
        await fail_media_submissions(
            {
                "submission_id": 10,
                "submission_ids": [11, "12", 10],
            }
        )
        await fail_media_submissions(
            {"submission_ids": {"es": 21, "fr": 22, "dup": 21}}
        )

    statuses = [c.kwargs["processing_status"] for c in mock_update.call_args_list]
    assert all(s == SubmissionStatus.FAILED for s in statuses)
    ids = [c.kwargs["submission_id"] for c in mock_update.call_args_list]
    assert ids == [10, 11, 12, 21, 22]


@pytest.mark.asyncio
async def test_update_submission_status_defaults_to_completed():
    from app.ray.events.media_pipeline_events import update_submission_status
    from app.ray.submissions import SubmissionStatus

    with patch(
        "app.ray.events.media_pipeline_events.updated_submission_status"
    ) as mock_update:
        await update_submission_status({"submission_id": 7})

    mock_update.assert_called_once_with(
        submission_id=7,
        processing_status=SubmissionStatus.COMPLETED,
    )


@pytest.mark.asyncio
async def test_handle_transcribe_embed_pipeline_missing_file_returns_false():
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-embed",
        file_name="clip.mp4",
        pipeline_type="translate_embed",
    )

    ok = await handle_transcribe_embed_pipeline(
        client, None, None, task_info, "C1", "123.456", auth=None
    )

    assert ok is False
    assert client.chat_postMessage.await_count == 1
    assert "no output file" in client.chat_postMessage.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_transcribe_embed_pipeline_upload_success(tmp_path):
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    media = tmp_path / "out.mp4"
    media.write_bytes(b"fake-video")

    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-embed",
        file_name="clip.mp4",
        pipeline_type="embed",
    )

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(media)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
    ):
        ok = await handle_transcribe_embed_pipeline(
            client, "file-1", "out.mp4", task_info, "C1", "123.456", auth=None
        )

    assert ok is True


@pytest.mark.asyncio
async def test_handle_transcribe_embed_pipeline_source_embed_keeps_quote2_open(
    tmp_path,
):
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    media = tmp_path / "out.mp4"
    media.write_bytes(b"fake-video")
    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-embed",
        file_name="clip.mp4",
        pipeline_type="embed",
        extra_data={
            "workflow_type": "transcribe_translate",
            "media_quote_id": "q1",
        },
    )
    session = {
        "quote_id": "q1",
        "stage": "awaiting_translation_accept",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "submission_ids": [42],
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(media)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
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
        ok = await handle_transcribe_embed_pipeline(
            client, "file-1", "out.mp4", task_info, "C1", "123.456", auth=None
        )

    assert ok is True
    mock_complete.assert_not_awaited()
    mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_source_embed_does_not_mark_translated_embed_done(tmp_path):
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    media = tmp_path / "out.mp4"
    media.write_bytes(b"fake-video")
    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-source-embed",
        file_name="clip.mp4",
        pipeline_type="embed",
        extra_data={
            "workflow_type": "transcribe_translate",
            "media_quote_id": "q1",
            "embed_role": "source",
        },
    )
    session = {
        "quote_id": "q1",
        "stage": "embedding_translated",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": True,
        "review_gate": True,
        "target_languages": ["es"],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "submission_ids": [42],
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(media)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new_callable=AsyncMock,
            return_value=session,
        ),
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
        ok = await handle_transcribe_embed_pipeline(
            client, "file-1", "out.mp4", task_info, "C1", "123.456", auth=None
        )

    assert ok is True
    mock_complete.assert_not_awaited()
    mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_transcribe_embed_pipeline_source_embed_completes_transcribe_only(
    tmp_path,
):
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    media = tmp_path / "out.mp4"
    media.write_bytes(b"fake-video")
    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-embed",
        file_name="clip.mp4",
        pipeline_type="embed",
        extra_data={"workflow_type": "transcribe_only", "media_quote_id": "q1"},
    )
    session = {
        "quote_id": "q1",
        "stage": "embedding_source",
        "workflow_type": "transcribe_only",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "123.456",
        "submission_ids": [42],
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(media)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
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
        ok = await handle_transcribe_embed_pipeline(
            client, "file-1", "out.mp4", task_info, "C1", "123.456", auth=None
        )

    assert ok is True
    assert mock_update.await_args.args[1]["stage"] == "done"
    mock_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_transcribe_embed_pipeline_source_embed_then_posts_quote2(
    tmp_path,
):
    from app.ray.events.media_pipeline_events import handle_transcribe_embed_pipeline

    media = tmp_path / "out.mp4"
    media.write_bytes(b"fake-video")
    client = AsyncMock()
    client.chat_postMessage = AsyncMock(return_value={"ts": "1.1"})
    task_info = SimpleNamespace(
        task_uuid="task-embed",
        file_name="clip.mp4",
        pipeline_type="embed",
        extra_data={
            "workflow_type": "transcribe_translate",
            "media_quote_id": "q1",
            "embed_role": "source",
        },
    )
    session = {
        "quote_id": "q1",
        "stage": "embedding_source",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "source_text_length": 500,
        "duration_ms": 60_000,
        "channel_id": "C1",
        "thread_ts": "123.456",
        "file_id": "F1",
        "file_name": "clip.mp4",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.download_from_file_server_async",
            new=AsyncMock(return_value={"file": str(media)}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.upload_file_to_slack_memory_efficient",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ),
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
        ok = await handle_transcribe_embed_pipeline(
            client, "file-1", "out.mp4", task_info, "C1", "123.456", auth=None
        )

    assert ok is True
    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_continue_configure_after_failed_source_embed_posts_quote2():
    from app.ray.events.media_pipeline_events import (
        continue_configure_after_failed_source_embed,
    )

    client = AsyncMock()
    session = {
        "quote_id": "q1",
        "stage": "embedding_source",
        "workflow_type": "transcribe_translate",
        "embed_source": True,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": ["es"],
        "source_text_length": 500,
        "duration_ms": 60_000,
        "channel_id": "C1",
        "thread_ts": "123.456",
        "file_id": "F1",
        "file_name": "clip.mp4",
    }
    extra = {
        "media_quote_id": "q1",
        "embed_role": "source",
        "workflow_type": "transcribe_translate",
    }

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new_callable=AsyncMock,
            side_effect=lambda quote_id, updates: {**session, **updates},
        ),
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
        await continue_configure_after_failed_source_embed(
            client, extra, "C1", "123.456"
        )

    mock_post.assert_awaited_once()


@pytest.mark.asyncio
async def test_translation_completed_at_wrong_stage_does_not_raise():
    from app.ray.events.media_pipeline_events import (
        _advance_configure_after_translation,
    )

    client = AsyncMock()
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
    with (
        patch(
            "app.ray.events.media_pipeline_events.get_media_quote_session",
            new=AsyncMock(return_value=session),
        ),
        patch(
            "app.ray.events.media_pipeline_events.notify_exception",
        ) as mock_notify,
        patch(
            "app.slack.media_workflow_actions.execute_media_workflow_decision",
            new_callable=AsyncMock,
        ) as mock_execute,
    ):
        await _advance_configure_after_translation(
            client,
            {"media_quote_id": "q1"},
            "C1",
            "1.2",
        )

    mock_execute.assert_not_awaited()
    mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_spend_embedding_credits_uses_extra_data_duration_when_column_missing():
    from app.ray.events.media_pipeline_events import spend_embedding_credits

    task_info = SimpleNamespace(
        task_uuid="embed-task",
        file_name="clip.mp4",
        pipeline_type="embed",
        duration_ms=None,
        extra_data={"duration_ms": 60_000, "pipeline_type": "embed"},
        detected_language="en",
        translated_file_ids=None,
        num_target_languages=1,
        source_text_length=None,
    )
    auth = SimpleNamespace(
        slack_user=SimpleNamespace(
            ray_client_id="client-1",
            ray_user_group_id="group-1",
        )
    )

    class _FakeDb:
        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_transcription_task",
            new_callable=AsyncMock,
            return_value=task_info,
        ),
        patch(
            "app.ray.events.media_pipeline_events.log_embedding_by_client_id",
            new_callable=AsyncMock,
        ) as mock_log,
        patch(
            "app.ray.events.media_pipeline_events.AsyncSession",
            return_value=_FakeDb(),
        ),
    ):
        amount = await spend_embedding_credits(task_info, auth)

    assert amount == 30
    assert mock_log.await_args.kwargs["duration_ms"] == 60_000


@pytest.mark.asyncio
async def test_spend_embedding_credits_uses_billing_langs_not_mux_track_count():
    """Translated mux includes the source SRT; /mt/embed count must match billing langs."""
    from app.ray.events.media_pipeline_events import spend_embedding_credits

    task_info = SimpleNamespace(
        task_uuid="embed-task",
        file_name="test.mp4",
        pipeline_type="embed",
        duration_ms=60_000,
        extra_data={
            "embed_role": "translated",
            "embed_source": True,
            "embed_translated": True,
            "target_languages": ["bg", "en"],
            "language_codes": ["zh-CN", "bg", "en"],
            "srt_file_ids": ["src", "bg", "en"],
        },
        detected_language="zh-CN",
        translated_file_ids=None,
        num_target_languages=3,
        source_text_length=None,
    )
    auth = SimpleNamespace(
        slack_user=SimpleNamespace(
            ray_client_id="client-1",
            ray_user_group_id="group-1",
        )
    )

    class _FakeDb:
        async def execute(self, stmt):
            return None

        async def commit(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    with (
        patch(
            "app.ray.events.media_pipeline_events.get_transcription_task",
            new_callable=AsyncMock,
            return_value=task_info,
        ),
        patch(
            "app.ray.events.media_pipeline_events.log_embedding_by_client_id",
            new_callable=AsyncMock,
        ) as mock_log,
        patch(
            "app.ray.events.media_pipeline_events.AsyncSession",
            return_value=_FakeDb(),
        ),
    ):
        amount = await spend_embedding_credits(task_info, auth)

    assert mock_log.await_args.kwargs["target_languages"] == ["bg", "en"]
    assert mock_log.await_args.kwargs["num_target_languages"] == 2
    assert amount == 60

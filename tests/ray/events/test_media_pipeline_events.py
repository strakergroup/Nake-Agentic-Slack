"""Tests for media transcription / translation Slack callback helpers."""

from __future__ import annotations

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
        ),
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


@pytest.mark.asyncio
async def test_handle_translation_complete_posts_reupload_guidance():
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
        await handle_translation_complete(client, "C1", "123.456", task_info, auth)

    # No translated files → status only, no reupload guidance
    assert client.chat_postMessage.await_count == 1

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
        await handle_translation_complete(client, "C1", "123.456", task_info, auth)

    texts = [
        call.kwargs.get("text", "") for call in client.chat_postMessage.await_args_list
    ]
    assert any("reupload the edited files" in text for text in texts)

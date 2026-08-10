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
    assert any("downloaded above" in text for text in texts)
    assert any("reupload the edited files" in text for text in texts)
    mock_tokens.assert_awaited_once()


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
    assert any("reupload the edited files" in text for text in texts)


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

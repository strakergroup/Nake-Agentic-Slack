from __future__ import annotations

import zipfile
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_post_transcript_zip_skips_single_file():
    from app.slack.transcript_zip_delivery import post_transcript_zip_if_needed

    client = AsyncMock()
    session = {
        "workflow_type": "transcribe_only",
        "file_name": "clip.mp4",
        "channel_id": "C1",
        "thread_ts": "1.2",
        "transcript_zip_entries": [
            {"file_id": "srt-1", "filename": "clip.srt"},
        ],
    }
    with (
        patch(
            "app.slack.transcript_zip_delivery.download_from_file_server_async",
            new=AsyncMock(),
        ) as mock_download,
        patch(
            "app.slack.transcript_zip_delivery.upload_file_to_slack_memory_efficient",
            new=AsyncMock(),
        ) as mock_upload,
    ):
        await post_transcript_zip_if_needed(client, session)

    mock_download.assert_not_awaited()
    mock_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_transcript_zip_uploads_archive_of_srt_and_word(tmp_path):
    from app.slack.transcript_zip_delivery import post_transcript_zip_if_needed

    srt = tmp_path / "clip.srt"
    docx = tmp_path / "clip.docx"
    srt.write_bytes(b"srt-bytes")
    docx.write_bytes(b"docx-bytes")

    async def fake_download(file_id: str) -> dict[str, str]:
        source = srt if file_id == "srt-1" else docx
        copy = tmp_path / f"{file_id}.bin"
        copy.write_bytes(source.read_bytes())
        return {"file": str(copy)}

    captured: dict[str, object] = {}

    async def fake_upload(**kwargs):
        with zipfile.ZipFile(kwargs["file_path"]) as archive:
            captured["names"] = set(archive.namelist())
            captured["srt"] = archive.read("clip.srt")
            captured["docx"] = archive.read("clip.docx")
        captured["filename"] = kwargs["filename"]
        captured["comment"] = kwargs["initial_comment"]
        captured["channel_id"] = kwargs["channel_id"]
        captured["thread_ts"] = kwargs["thread_ts"]

    client = AsyncMock()
    session = {
        "workflow_type": "transcribe_only",
        "file_name": "clip.mp4",
        "channel_id": "C1",
        "thread_ts": "1.2",
        "transcript_zip_entries": [
            {"file_id": "srt-1", "filename": "clip.srt"},
            {"file_id": "docx-1", "filename": "clip.docx"},
        ],
    }
    with (
        patch(
            "app.slack.transcript_zip_delivery.download_from_file_server_async",
            new=AsyncMock(side_effect=fake_download),
        ),
        patch(
            "app.slack.transcript_zip_delivery.upload_file_to_slack_memory_efficient",
            new=AsyncMock(side_effect=fake_upload),
        ),
    ):
        await post_transcript_zip_if_needed(client, session)

    assert captured["filename"] == "clip_transcripts.zip"
    assert captured["comment"] == "Your transcript files are ready in this zip."
    assert captured["channel_id"] == "C1"
    assert captured["thread_ts"] == "1.2"
    assert captured["names"] == {"clip.srt", "clip.docx"}
    assert captured["srt"] == b"srt-bytes"
    assert captured["docx"] == b"docx-bytes"


@pytest.mark.asyncio
async def test_post_transcript_zip_failure_does_not_raise():
    from app.slack.transcript_zip_delivery import post_transcript_zip_if_needed

    client = AsyncMock()
    session = {
        "workflow_type": "transcribe_only",
        "file_name": "clip.mp4",
        "channel_id": "C1",
        "thread_ts": "1.2",
        "transcript_zip_entries": [
            {"file_id": "srt-1", "filename": "clip.srt"},
            {"file_id": "docx-1", "filename": "clip.docx"},
        ],
    }
    with (
        patch(
            "app.slack.transcript_zip_delivery.download_from_file_server_async",
            new=AsyncMock(side_effect=RuntimeError("file server down")),
        ),
        patch(
            "app.slack.transcript_zip_delivery.upload_file_to_slack_memory_efficient",
            new=AsyncMock(),
        ) as mock_upload,
        patch("app.slack.transcript_zip_delivery.notify_exception"),
    ):
        await post_transcript_zip_if_needed(client, session)

    mock_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_done_posts_transcript_zip_when_two_files(tmp_path):
    from app.media.media_workflow import (
        MediaWorkflowEvent,
        advance_media_workflow,
        media_workflow_session_from_quote,
    )
    from app.slack.media_workflow_actions import execute_media_workflow_decision

    srt = tmp_path / "clip.srt"
    docx = tmp_path / "clip.docx"
    srt.write_bytes(b"srt-bytes")
    docx.write_bytes(b"docx-bytes")

    async def fake_download(file_id: str) -> dict[str, str]:
        source = srt if file_id == "srt-1" else docx
        copy = tmp_path / f"{file_id}.bin"
        copy.write_bytes(source.read_bytes())
        return {"file": str(copy)}

    session = {
        "quote_id": "q1",
        "stage": "awaiting_source_review",
        "workflow_type": "transcribe_only",
        "file_name": "clip.mp4",
        "embed_source": False,
        "embed_translated": False,
        "review_gate": True,
        "target_languages": [],
        "channel_id": "C1",
        "thread_ts": "1.2",
        "submission_ids": [42],
        "transcript_zip_entries": [
            {"file_id": "srt-1", "filename": "clip.srt"},
            {"file_id": "docx-1", "filename": "clip.docx"},
        ],
    }
    decision = advance_media_workflow(
        media_workflow_session_from_quote(session),
        MediaWorkflowEvent.SOURCE_SRT_APPROVED,
    )
    client = AsyncMock()
    captured: dict[str, object] = {}

    async def fake_upload(**kwargs):
        captured["filename"] = kwargs["filename"]
        with zipfile.ZipFile(kwargs["file_path"]) as archive:
            captured["names"] = set(archive.namelist())

    with (
        patch(
            "app.slack.media_workflow_actions.update_media_quote_session",
            new=AsyncMock(side_effect=lambda quote_id, updates: {**session, **updates}),
        ),
        patch(
            "app.ray.events.media_pipeline_events.update_submission_status",
            new=AsyncMock(),
        ),
        patch(
            "app.slack.transcript_zip_delivery.download_from_file_server_async",
            new=AsyncMock(side_effect=fake_download),
        ),
        patch(
            "app.slack.transcript_zip_delivery.upload_file_to_slack_memory_efficient",
            new=AsyncMock(side_effect=fake_upload),
        ),
    ):
        await execute_media_workflow_decision(
            client=client, session=session, decision=decision
        )

    assert captured["filename"] == "clip_transcripts.zip"
    assert captured["names"] == {"clip.srt", "clip.docx"}

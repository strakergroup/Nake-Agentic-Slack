from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Mapping

from slack_sdk.web.async_client import AsyncWebClient

from app.media.transcript_zip import (
    should_upload_transcript_zip,
    transcript_zip_archive_bytes,
    transcript_zip_entries_from_session,
    transcript_zip_filename,
)
from app.ray.utils import download_from_file_server_async
from app.slack.buglog_notifier import notify_exception
from app.slack.web import upload_file_to_slack_memory_efficient
from app.translate import _

logger = logging.getLogger(__name__)


class TranscriptZipDeliveryFailed(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__("Could not deliver transcript zip to Slack")
        self.__cause__ = cause


async def post_deferred_word_transcript(
    client: AsyncWebClient,
    *,
    word_file_id: str,
    word_file_name: str,
    channel_id: str,
    thread_ts: str | None,
) -> None:
    """Upload a Word transcript withheld until its SRT was approved.

    Best-effort: a failure is logged and swallowed so the approval workflow
    never fails because of the Word extra.
    """
    word_path: str | None = None
    try:
        word_output = await download_from_file_server_async(word_file_id)
        word_path = word_output.get("file") if isinstance(word_output, dict) else None
        if not word_path or not os.path.exists(word_path):
            raise RuntimeError(
                f"File-server download returned no path for {word_file_id=}"
            )
        word_dir = os.path.dirname(word_path)
        renamed_word_path = os.path.join(word_dir, word_file_name)
        if word_path != renamed_word_path:
            os.rename(word_path, renamed_word_path)
            word_path = renamed_word_path
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=word_path,
            channel_id=channel_id,
            title=word_file_name,
            filename=word_file_name,
            thread_ts=thread_ts,
        )
    except Exception:
        logger.exception(
            "Deferred Word transcript upload failed; SRT already approved",
            extra={"word_file_id": word_file_id},
        )
    finally:
        try:
            if word_path and os.path.exists(word_path):
                os.unlink(word_path)
        except OSError:
            pass


async def post_transcript_zip_if_needed(
    client: AsyncWebClient, session: Mapping[str, Any]
) -> None:
    entries = transcript_zip_entries_from_session(session)
    if not should_upload_transcript_zip(entries):
        return
    channel_id = str(session.get("channel_id") or "")
    if not channel_id:
        return
    zip_path: str | None = None
    downloaded: list[str] = []
    try:
        files: dict[str, bytes] = {}
        for entry in entries:
            output = await download_from_file_server_async(entry.file_id)
            file_path = output.get("file") if isinstance(output, dict) else None
            if not file_path or not os.path.exists(file_path):
                continue
            downloaded.append(str(file_path))
            with open(file_path, "rb") as handle:
                payload = handle.read()
            if len(payload) <= 1:
                continue
            files[entry.filename] = payload
        if len(files) < 2:
            return
        zip_name = transcript_zip_filename(str(session.get("file_name") or "media"))
        fd, zip_path = tempfile.mkstemp(suffix=".zip")
        try:
            os.write(fd, transcript_zip_archive_bytes(files))
        finally:
            os.close(fd)
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=zip_path,
            channel_id=channel_id,
            title=zip_name,
            filename=zip_name,
            initial_comment=_("Your transcript files are ready in this zip."),
            thread_ts=session.get("thread_ts"),
        )
    except Exception as exc:
        notify_exception(TranscriptZipDeliveryFailed(exc))
        logger.exception(
            "Transcript zip delivery failed; files already in the thread",
            extra={"quote_id": session.get("quote_id")},
        )
    finally:
        for path in [*downloaded, zip_path] if zip_path else downloaded:
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass

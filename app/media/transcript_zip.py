from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

TRANSCRIPT_ZIP_ENTRIES_KEY = "transcript_zip_entries"


@dataclass(frozen=True)
class TranscriptZipEntry:
    file_id: str
    filename: str


def transcript_zip_filename(source_file_name: str) -> str:
    return f"{Path(source_file_name).stem}_transcripts.zip"


def should_upload_transcript_zip(entries: Sequence[TranscriptZipEntry]) -> bool:
    return len(entries) >= 2


def transcript_zip_entries_from_session(
    session: Mapping[str, Any],
) -> tuple[TranscriptZipEntry, ...]:
    if not session.get("workflow_type"):
        return ()
    raw_entries = session.get(TRANSCRIPT_ZIP_ENTRIES_KEY) or []
    entries: list[TranscriptZipEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id") or "").strip()
        filename = str(item.get("filename") or "").strip()
        if file_id and filename:
            entries.append(TranscriptZipEntry(file_id=file_id, filename=filename))
    return tuple(entries)


def record_transcript_zip_entry(
    existing: Sequence[Mapping[str, Any]] | None,
    *,
    file_id: str,
    filename: str,
) -> list[dict[str, str]]:
    recorded = [
        {
            "file_id": str(item.get("file_id") or ""),
            "filename": str(item.get("filename") or ""),
        }
        for item in existing or []
        if isinstance(item, dict) and item.get("filename")
    ]
    if not file_id or not filename:
        return recorded
    for item in recorded:
        if item["filename"] == filename:
            item["file_id"] = file_id
            return recorded
    recorded.append({"file_id": file_id, "filename": filename})
    return recorded


def replace_transcript_zip_entry_file_id(
    existing: Sequence[Mapping[str, Any]] | None,
    *,
    filename: str,
    file_id: str,
) -> list[dict[str, str]]:
    return record_transcript_zip_entry(existing, file_id=file_id, filename=filename)


def transcript_zip_session_updates(
    session: Mapping[str, Any],
    files: Sequence[tuple[str, str]],
) -> dict[str, list[dict[str, str]]]:
    existing = session.get(TRANSCRIPT_ZIP_ENTRIES_KEY)
    recorded: list[dict[str, str]] = []
    if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes)):
        recorded = record_transcript_zip_entry(existing, file_id="", filename="")
    for file_id, filename in files:
        recorded = record_transcript_zip_entry(
            recorded, file_id=file_id, filename=filename
        )
    return {TRANSCRIPT_ZIP_ENTRIES_KEY: recorded}


def transcript_zip_archive_bytes(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, payload in files.items():
            archive.writestr(filename, payload)
    return buffer.getvalue()

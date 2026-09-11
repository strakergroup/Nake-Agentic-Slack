import io
import zipfile

from app.media.transcript_zip import (
    record_transcript_zip_entry,
    replace_transcript_zip_entry_file_id,
    should_upload_transcript_zip,
    transcript_zip_archive_bytes,
    transcript_zip_entries_from_session,
    transcript_zip_filename,
)


def test_transcript_zip_filename_uses_source_stem():
    assert (
        transcript_zip_filename("spiderman (3).mp4") == "spiderman (3)_transcripts.zip"
    )


def test_one_srt_does_not_upload_zip():
    entries = transcript_zip_entries_from_session(
        {
            "workflow_type": "transcribe_only",
            "transcript_zip_entries": [
                {"file_id": "srt-1", "filename": "clip.srt"},
            ],
        }
    )
    assert [entry.filename for entry in entries] == ["clip.srt"]
    assert should_upload_transcript_zip(entries) is False


def test_source_srt_and_word_uploads_zip():
    entries = transcript_zip_entries_from_session(
        {
            "workflow_type": "transcribe_only",
            "transcript_zip_entries": [
                {"file_id": "srt-1", "filename": "clip.srt"},
                {"file_id": "docx-1", "filename": "clip.docx"},
            ],
        }
    )
    assert [entry.filename for entry in entries] == ["clip.srt", "clip.docx"]
    assert should_upload_transcript_zip(entries) is True


def test_leftover_embed_path_has_no_zip_entries():
    entries = transcript_zip_entries_from_session(
        {
            "transcript_zip_entries": [
                {"file_id": "srt-1", "filename": "clip.srt"},
                {"file_id": "docx-1", "filename": "clip.docx"},
            ],
        }
    )
    assert entries == ()
    assert should_upload_transcript_zip(entries) is False


def test_record_entry_replaces_same_filename():
    recorded = record_transcript_zip_entry(
        [{"file_id": "srt-old", "filename": "clip.srt"}],
        file_id="srt-new",
        filename="clip.srt",
    )
    recorded = record_transcript_zip_entry(
        recorded, file_id="docx-1", filename="clip.docx"
    )
    assert recorded == [
        {"file_id": "srt-new", "filename": "clip.srt"},
        {"file_id": "docx-1", "filename": "clip.docx"},
    ]


def test_replace_swaps_file_id_for_language_filename():
    recorded = replace_transcript_zip_entry_file_id(
        [
            {"file_id": "srt-cs", "filename": "clip_Czech.srt"},
            {"file_id": "srt-zh", "filename": "clip_Chinese (Traditional).srt"},
        ],
        filename="clip_Czech.srt",
        file_id="srt-replaced",
    )
    entries = transcript_zip_entries_from_session(
        {
            "workflow_type": "transcribe_translate",
            "transcript_zip_entries": recorded,
        }
    )
    by_name = {entry.filename: entry.file_id for entry in entries}
    assert by_name["clip_Czech.srt"] == "srt-replaced"
    assert by_name["clip_Chinese (Traditional).srt"] == "srt-zh"


def test_archive_contains_named_files():
    payload = transcript_zip_archive_bytes(
        {
            "clip.srt": b"1\n00:00:00,000 --> 00:00:01,000\nHi\n",
            "clip.docx": b"PK-word",
        }
    )
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {"clip.srt", "clip.docx"}
        assert archive.read("clip.srt") == b"1\n00:00:00,000 --> 00:00:01,000\nHi\n"
        assert archive.read("clip.docx") == b"PK-word"

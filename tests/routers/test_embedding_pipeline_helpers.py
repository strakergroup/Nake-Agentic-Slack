"""Tests for embed-only pipeline helpers (thread SRT embed, RAY-80000)."""

from types import SimpleNamespace

from app.media.embed_spend import (
    embedding_source_language,
    embedding_target_language_codes,
    is_embed_only_pipeline,
)


def _task_info(**kwargs):
    defaults = {
        "pipeline_type": "transcribe_translate_embed",
        "detected_language": None,
        "translated_file_ids": None,
        "extra_data": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_is_embed_only_when_pipeline_type_embed():
    task = _task_info(pipeline_type="embed")
    assert is_embed_only_pipeline(task) is True


def test_embedding_target_languages_from_language_codes():
    task = _task_info(
        pipeline_type="embed",
        extra_data={"language_codes": ["ja"], "target_languages": []},
    )
    assert embedding_target_language_codes(task) == ["ja"]


def test_embedding_source_from_language_codes_when_no_whisper():
    task = _task_info(
        pipeline_type="embed",
        detected_language=None,
        extra_data={"language_codes": ["ja"]},
    )
    assert embedding_source_language(task) == "ja"


def test_embedding_skips_und_language_code():
    task = _task_info(
        pipeline_type="embed",
        extra_data={"language_codes": ["und"]},
    )
    assert embedding_target_language_codes(task) == []
    assert embedding_source_language(task) is None

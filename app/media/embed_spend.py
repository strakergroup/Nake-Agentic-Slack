"""Pure helpers for media embed billing metadata (RAY-80000)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import TranscriptionTaskInfo


def embedding_target_language_codes(task_info: "TranscriptionTaskInfo") -> list[str]:
    """Target language codes for an embed debit (modal selection or translated SRTs)."""
    extra_data = task_info.extra_data or {}
    codes = extra_data.get("target_languages")
    if isinstance(codes, list) and codes:
        return [str(code) for code in codes if code]
    language_codes = extra_data.get("language_codes")
    if isinstance(language_codes, list) and language_codes:
        return [
            str(code) for code in language_codes if code and str(code).lower() != "und"
        ]
    translated = task_info.translated_file_ids or {}
    if translated:
        return list(translated.keys())
    return []


def embedding_source_language(task_info: "TranscriptionTaskInfo") -> str | None:
    """Source language for embed usage (Whisper, task column, or thread SRT hint)."""
    if task_info.detected_language:
        return task_info.detected_language
    extra_data = task_info.extra_data or {}
    for code in extra_data.get("language_codes") or []:
        if code and str(code).lower() != "und":
            return str(code)
    return None


def is_embed_only_pipeline(task_info: "TranscriptionTaskInfo") -> bool:
    """True when the task skips transcribe/translate (direct thread SRT embed)."""
    if task_info.pipeline_type == "embed":
        return True
    extra_data = task_info.extra_data or {}
    return extra_data.get("pipeline_type") == "embed"

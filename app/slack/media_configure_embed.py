"""Start Configure source/translated subtitle embed jobs after SRT approval."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import domains
from app.database import async_engines
from app.media.media_workflow import MediaEmbedRole
from app.models import ASRTask, TranscriptionTask, TranscriptionTaskData
from app.slack.media_quotes import PIPELINE_EMBED
from app.transcriber_tasks.tasks import create_asr_task


class TranscriptionTaskExtraDataError(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__("Could not parse transcription extra_data for Configure embed")
        self.__cause__ = cause


class TranslatedSrtLanguageRequired(Exception):
    def __init__(self) -> None:
        super().__init__(
            "Replacement translated SRT language is required when embedding "
            "multiple target languages"
        )


class TranscriptionTaskExtraData(BaseModel):
    """JSON extra_data on transcription_tasks for Configure embed jobs."""

    model_config = ConfigDict(frozen=True, extra="allow")

    media_quote_id: str | None = None
    pipeline_kind: str | None = None
    pipeline_type: str | None = None
    workflow_type: str | None = None
    original_video_file_id: str | None = None
    original_video_download_url: str | None = None
    original_video_file_name: str | None = None
    srt_file_ids: list[str] | None = None
    language_codes: list[str] | None = None
    target_languages: list[str] | None = None
    embed_role: MediaEmbedRole | None = None


def parse_transcription_task_extra_data(raw: object) -> TranscriptionTaskExtraData:
    try:
        return TranscriptionTaskExtraData.model_validate(raw or {})
    except ValidationError as exc:
        raise TranscriptionTaskExtraDataError(exc) from exc


def _translated_srt_map(
    task: TranscriptionTask,
    session: dict[str, Any],
    replacement_id: str | None,
    fallback_languages: list[str],
) -> dict[str, str]:
    ids_map = dict(task.translated_file_ids or {})
    if not replacement_id:
        return ids_map
    language = session.get("approved_translated_srt_language")
    if language:
        ids_map[str(language)] = replacement_id
        return ids_map
    if len(ids_map) == 1:
        ids_map[next(iter(ids_map))] = replacement_id
        return ids_map
    if not ids_map:
        lang = (fallback_languages[:1] or ["und"])[0]
        return {str(lang): replacement_id}
    return ids_map


def _ordered_translated_tracks(
    ids_map: dict[str, str], target_languages: list[str]
) -> tuple[list[str], list[str]]:
    ordered_ids: list[str] = []
    ordered_langs: list[str] = []
    for lang in target_languages:
        key = str(lang)
        if key in ids_map:
            ordered_langs.append(key)
            ordered_ids.append(ids_map[key])
    for lang, file_id in ids_map.items():
        if lang not in ordered_langs:
            ordered_langs.append(str(lang))
            ordered_ids.append(file_id)
    return ordered_ids, ordered_langs


def _configure_embed_tracks(
    *,
    session: dict[str, Any],
    task: TranscriptionTask,
    translated: bool,
) -> tuple[list[str] | None, list[str], list[str]]:
    replacement_id = (
        session.get("approved_translated_srt_file_id")
        if translated
        else session.get("approved_source_srt_file_id")
    )
    language_codes = (
        list(session.get("target_languages") or ["und"]) if translated else ["und"]
    )
    if not translated:
        if replacement_id:
            return [replacement_id], language_codes, language_codes
        if task.result_file_id:
            return [task.result_file_id], language_codes, language_codes
        return None, language_codes, language_codes

    ids_map = _translated_srt_map(
        task,
        session,
        replacement_id if isinstance(replacement_id, str) and replacement_id else None,
        language_codes,
    )
    translated_ids, translated_langs = _ordered_translated_tracks(
        ids_map, list(session.get("target_languages") or language_codes)
    )
    source_id = session.get("approved_source_srt_file_id") or task.result_file_id
    source_lang = getattr(task, "detected_language", None) or "und"
    srt_file_ids = ([source_id] if source_id else []) + translated_ids
    mux_languages = ([str(source_lang)] if source_id else []) + translated_langs
    return srt_file_ids or None, mux_languages, translated_langs


async def resume_configure_embed_phase(
    *,
    session: dict[str, Any],
    translated: bool,
) -> None:
    """Resume the existing transcription task as an embed-only job after SRT approval."""
    task_uuid = session.get("task_uuid")
    if not task_uuid:
        raise ValueError("Missing transcription task for subtitle embedding")

    async with AsyncSession(async_engines["sitecommons"]) as db_session:
        task = await db_session.get(TranscriptionTask, task_uuid)
        if not task:
            raise ValueError(f"Transcription task {task_uuid} not found")
        extra = parse_transcription_task_extra_data(task.extra_data)
        srt_file_ids, language_codes, billing_targets = _configure_embed_tracks(
            session=session, task=task, translated=translated
        )
        updates: dict[str, Any] = {
            "media_quote_id": session["quote_id"],
            "pipeline_kind": PIPELINE_EMBED,
            "workflow_type": session.get("workflow_type"),
            "original_video_file_id": session.get("file_id"),
            "original_video_download_url": session.get("download_url"),
            "original_video_file_name": session.get("file_name"),
            "language_codes": language_codes,
            "target_languages": billing_targets,
            "pipeline_type": PIPELINE_EMBED,
            "embed_role": (
                MediaEmbedRole.TRANSLATED if translated else MediaEmbedRole.SOURCE
            ),
        }
        if srt_file_ids is not None:
            updates["srt_file_ids"] = srt_file_ids
        extra_data = extra.model_copy(update=updates).model_dump(mode="json")
        if session.get("workflow_type"):
            task_data = TranscriptionTaskData(
                client_id=task.client_id,
                file_name=task.file_name,
                download_url=task.download_url,
                app_token=task.bot_token or "",
                out_stream_name=(
                    f"{domains.stream_proxy}/events/transcription:slack:media:results"
                ),
                service=task.service or "azure",
                model=task.model or "whisper-1",
                embed_subtitles=True,
                sandbox=False,
            )
            await create_asr_task(
                ASRTask(
                    member_uuid=task.client_id,
                    event_name="sup-subtitle-ai:media:asr",
                    app_source=task.app_source or "slack",
                    service=task.service or "azure",
                    model=task.model or "whisper-1",
                    extra_data=extra_data,
                    task_data=task_data,
                )
            )
            return
        await db_session.execute(
            update(TranscriptionTask)
            .where(TranscriptionTask.task_uuid == task_uuid)
            .values(
                pipeline_type=PIPELINE_EMBED,
                status="pending",
                stage=None,
                error_message=None,
                extra_data=extra_data,
            )
        )
        await db_session.commit()

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/sup-subtitle-ai:media:asr",
            json={
                "data": {"task_uuid": task_uuid},
                "source": "Straker Translate for Slack",
            },
        )

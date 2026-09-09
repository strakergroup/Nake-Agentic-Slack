"""Start Configure source/translated subtitle embed jobs after SRT approval."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import domains
from app.database import async_engines
from app.media.media_workflow import MediaEmbedRole
from app.models import ASRTask, TranscriptionTask, TranscriptionTaskData
from app.slack.media_quotes import PIPELINE_EMBED
from app.transcriber_tasks.tasks import create_asr_task


async def resume_configure_embed_phase(
    *,
    session: dict[str, Any],
    translated: bool,
) -> None:
    """Resume the existing transcription task as an embed-only job after SRT approval."""
    task_uuid = session.get("task_uuid")
    if not task_uuid:
        raise ValueError("Missing transcription task for subtitle embedding")

    srt_file_id = (
        session.get("approved_translated_srt_file_id")
        if translated
        else session.get("approved_source_srt_file_id")
    )
    language_codes = (
        list(session.get("target_languages") or ["und"]) if translated else ["und"]
    )

    async with AsyncSession(async_engines["sitecommons"]) as db_session:
        task = await db_session.get(TranscriptionTask, task_uuid)
        if not task:
            raise ValueError(f"Transcription task {task_uuid} not found")
        extra_data = dict(task.extra_data or {})
        extra_data["media_quote_id"] = session["quote_id"]
        extra_data["pipeline_kind"] = PIPELINE_EMBED
        extra_data["workflow_type"] = session.get("workflow_type")
        extra_data["original_video_file_id"] = session.get("file_id")
        extra_data["original_video_download_url"] = session.get("download_url")
        extra_data["original_video_file_name"] = session.get("file_name")
        if srt_file_id:
            extra_data["srt_file_ids"] = [srt_file_id]
        elif task.result_file_id and not translated:
            extra_data["srt_file_ids"] = [task.result_file_id]
        elif translated and task.translated_file_ids:
            extra_data["srt_file_ids"] = list(task.translated_file_ids.values())
            language_codes = list(task.translated_file_ids.keys())
        extra_data["language_codes"] = language_codes
        extra_data["target_languages"] = language_codes
        extra_data["pipeline_type"] = PIPELINE_EMBED
        extra_data["embed_role"] = (
            MediaEmbedRole.TRANSLATED if translated else MediaEmbedRole.SOURCE
        )
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

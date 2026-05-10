from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ray.events.models import MtSuccessResponseSchema


async def schedule_mt_success_upload(success_data: MtSuccessResponseSchema) -> None:
    from app.saq_jobs.dispatch import enqueue_mt_success_upload

    await enqueue_mt_success_upload(success_data)


async def schedule_transcription_upload(
    *,
    file_id: str,
    file_name: str,
    task_uuid: str,
    pipeline_type: str | None,
    client_id: str,
    channel_id: str,
    thread_ts: str | None,
    follow_up_message: str | None = None,
) -> None:
    from app.saq_jobs.dispatch import enqueue_transcription_upload

    await enqueue_transcription_upload(
        file_id=file_id,
        file_name=file_name,
        task_uuid=task_uuid,
        pipeline_type=pipeline_type,
        client_id=client_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        follow_up_message=follow_up_message,
    )


async def schedule_verify_complete_upload(
    *,
    grid_file_id: str,
    client_id: str,
    channel_id: str,
) -> None:
    from app.saq_jobs.dispatch import enqueue_verify_complete_upload

    await enqueue_verify_complete_upload(
        grid_file_id=grid_file_id,
        client_id=client_id,
        channel_id=channel_id,
    )


__all__ = [
    "schedule_mt_success_upload",
    "schedule_transcription_upload",
    "schedule_verify_complete_upload",
]

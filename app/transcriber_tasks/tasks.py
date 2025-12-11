"""Tasks for transcriber-task-consumer
Insert into database and add to redis
"""

import datetime
from uuid import uuid4

import httpx
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import domains
from ..database import async_engines
from ..models import (
    ASRTask,
    ASRTaskResult,
    TranscriptionRequest,
    TranscriptionTask,
    TranscriptionTaskInfo,
)


async def create_asr_task(asr_task: ASRTask):
    """Create task in sitecommons.transcription_tasks table then add to redis

    Args:
        asr_task: ASRTask with task_data and extra_data

    Returns:
        task_uuid: UUID of the created task
    """
    task_uuid = str(uuid4())

    extra_data = asr_task.extra_data.copy()
    pipeline_type = extra_data.get("pipeline_type", "transcribe")

    # Store task info in transcription_tasks table for lookup
    bot_token = asr_task.task_data.app_token

    async with AsyncSession(async_engines["sitecommons"]) as session:
        # Check if task already exists
        existing_task = await session.get(TranscriptionTask, task_uuid)

        if existing_task:
            # Update existing task timestamp (matching ON DUPLICATE KEY UPDATE behavior)
            await session.execute(
                update(TranscriptionTask)
                .where(TranscriptionTask.task_uuid == task_uuid)
                .values(updated_at=func.current_timestamp())
            )
            await session.commit()
        else:
            # Create new task
            new_task = TranscriptionTask(
                task_uuid=task_uuid,
                client_id=asr_task.task_data.client_id,
                file_name=asr_task.task_data.file_name,
                download_url=asr_task.task_data.download_url,
                bot_token=bot_token,
                pipeline_type=pipeline_type,
                status="pending",
                tokens_consumed=asr_task.task_data.tokens_consumed,
                extra_data=extra_data if extra_data else None,
                duration_ms=asr_task.len_ms,
                model=asr_task.model,
                service=asr_task.service,
                app_source=asr_task.app_source,
            )
            session.add(new_task)
            await session.commit()

    # Send to transcription-service - only task_uuid needed (client_id retrieved from database)
    transcription_request = TranscriptionRequest(task_uuid=task_uuid)
    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/{asr_task.event_name}",
            json={
                "data": transcription_request.model_dump(),
                "source": "Straker Translate for Slack",
            },
        )

    return task_uuid


async def get_asr_task(task_uuid: str) -> ASRTaskResult | None:
    """Get task from transcription_tasks table

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task result data with task_uuid, file_id, file_name, status, error
    """
    async with AsyncSession(async_engines["sitecommons"]) as session:
        task = await session.get(TranscriptionTask, task_uuid)

        if task:
            # Use Pydantic model for validation, then convert to dict for backward compatibility
            task_result = ASRTaskResult(
                task_uuid=task.task_uuid,
                file_id=task.result_file_id,
                file_name=task.result_file_name,
                status=task.status,
                error=task.error_message,
            )
            return task_result
    return None


async def get_asr_task_extra_data(task_uuid: str) -> dict | None:
    """Get task extra_data from transcription_tasks table.

    Used to retrieve pipeline info like target languages for translation.

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task extra_data or None if not found
    """
    async with AsyncSession(async_engines["sitecommons"]) as session:
        task = await session.get(TranscriptionTask, task_uuid)

        if task and task.extra_data:
            return task.extra_data
    return None


async def get_transcription_task(task_uuid: str) -> TranscriptionTaskInfo | None:
    """Get transcription task info from transcription_tasks table.

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        TranscriptionTaskInfo: Task info including bot_token, download_url, etc. or None if not found
    """
    async with AsyncSession(async_engines["sitecommons"]) as session:
        task = await session.get(TranscriptionTask, task_uuid)

        if task:
            # Use Pydantic model for validation, then convert to dict for backward compatibility
            task_info = TranscriptionTaskInfo(
                task_uuid=task.task_uuid,
                client_id=task.client_id,
                file_name=task.file_name,
                download_url=task.download_url,
                bot_token=task.bot_token,
                pipeline_type=task.pipeline_type,
                status=task.status,
                error_message=task.error_message,
                result_file_id=task.result_file_id,
                result_file_name=task.result_file_name,
                detected_language=task.detected_language,
                tokens_consumed=task.tokens_consumed,
                extra_data=task.extra_data,
                started_at=task.started_at,
                finished_at=task.finished_at,
                duration_ms=task.duration_ms,
                model=task.model,
                service=task.service,
                app_source=task.app_source,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            return task_info
    return None


async def update_transcription_task_status(
    task_uuid: str,
    status: str,
    error_message: str | None = None,
    result_file_id: str | None = None,
    result_file_name: str | None = None,
    detected_language: str | None = None,
    tokens_consumed: int | None = None,
) -> None:
    """Update transcription task status and results.

    Args:
        task_uuid: UUID of the task
        status: New status (pending, processing, completed, failed)
        error_message: Error message if failed
        result_file_id: Result file ID if completed
        result_file_name: Result file name if completed
        detected_language: Detected language code
        tokens_consumed: Tokens consumed
    """
    async with AsyncSession(async_engines["sitecommons"]) as session:
        task = await session.get(TranscriptionTask, task_uuid)

        if task:
            # Track when processing starts
            if status == "processing" and task.started_at is None:
                task.started_at = datetime.datetime.now(datetime.timezone.utc).replace(
                    tzinfo=None
                )

            # Track when processing finishes
            if status in ("completed", "failed") and task.finished_at is None:
                task.finished_at = datetime.datetime.now(datetime.timezone.utc).replace(
                    tzinfo=None
                )

            task.status = status
            if error_message is not None:
                task.error_message = error_message
            if result_file_id is not None:
                task.result_file_id = result_file_id
            if result_file_name is not None:
                task.result_file_name = result_file_name
            if detected_language is not None:
                task.detected_language = detected_language
            if tokens_consumed is not None:
                task.tokens_consumed = tokens_consumed

            await session.commit()

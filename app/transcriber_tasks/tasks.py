"""Tasks for transcriber-task-consumer
Insert into database and add to redis
"""

import json
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import text
from straker_utils.sql.async_engine import execute, fetch_one

from ..config import domains
from ..database import async_engines
from ..models import ASRTask


async def create_asr_task(asr_task: ASRTask):
    """Create task in sitecommons.transcription_tasks table then add to redis

    Args:
        asr_task: ASRTask with task_data and extra_data

    Returns:
        task_uuid: UUID of the created task
    """
    task_status = "Pending"
    task_uuid = str(uuid4())
    entry_id = ""

    # Map pipeline_type from slack format to transcription-service format
    pipeline_type_mapping = {
        "transcription_translation": "transcribe_translate",
        "transcription_translation_embed": "transcribe_translate_embed",
    }
    extra_data = asr_task.extra_data.copy()
    pipeline_type = extra_data.get("pipeline_type", "transcribe")
    pipeline_type = pipeline_type_mapping.get(pipeline_type, pipeline_type)

    # Store task info in transcription_tasks table for lookup
    # Get bot token - try app_token first, then fetch from database if needed
    bot_token = asr_task.task_data.app_token
    if not bot_token:
        # Try to get bot token from extra_data if available
        slack_team_id = extra_data.get("slack_team_id")
        slack_enterprise_id = extra_data.get("slack_enterprise_id")
        if slack_team_id:
            from ..auth.connector import get_bot_token_async

            bot_token = (
                await get_bot_token_async(
                    team_id=slack_team_id,
                    enterprise_id=slack_enterprise_id,
                )
                or ""
            )

    insert_task_sql = text(
        """
        INSERT INTO transcription_tasks
            (task_uuid, client_id, file_name, download_url, bot_token, pipeline_type,
             status, tokens_consumed, extra_data)
        VALUES
            (:task_uuid, :client_id, :file_name, :download_url, :bot_token, :pipeline_type,
             'pending', :tokens_consumed, :extra_data)
        ON DUPLICATE KEY UPDATE
            updated_at = CURRENT_TIMESTAMP
        """
    ).bindparams(
        task_uuid=task_uuid,
        client_id=asr_task.task_data.client_id,
        file_name=asr_task.task_data.file_name,
        download_url=asr_task.task_data.download_url,
        bot_token=bot_token,
        pipeline_type=pipeline_type,
        tokens_consumed=asr_task.task_data.tokens_consumed,
        extra_data=json.dumps(extra_data) if extra_data else None,
    )
    await execute(insert_task_sql, async_engines["sitecommons"], commit_after=True)

    # Send to transcription-service in new format - includes task_uuid and client_id
    transcription_request = {
        "task_uuid": task_uuid,
        "client_id": asr_task.task_data.client_id,
    }
    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/{asr_task.event_name}",
            json={
                "data": transcription_request,
                "source": "Straker Translate for Slack",
            },
        )

    return task_uuid


async def get_asr_task(task_uuid: str, member_uuid: str):
    """Get task from transcription_tasks table

    Args:
        task_uuid (str): UUID of the task to get
        member_uuid (str): UUID of the member (for compatibility, checked via client_id)

    Returns:
        dict: Task data in format compatible with old API (with task_result field)
    """
    sql = text(
        """
        SELECT
            task_uuid, client_id, file_name, download_url, bot_token, pipeline_type,
            status, error_message, result_file_id, result_file_name, detected_language,
            tokens_consumed, extra_data, created_at, updated_at
        FROM sitecommons.transcription_tasks
        WHERE task_uuid = :task_uuid
        """
    ).bindparams(task_uuid=task_uuid)
    result = await fetch_one(sql, async_engines["sitecommons"])

    if result:
        # Parse JSON fields and format as task_result for backward compatibility
        if result.get("extra_data"):
            result["extra_data"] = json.loads(result["extra_data"])
        # Return in format expected by callers (as task_result JSON string)
        return {
            "task_result": json.dumps(
                {
                    "task_uuid": result["task_uuid"],
                    "file_id": result.get("result_file_id"),
                    "file_name": result.get("result_file_name"),
                    "status": result["status"],
                    "error": result.get("error_message"),
                }
            )
        }
    return None


async def get_asr_task_extra_data(task_uuid: str) -> dict | None:
    """Get task extra_data from transcription_tasks table.

    Used to retrieve pipeline info like target languages for translation.

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task extra_data or None if not found
    """
    sql = text(
        """
        SELECT extra_data FROM sitecommons.transcription_tasks
        WHERE task_uuid = :task_uuid
        """
    ).bindparams(task_uuid=task_uuid)
    result = await fetch_one(sql, async_engines["sitecommons"])

    if result and result["extra_data"]:
        return json.loads(result["extra_data"])
    return None


async def get_asr_task_duration(task_uuid: str) -> int | None:
    """Get task duration from transcription_tasks table.

    Note: Duration is not stored in transcription_tasks table.
    This function is kept for backward compatibility but returns None.

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        int: Duration in milliseconds or None if not found
    """
    # Duration is not stored in transcription_tasks table
    # This function is kept for backward compatibility
    return None


async def get_transcription_task(task_uuid: str) -> dict | None:
    """Get transcription task info from transcription_tasks table.

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task info including bot_token, download_url, etc. or None if not found
    """
    sql = text(
        """
        SELECT
            task_uuid, client_id, file_name, download_url, bot_token, pipeline_type,
            status, error_message, result_file_id, result_file_name, detected_language,
            tokens_consumed, extra_data, created_at, updated_at
        FROM sitecommons.transcription_tasks
        WHERE task_uuid = :task_uuid
        """
    ).bindparams(task_uuid=task_uuid)
    result = await fetch_one(sql, async_engines["sitecommons"])

    if result:
        # Parse JSON fields
        if result.get("extra_data"):
            result["extra_data"] = json.loads(result["extra_data"])
        return result
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
    updates = ["status = :status", "updated_at = CURRENT_TIMESTAMP"]
    params: dict[str, Any] = {"task_uuid": task_uuid, "status": status}

    if error_message is not None:
        updates.append("error_message = :error_message")
        params["error_message"] = error_message

    if result_file_id is not None:
        updates.append("result_file_id = :result_file_id")
        params["result_file_id"] = result_file_id

    if result_file_name is not None:
        updates.append("result_file_name = :result_file_name")
        params["result_file_name"] = result_file_name

    if detected_language is not None:
        updates.append("detected_language = :detected_language")
        params["detected_language"] = detected_language

    if tokens_consumed is not None:
        updates.append("tokens_consumed = :tokens_consumed")
        params["tokens_consumed"] = tokens_consumed

    sql = text(
        f"""
        UPDATE sitecommons.transcription_tasks
        SET {", ".join(updates)}
        WHERE task_uuid = :task_uuid
        """
    ).bindparams(**params)
    await execute(sql, async_engines["sitecommons"], commit_after=True)

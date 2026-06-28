"""
Slack Job management functions for handling translation jobs.
"""

import json
import uuid
from typing import Any, Optional

from sqlalchemy import text
from straker_utils.sql.async_engine import fetch_one

from app.database import async_engines
from app.ray.events.models import MtFileRequestSchema


async def create_slack_job(
    mt_request: MtFileRequestSchema,
    file_name: Optional[str] = None,
    status: str = "pending",
) -> str:
    """
    Create a new slack job record from MtFileRequestSchema.

    Args:
        mt_request: The machine translation file request schema
        file_name: Optional file name
        status: Job status (default: "pending")

    Returns:
        str: The task ID
    """
    task_uuid = str(uuid.uuid4())

    # Store embed_subtitles and original_video_file_id in a JSON extra_data field
    # If the column doesn't exist, this will be ignored (graceful degradation)
    extra_data: dict[str, Any] = {}
    if mt_request.embed_subtitles:
        extra_data["embed_subtitles"] = True
        if mt_request.original_video_file_id:
            extra_data["original_video_file_id"] = mt_request.original_video_file_id
        if mt_request.original_video_file_name:
            extra_data["original_video_file_name"] = mt_request.original_video_file_name
    if len(mt_request.target_languages) > 1:
        extra_data["target_languages"] = mt_request.target_languages
        extra_data["submission_ids"] = mt_request.submission_ids

    async with async_engines["verify"].connect() as conn:
        # Try to insert with extra_data column if it exists
        try:
            sql = text("""
                INSERT INTO slack_job
                (status, client_uuid, grid_fs_id, file_name, app_source, selected_language,
                 ai_engine, task_uuid, extra_data)
                VALUES
                (:status, :client_uuid, :grid_fs_id, :file_name, :app_source,
                 :selected_language, :ai_engine, :task_uuid, :extra_data)
            """)
            await conn.execute(
                sql,
                {
                    "status": status,
                    "client_uuid": mt_request.client_id,
                    "grid_fs_id": mt_request.file_id,
                    "file_name": file_name,
                    "app_source": mt_request.data_source,
                    "selected_language": mt_request.target_language,
                    "ai_engine": mt_request.ai_engine,
                    "task_uuid": task_uuid,
                    "extra_data": json.dumps(extra_data) if extra_data else None,
                },
            )
        except Exception:
            # Fallback if extra_data column doesn't exist
            sql = text("""
                INSERT INTO slack_job
                (status, client_uuid, grid_fs_id, file_name, app_source, selected_language,
                 ai_engine, task_uuid)
                VALUES
                (:status, :client_uuid, :grid_fs_id, :file_name, :app_source,
                 :selected_language, :ai_engine, :task_uuid)
            """)
            await conn.execute(
                sql,
                {
                    "status": status,
                    "client_uuid": mt_request.client_id,
                    "grid_fs_id": mt_request.file_id,
                    "file_name": file_name,
                    "app_source": mt_request.data_source,
                    "selected_language": mt_request.target_language,
                    "ai_engine": mt_request.ai_engine,
                    "task_uuid": task_uuid,
                },
            )
        await conn.commit()
    return task_uuid


async def get_slack_job(task_uuid: str) -> Optional[dict]:
    """
    Get slack job record by task_uuid.

    Args:
        task_uuid: The task UUID

    Returns:
        dict: Job record or None if not found
    """
    async with async_engines["verify"].connect() as conn:
        sql = text("""
            SELECT status, client_uuid, grid_fs_id, file_name, app_source,
                   selected_language, ai_engine, task_uuid,
                   extra_data
            FROM slack_job
            WHERE task_uuid = :task_uuid
        """)
        result = await fetch_one(
            sql.bindparams(task_uuid=task_uuid), async_engines["verify"]
        )

        if result:
            job_dict = dict(result)
            # Parse extra_data JSON if it exists
            if job_dict.get("extra_data"):
                try:
                    job_dict["extra_data"] = json.loads(job_dict["extra_data"])
                except (json.JSONDecodeError, TypeError):
                    job_dict["extra_data"] = {}
            else:
                job_dict["extra_data"] = {}
            return job_dict
        return None


async def update_slack_job(
    task_uuid: str | None,
    status: str,
) -> None:
    """
    Update a slack job record.

    Args:
        task_uuid: The task ID
        status: The status to update

    Returns:
        None
    """
    if not task_uuid:
        return
    async with async_engines["verify"].connect() as conn:
        sql = text("""
            UPDATE slack_job
            SET status = :status
            WHERE task_uuid = :task_uuid
        """)
        await conn.execute(
            sql,
            {
                "task_uuid": task_uuid,
                "status": status,
            },
        )
        await conn.commit()

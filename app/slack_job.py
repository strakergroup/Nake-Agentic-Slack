"""
Slack Job management functions for handling translation jobs.
"""

import uuid
from typing import Optional

from sqlalchemy import text

from app.database import engines
from app.ray.events.models import MtFileRequestSchema


def create_slack_job(
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
    with engines["verify"].connect() as conn:
        sql = text("""
            INSERT INTO slack_job
            (status, client_uuid, grid_fs_id, file_name, app_source, selected_language,
             ai_engine, task_uuid)
            VALUES
            (:status, :client_uuid, :grid_fs_id, :file_name, :app_source,
             :selected_language, :ai_engine, :task_uuid)
        """)

        conn.execute(
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
        conn.commit()
    return task_uuid


def update_slack_job(
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
    with engines["verify"].connect() as conn:
        sql = text("""
            UPDATE slack_job
            SET status = :status
            WHERE task_uuid = :task_uuid
        """)
        conn.execute(
            sql,
            {
                "task_uuid": task_uuid,
                "status": status,
            },
        )
        conn.commit()

"""Tasks for transcriber-task-consumer
Insert into database and add to redis
"""

import json
from uuid import uuid4

import httpx
from sqlalchemy import text
from straker_utils.sql.async_engine import execute, fetch_one

from ..config import domains
from ..database import async_engines
from ..models import ASRTask


async def create_asr_task(asr_task: ASRTask):
    """Create task in sitecommons.transcriber_tasks_consumer_queue table then add to redis

    Args:
        member_uuid (str): UUID of the member lc account
        event_name (str): Name of the event to call in wb-task-consumer
        task_data (dict): Task data, matches params of the task you are running in wb-task-consumer
    """
    task_status = "Pending"
    task_uuid = str(uuid4())
    entry_id = ""

    task_data = asr_task.task_data.model_dump(mode="json")
    task_data["task_uuid"] = task_uuid

    # Insert task into database
    sql = text(
        """
        INSERT INTO transcriber_task_consumer_queue
            (obj_uuid, member_uuid, event_name, app_source, len_ms, service, model, task_data, task_status, entry_id, extra_data)
        VALUES
            (:task_uuid, :member_uuid, :event_name, :app_source, :len_ms, :service, :model, :task_data, :task_status, :entry_id, :extra_data)
        """
    ).bindparams(
        task_uuid=task_uuid,
        member_uuid=asr_task.member_uuid,
        event_name=asr_task.event_name,
        app_source=asr_task.app_source,
        len_ms=asr_task.len_ms,
        service=asr_task.service,
        model=asr_task.model,
        task_data=json.dumps(task_data),
        task_status=task_status,
        entry_id=entry_id,
        extra_data=json.dumps(asr_task.extra_data),
    )
    await execute(sql, async_engines["sitecommons"], commit_after=True)

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/{asr_task.event_name}",
            json={
                "data": task_data,
                "source": "Straker Translate for Slack",
            },
        )

    return task_uuid


async def get_asr_task(task_uuid: str, member_uuid: str):
    """Get task from transcriber_tasks_consumer_queue table

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task data
    """
    sql = text(
        """
        SELECT task_result FROM transcriber_task_consumer_queue
        WHERE obj_uuid = :task_uuid
        AND member_uuid = :member_uuid
        """
    ).bindparams(task_uuid=task_uuid, member_uuid=member_uuid)
    result = await fetch_one(sql, async_engines["sitecommons"])

    return json.loads(result["task_result"]) if result else None


async def get_asr_task_extra_data(task_uuid: str) -> dict | None:
    """Get task extra_data from transcriber_tasks_consumer_queue table.

    Used to retrieve pipeline info like target languages for translation.

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task extra_data or None if not found
    """
    sql = text(
        """
        SELECT extra_data FROM transcriber_task_consumer_queue
        WHERE obj_uuid = :task_uuid
        """
    ).bindparams(task_uuid=task_uuid)
    result = await fetch_one(sql, async_engines["sitecommons"])

    if result and result["extra_data"]:
        return json.loads(result["extra_data"])
    return None

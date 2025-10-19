"""Tasks for wb-task-consumer
Insert into database and add to redis
"""

import json
from uuid import uuid4

import httpx
from sqlalchemy import text
from straker_utils.sql.async_engine import execute, fetch_one

from ..config import domains
from ..database import async_engines


async def create_task(
    member_uuid: str,
    event_name: str,
    callback_name: str,
    task_data: dict,
) -> str:
    """Create task in sitecommons.wb_tasks_consumer_queue table then add to redis

    Args:
        member_uuid (str): UUID of the member lc account
        event_name (str): Name of the event to call in wb-task-consumer
        callback_name (str): Name of the callback callback triggers slack event
        slack_client_id (str): Slack client ID used on slack events to know who to respond to
        task_data (dict): Task data, matches params of the task you are running in wb-task-consumer
    """
    task_status = "Pending"
    task_uuid = str(uuid4())
    entry_id = ""
    # create task data object
    task_data["task_id"] = task_uuid
    task_data["on_completed"] = {
        "callback_uri": f"{domains.stream_proxy}/events/{callback_name}",
        "data": {"client_id": member_uuid},
    }
    # Insert task into database
    sql = text(
        """
        INSERT INTO wb_task_consumer_queue
            (obj_uuid, member_uuid, event_name, task_data, task_status, entry_id)
        VALUES
            (:task_uuid, :member_uuid, :event_name, :task_data, :task_status, :entry_id)
        """
    ).bindparams(
        task_uuid=task_uuid,
        member_uuid=member_uuid,
        event_name=event_name,
        task_data=json.dumps(task_data),
        task_status=task_status,
        entry_id=entry_id,
    )
    await execute(sql, async_engines["sitecommons"], commit_after=True)

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/{event_name}",
            json={"data": task_data, "source": "Straker Translate for Slack"},
        )
    return "Task created!"


async def get_task(task_uuid: str, member_uuid: str) -> dict | None:
    """Get task from wb_tasks_consumer_queue table

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task data
    """
    sql = text(
        """
        SELECT task_result FROM wb_task_consumer_queue
        WHERE obj_uuid = :task_uuid
        AND member_uuid = :member_uuid
        """
    ).bindparams(task_uuid=task_uuid, member_uuid=member_uuid)
    result = await fetch_one(sql, async_engines["sitecommons"])

    return json.loads(result["task_result"]) if result else None

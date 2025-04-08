"""Tasks for transcriber-task-consumer
Insert into database and add to redis
"""

from uuid import uuid4
from sqlalchemy import text
from ..database import engines
import httpx
from ..config import domains
import json
from datetime import datetime


async def create_asr_task(
    member_uuid: str,
    event_name: str,
    callback_name: str,
    task_data: dict,
) -> str:
    """Create task in sitecommons.transcriber_tasks_consumer_queue table then add to redis

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

    # print with timestamp
    print(
        f"Before insert into database: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
    )

    # Insert task into database
    with engines["sitecommons"].begin() as conn:
        sql = text(
            """
            INSERT INTO transcriber_task_consumer_queue
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
        conn.execute(sql)

    # print with timestamp with ms
    print(
        f"After insert into database: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
    )

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/{event_name}",
            json={"data": task_data, "source": "Straker Translate for Slack"},
        )

    # print with timestamp
    print(
        f"After post to event: {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
    )

    return "Task created!"


async def get_asr_task(task_uuid: str, member_uuid: str) -> dict:
    """Get task from transcriber_tasks_consumer_queue table

    Args:
        task_uuid (str): UUID of the task to get

    Returns:
        dict: Task data
    """
    with engines["sitecommons"].begin() as conn:
        sql = text(
            """
            SELECT task_result FROM transcriber_task_consumer_queue
            WHERE obj_uuid = :task_uuid
            AND member_uuid = :member_uuid
            """
        ).bindparams(task_uuid=task_uuid, member_uuid=member_uuid)
        result = conn.execute(sql).fetchone()

        return json.loads(result[0]) if result else None

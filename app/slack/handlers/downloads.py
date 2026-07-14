"""Handlers for downloading transcribed files and AI translations."""

import os
from typing import Any, Dict, Optional

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import download_verify_file, get_client_evaluation_job
from app.auth.connector import RayContext
from app.ray.utils import download_from_file_server_async
from app.slack.evaluation_combined_quotes import ai_translation_target_file_uuids
from app.slack.middleware import require_ray_client
from app.slack.web import upload_file_to_slack_memory_efficient
from app.transcriber_tasks.tasks import get_asr_task
from app.translate import _


async def handle_download_transcribed_file(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    if await require_ray_client(context):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        assert action is not None
        task_uuid = action["value"]
        task_result = await get_asr_task(task_uuid)
        assert task_result is not None
        file_id = task_result.file_id
        assert file_id is not None, "ASR task has no file_id"
        file = await download_from_file_server_async(file_id)

        try:
            # Upload file to Slack using memory-efficient method
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file["file"],
                channel_id=context["channel_id"],
                title=file["file_name"],
                filename=file["file_name"],
            )
        finally:
            # Clean up the temporary file
            if os.path.exists(file["file"]):
                os.unlink(file["file"])


async def handle_download_ai_translation(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    if await require_ray_client(context):
        assert action is not None
        file_uuid = action["value"]
        assert context["ray"] is not None
        assert context["ray"].client is not None
        file = await download_verify_file(context["ray"].client, file_uuid)

        try:
            # Upload file to Slack using memory-efficient method
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file["file"],
                channel_id=context["channel_id"],
                title=file["file_name"],
                filename=file["file_name"],
            )
        finally:
            # Clean up the temporary file
            if os.path.exists(file["file"]):
                os.unlink(file["file"])


async def handle_download_ai_translations(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    if not await require_ray_client(context):
        return

    assert action is not None
    assert context["ray"] is not None
    assert context["ray"].client is not None

    job_uuid = action["value"]
    job = await get_client_evaluation_job(context["ray"].client, job_uuid)
    target_file_uuids = ai_translation_target_file_uuids(job["data"])
    channel_id = context.get("channel_id") or body.get("channel", {}).get("id")
    if not channel_id:
        raise AssertionError("No channel to upload AI translations to")
    thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get(
        "ts"
    )

    if not target_file_uuids:
        await client.chat_postMessage(
            channel=channel_id,
            text=_("No AI translation files are available to download yet."),
            thread_ts=thread_ts,
        )
        return

    await client.chat_postMessage(
        channel=channel_id,
        text=_("Uploading your AI translation files to this thread."),
        thread_ts=thread_ts,
    )

    for file_uuid in target_file_uuids:
        file = await download_verify_file(context["ray"].client, file_uuid)
        try:
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file["file"],
                channel_id=channel_id,
                title=file["file_name"],
                filename=file["file_name"],
                thread_ts=thread_ts,
            )
        finally:
            if os.path.exists(file["file"]):
                os.unlink(file["file"])

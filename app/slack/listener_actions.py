"""
This module contains functions for common actions which are executed in
Slack Bolt listener functions.
"""

from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_bolt.context.async_context import AsyncBoltContext
from ray_sdk import RayResponse

from .templates.messages import JobStatusMessage, InvalidJobMessage
from .templates.models import NewJobForm
from ..auth.connector import RayClient, approve_pending_groups
from ..ray import RayService
from .web import download_files


async def post_job_status(
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    channel_id: str | None = None,
) -> AsyncSlackResponse:
    """Tries to get the job details from the RAY API and post the job status
    to the Slack user. If the user cannot access the job, post another message
    instead.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        job_id (str): The ID of the job to get.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if not context.channel_id and not channel_id:
        raise AssertionError("No channel to post the job status to")

    job, response = await RayService.get_service(ray_client).get_job(job_id)
    try:
        if job is not None:
            msg = JobStatusMessage(job, ray_client.id)
            return await context.client.chat_postMessage(
                channel=context.channel_id or channel_id,
                text=msg.text,
                blocks=msg.blocks,
            )
        else:
            return await context.client.chat_postMessage(
                channel=context.channel_id or channel_id,
                text=InvalidJobMessage(job_id).text,
            )
    finally:
        if response is not None:
            context["log"].add_api_log(
                status_code=response.status_code,
                url=str(response.url),
                payload=response.request.content.decode() or None,
                response=response.content.decode() or None,
                headers=dict(response.headers.items()),
                version="v3",
            )


async def submit_job(
    context: AsyncBoltContext, ray_client: RayClient, form: NewJobForm
) -> list[RayResponse[None]]:
    """Submit a new job."""
    file_ids = (file.id for file in form.files if file.id)
    file_paths = await download_files(context.client, file_ids)
    return await RayService.get_service(ray_client).new_job(
        files=file_paths,
        sl=form.source_lang.code,
        tl=[lang.code for lang in form.target_langs],
        workflow=form.workflow,
        reference=form.reference,
        job_notes=form.notes,
    )


async def approve_pending_client(
    context: AsyncBoltContext,
    ray_client: RayClient,
    pending_client_id: str,
    pending_client_username: str,
):
    # TODO: Use API to approve clients when available.
    return await approve_pending_groups(
        ray_client.id, pending_client_id, pending_client_username
    )

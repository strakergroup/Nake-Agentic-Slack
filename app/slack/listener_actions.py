"""
This module contains functions for common actions which are executed in
Slack Bolt listener functions.
"""

import asyncio
from sentry_sdk import capture_exception, capture_message
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook.webhook_response import WebhookResponse
from slack_bolt.context.async_context import AsyncBoltContext
from ray_sdk import RayResponse

from .templates.messages import (
    JobStatusMessage,
    InvalidJobMessage,
    JobSummaryMessage,
    JobListMessage,
)
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
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id

    job, response = await RayService.get_service(ray_client).get_job(job_id)
    try:
        if job is not None:
            msg = JobStatusMessage(job, ray_client.id)
            return await context.client.chat_postMessage(
                channel=channel_id,
                text=msg.text,
                blocks=msg.blocks,
            )
        else:
            return await context.client.chat_postMessage(
                channel=channel_id,
                text=InvalidJobMessage(job_id).text,
            )
    finally:
        if response is not None:
            try:
                response_data = response.json()
            except Exception:
                response_data = response.content.decode() or None
            context["log"].add_api_log(
                status_code=response.status_code,
                url=str(response.url),
                payload=None,
                response=response_data,
                headers=dict(response.headers.items()),
                version="v3",
            )


async def post_job_summary(
    context: AsyncBoltContext,
    ray_client: RayClient,
    channel_id: str | None = None,
) -> AsyncSlackResponse:
    """Gets the job summary from the RAY API and posts it to the Slack user.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if not context.channel_id and not channel_id:
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id

    responses = await asyncio.gather(
        RayService.get_service(ray_client).get_job_summary(
            ["IN_PROGRESS", "VALIDATION", "PENDING_QUOTES", "ORDER_NOW"]
        ),
        RayService.get_service(ray_client).get_job_summary(
            ["COMPLETED"], completed_from=7 * 24
        ),
        return_exceptions=True,
    )

    in_progress_count = 0
    completed_count = 0
    validation_count = 0
    pending_quotes_count = 0
    order_now_count = 0

    if isinstance(responses[0], RayResponse):
        in_progress_count = responses[0].data.summary.get("in_progress", 0)
        validation_count = responses[0].data.summary.get("validation", 0)
        pending_quotes_count = responses[0].data.summary.get("pending_quotes", 0)
        order_now_count = responses[0].data.summary.get("order_now", 0)
    else:
        capture_exception(responses[0])
    if isinstance(responses[1], RayResponse):
        completed_count = responses[1].data.summary.get("completed", 0)
    else:
        capture_exception(responses[1])

    try:
        msg = JobSummaryMessage(
            in_progress=in_progress_count,
            completed=completed_count,
            validation=validation_count,
            pending_quotes=pending_quotes_count,
            order_now=order_now_count,
        )
        return await context.client.chat_postMessage(
            channel=channel_id,
            text=msg.text,
            blocks=msg.blocks,
        )
    finally:
        for response in responses:
            if isinstance(response, RayResponse):
                try:
                    response_data = response.response.json()
                except Exception:
                    response_data = response.response.content.decode() or None
                context["log"].add_api_log(
                    status_code=response.status_code,
                    url=str(response.response.url),
                    payload=None,
                    response=response_data,
                    headers=dict(response.response.headers.items()),
                    version="v3",
                )


async def post_job_list(
    context: AsyncBoltContext,
    ray_client: RayClient,
    preset: str,
    page: int = 1,
    page_size: int = 5,
    channel_id: str | None = None,
    replace_original: bool = False,
) -> AsyncSlackResponse | WebhookResponse:
    """Gets the job list from the RAY API and posts it to the Slack user.
    The list of jobs is filtered depending on the `preset` argument.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        preset (str): The preset to filter the job list.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if not context.channel_id and not channel_id and not context.response_url:
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id

    match preset:
        case "IN_PROGRESS:ACCEPTED:24H":
            title = "Jobs accepted within the last 24 hours"
            response = await RayService.get_service(ray_client).get_job_list(
                status="IN_PROGRESS", started_from=24, page=page, page_size=page_size
            )
        case "IN_PROGRESS:DUE:24H":
            title = "Jobs due within the next 24 hours"
            response = await RayService.get_service(ray_client).get_job_list(
                status="IN_PROGRESS", due_before=24, page=page, page_size=page_size
            )
        case "IN_PROGRESS":
            title = "All jobs in progress"
            response = await RayService.get_service(ray_client).get_job_list(
                status="IN_PROGRESS", page=page, page_size=page_size
            )
        case "COMPLETED:24H":
            title = "Jobs completed within the last 24 hours"
            response = await RayService.get_service(ray_client).get_job_list(
                status="COMPLETED", completed_from=24, page=page, page_size=page_size
            )
        case "COMPLETED:48H":
            title = "Jobs completed within the last 48 hours"
            response = await RayService.get_service(ray_client).get_job_list(
                status="COMPLETED", completed_from=48, page=page, page_size=page_size
            )
        case "COMPLETED:7D":
            title = "Jobs completed within the last 7 days"
            response = await RayService.get_service(ray_client).get_job_list(
                status="COMPLETED",
                completed_from=24 * 7,
                page=page,
                page_size=page_size,
            )
        case "VALIDATION":
            title = "All jobs in validation"
            response = await RayService.get_service(ray_client).get_job_list(
                status="VALIDATION", page=page, page_size=page_size
            )
        case "PENDING_QUOTES:24H":
            title = "Pending quotes from the last 24 hours"
            response = await RayService.get_service(ray_client).get_job_list(
                status="PENDING_QUOTES", from_hours=24, page=page, page_size=page_size
            )
        case "PENDING_QUOTES":
            title = "All pending quotes"
            response = await RayService.get_service(ray_client).get_job_list(
                status="PENDING_QUOTES", page=page, page_size=page_size
            )
        case "ORDER_NOW:24H":
            title = "Jobs quoted from the last 24 hours"
            response = await RayService.get_service(ray_client).get_job_list(
                status="ORDER_NOW", quoted_from=24, page=page, page_size=page_size
            )
        case "ORDER_NOW:7D":
            title = "Jobs quoted from the last 7 days"
            response = await RayService.get_service(ray_client).get_job_list(
                status="ORDER_NOW", quoted_from=24 * 7, page=page, page_size=page_size
            )
        case "ORDER_NOW":
            title = "All jobs quoted"
            response = await RayService.get_service(ray_client).get_job_list(
                status="ORDER_NOW", page=page, page_size=page_size
            )
        case _:
            capture_message(f"post_job_list: Invalid preset ({preset})")
            return
    try:
        msg = JobListMessage(
            preset=preset,
            title=title,
            jobs=response.data[0],
            pagination=response.data[1],
            client_id=ray_client.id
        )
        if context.response_url:
            return await context.respond(
                text=msg.text, blocks=msg.blocks, replace_original=replace_original
            )
        else:
            return await context.client.chat_postEphemeral(
                channel=channel_id,
                user=context.user_id,
                text=msg.text,
                blocks=msg.blocks,
            )
    finally:
        try:
            response_data = response.response.json()
        except Exception:
            response_data = response.response.content.decode() or None
        context["log"].add_api_log(
            status_code=response.status_code,
            url=str(response.response.url),
            payload=None,
            response=response_data,
            headers=dict(response.response.headers.items()),
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
    ray_client: RayClient,
    pending_client_id: str,
    pending_client_username: str,
):
    # TODO: Use API to approve clients when available.
    return await approve_pending_groups(
        ray_client.id, pending_client_id, pending_client_username
    )

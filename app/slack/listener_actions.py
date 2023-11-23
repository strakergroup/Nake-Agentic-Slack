"""
This module contains functions for common actions which are executed in
Slack Bolt listener functions.
"""

import asyncio
from typing import Any

from buglog import notify_exception, notify_message
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook.webhook_response import WebhookResponse
from slack_bolt.context.async_context import AsyncBoltContext
from ray_sdk import RayResponse
import httpx
import re

from .middleware import require_ray_client
from .templates.messages import (
    HelpMessage,
    LoginMessage,
    LogoutMessage,
    JobStatusNoIdMessage,
    NewJobMessage,
    JobQuotedMessage,
    JobStatusMessage,
    InvalidJobMessage,
    JobSummaryMessage,
    JobListMessage,
    JobDetailsMessage,
    InsightsMessage,
    ReportInsightsMessage,
    BatchListMessage,
    FileListMessage,
    JobTargetsNoIdMessage,
    JobTargetLangMessage,
    MachineTranslationMessage,
    InvalidMTResultMessage
)
from .templates.models import NewJobForm
from .templates.views import new_job_modal
from .templates.views import job_search_modal
from .web import files_list_simple, download_files
from ..auth.connector import RayClient, approve_pending_groups
from ..config import config, domains, Environment
from ..ray.service import RayService, get_job_predictions
from ..watson import watson_message


async def respond_to_message(
    context: AsyncBoltContext, message: dict[str, Any], *, use_thread: bool = False
):
    """Respond to a Slack message event (or app mention event).

    Args:
        context (AsyncBoltContext): The listener function context.
        message (dict[str, Any]): The message data from the request body.
        use_thread (bool, optional): Reply to messages in a thread. Defaults to False.
    """
    # Reply in a thread in channels and groups (non-ephemeral messages only).
    thread_ts = message.get("thread_ts", message.get("ts")) if use_thread else None

    # If there is no text, show new job button or ignore the message.
    if not message.get("text"):
        if message.get("files"):
            msg = NewJobMessage(context["channel_id"], message["ts"])
            await context.say(text=msg.text, blocks=msg.blocks, thread_ts=thread_ts)
        return

    response = watson_message(message["text"], context.get("user_id"))
    context["log"].set_watson_log(
        status_code=response.status_code,
        text=message["text"],
        response=response.data,
        headers=dict(response.headers),
        intents=response.data["output"]["intents"],
        entities=response.data["output"]["entities"],
    )
    match response.intent:
        case "General_About_You" | "General_Agent_Capabilities" | "General_Greetings":
            await context.say(
                text=HelpMessage(context).text,
                blocks=HelpMessage(context).blocks,
                thread_ts=thread_ts,
            )
        case "Login":
            await context.client.chat_postEphemeral(
                channel=context["channel_id"],
                user=context["user_id"],
                text=context["login_prompt"].text,
                blocks=context["login_prompt"].blocks,
            )
        case "Logout":
            if await require_ray_client(context):
                msg = LogoutMessage(context["ray"].client.username)
                await context.client.chat_postEphemeral(
                    channel=context["channel_id"],
                    user=context["user_id"],
                    text=msg.text,
                    blocks=msg.blocks,
                )
        case "Job_Overview":
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                await post_job_summary(
                    context, context["ray"].client, thread_ts=thread_ts
                )
        case "Job_Status":
            if tj_number_entity := response.findEntity("tj-number"):
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        context,
                        context["ray"].client,
                        tj_number_entity.groups[0],
                        thread_ts=thread_ts,
                    )
            else:
                await context.say(JobStatusNoIdMessage().text, thread_ts=thread_ts)
        case "Job_Targets":
            if tj_number_entity := response.findEntity("tj-number"):
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_target_lang(
                        context,
                        context["ray"].client,
                        tj_number_entity.groups[0]
                    )
            else:
                await context.say(JobTargetsNoIdMessage().text, thread_ts=thread_ts)
        case "New_Translation_Job":
            if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                msg = NewJobMessage(context["channel_id"], message["ts"])
                await context.say(text=msg.text, blocks=msg.blocks, thread_ts=thread_ts)
        case "Show_Insights":
            # TODO Disable insights on production for now.
            if config.environment != Environment.production:
                if await require_ray_client(context, variation=LoginMessage.INSIGHTS):
                    await post_insights(
                        context,
                        context["ray"].client,
                        message["text"],
                        thread_ts=thread_ts,
                    )
            else:
                await context.say(response.reply, thread_ts=thread_ts)
        case "Jokes":
            # Delegate jokes to IBM Watson Assistant dialog.
            await context.say(response.reply, thread_ts=thread_ts)
        case "Machine_Translate":
            # splict target and source language from the text
            try:
                message_match = re.findall(
                    r'(mt|Mt|mT|MT)\s(\w+)?(\s\w+)?\sto\s(\w+)(\s\w+)?\stranslate:\s?(.*)', message["text"], re.I)
                if message_match is not None:
                    mt_sl = message_match[-1][1]+message_match[-1][2]
                    mt_tl = message_match[-1][3]+message_match[-1][4]
                    mt_text = message_match[-1][-1]

                    await get_mt_translation(
                        context,
                        context["ray"].client,
                        source_lang=mt_sl,
                        target_lang=mt_tl,
                        sentence=mt_text[1],
                        thread_ts=thread_ts,
                    )
                else:
                    await context.say('Invalid machine translation request. Please try "Mt source language to target language translate: sentence."', thread_ts=thread_ts)
            except Exception as e:
                # Default to Watson Assistant fallback response if no other matches.
                await context.say(response.reply, thread_ts=thread_ts)
                notify_exception(
                    e, "Failed to get machine translation from watson response")
        case _:
            if tj_number_entity := response.findEntity("tj-number"):
                # Show the job status if only a job id is entered.
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        context,
                        context["ray"].client,
                        tj_number_entity.groups[0],
                        thread_ts=thread_ts,
                    )
            elif response.reply:
                # Default to Watson Assistant fallback response if no other matches.
                await context.say(response.reply, thread_ts=thread_ts)


async def post_job_status(
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
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
        thread_ts (str | None, optional): The message thread to reply to.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id

    job, response = await RayService.get_service(ray_client).get_job(job_id)
    try:
        if job is not None:
            job_prediction = (
                (await get_job_predictions([job_id]))[0].get("prediction", "")
                if job.status == "IN_PROGRESS"
                else ""
            )
            msg = JobStatusMessage(job, ray_client.id, job_prediction)
            if context.response_url:
                return await context.respond(text=msg.text, blocks=msg.blocks)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
        else:
            msg = InvalidJobMessage(job_id)
            if context.response_url:
                return await context.respond(text=msg.text)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    thread_ts=thread_ts,
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


async def post_job_details(
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    status: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
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
        thread_ts (str | None, optional): The message thread to reply to.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id

    if status == "PENDING_QUOTES":
        job, response = await RayService.get_service(ray_client).get_quote(job_id)
    else:
        job, response = await RayService.get_service(ray_client).get_job(job_id)
    # print job
    try:
        if job is not None:
            if status == "PENDING_QUOTES":
                msg = JobQuotedMessage(job)
                if context.response_url:
                    return await context.respond(text=msg.text, blocks=msg.blocks)
                else:
                    return await context.client.chat_postMessage(
                        channel=channel_id,
                        text=msg.text,
                        blocks=msg.blocks,
                        thread_ts=thread_ts,
                    )
            # get the job prediction
            job_prediction = (
                (await get_job_predictions([job_id]))[0].get("prediction", "")
                if job.status == "IN_PROGRESS"
                else ""
            )
            msg = JobDetailsMessage(job, ray_client.id, job_prediction)
            if context.response_url:
                return await context.respond(text=msg.text, blocks=msg.blocks)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
        else:
            msg = InvalidJobMessage(job_id)
            if context.response_url:
                return await context.respond(text=msg.text)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    thread_ts=thread_ts,
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
    thread_ts: str | None = None,
    all_jobs: bool = False,
) -> AsyncSlackResponse:
    """Gets the job summary from the RAY API and posts it to the Slack user.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.
        thread_ts (str | None, optional): The message thread to reply to.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id

    responses = await asyncio.gather(
        RayService.get_service(ray_client).get_job_summary(
            ["IN_PROGRESS", "VALIDATION", "PENDING_QUOTES", "ORDER_NOW"]
        ),
        RayService.get_service(ray_client).get_job_summary(
            ["COMPLETED"], completed_from=7 * 24
        ),
        RayService.get_service(ray_client).get_job_summary(
            ["IN_PROGRESS"], from_hours=24
        ),
        RayService.get_service(ray_client).get_job_summary(
            ["IN_PROGRESS"], due_before=24
        ),
        return_exceptions=True,
    )

    in_progress_count = 0
    completed_count = 0
    validation_count = 0
    pending_quotes_count = 0
    order_now_count = 0
    in_progress_count_24 = 0
    in_progress_due = 0
    predictions = {"on_time": 0, "late": 0, "over_due": 0}
    if isinstance(responses[2], RayResponse):
        in_progress_count_24 = responses[2].data.summary.get("in_progress", 0)
    if isinstance(responses[3], RayResponse):
        in_progress_due = responses[3].data.summary.get("in_progress", 0)
    if isinstance(responses[0], RayResponse):
        in_progress_count = responses[0].data.summary.get("in_progress", 0)
        validation_count = responses[0].data.summary.get("validation", 0)
        pending_quotes_count = responses[0].data.summary.get("pending_quotes", 0)
        order_now_count = responses[0].data.summary.get("order_now", 0)
        # Get job predictions.
        job_ids: list[str] = []
        for group in responses[0].data.groups:
            group_in_progress = group.get("in_progress", {})
            group_total = group_in_progress.get("count", 0)
            group_overdue = group.get("over_due_count", 0)
            predictions["over_due"] += group_overdue
            job_ids.extend(
                group_in_progress.get("jobs", [])[: group_total - group_overdue]
            )
        if job_ids and config.environment != Environment.production:
            try:
                job_predictions = await get_job_predictions(job_ids)
                for pred in job_predictions:
                    if pred.get("prediction") == "on_time":
                        predictions["on_time"] += 1
                    else:
                        predictions["late"] += 1
            except Exception as e:
                notify_exception(e)
    else:
        notify_exception(responses[0])
    if isinstance(responses[1], RayResponse):
        completed_count = responses[1].data.summary.get("completed", 0)
    else:
        notify_exception(responses[1])
    try:
        msg = JobSummaryMessage(
            in_progress=in_progress_count,
            in_progress_count_24=in_progress_count_24,
            in_progress_due=in_progress_due,
            completed=completed_count,
            validation=validation_count,
            pending_quotes=pending_quotes_count,
            order_now=order_now_count,
            predictions=predictions,
            all_jobs=all_jobs,
        )
        if context.response_url:
            return await context.respond(text=msg.text, blocks=msg.blocks)
        else:
            return await context.client.chat_postMessage(
                channel=channel_id,
                text=msg.text,
                blocks=msg.blocks,
                thread_ts=thread_ts,
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
    client_ref: str = "",
    page: int = 1,
    page_size: int = 5,
    channel_id: str | None = None,
    replace_original: bool = False,
) -> AsyncSlackResponse | WebhookResponse | None:
    """Gets the job list from the RAY API and posts it to the Slack user.
    The list of jobs is filtered depending on the `preset` argument.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        preset (str): The preset to filter the job list.
        client_ref (str, optional): The client reference to filter the job list
            if the preset is "CLIENT_REF".
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.
        replace_original (bool, optional): Replace the original ephemeral message.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    try:
        if (
            not channel_id
            and not context.channel_id
            and not context.user_id
            and not context.response_url
        ):
            raise AssertionError("No channel to post to")
        channel_id = channel_id or context.channel_id or context.user_id

        # Truncate client_ref due to DB 100 char limit.
        client_ref = client_ref[:100] if client_ref else ""

        match preset:
            case "IN_PROGRESS:ACCEPTED:24H":
                title = "Jobs accepted within the last 24 hours"
                response = await RayService.get_service(ray_client).get_job_list(
                    status="IN_PROGRESS",
                    started_from=24,
                    page=page,
                    page_size=page_size,
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
                    status="COMPLETED",
                    completed_from=24,
                    page=page,
                    page_size=page_size,
                )
            case "COMPLETED:48H":
                title = "Jobs completed within the last 48 hours"
                response = await RayService.get_service(ray_client).get_job_list(
                    status="COMPLETED",
                    completed_from=48,
                    page=page,
                    page_size=page_size,
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
                    status="PENDING_QUOTES",
                    from_hours=24,
                    page=page,
                    page_size=page_size,
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
                    status="ORDER_NOW",
                    quoted_from=24 * 7,
                    page=page,
                    page_size=page_size,
                )
            case "ORDER_NOW":
                title = "All jobs quoted"
                response = await RayService.get_service(ray_client).get_job_list(
                    status="ORDER_NOW", page=page, page_size=page_size
                )
            case "CLIENT_REFERENCE":
                title = f"Reference: {client_ref}"
                response = await RayService.get_service(ray_client).get_job_list(
                    client_ref=client_ref, page=page, page_size=page_size
                )
            case _:
                notify_message(f"post_job_list: Invalid preset ({preset})")
                return
    except Exception as e:
        notify_exception(e)
        raise
    try:
        job_ids_in_progress = [
            Job.id for Job in response.data[0] if Job.status == "IN_PROGRESS"
        ]
        job_predictions = (
            await get_job_predictions(job_ids_in_progress)
            if job_ids_in_progress
            else []
        )
        msg = JobListMessage(
            preset=preset,
            title=title,
            jobs=response.data[0],
            pagination=response.data[1],
            client_ref=client_ref,
            job_predictions=job_predictions,
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
    except Exception as e:
        notify_exception(e)
        raise
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


async def post_insights(
    context: AsyncBoltContext,
    ray_client: RayClient,
    prompt: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id

    async def send_insights_message():
        insights_response = httpx.post(
            f"{domains.insights_api}/nlp",
            json={"clientId": ray_client.id, "prompt": prompt},
            timeout=30,
        )
        insights_response = insights_response.json()
        insights_msg = InsightsMessage(insights_response["result"].strip())
        try:
            if context.response_url:
                await context.respond(
                    text=insights_msg.text, blocks=insights_msg.blocks
                )
            else:
                await context.client.chat_postMessage(
                    channel=channel_id,
                    text=insights_msg.text,
                    blocks=insights_msg.blocks,
                    thread_ts=thread_ts,
                )
        except Exception as e:
            notify_exception(e, "Failed to get insights from Insights API")
            # TODO send error message

    waiting_msg = ":stopwatch: Please wait as we gather your information..."
    if context.response_url:
        response = await context.respond(text=waiting_msg)
    else:
        response = await context.client.chat_postMessage(
            channel=channel_id,
            text=waiting_msg,
            thread_ts=thread_ts,
        )

    # Send insights message async because it might take a long time.
    asyncio.create_task(send_insights_message())

    return response


async def show_quote_form_modal(
    context: AsyncBoltContext,
    trigger_id: str,
    ray_client: RayClient,
    *,
    initial_files: list[dict[str, Any]] | None = None,
    check_last_messages: int = 0,
):
    """Show the quote form (new job form) modal.

    Args:
        context (AsyncBoltContext): The context from the listener.
        trigger_id (str): The trigger ID.
        ray_client (RayClient): The RAY client details.
        initial_files (list[dict[str, Any]] | None): The files to be selected
            in the source file dropdown when the form is shown.
        check_last_messages (int, optional): If no initial files set and this
            argument is greater than 0, check the last `check_last_messages`
            messages with the bot to find files to set as the initial files. If
            a message has files attached, select those files and stop finding.
            Only works with DM with the bot, not channels or groups.
    """
    # Include a bit more than the max 100 options due to hidden files.
    files = await files_list_simple(context.client, count=110)
    # Set initial selected files.
    if not initial_files and check_last_messages > 0:
        # Check last 100 messages maximum.
        check_last_messages = min(check_last_messages, 100)
        # Try to get the files from the last n messages to set as the
        # default files in the form dropdown.
        try:
            response = await context.client.conversations_history(
                channel=context["channel_id"],
                limit=check_last_messages,
            )
            for message in response["messages"]:
                if message.get("files"):
                    initial_files = message.get("files")
                    break
        except SlackApiError:
            # Unknown or forbidden conversation (e.g. channel, DM with other user).
            pass
    await context.client.views_open(
        trigger_id=trigger_id,
        view=new_job_modal(
            ray_client.username,
            file_options=files,
            initial_files=initial_files,
        ),
    )


# Show job search modal view dialog
async def show_job_search_modal(
    context: AsyncBoltContext,
    trigger_id: str,
    ray_client: RayClient,
):
    """Show the job search modal view dialog.

    Args:
        context (AsyncBoltContext): The context from the listener.
        trigger_id (str): The trigger ID.
        ray_client (RayClient): The RAY client details.
    """
    await context.client.views_open(
        trigger_id=trigger_id,
        view=job_search_modal(
            ray_client.username,
        ),
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
        group_id=form.group_id,
        workflow=form.workflow,
        timeframe=form.timeframe,
        reference=form.reference,
        job_notes=form.notes,
        translation_notes=form.translation_notes,
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


async def get_groups(ray_client: RayClient) -> list[dict[str, Any]]:
    """Get the groups for a client."""

    groups = await RayService.get_service(ray_client).get_groups()
    # return sorted by name lower case
    groups.sort(key=lambda x: x.name.lower())
    return [
        {
            "text": {"type": "plain_text", "text": group.name, "emoji": False},
            "value": group.id,
        }
        for group in groups
    ]


async def post_batch_list(
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    page: int,
    page_size: int,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    replace_original: bool = False,
) -> AsyncSlackResponse:
    """Tries to get the batch list from the RAY API and list in progress batches.
    If the user cannot access the job, post another message instead.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        job_id (str): The ID of the job to get.
        page (int): The page number to get.
        page_size (int): The page size to get.
        channel_id (str | None, optional): The channel to post the message to. If not given, posts to the source channel.
        thread_ts (str | None, optional): The message thread to reply to.
        replace_original (bool, optional): Replace the original ephemeral message.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id
    job, response = await RayService.get_service(ray_client).get_job(
        job_id, page, page_size
    )

    try:
        if job is not None:
            msg = BatchListMessage(job, ray_client.id)
            if context.response_url:
                return await context.respond(
                    text=msg.text, blocks=msg.blocks, replace_original=replace_original
                )
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
        else:
            msg = InvalidJobMessage(job_id)
            if context.response_url:
                return await context.respond(text=msg.text)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    thread_ts=thread_ts,
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


async def post_file_list(
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    page: int,
    page_size: int,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    replace_original: bool = False,
) -> AsyncSlackResponse:
    """Tries to get the file list from the RAY API and list translated files.
    If the user cannot access the job, post another message
    instead.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        job_id (str): The ID of the job to get.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.
        thread_ts (str | None, optional): The message thread to reply to.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id

    job, response = await RayService.get_service(ray_client).get_job(
        job_id, page, page_size
    )
    try:
        if job is not None:
            msg = FileListMessage(job, ray_client.id)
            if context.response_url:
                return await context.respond(
                    text=msg.text, blocks=msg.blocks, replace_original=replace_original
                )
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
        else:
            msg = InvalidJobMessage(job_id)
            if context.response_url:
                return await context.respond(text=msg.text)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    thread_ts=thread_ts,
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


async def post_job_target_lang(
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    page: int = 1,
    page_size: int = 5,
    channel_id: str | None = None,
) -> AsyncSlackResponse:
    """Tries to get the file list from the RAY API and list translated files, and batch file.
    If the user cannot access the job, post another message
    instead.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        job_id (str): The ID of the job to get.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.
        thread_ts (str | None, optional): The message thread to reply to.

    Raises:
        AssertionError: The `channel_id` is not given and there is no source channel.
    """
    # add check job status then get correct redirection function\
    job, response = await RayService.get_service(ray_client).get_job(
        job_id, page, page_size
    )
    no_job = False
    try:
        if job is not None:
            if len(job.batches):
                await post_batch_list(
                    context,
                    context["ray"].client,
                    job_id=job_id,
                    page=1,
                    page_size=5,
                )
            else:
                no_job = True

            if len(job.translated_file):
                await post_file_list(
                    context,
                    context["ray"].client,
                    job_id=job_id,
                    page=1,
                    page_size=5
                )
            else:
                no_job = True

            if no_job:
                msg = JobTargetLangMessage(job, ray_client.id)
                if context.response_url:
                    return await context.respond(text=msg.text, blocks=msg.blocks)
                else:
                    return await context.client.chat_postMessage(
                        channel=channel_id,
                        text=msg.text,
                        blocks=msg.blocks,
                    )
        else:
            msg = InvalidJobMessage(job_id)
            if context.response_url:
                return await context.respond(text=msg.text)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
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


async def post_report_insights(
    context: AsyncBoltContext,
    ray_client: RayClient,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
    """Show Insight message modal.

    Args:
        context (AsyncBoltContext): The context from the listener.
        ray_client (RayClient): The RAY client details.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.
        thread_ts (str | None, optional): The message thread to reply to.
    """
    if (
        not channel_id
        and not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = channel_id or context.channel_id or context.user_id

    async def send_insights_message():
        insights_msg = ReportInsightsMessage(ray_client.planname)
        try:
            if context.response_url:
                await context.respond(
                    text=insights_msg.text, blocks=insights_msg.blocks
                )
            else:
                await context.client.chat_postMessage(
                    channel=channel_id,
                    text=insights_msg.text,
                    blocks=insights_msg.blocks,
                    thread_ts=thread_ts,
                )
        except Exception as e:
            notify_exception(e, "Failed to get insights from Insights API")

    waiting_msg = ":stopwatch: Please wait as we gather your information..."
    if context.response_url:
        response = await context.respond(text=waiting_msg)
    else:
        response = await context.client.chat_postMessage(
            channel=channel_id,
            text=waiting_msg,
            thread_ts=thread_ts,
        )

    # Send insights message async because it might take a long time.
    asyncio.create_task(send_insights_message())

    return response


async def get_mt_translation(
    context: AsyncBoltContext,
    ray_client: RayClient,
    target_lang: str | None = None,
    source_lang: str | None = None,
    sentence: str | None = None,
    thread_ts: str | None = None,
):
    """ Get google machine translation for sentence by correct language pair.

    Args:
        context (AsyncBoltContext): The context from the listener.
        ray_client (RayClient): The RAY client details.
        source_lan (str | None, optional): The source language use for detect sentence.
        target_lang (str | None, optional): The target language use for translation.
        sentence (str | None, optional): The sentence post on RAY need to be translated.
        thread_ts (str | None, optional): The message thread to reply to.
    """
    if (
        not context.channel_id
        and not context.user_id
        and not context.response_url
    ):
        raise AssertionError("No channel to post to")
    channel_id = context.channel_id or context.user_id

    try:
        mt_data, response = await RayService.get_service(ray_client).get_machine_translation(target_lang, source_lang, sentence)
        if mt_data is not None:
            msg = MachineTranslationMessage(
                mt_data["target_lang"], mt_data["source_lang"], mt_data["text"])

            if context.response_url:
                return await context.respond(text=msg.text, blocks=msg.blocks)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
        else:
            msg = InvalidMTResultMessage()
            if context.response_url:
                return await context.respond(text=msg.text)
            else:
                return await context.client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    thread_ts=thread_ts,
                )
    except Exception as e:
        notify_exception(e, "Failed to get machine translation from language cloud API")

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

"""
This module contains functions for common actions which are executed in
Slack Bolt listener functions.
"""

import asyncio
from typing import Any
import re
import httpx
import langcodes
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_bolt.context.async_context import AsyncBoltContext
from ray_sdk import RayResponse
from buglog import notify_exception, notify_message

from app.mt.translate import get_ai_translation
from app.ray.events.models import MtFileRequestSchema
from app.translate import _
from app.wb_tasks.tasks import create_task

from .middleware import require_mt_tokens, require_ray_client
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
    AIHelperMessage,
    VerifyHelperMessage,
    BatchListMessage,
    FileListMessage,
    JobTargetsNoIdMessage,
    JobTargetLangMessage,
    AutoTranslationMessage,
    MachineTranslationMessage,
    InvalidMTResultMessage,
    CancelTJMessage,
    CancelJobMessage,
    TranscriptionMessage,
)
from .templates.models import NewJobForm
from .templates.views import (
    new_job_modal,
)
from .web import files_list_simple, download_files, get_mt_ts_cached, set_mt_ts_edit
from ..auth.connector import (
    RayClient,
    RayContext,
    approve_pending_groups,
    get_group_mt_engine,
)
from ..config import config, domains, Environment
from ..ray.service import RayService, get_job_predictions
from ..ray.settings import (
    get_auto_translate_settings_and_langs,
)
from ..ray.utils import is_ibm_enterprise
from ..watson import watson_message
from .select_options import get_file_options_cached


async def respond_to_message(
    client: AsyncWebClient,
    context: RayContext,
    message: dict[str, Any],
    *,
    use_thread: bool = False,
):
    """Respond to a Slack message event (or app mention event).

    Args:
        context (AsyncBoltContext): The listener function context.
        message (dict[str, Any]): The message data from the request body.
        use_thread (bool, optional): Reply to messages in a thread. Defaults to False.
    """
    # Reply in a thread in channels and groups (non-ephemeral messages only).
    thread_ts = message.get("thread_ts", message.get("ts")) if use_thread else ""
    # If there is no text, show new job button or ignore the message.
    if message.get("files"):
        if await require_ray_client(context):
            # Trigger file list to enter into cache. So that new job button click does not timeout
            asyncio.create_task(
                files_list_simple(client, channel_id=context["channel_id"], count=120)
            )
            # Handle video file
            for file in message["files"]:
                if file["filetype"] in ["mp4", "mp3"]:
                    file_info = await client.files_info(file=file["id"])
                    download_url = file_info["file"]["url_private"]
                    token = client.token
                    # send video to wb consumer
                    if await require_ray_client(context, prompt_login=False):
                        # TODO: Requires token check
                        if await require_mt_tokens(context):
                            await create_task(
                                context["ray"].client.id,
                                "wb_task:media:asr",
                                "ray:job:transcribed",
                                {
                                    "input_url": download_url,
                                    "input_token": token,
                                },
                            )
                            msg = TranscriptionMessage()
                            await context.say(text=msg.text, thread_ts=thread_ts)
                else:
                    new_job_msg = NewJobMessage(
                        context["channel_id"],
                        message["ts"],
                        file["id"],
                        context.ray.super_group[0].enable_verify_in_slack,
                    )
                    await context.say(
                        text=new_job_msg.text,
                        blocks=new_job_msg.blocks,
                        thread_ts=thread_ts,
                    )
            return
    # process mt
    message_match = re.search(
        r"mt:?(?:\s+([\w-]+))?\s+to\s+([\w-]+):?\s+(.*)",
        message["text"],
        re.I,
    )

    if message_match:
        if await require_ray_client(context):
            mt_sl = message_match.group(1) or ""
            # TODO: read user lang to default target
            mt_tl = message_match.group(2) or context.get("locale") or "en"
            mt_text = message_match.group(3)
            if await require_mt_tokens(context, len(mt_text)):
                await get_mt_translation(
                    client,
                    context,
                    source_lang=mt_sl,
                    target_lang=mt_tl,
                    sentence=mt_text,
                    thread_ts=thread_ts,
                )
        return
    if message["text"] == "debug":
        # retrieve workspace name based on bot token
        workspace_name = await client.auth_test()
        await context.say(f"Workspace name: {workspace_name['team']}")
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
            login_msg = LoginMessage(
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=context.get("channel_id", context["user_id"]),
                ray_client=(
                    context["ray"].client if context["ray"] is not None else None
                ),
            )
            await client.chat_postEphemeral(
                channel=context["channel_id"],
                user=context["user_id"],
                text=login_msg.text,
                blocks=login_msg.blocks,
            )
            # await client.chat_postEphemeral(
            #     channel=context["channel_id"],
            #     user=context["user_id"],
            #     text=context["login_prompt"].text,
            #     blocks=context["login_prompt"].blocks,
            # )
        case "Logout":
            if await require_ray_client(context):
                logout_msg = LogoutMessage(context["ray"].client)
                await client.chat_postEphemeral(
                    channel=context["channel_id"],
                    user=context["user_id"],
                    text=logout_msg.text,
                    blocks=logout_msg.blocks,
                )
        case "Job_Overview":
            if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                await post_job_summary(
                    client, context, context["ray"].client, thread_ts=thread_ts
                )
        case "Job_Status":
            if tj_number_entity := response.findEntity("tj-number"):
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        client,
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
                        client,
                        context,
                        context["ray"].client,
                        tj_number_entity.groups[0],
                    )
            else:
                await context.say(JobTargetsNoIdMessage().text, thread_ts=thread_ts)
        # case "New_Translation_Job":
        #     if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        #         asyncio.create_task(
        #             files_list_simple(
        #                 client, channel_id=context["channel_id"], count=120
        #             )
        #         )
        #         new_job_msg = NewJobMessage(
        #             context["channel_id"],
        #             message["ts"],
        #             "",
        #             context.ray.super_group[0].enable_verify_in_slack,
        #         )
        #         await context.say(
        #             text=new_job_msg.text,
        #             blocks=new_job_msg.blocks,
        #             thread_ts=thread_ts,
        #         )
        case "Show_Insights":
            if await require_ray_client(context, variation=LoginMessage.INSIGHTS):
                await post_insights(
                    client,
                    context,
                    context["ray"].client,
                    message["text"],
                    thread_ts=thread_ts,
                )
        case "Jokes":
            # Delegate jokes to IBM Watson Assistant dialog.
            if response.reply:
                await context.say(response.reply, thread_ts=thread_ts)
        case "Cancel":
            cancel_content = message["text"].lower().split("cancel")
            is_tj = re.findall(r"tj\d+", cancel_content[1].lower(), re.IGNORECASE)
            if len(is_tj) > 0:
                # Cancel th job
                await job_tj_cancel(client, context, context["ray"].client, is_tj[0])
            else:
                if await require_ray_client(context, variation=LoginMessage.CANCEL_JOB):
                    asyncio.create_task(
                        files_list_simple(
                            client, channel_id=context["channel_id"], count=120
                        )
                    )
                    cancel_msg = CancelJobMessage(context["channel_id"], message["ts"])
                    await context.say(
                        text=cancel_msg.text,
                        blocks=cancel_msg.blocks,
                        thread_ts=thread_ts,
                    )
        case "Quality_Evaluation":
            if await require_ray_client(
                context, variation=LoginMessage.QUALITY_EVALUATION
            ):
                quality_evaluation_msg = VerifyHelperMessage()
                await context.say(
                    text=quality_evaluation_msg.text,
                    blocks=quality_evaluation_msg.blocks,
                    thread_ts=thread_ts,
                )
        case _:
            if tj_number_entity := response.findEntity("tj-number"):
                # Show the job status if only a job id is entered.
                if await require_ray_client(context, variation=LoginMessage.GET_JOB):
                    await post_job_status(
                        client,
                        context,
                        context["ray"].client,
                        tj_number_entity.groups[0],
                        thread_ts=thread_ts,
                    )
            elif response.reply:
                help_site = "https://help.strakertranslations.com/hc/en-us/articles/28180054192153-Direct-Machine-Translation-MT-in-Straker-Translate-App-for-Slack"
                none_msg = _(
                    "I didn't understand, please refer to the <{help_site}|help docs>"
                )
                reply = none_msg if response.intent is None else _(response.reply)
                # Default to Watson Assistant fallback response if no other matches.
                await context.say(reply, thread_ts=thread_ts)


async def auto_translate_message(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    message: dict[str, Any],
    is_edit: bool = False,
):
    text: str | None = message.get("text")
    ts: str = message["ts"]
    thread_ts: str | None = message.get("thread_ts")
    if not text:
        return
    if message.get("bot_id"):
        # Do not translate bot messages.
        return
    assert context.channel_id  # TODO enforce this
    # TODO make this fetch all settings for channel
    settings, target_langs = get_auto_translate_settings_and_langs(
        context, context.channel_id
    )
    assert settings  # TODO Fix typing
    if not target_langs:
        return
    try:
        source_lang, translations = await get_ai_translation(
            context, text, target_langs, "channel_translation"
        )
    except Exception as e:
        notify_exception(e, "Slack channel MT failed")
        return
    if not source_lang:
        return
    translations = [
        (target_lang, translated)
        for target_lang, translated in translations
        if target_lang != source_lang
        and langcodes.get(target_lang).language != langcodes.get(source_lang).language
    ]
    if not translations:
        return
    msg = AutoTranslationMessage(
        None,
        source_lang,
        translations=translations,
    )
    try:
        timestamp = None
        if is_edit:
            timestamp = await get_mt_ts_cached(ts)
        if settings[0].display_format == "thread":
            if timestamp:
                await client.chat_update(
                    channel=context.channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    ts=timestamp,
                )
            else:
                request = await client.chat_postMessage(
                    channel=context.channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=ts,
                )
                # save timestamp to cache
                asyncio.create_task(set_mt_ts_edit(send_ts=ts, reply_ts=request["ts"]))
        elif settings[0].display_format == "message":
            if timestamp:
                await client.chat_update(
                    channel=context.channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    ts=timestamp,
                )
            else:
                request = await client.chat_postMessage(
                    channel=context.channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
                # save timestamp to cache
                asyncio.create_task(set_mt_ts_edit(send_ts=ts, reply_ts=request["ts"]))
        else:
            notify_message("Slack app: Invalid display format")

    except Exception as e:
        notify_exception(
            e,
            "Failed to get machine translation from LanguageCloud API or Microsoft API",
        )


async def document_machine_translate(
    context: AsyncBoltContext,
    file_id: str,
    selected_language: str,
):
    """Translate the Document using verify-task-consumer

    Args:
        client (AsyncWebClient): The Slack client.
        channel_id (str): The channel ID of the message.
        output_file (str): The output file name.
    """

    is_gropid = False
    if context["ray"].client is None:
        user_group_id = context["ray"].super_group[0].id
        is_gropid = True
    else:
        user_group_id = context["ray"].client.user_group_id
    # check ai engine from group setting and only fr-ca will support by microsoft
    ai_engine = get_group_mt_engine(user_group_id, is_gropid)
    if selected_language.lower() == "fr-ca":
        ai_engine = "microsoft"

    client: RayClient | None = context["ray"].client
    if not file_id or not client:
        return
    try:
        # file_info = await client.files_info(file=slack_file_id)
        # download_url = file_info["file"]["url_private"]
        task_data = MtFileRequestSchema.model_validate(
            {
                "file_id": file_id,
                "client_id": client.id,
                "channel_id": context["channel_id"],
                "target_language": selected_language,
                "ai_engine": ai_engine,
                "data_source": "slack",
            }
        )
        async with httpx.AsyncClient() as http:
            await http.post(
                f"{domains.stream_proxy}/events/slack:job:machine:translate",
                json={
                    "data": task_data.model_dump(),
                    "source": "Straker Translate for Slack",
                },
            )
    except Exception as e:
        notify_exception(e, "Failed to translate SRT file")


async def update_machine_translation_score(
    client: AsyncWebClient,
    channel_id: str,
    ts: str,
    message: AutoTranslationMessage,
    source_lang: str,
    source_text: str,
    translations: list[tuple[str, str]],
):
    """Get the machine translation score from Taus API and update the auto-translated
    message to include the score.

    Args:
        channel_id (str): The channel ID of the message.
        ts (str): The message ID (ts).
        message (AutoTranslationMessage): The auto-translated message.
        source_lang (str): The source language, e.g. "en", "de".
        source_text (str): The source text
        translations (list[tuple[str, str]]): List of 2-tuples, including the
            target language and the translated text.
    """
    # RAY-65319 Disable function for now, re-enable when required.
    return
    # TODO Update after using live Taus API
    taus_valid_languages = ["en", "fr", "de", "it", "es"]
    translations = [t for t in translations if t[0] in taus_valid_languages]
    if not translations:
        return
    try:
        response = httpx.post(
            "https://api.sandbox.taus.net/1.0/estimate",
            json={
                "source": {"value": source_text, "language": source_lang},
                "targets": [
                    {"value": text, "language": lang} for lang, text in translations
                ],
                "metrics": [{"uid": "taus_qe"}, {"uid": "comet_qe"}],
            },
            # headers={
            #     "Authorization": f"Bearer {config.taus_api_key.get_secret_value()}"
            # },
        )
        response.raise_for_status()
        data = response.json()
        estimates = data["estimates"]
        metrics = [est["metrics"] for est in estimates]
        comet_scores: list[float] = [
            m[0]["value"] if m[0]["uid"] == "comet_qe" else m[1]["value"]
            for m in metrics
        ]
        taus_scores: list[float] = [
            m[1]["value"] if m[1]["uid"] == "taus_qe" else m[0]["value"]
            for m in metrics
        ]
        # Use Taus scores only for now
        scores = list(zip([t[0] for t in translations], taus_scores, strict=False))
        new_msg = message.add_translation_scores(scores)
        await client.chat_update(
            channel=channel_id,
            ts=ts,
            text=new_msg.text,
            blocks=new_msg.blocks,
        )
    except httpx.HTTPStatusError as e:
        notify_exception(e)
    except Exception as e:
        notify_exception(e)


async def post_job_status(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
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
    jobs, response = await RayService.get_service(ray_client).get_job(job_id)
    try:
        if jobs is not None:
            for job in jobs:
                job_prediction = (
                    (await get_job_predictions([job_id]))[0].get("prediction", "")
                    if job.status == "IN_PROGRESS"
                    else ""
                )
                msg = JobStatusMessage(
                    job,
                    ray_client.id,
                    is_ibm_enterprise(
                        enterprise_id=context.enterprise_id,
                    ),
                    job_prediction,
                )
                if context.response_url and context.respond:
                    await context.respond(text=msg.text, blocks=msg.blocks)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    await client.chat_postMessage(
                        channel=channel_id,
                        text=msg.text,
                        blocks=msg.blocks,
                        thread_ts=thread_ts,
                    )
        else:
            invalid_msg = InvalidJobMessage(job_id)
            if context.response_url and context.respond:
                return await context.respond(text=invalid_msg.text)
            else:
                if not channel_id:
                    raise AssertionError("No channel to post to")
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=invalid_msg.text,
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    status: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
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

    if status == "ORDER_NOW":
        quote_job, response = await RayService.get_service(ray_client).get_quote(job_id)
    else:
        jobs, response = await RayService.get_service(ray_client).get_job(job_id)
    try:
        if status == "ORDER_NOW":
            if quote_job is not None:
                msg = JobQuotedMessage(
                    quote_job,
                    is_ibm_enterprise(context.enterprise_id),
                )
                if context.response_url and context.respond:
                    return await context.respond(text=msg.text, blocks=msg.blocks)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=msg.text,
                        blocks=msg.blocks,
                        thread_ts=thread_ts,
                    )
            else:
                invalid_msg = InvalidJobMessage(job_id)
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg.text)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg.text,
                        thread_ts=thread_ts,
                    )
        else:
            if jobs is not None:
                for job in jobs:
                    if job:
                        # get the job prediction
                        job_prediction = (
                            (await get_job_predictions([job_id]))[0].get(
                                "prediction", ""
                            )
                            if job.status == "IN_PROGRESS"
                            else ""
                        )
                        job_msg = JobDetailsMessage(
                            job,
                            ray_client.id,
                            is_ibm_enterprise(
                                enterprise_id=context.enterprise_id,
                            ),
                            job_prediction,
                        )
                        if context.response_url and context.respond:
                            return await context.respond(
                                text=job_msg.text, blocks=job_msg.blocks
                            )
                        else:
                            if not channel_id:
                                raise AssertionError("No channel to post to")
                            return await client.chat_postMessage(
                                channel=channel_id,
                                text=job_msg.text,
                                blocks=job_msg.blocks,
                                thread_ts=thread_ts,
                            )
            else:
                invalid_msg = InvalidJobMessage(job_id)
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg.text)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg.text,
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    all_jobs: bool = False,
):
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
        RayService.get_service(ray_client).get_job_summary(["IN_PROGRESS"]),
        RayService.get_service(ray_client).get_job_summary(
            ["COMPLETED"], completed_from=7 * 24
        ),
        RayService.get_service(ray_client).get_job_summary(
            ["IN_PROGRESS"], from_hours=24
        ),
        RayService.get_service(ray_client).get_job_summary(
            ["IN_PROGRESS"], due_before=24
        ),
        RayService.get_service(ray_client).get_job_summary(["VALIDATION"]),
        RayService.get_service(ray_client).get_job_summary(["PENDING_QUOTES"]),
        RayService.get_service(ray_client).get_job_summary(["ORDER_NOW"]),
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
        if isinstance(responses[0], RayResponse):
            in_progress_count = responses[0].data.summary.get("in_progress", 0)
        else:
            in_progress_count = 0  # or handle the exception case appropriately

        if isinstance(responses[4], RayResponse):
            validation_count = responses[4].data.summary.get("validation", 0)
        else:
            validation_count = 0  # or handle the exception case appropriately

        if isinstance(responses[5], RayResponse):
            pending_quotes_count = responses[5].data.summary.get("pending_quotes", 0)
        else:
            pending_quotes_count = 0  # or handle the exception case appropriately

        if isinstance(responses[6], RayResponse):
            order_now_count = responses[6].data.summary.get("order_now", 0)
        else:
            order_now_count = 0  # or handle the exception case appropriately
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
        if context.response_url and context.respond:
            return await context.respond(
                text=msg.text, blocks=msg.blocks, replace_original=False
            )
        else:
            if not channel_id:
                raise AssertionError("No channel to post to")
            return await client.chat_postMessage(
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    preset: str,
    client_ref: str = "",
    page: int = 1,
    page_size: int = 5,
    channel_id: str | None = None,
    replace_original: bool = False,
):
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
                title = _("Jobs accepted within the last 24 hours")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="IN_PROGRESS",
                    started_from=24,
                    page=page,
                    page_size=page_size,
                )
            case "IN_PROGRESS:DUE:24H":
                title = _("Jobs due within the next 24 hours")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="IN_PROGRESS", due_before=24, page=page, page_size=page_size
                )
            case "IN_PROGRESS":
                title = _("All jobs in progress")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="IN_PROGRESS", page=page, page_size=page_size
                )
            case "COMPLETED:24H":
                title = _("Jobs completed within the last 24 hours")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="COMPLETED",
                    completed_from=24,
                    page=page,
                    page_size=page_size,
                )
            case "COMPLETED:48H":
                title = _("Jobs completed within the last 48 hours")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="COMPLETED",
                    completed_from=48,
                    page=page,
                    page_size=page_size,
                )
            case "COMPLETED:7D":
                title = _("Jobs completed within the last 7 days")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="COMPLETED",
                    completed_from=24 * 7,
                    page=page,
                    page_size=page_size,
                )
            case "VALIDATION":
                title = _("All jobs in validation")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="VALIDATION", page=page, page_size=page_size
                )
            case "PENDING_QUOTES:24H":
                title = _("Pending quotes from the last 24 hours")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="PENDING_QUOTES",
                    from_hours=24,
                    page=page,
                    page_size=page_size,
                )
            case "PENDING_QUOTES":
                title = _("All pending quotes")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="PENDING_QUOTES", page=page, page_size=page_size
                )
            case "ORDER_NOW:24H":
                title = _("Jobs quoted from the last 24 hours")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="ORDER_NOW", quoted_from=24, page=page, page_size=page_size
                )
            case "ORDER_NOW:7D":
                title = _("Jobs quoted from the last 7 days")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="ORDER_NOW",
                    quoted_from=24 * 7,
                    page=page,
                    page_size=page_size,
                )
            case "ORDER_NOW":
                title = _("All jobs quoted")
                response = await RayService.get_service(ray_client).get_job_list(
                    status="ORDER_NOW", page=page, page_size=page_size
                )
            case "CLIENT_REFERENCE":
                title = _("Reference:") + client_ref
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
        if context.response_url and context.respond:
            return await context.respond(
                text=msg.text, blocks=msg.blocks, replace_original=replace_original
            )
        else:
            if not channel_id:
                raise AssertionError("No channel to post to")
            if not context.user_id:
                raise AssertionError("No user to post to")
            return await client.chat_postEphemeral(
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    prompt: str,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
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
            if context.response_url and context.respond:
                await context.respond(
                    text=insights_msg.text, blocks=insights_msg.blocks
                )
            else:
                if not channel_id:
                    raise AssertionError("No channel to post to")
                await client.chat_postMessage(
                    channel=channel_id,
                    text=insights_msg.text,
                    blocks=insights_msg.blocks,
                    thread_ts=thread_ts,
                )
        except Exception as e:
            notify_exception(e, "Failed to get insights from Insights API")
            # TODO send error message

    waiting_msg = ":stopwatch: Please wait as we gather your information..."
    if context.response_url and context.respond:
        await context.respond(text=waiting_msg)
    else:
        if not channel_id:
            raise AssertionError("No channel to post to")
        await client.chat_postMessage(
            channel=channel_id,
            text=waiting_msg,
            thread_ts=thread_ts,
        )

    # Send insights message async because it might take a long time.
    asyncio.create_task(send_insights_message())


async def show_quote_form_modal(
    client: AsyncWebClient,
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
    files = await get_file_options_cached(context["channel_id"])
    # Reduce list to 10 if initial files are set.
    if initial_files and len(files) + len(initial_files) > 10:
        files = files[: 10 - len(initial_files)]
    # Set initial selected files.
    if not initial_files and check_last_messages > 0:
        # Check last 100 messages maximum.
        check_last_messages = min(check_last_messages, 100)
        # Try to get the files from the last n messages to set as the
        # default files in the form dropdown.
        try:
            response = await client.conversations_history(
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
    await client.views_open(
        trigger_id=trigger_id,
        view=new_job_modal(
            ray_client.username,
            channel_id=context["channel_id"],
            file_options=files,
            initial_files=initial_files,
        ),
    )


async def submit_job(
    client: AsyncWebClient, ray_client: RayClient, form: NewJobForm
) -> list[RayResponse[None]]:
    """Submit a new job."""
    file_ids = (file.id for file in form.files if file.id)
    file_paths = await download_files(client, file_ids)
    if not file_paths:
        raise Exception("Failed to download files from the Slack API")
    return await RayService.get_service(ray_client).new_job(
        files=file_paths,
        sl=form.source_lang.code,
        tl=[lang.code for lang in form.target_langs],
        group_id=form.group_id,
        workflow=form.workflow,
        timeframe=form.timeframe,
        reference=form.reference,
        job_notes=form.notes,
        # translation_notes=form.translation_notes,
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    page: int,
    page_size: int,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    replace_original: bool = False,
):
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
    jobs, response = await RayService.get_service(ray_client).get_job(
        job_id, page, page_size
    )

    try:
        if jobs is not None:
            for job in jobs:
                msg = BatchListMessage(job, ray_client.id)
                if context.response_url and context.respond:
                    return await context.respond(
                        text=msg.text,
                        blocks=msg.blocks,
                        replace_original=replace_original,
                    )
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=msg.text,
                        blocks=msg.blocks,
                        thread_ts=thread_ts,
                    )
            else:
                invalid_msg = InvalidJobMessage(job_id)
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg.text)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg.text,
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    page: int,
    page_size: int,
    channel_id: str | None = None,
    thread_ts: str | None = None,
    replace_original: bool = False,
):
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

    jobs, response = await RayService.get_service(ray_client).get_job(
        job_id, page, page_size
    )
    try:
        if jobs is not None:
            for job in jobs:
                msg = FileListMessage(job, ray_client.id)
                if context.response_url and context.respond:
                    return await context.respond(
                        text=msg.text,
                        blocks=msg.blocks,
                        replace_original=replace_original,
                    )
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=msg.text,
                        blocks=msg.blocks,
                        thread_ts=thread_ts,
                    )
            else:
                invlaid_msg = InvalidJobMessage(job_id)
                if context.response_url and context.respond:
                    return await context.respond(text=invlaid_msg.text)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invlaid_msg.text,
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
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str,
    page: int = 1,
    page_size: int = 5,
    channel_id: str | None = None,
):
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
    jobs, response = await RayService.get_service(ray_client).get_job(
        job_id, page, page_size
    )
    no_job = False
    try:
        if jobs is not None:
            for job in jobs:
                if len(job.batches):
                    await post_batch_list(
                        client,
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
                        client,
                        context,
                        context["ray"].client,
                        job_id=job_id,
                        page=1,
                        page_size=5,
                    )
                else:
                    no_job = True

                if no_job:
                    msg = JobTargetLangMessage(job, ray_client.id)
                    if context.response_url and context.respond:
                        return await context.respond(text=msg.text, blocks=msg.blocks)
                    else:
                        if not channel_id:
                            raise AssertionError("No channel to post to")
                        return await client.chat_postMessage(
                            channel=channel_id,
                            text=msg.text,
                            blocks=msg.blocks,
                        )
            else:
                invalid_msg = InvalidJobMessage(job_id)
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg.text)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg.text,
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
    client: AsyncWebClient,
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
    channel_id = channel_id or context.channel_id or context.user_id

    insights_msg = ReportInsightsMessage(ray_client.planname)
    try:
        if context.response_url and context.respond:
            await context.respond(
                text=insights_msg.text,
                blocks=insights_msg.blocks,
                replace_original=False,
            )
        else:
            if not channel_id:
                raise AssertionError("No channel to post to")
            await client.chat_postMessage(
                channel=channel_id,
                text=insights_msg.text,
                blocks=insights_msg.blocks,
                thread_ts=thread_ts,
            )
    except Exception as e:
        notify_exception(e, "Failed to get insights from Insights API")


async def ai_translate_help(
    client: AsyncWebClient,
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
    channel_id = channel_id or context.channel_id or context.user_id
    ai_helper_msg = AIHelperMessage()
    try:
        if context.response_url and context.respond:
            await context.respond(text=ai_helper_msg.text, blocks=ai_helper_msg.blocks)
        else:
            if not channel_id:
                raise AssertionError("No channel to post to")
            await client.chat_postMessage(
                channel=channel_id,
                text=ai_helper_msg.text,
                blocks=ai_helper_msg.blocks,
                thread_ts=thread_ts,
            )
    except Exception as e:
        notify_exception(e, "Failed to get AI Translate help message")


async def verify_help(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
    """Show Verify message modal.

    Args:
        context (AsyncBoltContext): The context from the listener.
        ray_client (RayClient): The RAY client details.
        channel_id (str | None, optional): The channel to post the message to.
            If not given, posts to the source channel.
        thread_ts (str | None, optional): The message thread to reply to.
    """
    channel_id = channel_id or context.channel_id or context.user_id
    verify_helper_msg = VerifyHelperMessage()
    try:
        if context.response_url and context.respond:
            await context.respond(
                text=verify_helper_msg.text,
                blocks=verify_helper_msg.blocks,
                replace_original=False,
            )
        else:
            if not channel_id:
                raise AssertionError("No channel to post to")
            await client.chat_postMessage(
                channel=channel_id,
                text=verify_helper_msg.text,
                blocks=verify_helper_msg.blocks,
                thread_ts=thread_ts,
            )
    except Exception as e:
        notify_exception(e, "Failed to get Verify help message")


async def get_mt_translation(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    target_lang: str,
    source_lang: str,
    sentence: str,
    thread_ts: str | None = None,
    is_edit: bool = False,
):
    """Get google machine translation for sentence by correct language pair.

    Args:
        context (AsyncBoltContext): The context from the listener.
        ray_client (RayClient): The RAY client details.
        target_lang (str): The target language use for translation.
        source_lang (str): The source language use for detect sentence.
        sentence (str): The sentence post on RAY need to be translated.
        thread_ts (str | None, optional): The message thread to reply to.
    """
    try:
        channel_id = context.channel_id or context.user_id
        if not channel_id:
            raise AssertionError("No channel to post to")
        target_lang = target_lang.lower()

        result_source_lang, translation = await get_ai_translation(
            context, sentence, [target_lang], "direct_machine_translation"
        )
        if not result_source_lang:
            return
        target_lang, translation_direct = translation[0]
        msg = MachineTranslationMessage(
            target_lang, result_source_lang, translation_direct
        )
        if context.response_url and context.respond:
            return await context.respond(text=msg.text, blocks=msg.blocks)
        else:
            if is_edit and thread_ts:
                return await client.chat_update(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    ts=thread_ts,
                )
            else:
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    blocks=msg.blocks,
                    thread_ts=thread_ts,
                )
    except Exception as e:
        notify_exception(e, "Failed to get machine translation")
        error_msg = InvalidMTResultMessage()
        if context.response_url and context.respond:
            return await context.respond(text=error_msg.text)
        else:
            if channel_id:
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=msg.text,
                    thread_ts=thread_ts,
                )


async def cancel_job_process(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str = "",
    job_uuid: str = "",
):
    """Tries to get the job details from the RAY API and post the job status
    to the Slack user. If the user cannot access the job, post another message
    instead.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        job_id (str): The ID of the obj_tp_job to get.
        job_uuid (str): The UUID of the api human_job table obj_uuid
    """
    try:
        job, response = await RayService.get_service(ray_client).cancel_job(
            job_id, job_uuid
        )
        if job_id:
            msg = "TJ" + job_id + " - " + job["message"]
        else:
            msg = job["message"]
        await client.chat_postMessage(channel=context["user_id"], text=msg)
    except Exception as e:
        notify_exception(e)
        raise
    finally:
        if "response" in locals() and response is not None:
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


async def job_tj_cancel(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    job_id: str = "",
):
    """Tries to get the job details from the RAY API and post the job status
    to the Slack user. If the user cannot access the job, post another message
    instead.

    Args:
        context (AsyncBoltContext): The listener function context.
        ray_client (RayClient): The RAY client.
        job_id (str): The ID of the obj_tp_job to get.
        job_uuid (str): The UUID of the api human_job table obj_uuid
    """
    try:
        jobs, response = await RayService.get_service(ray_client).get_job(job_id)
        if jobs is not None:
            for job in jobs:
                if job.status == "CANCELLED":
                    msg = (
                        job_id.upper() + " - " + "This job has already been cancelled."
                    )
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=msg,
                    )
                else:
                    jobdetail = {
                        "job_id": job_id.upper(),
                        "status": job.status,
                        "sourcelang": job.sl,
                        "targetlang": job.tl,
                    }
                    cancel_msg = CancelTJMessage(jobdetail)
                    if context.response_url and context.respond:
                        await context.respond(
                            text=cancel_msg.text, blocks=cancel_msg.blocks
                        )
                    else:
                        await client.chat_postMessage(
                            channel=context["user_id"],
                            text=cancel_msg.text,
                            blocks=cancel_msg.blocks,
                        )
        else:
            msg = (
                job_id.upper()
                + " - "
                + "This job does not exist. Please check the job ID and try again."
            )
            await client.chat_postMessage(
                channel=context["user_id"],
                text=msg,
            )
    except Exception as e:
        notify_exception(e)
        raise
    finally:
        if "response" in locals() and response is not None:
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


async def resendMT(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    message: dict[str, Any],
):
    # process mt
    message_match = re.search(
        r"mt:?(?:\s+([\w-]+))?\s+to\s+([\w-]+):?\s+(.*)",
        message["message"]["text"],
        re.I,
    )

    if message_match and await require_ray_client(context, prompt_login=False):
        mt_sl = message_match.group(1) or ""
        mt_tl = message_match.group(2) or context.get("locale") or "en"
        mt_text = message_match.group(3)
        try:
            await get_mt_translation(
                client=client,
                context=context,
                source_lang=mt_sl,
                target_lang=mt_tl,
                sentence=mt_text,
                thread_ts=message["message"]["latest_reply"],
                is_edit=True,
            )
        except Exception as e:
            print(e)

        return

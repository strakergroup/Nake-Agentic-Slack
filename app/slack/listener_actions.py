"""
This module contains functions for common actions which are executed in
Slack Bolt listener functions.
"""

import asyncio
import re
from typing import Any

import httpx
from buglog import notify_exception, notify_message
from ray_sdk import RayResponse
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.web.async_client import AsyncWebClient

from app.api.language_cloud import detect_language
from app.api.models import MtTranslationExtraData
from app.api.stream_proxy import send_mt_translation_request
from app.api.verify import (
    create_human_job,
    get_job_pricing,
)
from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.models import ASRTask, TranscriptionTaskData
from app.mt.service import evaluate_get_glossary_resource, resolve_language
from app.ray.events.models import MtFileRequestSchema
from app.slack.utils import escape_slack_emoji
from app.slack_job import create_slack_job
from app.transcriber_tasks.tasks import create_asr_task
from app.translate import _

from ..auth.connector import (
    RayClient,
    RayContext,
    approve_pending_groups,
    duration_to_tokens,
    get_group_mt_engine,
    log_transcribe_request,
)
from ..config import Environment, config, domains
from ..ray.service import RayService, get_job_predictions
from ..ray.settings import (
    get_auto_translate_settings_and_langs,
)
from ..ray.utils import get_media_duration, is_ibm_enterprise, validate_file_type
from ..redis import redis_conn
from ..watson import watson_message
from .middleware import require_mt_tokens, require_ray_client
from .templates.messages import (
    AIHelperMessage,
    AutoTranslationMessage,
    BatchListMessage,
    CancelJobMessage,
    CancelTJMessage,
    EvaluateSuccessMessage,
    FileListMessage,
    HelpMessage,
    HumanJobQuoteMessage,
    InsightsMessage,
    InvalidJobMessage,
    InvalidMTResultMessage,
    JobDetailsMessage,
    JobListMessage,
    JobQuotedMessage,
    JobStatusMessage,
    JobStatusNoIdMessage,
    JobSummaryMessage,
    JobTargetLangMessage,
    JobTargetsNoIdMessage,
    LoginMessage,
    LogoutMessage,
    NewJobMessage,
    ReportInsightsMessage,
    SlackMessage,
    TranscriptionMessage,
    VerifyHelperMessage,
)
from .templates.models import NewJobForm
from .web import download_files, files_list_simple

VIDEO_FILE_TYPES = ["mp4", "mp3", "mpeg", "mpga", "m4a", "wav", "webm"]


def create_service_language_mapping(
    target_langs: list[str], glossary_ids: dict[str, str] | None = None
) -> dict[str, dict[str, str]]:
    """Create service language mapping based on target languages with glossary IDs.

    Args:
        target_langs: List of target languages
        glossary_ids: Optional dictionary mapping language codes to glossary IDs

    Returns:
        Dictionary mapping services to dictionaries of language codes to glossary IDs
    """
    service_language_mapping: dict[str, dict[str, str]] = {}
    glossary_ids = glossary_ids or {}

    for target_lang in target_langs:
        glossary_id = glossary_ids.get(target_lang, "")
        if target_lang.lower() in ["fr-ca", "french-canada", "french-canadian"]:
            if "microsoft" not in service_language_mapping:
                service_language_mapping["microsoft"] = {}
            service_language_mapping["microsoft"]["fr-ca"] = glossary_id
        else:
            if "google" not in service_language_mapping:
                service_language_mapping["google"] = {}
            service_language_mapping["google"][target_lang] = glossary_id

    return service_language_mapping


def is_video_file(file_details: dict[str, Any]) -> bool:
    """Check if a file from Slack event is a video/audio file."""
    filetype = file_details.get("filetype", "").lower()
    filename = file_details.get("name", "")

    # Determine the file extension (if any) and convert to lower-case for comparison.
    extension = ""
    if "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()

    return (
        filetype in VIDEO_FILE_TYPES
        or extension in VIDEO_FILE_TYPES
        or (".mpga" in filename.lower() and filename.lower().endswith(".mpga"))
    )


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
            files = []
            unsupported_files = []
            for file in message["files"]:
                if is_video_file(file):
                    file_info = await client.files_info(file=file["id"])
                    download_url = file_info["file"]["url_private_download"]
                    # duration_ms = file_info["file"].get("duration_ms", 0)
                    duration_ms = 0
                    if not duration_ms:
                        duration_ms = get_media_duration(
                            download_url, client.token or ""
                        )
                    file_name = file_info["file"]["name"]

                    actual_bot_token = (
                        message.get("metadata", {}).get("bot_token") or client.token
                    )
                    # send video to wb consumer
                    if (
                        await require_ray_client(context, prompt_login=False)
                        and duration_ms
                    ):
                        tokens = duration_to_tokens(duration_ms)
                        if await require_mt_tokens(context, tokens):
                            await log_transcribe_request(
                                duration_ms, file_name, context["ray"]
                            )
                            output_stream_name = f"{domains.stream_proxy}/events/transcription:slack:media:results"
                            service = "azure"
                            model_name = "whisper-1"

                            task_data = TranscriptionTaskData(
                                client_id=context["ray"].client.id,
                                file_name=file_name,
                                download_url=download_url,
                                app_token=actual_bot_token or "",
                                service=service,
                                model=model_name,
                                out_stream_name=output_stream_name,
                                tokens_consumed=tokens,
                            )
                            asr_task = ASRTask(
                                member_uuid=context["ray"].client.id,
                                event_name="transcription:media:asr",
                                app_source="slack",
                                len_ms=duration_ms,
                                service=service,
                                model=model_name,
                                extra_data={},
                                task_data=task_data,
                            )
                            await create_asr_task(asr_task)
                            msg = TranscriptionMessage(file_name)
                            await context.say(text=msg.text, thread_ts=thread_ts)
                else:
                    if not validate_file_type(file["name"]):
                        unsupported_files.append(file)
                    else:
                        files.append(file)
            if len(files) > 10:
                await context.say(
                    text=_(
                        "Too many files selected. Please upload a maximum of 10 files."
                    ),
                    thread_ts=thread_ts,
                )
            elif files:
                new_job_msg = NewJobMessage(
                    context["channel_id"],
                    message["ts"],
                    files,
                    context.ray.super_group[0].enable_verify_in_slack
                    if context.ray
                    else False,
                )
                await context.say(
                    text=new_job_msg.text,
                    blocks=new_job_msg.blocks,
                    thread_ts=thread_ts,
                )
            if unsupported_files:
                unsupported_files_str = ", ".join(
                    [
                        f"{file['name']} ({file['filetype']})"
                        for file in unsupported_files
                    ]
                )
                await context.say(
                    text=_("Unsupported file type: {unsupported_files_str}."),
                    thread_ts=thread_ts,
                )
            return
    # process mt
    message_match = re.search(
        r"mt:?(?:\s+([\w-]+))?\s+to\s+([\w-]+):?\s+(.*)",
        message["text"],
        re.I | re.S,
    )

    if message_match:
        if await require_ray_client(context):
            mt_sl = message_match.group(1) or ""
            if not mt_sl:
                mt_sl = await detect_language(context, message["text"])
                mt_sl = mt_sl.language
            mt_tl = message_match.group(2) or context.get("locale") or "en"
            mt_text = message_match.group(3)
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
                help_site = "https://help.straker.ai/en/docs/direct-machine-translation-mt-in-straker-translate-app-for-slack"
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
    # Check for 5K character limit
    if len(text) > 5000:
        error_msg = _("The message is over the 5K character limit")
        if context.channel_id:
            try:
                await client.chat_postMessage(
                    channel=context.channel_id,
                    text=error_msg,
                    thread_ts=ts,
                )
            except Exception as e:
                notify_exception(e, "Failed to post 5K character limit error message")
        return
    assert context.channel_id  # TODO enforce this

    # TODO make this fetch all settings for channel
    settings = await get_auto_translate_settings_and_langs(context, context.channel_id)
    required_tokens = len(text) * len(settings)
    if not required_tokens or not await require_mt_tokens(context, required_tokens):
        return None
    detected_source_lang_response = await detect_language(context, text)

    # Remove the detected source language from the target languages
    target_langs = [
        langs["target_lang"]
        for langs in settings
        if langs["target_lang"] != detected_source_lang_response.language
    ]

    if not target_langs or not settings:
        return
    source_lang = detected_source_lang_response.language

    try:
        org_uuid = context["ray"].super_group[0].verify_organization_uuid
        client_id = context["ray"].client.id if context["ray"].client else org_uuid

        # Get display_format from settings
        display_format = settings[0]["display_format"] if settings else None
        # Get glossary_id for each target language
        glossary_ids: dict[str, str] = {}
        for target_lang in target_langs:
            is_fr_ca = target_lang.lower() in [
                "fr-ca",
                "french-canada",
                "french-canadian",
            ]
            target_lang = "fr-ca" if is_fr_ca else target_lang
            engine = "microsoft" if is_fr_ca else "google"
            glossary_id = await evaluate_get_glossary_resource(
                org_uuid, context["ray"].client, source_lang, target_lang, engine
            )
            glossary_ids[target_lang] = glossary_id
        service_language_mapping = create_service_language_mapping(
            target_langs, glossary_ids
        )
        assert context.team_id is not None
        await send_mt_translation_request(
            [escape_slack_emoji(text)],
            service_language_mapping,
            source_lang,
            MtTranslationExtraData(
                client_id=client_id,
                team_id=context.team_id,
                slack_user_id=context.user_id,
                service_language_mapping=service_language_mapping,
                source_language=detected_source_lang_response.language,
                organization_uuid=org_uuid,
                channel_id=context.channel_id,
                text_length=len(text),
                usage_type="channel_translation",
                source_text=text,
                response_url=context.response_url,
                thread_ts=thread_ts,
                is_edit=is_edit,
                display_format=display_format,
                message_ts=ts,
            ),
        )
    except Exception as e:
        notify_exception(e, "Slack channel MT failed")
        return
    # if not source_lang:
    #     return
    # translations = [
    #     (target_lang, translated)
    #     for target_lang, translated in translations
    #     if target_lang != source_lang
    #     and langcodes.get(target_lang).language != langcodes.get(source_lang).language
    # ]
    # if not translations:
    #     return
    # msg = AutoTranslationMessage(
    #     None,
    #     source_lang,
    #     translations=translations,
    # )


async def document_machine_translate(
    context: AsyncBoltContext,
    file_id: str,
    selected_language: str,
    submission_id: int,
):
    """Translate the Document using verify-task-consumer

    Args:
        client (AsyncWebClient): The Slack client.
        channel_id (str): The channel ID of the message.
        output_file (str): The output file name.
    """

    is_gropid = False
    if "channel_id" not in context:
        context["channel_id"] = context["user_id"]
    if context["ray"].client is None:
        user_group_id = context["ray"].super_group[0].id
        is_gropid = True
    else:
        user_group_id = context["ray"].client.user_group_id
    # check ai engine from group setting and only fr-ca will support by microsoft
    ai_engine = await get_group_mt_engine(user_group_id, is_gropid)
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
                "submission_id": submission_id,
            }
        )
        task_uuid = await create_slack_job(task_data, status="pending")
        task_data.task_uuid = task_uuid
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
    preset: str | None,
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


async def get_groups(ray_client: RayClient):
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
    context: RayContext,
    target_lang: str,
    source_lang: str,
    sentence: str,
    thread_ts: str | None = None,
    is_edit: bool = False,
    usage_type: str = "direct_machine_translation",
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
        required_tokens = len(sentence)
        if not required_tokens or not await require_mt_tokens(context, required_tokens):
            return None
        channel_id = context.channel_id or context.user_id
        if not channel_id:
            raise AssertionError("No channel to post to")
        # Check for 5K character limit
        if len(sentence) > 5000:
            error_msg = _("The message is over the 5K character limit")
            if context.response_url and context.respond:
                return await context.respond(text=error_msg)
            else:
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=error_msg,
                    thread_ts=thread_ts,
                )
        target_lang = target_lang.lower()
        target_langs = await resolve_language([target_lang], engine="google")
        source_langs = await resolve_language([source_lang], engine="google")
        source_lang = source_langs[0]
        target_lang = target_langs[0]
        assert context.ray
        assert context.ray.super_group
        client_id = (
            context.ray.client.id
            if context.ray.client
            else context.ray.super_group[0].verify_organization_uuid
        )

        # Get glossary_id for each target language
        glossary_ids: dict[str, str] = {}
        for target_lang in target_langs:
            engine = (
                "microsoft"
                if target_lang.lower() in ["fr-ca", "french-canada", "french-canadian"]
                else "google"
            )
            glossary_id = await evaluate_get_glossary_resource(
                context.ray.super_group[0].verify_organization_uuid,
                context.ray.client,
                source_lang,
                target_lang,
                engine,
            )
            glossary_ids[target_lang] = glossary_id
        # Create service language mapping based on target language
        service_language_mapping = create_service_language_mapping(
            target_langs, glossary_ids
        )
        assert context.team_id is not None
        extra_data = MtTranslationExtraData(
            client_id=client_id,
            service_language_mapping=service_language_mapping,
            source_language=source_lang,
            organization_uuid=context.ray.super_group[0].verify_organization_uuid,
            channel_id=channel_id,
            team_id=context.team_id,
            text_length=len(sentence),
            usage_type=usage_type,
            source_text=sentence,
            # Response method fields
            response_url=context.get("response_url"),
            thread_ts=thread_ts,
            is_edit=is_edit,
            slack_user_id=context.user_id,
        )

        await send_mt_translation_request(
            text=[escape_slack_emoji(sentence)],
            service_language_mapping=service_language_mapping,
            source_language=source_lang,
            extra_data=extra_data,
        )

    except Exception as e:
        notify_exception(e, "Failed to get machine translation")
        error_msg_obj = InvalidMTResultMessage()
        if context.response_url and context.respond:
            return await context.respond(text=error_msg_obj.text)
        else:
            if channel_id:
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=error_msg_obj.text,
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
    job, response = await RayService.get_service(ray_client).cancel_job(
        job_id, job_uuid
    )
    if job_id:
        msg = "TJ" + job_id
    else:
        msg = "cancelled"
    await client.chat_postMessage(channel=context["user_id"], text=msg)


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


async def submit_verification_job(
    client: AsyncWebClient,
    context: RayContext,
    job_uuid: str,
    selected_languages: list[str],
    user_id: str,
    timestamp: str,
    job: dict[str, Any],
):
    """Submit a verification job with selected languages.

    Args:
        client (AsyncWebClient): The Slack client.
        context (RayContext): The context containing ray client.
        job_uuid (str): The UUID of the job to verify.
        selected_languages (list[str]): List of selected language and file UUIDs.
        user_id (str): The user ID to send the response to.
        timestamp (str | None): Optional timestamp of the message to update.
        channel_id (str | None): Optional channel ID where the message is posted.
    """
    # Send initial confirmation
    msg = _(
        "Thank you for sending your document(s) for human translation! We will notify you as soon as the translation is complete."
    )
    response = await client.chat_postMessage(
        channel=user_id,
        text=msg,
    )
    try:
        # Get the updated job details after submission
        if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
            assert context.ray is not None
            assert context.ray.client is not None
            costs = await get_job_pricing(
                context.ray.client,
                job_uuid,
                [file["file_uuid"] for file in job["data"]["source_files"]],
                [lang["uuid"] for lang in job["data"]["target_languages"]],
            )
            #
            updated_msg: SlackMessage = HumanJobQuoteMessage(
                job["data"], costs["data"], actions=False
            )
        else:
            updated_msg: SlackMessage = EvaluateSuccessMessage(
                job["data"],
                is_ibm_enterprise(
                    context.ray.client.slack_enterprise_id
                    if context.ray and context.ray.client
                    else None
                ),
                actions=False,
            )

        # Update the original message if timestamp and channel_id are provided
        if timestamp and response["channel"]:
            await client.chat_update(
                channel=response["channel"],
                text=updated_msg.text,
                blocks=updated_msg.blocks,
                ts=timestamp,
            )
        elif context.response_url and context.respond:
            await context.respond(
                text=updated_msg.text,
                blocks=updated_msg.blocks,
                replace_original=True,
            )
        if selected_languages:
            assert context.ray is not None
            assert context.ray.client is not None
            # submit the job
            await create_human_job(
                context.ray.client,
                job_uuid,
                selected_languages,
            )
        else:
            msg = _("Your request has been cancelled.")
            await client.chat_postMessage(
                channel=user_id,
                text=msg,
            )
    except Exception as e:
        notify_exception(e)
        raise
    finally:
        await redis_conn.delete(f"verify_job_submission_{job_uuid}")

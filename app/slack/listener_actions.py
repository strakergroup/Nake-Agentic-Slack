"""
This module contains functions for common actions which are executed in
Slack Bolt listener functions.
"""

import asyncio
import json
import os
import re
from typing import Any, cast

import httpx
from ray_sdk import RayResponse
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.api.language_cloud import detect_language
from app.api.models import MtTranslationExtraData
from app.api.stream_proxy import send_mt_translation_request
from app.api.verify import (
    create_human_job,
    get_job_pricing,
)
from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.models import (  # noqa: F401 - kept for potential future use
    ASRTask,
    TranscriptionTaskData,
)
from app.mt.service import (
    evaluate_get_glossary_resource,
    get_group_id,
    is_no_op_translation_pair,
    resolve_language,
)
from app.ray.events.models import MtFileRequestSchema
from app.slack.buglog_notifier import notify_exception, notify_message
from app.slack.utils import escape_slack_emoji
from app.slack_job import create_slack_job
from app.transcriber_tasks.tasks import (
    create_asr_task,  # noqa: F401 - kept for potential future use
)
from app.translate import _

from ..auth.connector import (
    RayClient,
    RayConnection,
    RayContext,
    approve_pending_groups,
    duration_to_tokens,  # noqa: F401 - kept for potential future use
    get_client_tokens,
    get_group_mt_engine,
    get_group_tokens,
    log_transcribe_request,  # noqa: F401 - kept for potential future use
)
from ..config import domains
from ..ray.service import RayService
from ..ray.settings import (
    get_auto_translate_language_entries,
    get_auto_translate_settings_and_langs,
)
from ..ray.submissions import (
    SubmissionStatus,
    check_and_record_direct_embed_submission_async,
    updated_submission_status,
)
from ..ray.utils import (
    is_ibm_enterprise,
    upload_to_file_server,
    validate_file_type,
)
from ..redis import redis_conn
from ..watson import watson_message
from .bot_translation import (
    bump_channel_mt_generation,
    is_slack_emoji_only,
    schedule_bot_message_translation,
)
from .bot_translation_limits import can_translate_bot_message
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
    InvalidJobMessage,
    InvalidMTResultMessage,
    JobDetailsMessage,
    JobFileListEmptyMessage,
    JobListMessage,
    JobQuotedMessage,
    JobStatusMessage,
    JobStatusNoIdMessage,
    JobSummaryMessage,
    JobTargetLangMessage,
    JobTargetsNoIdMessage,
    LoginMessage,
    LogoutMessage,
    MediaEmbedOptionMessage,
    MissingSlackFilesMessage,
    NewJobMessage,
    SlackMessage,
    TranscriptionMessage,  # noqa: F401 - kept for potential future use
    VerifyHelperMessage,
    VideoOptionsMessage,
)
from .templates.models import NewJobForm, build_human_translation_reference
from .web import (
    download_file,
    download_files,
    files_list_simple,
    get_bot_accessible_files,
)

VIDEO_FILE_TYPES = ["mp4", "mp3", "mpeg", "mpga", "m4a", "wav", "webm"]

# Audio-only formats (cannot have subtitles embedded)
AUDIO_ONLY_TYPES = ["mp3", "mpga", "m4a", "wav"]

# Video formats (can have subtitles embedded)
VIDEO_ONLY_TYPES = ["mp4", "mpeg", "webm"]

MEDIA_ACTION_IDS = frozenset(
    {
        "video_transcribe_only",
        "video_transcribe_translate",
        "video_embed_subtitles",
    }
)

FR_CA_VARIANTS = frozenset({"fr-ca", "french-canada", "french-canadian"})


def _normalize_mt_language_code(language_code: str) -> str:
    normalized = language_code.lower().replace("_", "-")
    if normalized in FR_CA_VARIANTS:
        return "fr-ca"
    return language_code


def build_human_translation_purchase_order_number(job: dict[str, Any]) -> str:
    source_files = job.get("data", {}).get("source_files", [])
    file_titles = [
        source_file.get("filename", "")
        for source_file in source_files
        if isinstance(source_file, dict)
    ]
    return build_human_translation_reference(file_titles, manual_reference=None)


def slack_api_error_code(error: SlackApiError) -> str | None:
    response = error.response
    if isinstance(response, dict):
        error_code = response.get("error")
        return str(error_code) if error_code else None
    try:
        error_code = response.get("error")
    except Exception:
        return None
    return str(error_code) if error_code else None


def is_slack_file_not_found(error: SlackApiError) -> bool:
    return slack_api_error_code(error) == "file_not_found"


def slack_file_id(file: dict[str, Any]) -> str | None:
    file_id = file.get("id") or file.get("value")
    return str(file_id) if file_id else None


def document_mt_selected_languages(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")
    if isinstance(raw_value, str):
        try:
            parsed_value = json.loads(raw_value)
        except json.JSONDecodeError:
            return [raw_value] if raw_value else []
        if isinstance(parsed_value, list):
            return [str(language) for language in parsed_value if language]
        return [str(parsed_value)] if parsed_value else []
    if isinstance(raw_value, list):
        return [str(language) for language in raw_value if language]
    return [str(raw_value)] if raw_value else []


async def get_accessible_slack_files(
    client: AsyncWebClient, files: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    file_ids = [
        file_id for file_id in (slack_file_id(file) for file in files) if file_id
    ]
    accessible_files = await get_bot_accessible_files(client, file_ids)
    accessible_file_ids = {
        file_id
        for file_id in (slack_file_id(file) for file in accessible_files)
        if file_id
    }
    missing_files = [
        file for file in files if slack_file_id(file) not in accessible_file_ids
    ]
    return accessible_files, missing_files


async def notify_missing_slack_files(
    client: AsyncWebClient, user_id: str, files: list[dict[str, Any]]
) -> None:
    if not files:
        return
    message = MissingSlackFilesMessage(files)
    await client.chat_postMessage(channel=user_id, text=message.text)


def _normalize_document_target_languages(
    selected_language: str | list[str],
) -> list[str]:
    if isinstance(selected_language, str):
        return [selected_language] if selected_language else []

    return [str(language) for language in selected_language if language]


def _job_has_batches(job: Any) -> bool:
    batches = getattr(job, "batches", "[]")
    if not batches:
        return False
    if isinstance(batches, str):
        try:
            batches = json.loads(batches)
        except (TypeError, ValueError):
            return False
    return bool(batches)


def _language_code_from_srt_filename(filename: str) -> str:
    """Infer a language code from an SRT filename like ``video_Japanese.srt``.

    The translation pipeline names output files as ``{stem}_{LanguageName}.srt``.
    This reverses that convention by matching the trailing segment against the
    known auto-translate language list.

    Returns the ISO language code (e.g. ``"ja"``) or ``"und"`` if no match.
    """
    stem = os.path.splitext(filename)[0]  # "video_Japanese"
    if "_" not in stem:
        return "und"

    candidate = stem.rsplit("_", 1)[1]  # "Japanese"
    candidate_lower = candidate.casefold()

    for code, name in get_auto_translate_language_entries():
        if name.casefold() == candidate_lower or code.casefold() == candidate_lower:
            return code

    return "und"


def create_service_language_mapping(
    target_langs: list[str],
    glossary_ids: dict[str, str] | None = None,
    service_overrides: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Create service language mapping based on target languages with glossary IDs.

    Args:
        target_langs: List of target languages
        glossary_ids: Optional dictionary mapping language codes to glossary IDs
        service_overrides: Optional dictionary mapping language codes to services

    Returns:
        Dictionary mapping services to dictionaries of language codes to glossary IDs
    """
    service_language_mapping: dict[str, dict[str, str]] = {}
    glossary_ids = glossary_ids or {}
    service_overrides = service_overrides or {}

    for target_lang in target_langs:
        normalized_target = _normalize_mt_language_code(target_lang)
        glossary_id = glossary_ids.get(
            normalized_target, glossary_ids.get(target_lang, "")
        )
        service = service_overrides.get(
            normalized_target,
            service_overrides.get(
                target_lang,
                "microsoft" if target_lang.lower() in FR_CA_VARIANTS else "google",
            ),
        )
        if service not in service_language_mapping:
            service_language_mapping[service] = {}
        service_language_mapping[service][normalized_target] = glossary_id

    return service_language_mapping


async def _resolve_mt_route_and_glossary(
    org_uuid: str,
    client: RayClient | None,
    source_lang: str,
    target_lang: str,
) -> tuple[str, str, str]:
    """Resolve the target code, engine, and glossary for a single MT pair."""
    normalized_source = _normalize_mt_language_code(source_lang)
    normalized_target = _normalize_mt_language_code(target_lang)

    microsoft_glossary = ""
    if (
        normalized_source.lower() in FR_CA_VARIANTS
        or normalized_target.lower() in FR_CA_VARIANTS
    ):
        microsoft_glossary = await evaluate_get_glossary_resource(
            org_uuid, client, normalized_source, normalized_target, "microsoft"
        )

    if normalized_target.lower() in FR_CA_VARIANTS or microsoft_glossary:
        return normalized_target, "microsoft", microsoft_glossary

    google_glossary = await evaluate_get_glossary_resource(
        org_uuid, client, normalized_source, normalized_target, "google"
    )
    return normalized_target, "google", google_glossary


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


def is_audio_only_file(file_details: dict[str, Any]) -> bool:
    """Check if a file is audio-only (cannot have subtitles embedded).

    Handles both Slack event format (name, filetype) and internal format (file_name).
    """
    filetype = file_details.get("filetype", "").lower()
    # Handle both "name" (Slack event) and "file_name" (internal format)
    filename = file_details.get("name", "") or file_details.get("file_name", "")

    extension = ""
    if "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()

    return filetype in AUDIO_ONLY_TYPES or extension in AUDIO_ONLY_TYPES


def has_video_files(files: list[dict[str, Any]]) -> bool:
    """Check if any files in the list are video files (not audio-only).

    Returns True if at least one file can have subtitles embedded (video file).
    Returns False if all files are audio-only.
    """
    for file in files:
        if not is_audio_only_file(file):
            return True
    return False


def is_srt_file(file_details: dict[str, Any]) -> bool:
    """Check whether a Slack file payload represents an SRT subtitle file."""
    filetype = file_details.get("filetype", "").lower()
    mimetype = file_details.get("mimetype", "").lower()
    filename = (file_details.get("name", "") or file_details.get("title", "")).lower()
    return (
        filetype == "srt"
        or mimetype == "application/x-subrip"
        or filename.endswith(".srt")
    )


def _extract_media_files_from_blocks(root_message: dict[str, Any]) -> list[dict]:
    """Extract video file info from VideoOptionsMessage action button values.

    When the thread root is a VideoOptionsMessage (no attached files), the
    original video metadata lives inside the JSON ``value`` of its action
    buttons.
    """
    for block in root_message.get("blocks", []):
        accessory = block.get("accessory") or {}
        if accessory.get("action_id") not in MEDIA_ACTION_IDS:
            continue
        try:
            payload = json.loads(accessory.get("value", "{}"))
        except (json.JSONDecodeError, TypeError):
            continue
        files = payload.get("files")
        if files:
            return [
                {"file_id": f["file_id"], "file_name": f["file_name"]}
                for f in files
                if f.get("file_id") and f.get("file_name")
            ]
    return []


def build_thread_media_embed_action_value(
    channel_id: str,
    thread_ts: str,
    root_message: dict[str, Any],
    reply_message: dict[str, Any],
) -> str:
    """Build an embed payload from the root media message and uploaded thread SRT."""
    subtitle_file = next(
        (file for file in reply_message.get("files", []) if is_srt_file(file)),
        None,
    )
    if subtitle_file is None:
        return ""

    media_files = []
    for file in root_message.get("files", []):
        # VIDEO_FILE_TYPES includes audio formats (mp3, wav, etc.) so
        # is_audio_only_file is needed to exclude non-embeddable audio.
        if not is_video_file(file) or is_audio_only_file(file):
            continue

        file_id = file.get("id")
        file_name = file.get("name") or file.get("title")
        if not file_id or not file_name:
            continue

        media_files.append({"file_id": file_id, "file_name": file_name})

    if not media_files:
        media_files = _extract_media_files_from_blocks(root_message)

    if not media_files:
        return ""

    action_data: dict[str, Any] = {
        "channel_id": channel_id,
        "files": media_files,
        "thread_ts": thread_ts,
    }

    srt_name = (
        subtitle_file.get("name") or subtitle_file.get("title") or "subtitles.srt"
    )
    action_data["subtitle_file"] = {
        "file_id": subtitle_file["id"],
        "file_name": srt_name,
        "language_code": _language_code_from_srt_filename(srt_name),
    }
    return json.dumps(action_data)


def resolve_media_thread_ts(
    action_data: dict[str, Any] | None, body: dict[str, Any] | None
) -> str | None:
    """Resolve the best thread timestamp for media workflows."""
    action_data = action_data or {}
    body = body or {}
    container = body.get("container", {}) or {}
    message = body.get("message", {}) or {}
    return (
        action_data.get("thread_ts")
        or container.get("thread_ts")
        or message.get("thread_ts")
        or container.get("message_ts")
        or message.get("ts")
    )


async def get_thread_root_message(
    client: AsyncWebClient, channel_id: str, thread_ts: str
) -> dict[str, Any] | None:
    """Fetch the root message for a thread."""
    history = await client.conversations_history(
        channel=channel_id,
        latest=thread_ts,
        oldest=thread_ts,
        inclusive=True,
        limit=1,
    )
    messages = cast(list[dict[str, Any]], history.get("messages", []))
    return messages[0] if messages else None


async def maybe_show_thread_media_embed_option(
    client: AsyncWebClient,
    context: RayContext,
    message: dict[str, Any],
) -> bool:
    """Reply with the original video embed option when an SRT lands in that thread."""
    thread_ts = message.get("thread_ts")
    if not thread_ts or not any(is_srt_file(file) for file in message.get("files", [])):
        return False

    channel_id = context.get("channel_id")
    if not channel_id or not await require_ray_client(context):
        return False

    root_message = await get_thread_root_message(client, channel_id, thread_ts)
    if not root_message:
        return False

    action_value = build_thread_media_embed_action_value(
        channel_id, thread_ts, root_message, message
    )
    if not action_value:
        return False

    embed_msg = MediaEmbedOptionMessage(action_value)
    await context.say(
        text=embed_msg.text,
        blocks=embed_msg.blocks,
        thread_ts=thread_ts,
    )
    return True


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
            # Handle video files - collect all first, then show one message
            files = []
            unsupported_files = []
            video_files = []
            is_ibm = is_ibm_enterprise(context.enterprise_id)
            for file in message["files"]:
                if is_video_file(file):
                    file_info = await client.files_info(file=file["id"])
                    duration_ms = file_info["file"].get("duration_ms", 0)
                    # Default to 1 minute if duration couldn't be detected
                    if not duration_ms:
                        duration_ms = 60000
                    video_files.append(
                        {
                            "file_id": file["id"],
                            "file_name": file_info["file"]["name"],
                            "duration_ms": duration_ms,
                        }
                    )
                else:
                    if not validate_file_type(file["name"]):
                        unsupported_files.append(file)
                    else:
                        files.append(file)

            # Show video options message for all video files at once
            if video_files and await require_ray_client(context, prompt_login=False):
                # Get token balance for non-IBM workspaces.
                tokens = None
                if not is_ibm:
                    if context["ray"].client is not None:
                        user_tokens = await get_client_tokens(
                            context["ray"].client.id_token
                        )
                        tokens = user_tokens.ai_token
                    elif context["ray"].super_group is not None:
                        group_tokens = await get_group_tokens(
                            context["ray"].super_group[0].verify_organization_uuid
                        )
                        tokens = group_tokens.ai_token

                # Check if any files are actual video (not audio-only)
                # to determine if Embed Subtitles option should be shown
                has_embeddable_video = has_video_files(video_files)

                video_msg = VideoOptionsMessage(
                    channel_id=context["channel_id"],
                    files=video_files,
                    thread_ts=thread_ts,
                    is_ibm_enterprise=is_ibm,
                    tokens=tokens,
                    show_embed_option=has_embeddable_video,
                )
                await context.say(
                    text=video_msg.text,
                    blocks=video_msg.blocks,
                    thread_ts=thread_ts,
                )
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
                    is_ibm,
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
            mt_tl = message_match.group(2) or context.get("locale") or "en"
            mt_text = message_match.group(3)
            if not mt_sl:
                # Truncate text to 5000 chars for detect_language API limit
                text_for_detection = mt_text[:5000] if len(mt_text) > 5000 else mt_text
                mt_sl = await detect_language(context, text_for_detection)
                mt_sl = mt_sl.language
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
    response = await watson_message(message["text"], context.get("user_id"))
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


async def submit_existing_srt_embed_task(
    client: AsyncWebClient,
    context: RayContext,
    action_data: dict[str, Any],
    thread_ts: str | None,
) -> bool:
    """Create an embed-only task using an uploaded SRT and the original video."""
    ray_conn = context.get("ray")
    if not ray_conn or not ray_conn.client:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You must be connected to use subtitle embedding."),
        )
        return False

    subtitle_file = action_data.get("subtitle_file") or {}
    subtitle_file_id = subtitle_file.get("file_id")
    subtitle_language_code = subtitle_file.get("language_code", "und")
    subtitle_file_path: str | None = None
    channel_id = (
        action_data.get("channel_id") or context.get("channel_id") or context["user_id"]
    )

    if not subtitle_file_id:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "Please upload an SRT file in the thread before embedding subtitles."
            ),
        )
        return False

    video_files = [
        file_info
        for file_info in action_data.get("files", [])
        if not is_audio_only_file(file_info)
    ]
    if len(video_files) != 1:
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "Direct subtitle embedding from a thread requires exactly one original video file."
            ),
            thread_ts=thread_ts,
        )
        return False

    video_file = video_files[0]
    is_dup, submission_record = await check_and_record_direct_embed_submission_async(
        video_file_id=video_file["file_id"],
        subtitle_file_id=subtitle_file_id,
        file_name=video_file["file_name"],
        user_id=context["user_id"],
        team_id=context["team_id"],
        channel_id=channel_id,
    )
    if is_dup:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "Please allow the system to complete the ongoing subtitle embedding to prevent duplicate submissions."
            ),
        )
        return False

    slack_file_info = await client.files_info(file=video_file["file_id"])
    slack_file_data: dict[str, Any] = slack_file_info.get("file", {})
    download_url = slack_file_data.get("url_private_download") or slack_file_data.get(
        "url_private"
    )
    if not download_url:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("Unable to access the original video file for subtitle embedding."),
        )
        return False

    try:
        subtitle_file_path = await download_file(
            client=client, file_id=subtitle_file_id, http=None
        )
        uploaded_srt_file_id = await upload_to_file_server(subtitle_file_path)

        task_data = TranscriptionTaskData(
            client_id=ray_conn.client.id,
            file_name=video_file["file_name"],
            download_url=download_url,
            app_token=client.token or "",
            out_stream_name=f"{domains.stream_proxy}/events/transcription:slack:media:results",
            service="azure",
            model="whisper-1",
            embed_subtitles=True,
            sandbox=False,
        )
        embed_target_languages = (
            [subtitle_language_code]
            if subtitle_language_code and subtitle_language_code != "und"
            else []
        )
        extra_data_dict = {
            "slack_user_id": context["user_id"],
            "slack_team_id": context["team_id"],
            "slack_enterprise_id": context.enterprise_id,
            "slack_channel_id": channel_id,
            "slack_thread_ts": thread_ts,
            "pipeline_type": "embed",
            "original_video_file_id": video_file["file_id"],
            "original_video_download_url": download_url,
            "original_video_file_name": video_file["file_name"],
            "srt_file_ids": [uploaded_srt_file_id],
            "language_codes": [subtitle_language_code],
            "target_languages": embed_target_languages,
            "submission_ids": [submission_record.id],
        }
        asr_task = ASRTask(
            member_uuid=ray_conn.client.id,
            event_name="sup-subtitle-ai:media:asr",
            app_source="slack",
            service="azure",
            model="whisper-1",
            extra_data=extra_data_dict,
            task_data=task_data,
        )

        await create_asr_task(asr_task)
    except Exception as e:
        notify_exception(e, "Failed to create direct embed task")
        updated_submission_status(
            submission_id=submission_record.id,
            processing_status=SubmissionStatus.FAILED,
        )
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "An error occurred while preparing your subtitle embedding. Please try again."
            ),
            thread_ts=thread_ts,
        )
        return False
    finally:
        if subtitle_file_path and os.path.exists(subtitle_file_path):
            os.unlink(subtitle_file_path)

    await client.chat_postMessage(
        channel=channel_id,
        text=_(
            ":stopwatch: Please wait a moment while we embed the uploaded subtitles into your video."
        ),
        thread_ts=thread_ts,
    )
    return True


async def auto_translate_message(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    message: dict[str, Any],
    is_edit: bool = False,
    *,
    skip_bot_debounce: bool = False,
):
    text: str | None = message.get("text")
    ts: str = message["ts"]
    thread_ts: str | None = message.get("thread_ts")
    if not text:
        return
    if is_slack_emoji_only(text):
        return

    bot_id = message.get("bot_id")
    is_bot_message = isinstance(bot_id, str)
    if is_bot_message and not is_edit and not skip_bot_debounce:
        await schedule_bot_message_translation(client, context, message)
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
    ray_connection = context.get("ray")
    if not isinstance(ray_connection, RayConnection) or not ray_connection.super_group:
        return
    assert context.channel_id  # TODO enforce this

    # TODO make this fetch all settings for channel
    settings = await get_auto_translate_settings_and_langs(context, context.channel_id)
    required_tokens = len(text) * len(settings)
    if not required_tokens or not await require_mt_tokens(context, required_tokens):
        return None
    detected_source_lang_response = await detect_language(context, text)
    detected_lang = detected_source_lang_response.language

    # Drop targets that would produce a no-op translation against the detected
    # source. is_no_op_translation_pair handles exact matches plus same-base /
    # bare-code dialect pairs (e.g. fr ↔ fr-ca, zh ↔ zh-CN), while still keeping
    # distinct dialects like zh-CN ↔ zh-TW.
    target_langs = [
        langs["target_lang"]
        for langs in settings
        if not is_no_op_translation_pair(detected_lang, langs["target_lang"])
    ]

    if not target_langs or not settings:
        return
    source_lang = detected_source_lang_response.language

    slack_user_id = context.user_id
    slack_user_name = None
    if isinstance(bot_id, str):
        if not await can_translate_bot_message(
            context.channel_id, bot_id, is_edit=is_edit
        ):
            return
        slack_user_id = message.get("user") or bot_id
        bot_profile = message.get("bot_profile")
        if isinstance(bot_profile, dict):
            slack_user_name = bot_profile.get("name") or bot_profile.get("real_name")
        slack_user_name = slack_user_name or message.get("username")

    edit_generation = await bump_channel_mt_generation(ts)

    try:
        org_uuid = ray_connection.super_group[0].verify_organization_uuid
        client_id = ray_connection.client.id if ray_connection.client else org_uuid
        group_id = await get_group_id(org_uuid)

        # Get display_format from settings
        display_format = settings[0]["display_format"] if settings else None
        # Resolve service + glossary for each target language
        glossary_ids: dict[str, str] = {}
        service_overrides: dict[str, str] = {}
        target_language_order: list[str] = []
        for target_lang in target_langs:
            (
                normalized_target,
                service,
                glossary_id,
            ) = await _resolve_mt_route_and_glossary(
                org_uuid,
                ray_connection.client,
                source_lang,
                target_lang,
            )
            target_language_order.append(normalized_target)
            glossary_ids[normalized_target] = glossary_id
            service_overrides[normalized_target] = service
        service_language_mapping = create_service_language_mapping(
            target_langs, glossary_ids, service_overrides
        )
        assert context.team_id is not None
        await send_mt_translation_request(
            [escape_slack_emoji(text)],
            service_language_mapping,
            source_lang,
            MtTranslationExtraData(
                client_id=client_id,
                team_id=context.team_id,
                slack_user_id=slack_user_id,
                slack_user_name=slack_user_name,
                is_bot=is_bot_message,
                service_language_mapping=service_language_mapping,
                source_language=detected_source_lang_response.language,
                organization_uuid=org_uuid,
                channel_id=context.channel_id,
                text_length=len(text),
                usage_type="channel_translation",
                group_id=group_id or "",
                source_text=text,
                target_language_order=target_language_order,
                response_url=context.response_url,
                thread_ts=thread_ts,
                is_edit=is_edit,
                display_format=display_format,
                message_ts=ts,
                edit_generation=edit_generation,
            ),
        )
    except Exception as e:
        notify_exception(e, "Slack channel MT failed")
        return


async def document_machine_translate(
    context: AsyncBoltContext,
    file_id: str,
    source_language: str | None,
    selected_language: str | list[str],
    submission_id: int | dict[str, int],
):
    """Translate the Document using verify-task-consumer

    Args:
        client (AsyncWebClient): The Slack client.
        channel_id (str): The channel ID of the message.
        output_file (str): The output file name.
    """

    is_gropid = False
    # Set channel_id if not present (same pattern as document_mt_job)
    if "channel_id" not in context:
        context["channel_id"] = context["user_id"]
    if context["ray"].client is None:
        user_group_id = context["ray"].super_group[0].id
        is_gropid = True
    else:
        user_group_id = context["ray"].client.user_group_id
    target_languages = _normalize_document_target_languages(selected_language)
    if not target_languages:
        return

    # check ai engine from group setting and only fr-ca will support by microsoft
    ai_engine = await get_group_mt_engine(user_group_id, is_gropid)
    if len(target_languages) == 1 and target_languages[0].lower() == "fr-ca":
        ai_engine = "microsoft"

    client: RayClient | None = context["ray"].client
    if not file_id or not client:
        return
    try:
        # file_info = await client.files_info(file=slack_file_id)
        # download_url = file_info["file"]["url_private"]
        submission_ids = (
            submission_id
            if isinstance(submission_id, dict)
            else {target_languages[0]: submission_id}
        )
        task_data = MtFileRequestSchema.model_validate(
            {
                "file_id": file_id,
                "client_id": client.id,
                "channel_id": context["channel_id"],
                "source_language": source_language,
                "target_language": target_languages[0],
                "target_languages": target_languages,
                "ai_engine": ai_engine,
                "data_source": "slack",
                "submission_id": submission_ids.get(target_languages[0]),
                "submission_ids": submission_ids,
            }
        )
        task_uuid = await create_slack_job(task_data, status="pending")
        task_data.task_uuid = task_uuid
        async with httpx.AsyncClient() as http:
            await http.post(
                f"{domains.stream_proxy}/events/slack:job:machine:translate:v2",
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
                msg = JobStatusMessage(
                    job,
                    ray_client.id,
                    is_ibm_enterprise(
                        enterprise_id=context.enterprise_id,
                    ),
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
            invalid_msg = InvalidJobMessage(job_id).text
            if context.response_url and context.respond:
                return await context.respond(text=invalid_msg)
            else:
                if not channel_id:
                    raise AssertionError("No channel to post to")
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=invalid_msg,
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
                invalid_msg = InvalidJobMessage(job_id).text
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg,
                        thread_ts=thread_ts,
                    )
        else:
            if jobs is not None:
                for job in jobs:
                    if job:
                        job_msg = JobDetailsMessage(
                            job,
                            ray_client.id,
                            is_ibm_enterprise(
                                enterprise_id=context.enterprise_id,
                            ),
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
                invalid_msg = InvalidJobMessage(job_id).text
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg,
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
    if isinstance(responses[2], RayResponse):
        in_progress_count_24 = responses[2].data.summary.get("in_progress", 0)
    if isinstance(responses[3], RayResponse):
        in_progress_due = responses[3].data.summary.get("in_progress", 0)
    if isinstance(responses[0], RayResponse):
        in_progress_count = responses[0].data.summary.get("in_progress", 0)

        if isinstance(responses[4], RayResponse):
            validation_count = responses[4].data.summary.get("validation", 0)

        if isinstance(responses[5], RayResponse):
            pending_quotes_count = responses[5].data.summary.get("pending_quotes", 0)

        if isinstance(responses[6], RayResponse):
            order_now_count = responses[6].data.summary.get("order_now", 0)
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
        msg = JobListMessage(
            preset=preset,
            title=title,
            jobs=response.data[0],
            pagination=response.data[1],
            client_ref=client_ref,
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
                if not _job_has_batches(job):
                    msg = JobFileListEmptyMessage(job.id, "in-progress")
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
                invalid_msg = InvalidJobMessage(job_id).text
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg,
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
                if not job.translated_file:
                    msg = JobFileListEmptyMessage(job.id, "completed")
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
                invalid_msg = InvalidJobMessage(job_id).text
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg,
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
                invalid_msg = InvalidJobMessage(job_id).text
                if context.response_url and context.respond:
                    return await context.respond(text=invalid_msg)
                else:
                    if not channel_id:
                        raise AssertionError("No channel to post to")
                    return await client.chat_postMessage(
                        channel=channel_id,
                        text=invalid_msg,
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


async def ai_translate_help(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    ray_client: RayClient,
    channel_id: str | None = None,
    thread_ts: str | None = None,
):
    """Show AI Translate Help message.

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
        if is_no_op_translation_pair(source_lang, target_lang):
            same_lang_msg = _(
                "Source and target languages are the same ({source_lang}). "
                "No translation needed."
            )
            if context.response_url and context.respond:
                return await context.respond(text=same_lang_msg)
            return await client.chat_postMessage(
                channel=channel_id,
                text=same_lang_msg,
                thread_ts=thread_ts,
            )
        assert context.ray
        assert context.ray.super_group
        client_id = (
            context.ray.client.id
            if context.ray.client
            else context.ray.super_group[0].verify_organization_uuid
        )
        group_id = await get_group_id(
            context.ray.super_group[0].verify_organization_uuid
        )
        # Resolve service + glossary for each target language
        glossary_ids: dict[str, str] = {}
        service_overrides: dict[str, str] = {}
        for target_lang in target_langs:
            (
                normalized_target,
                service,
                glossary_id,
            ) = await _resolve_mt_route_and_glossary(
                context.ray.super_group[0].verify_organization_uuid,
                context.ray.client,
                source_lang,
                target_lang,
            )
            glossary_ids[normalized_target] = glossary_id
            service_overrides[normalized_target] = service
        # Create service language mapping based on target language
        service_language_mapping = create_service_language_mapping(
            target_langs, glossary_ids, service_overrides
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
            group_id=group_id or "",
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
        error_msg = InvalidMTResultMessage().text
        if context.response_url and context.respond:
            return await context.respond(text=error_msg)
        else:
            if channel_id:
                return await client.chat_postMessage(
                    channel=channel_id,
                    text=error_msg,
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
    # Send initial message based on whether languages were selected.
    msg = (
        _(
            "Thank you for sending your document(s) for human translation! We will notify you as soon as the translation is complete."
        )
        if selected_languages
        else _("Your request has been cancelled.")
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
                purchase_order_number=build_human_translation_purchase_order_number(
                    job
                ),
            )
    except Exception as e:
        notify_exception(e)
    finally:
        await redis_conn.delete(f"verify_job_submission_{job_uuid}")

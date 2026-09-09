"""Handlers for video/audio media quote and transcription flows."""

import json
from typing import Any, Dict, Optional

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.ray.submissions import (
    check_and_record_transcription_only_submission_async,
)
from app.slack.buglog_notifier import notify_exception
from app.slack.listener_actions import (
    is_audio_only_file,
    quote_existing_srt_embed_task,
    resolve_media_thread_ts,
)
from app.slack.media_duration import file_info_with_quote_duration
from app.slack.media_quote_actions import (
    accept_media_quote,
    accept_media_translation_quote,
    cancel_media_quote,
    post_or_auto_start_media_quote,
)
from app.slack.media_quotes import (
    ACTION_MEDIA_QUOTE_ACCEPT,
    ACTION_MEDIA_QUOTE_CANCEL,
    PIPELINE_TRANSCRIBE,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    create_media_quote_session,
)
from app.slack.middleware import populate_ray_connection, require_ray_client
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from app.slack.templates.views import (
    video_configure_media_modal,
    video_embed_subtitles_modal,
    video_transcribe_translate_modal,
)
from app.translate import _


async def handle_media_quote_accept(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept Quote1 (transcription / embedding) and start the first pipeline phase."""
    if not await require_ray_client(context, allow_org_billing=True):
        return
    await accept_media_quote(client=client, body=body, action=action, context=context)


async def handle_media_quote_cancel(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Cancel Quote1 media quote."""
    await cancel_media_quote(client=client, body=body, action=action, context=context)


async def handle_media_translation_quote_accept(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept Quote2 (AI translation) and resume translate / translate+embed."""
    if not await require_ray_client(context, allow_org_billing=True):
        return
    await accept_media_translation_quote(
        client=client, body=body, action=action, context=context
    )


async def handle_media_translation_quote_cancel(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Cancel Quote2 AI translation quote."""
    await cancel_media_quote(
        client=client,
        body=body,
        action=action,
        context=context,
        is_translation_quote=True,
    )


async def handle_video_transcribe_only(
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Handle transcribe-only button — post Quote1 before starting ASR."""
    if not await require_ray_client(context, allow_org_billing=True):
        return

    try:
        assert action is not None
        assert context["ray"] is not None

        action_data = json.loads(action.get("value", "{}"))
        channel_id = (
            action_data.get("channel_id")
            or context.get("channel_id")
            or context["user_id"]
        )

        files = action_data["files"]
        thread_ts = resolve_media_thread_ts(action_data, body)

        files_to_process = []
        duplicate_files = []
        for file_data in files:
            (
                is_dup,
                submission_record,
            ) = await check_and_record_transcription_only_submission_async(
                slack_file_id=file_data["file_id"],
                file_name=file_data["file_name"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                channel_id=channel_id,
            )

            if is_dup:
                duplicate_files.append(file_data["file_name"])
            else:
                files_to_process.append(
                    {
                        **file_data,
                        "submission_id": submission_record.id,
                    }
                )

        if not files_to_process:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription to prevent duplicate submissions."
                ),
            )
            return

        quotes_posted = 0
        for file_info in files_to_process:
            slack_file_info = await client.files_info(file=file_info["file_id"])
            slack_file_data: dict[str, Any] = slack_file_info.get("file", {})
            download_url = slack_file_data.get(
                "url_private_download"
            ) or slack_file_data.get("url_private")

            if not download_url:
                continue

            file_info = await file_info_with_quote_duration(
                file_info,
                slack_file_data,
                download_url=download_url,
                bot_token=client.token or "",
            )
            session = await create_media_quote_session(
                pipeline_kind=PIPELINE_TRANSCRIBE,
                stage=STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                file_info=file_info,
                download_url=download_url,
                submission_id=file_info["submission_id"],
            )
            await post_or_auto_start_media_quote(
                client,
                context,
                session,
                accept_action_id=ACTION_MEDIA_QUOTE_ACCEPT,
                cancel_action_id=ACTION_MEDIA_QUOTE_CANCEL,
            )
            quotes_posted += 1

        if quotes_posted == 0:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Unable to prepare a quote for the selected file(s)."),
            )

        if duplicate_files:
            files = ", ".join(duplicate_files)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Skipped duplicate files already being processed: {files}"),
            )

    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


async def handle_video_transcribe_translate(
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Show the transcribe & translate modal for language selection."""
    assert action is not None

    action_data = json.loads(action.get("value", "{}"))
    thread_ts = resolve_media_thread_ts(action_data, body)
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, allow_org_billing=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            video_transcribe_translate_modal(
                channel_id=action_data.get("channel_id", context.get("channel_id", "")),
                files=action_data["files"],
                thread_ts=thread_ts,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_video_embed_subtitles(
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Handle subtitle embedding from either the modal flow or a thread-uploaded SRT."""
    assert action is not None

    action_data = json.loads(action.get("value", "{}"))
    thread_ts = resolve_media_thread_ts(action_data, body)
    if action_data.get("subtitle_file"):
        await populate_ray_connection(context)
        if await require_ray_client(context, allow_org_billing=True):
            await quote_existing_srt_embed_task(client, context, action_data, thread_ts)
        return

    all_files = action_data["files"]
    video_files = [f for f in all_files if not is_audio_only_file(f)]
    if not video_files:
        await populate_ray_connection(context)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "Subtitle embedding is only available for video files (MP4, MPEG, WEBM). "
                "Audio files (MP3, WAV, M4A) cannot have subtitles embedded."
            ),
        )
        return

    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, allow_org_billing=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            video_embed_subtitles_modal(
                channel_id=action_data.get("channel_id", context.get("channel_id", "")),
                files=video_files,
                thread_ts=thread_ts,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_video_configure_media(
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Open the unified Configure media modal."""
    assert action is not None

    action_data = json.loads(action.get("value", "{}"))
    thread_ts = resolve_media_thread_ts(action_data, body)
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, allow_org_billing=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            video_configure_media_modal(
                channel_id=action_data.get("channel_id", context.get("channel_id", "")),
                files=action_data["files"],
                thread_ts=thread_ts,
                show_embed_option=bool(action_data.get("show_embed_option", True)),
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_video_configure_workflow_type(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
):
    """Rebuild the Configure modal when the workflow type radio changes."""
    view = body["view"]
    metadata = json.loads(view.get("private_metadata") or "{}")
    selected = (action.get("selected_option") or {}).get("value")
    await client.views_update(
        view_id=view["id"],
        view=video_configure_media_modal(
            channel_id=metadata.get("channel_id", ""),
            files=metadata.get("files") or [],
            thread_ts=metadata.get("thread_ts"),
            show_embed_option=bool(metadata.get("show_embed_option", True)),
            show_translate_options=selected == "transcribe_translate",
        ),
    )


async def handle_media_srt_approve_continue(
    client: AsyncWebClient,
    action: Optional[Dict[str, Any]],
    context: RayContext,
):
    assert action is not None
    from app.slack.media_workflow_actions import (
        handle_media_srt_approve_continue as approve,
    )

    await approve(client=client, action=action, context=context)


async def handle_media_srt_replace(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Optional[Dict[str, Any]],
):
    assert action is not None
    from app.slack.media_workflow_actions import handle_media_srt_replace_open

    await handle_media_srt_replace_open(client=client, body=body, action=action)


async def handle_media_srt_replace_submit(
    view: Optional[dict],
    context: RayContext,
    client: AsyncWebClient,
):
    assert view is not None
    from app.slack.media_workflow_actions import (
        handle_media_srt_replace_submit as submit,
    )

    await submit(view=view, client=client, context=context)

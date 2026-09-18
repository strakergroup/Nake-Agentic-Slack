"""Handlers for media transcribe/translate and embed form submissions."""

import json
from typing import Any, Optional

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.media.media_workflow import MediaWorkflowType
from app.ray.submissions import (
    check_and_record_transcription_only_submission_async,
    check_and_record_transcription_submission_async,
)
from app.slack.buglog_notifier import notify_exception
from app.slack.media_configure import (
    VideoConfigureMediaError,
    VideoConfigureMediaSelection,
    configure_media_quote_fields,
    media_configure_enabled_for_user,
    parse_video_configure_media_view,
)
from app.slack.media_duration import file_info_with_quote_duration
from app.slack.media_quote_actions import post_or_auto_start_media_quote
from app.slack.media_quotes import (
    ACTION_MEDIA_QUOTE_ACCEPT,
    ACTION_MEDIA_QUOTE_CANCEL,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    create_media_quote_session,
)
from app.slack.middleware import require_ray_client
from app.translate import _


async def handle_video_transcribe_translate_submit(
    view: Optional[dict],
    context: RayContext,
    client: AsyncWebClient,
):
    """Handle transcribe & translate form submission — post Quote1 before ASR."""
    if not await require_ray_client(context, prompt_login=True, allow_org_billing=True):
        return

    try:
        assert view is not None
        assert context["ray"] is not None

        metadata = json.loads(view["private_metadata"])
        form_values = view["state"]["values"]

        lang_selection = form_values.get("target_languages", {}).get(
            "language_mt_options", {}
        )
        selected_options = lang_selection.get("selected_options", [])

        if not selected_options:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one target language for translation."),
            )
            return

        target_language_codes = [opt["value"] for opt in selected_options]
        target_language_names = [opt["text"]["text"] for opt in selected_options]

        channel_id = (
            metadata.get("channel_id")
            or context.get("channel_id")
            or context["user_id"]
        )

        file_selection = form_values.get("selected_file", {}).get("file_display", {})
        selected_file_options = file_selection.get("selected_options", [])
        selected_file_ids = {opt["value"] for opt in selected_file_options}

        all_files = metadata["files"]
        files = [f for f in all_files if f["file_id"] in selected_file_ids]

        if not files:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one file to process."),
            )
            return

        thread_ts = metadata.get("thread_ts")

        quotes_posted = 0
        all_duplicate_languages = []

        for file_info in files:
            duplicate_languages = []
            valid_languages = []
            submission_ids = []
            for lang_code, lang_name in zip(
                target_language_codes, target_language_names, strict=True
            ):
                (
                    is_dup,
                    submission_record,
                ) = await check_and_record_transcription_submission_async(
                    slack_file_id=file_info["file_id"],
                    file_name=file_info["file_name"],
                    user_id=context["user_id"],
                    team_id=context["team_id"],
                    channel_id=channel_id,
                    target_language=lang_code,
                )
                if is_dup:
                    duplicate_languages.append(lang_name)
                else:
                    valid_languages.append({"code": lang_code, "name": lang_name})
                    submission_ids.append(submission_record.id)

            if not valid_languages:
                all_duplicate_languages.extend(duplicate_languages)
                continue

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
            valid_language_codes = [lang["code"] for lang in valid_languages]
            valid_language_names_list = [lang["name"] for lang in valid_languages]

            session = await create_media_quote_session(
                pipeline_kind=PIPELINE_TRANSCRIBE_TRANSLATE,
                stage=STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                file_info=file_info,
                download_url=download_url,
                target_languages=valid_language_codes,
                target_language_names=valid_language_names_list,
                submission_ids=submission_ids,
            )
            await post_or_auto_start_media_quote(
                client,
                context,
                session,
                accept_action_id=ACTION_MEDIA_QUOTE_ACCEPT,
                cancel_action_id=ACTION_MEDIA_QUOTE_CANCEL,
            )
            quotes_posted += 1

            if duplicate_languages:
                all_duplicate_languages.extend(duplicate_languages)

        if quotes_posted == 0:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription & translation(s) to prevent duplicate submissions."
                ),
            )
            return

        if all_duplicate_languages:
            langs = ", ".join(list(set(all_duplicate_languages)))
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Some translations were skipped as duplicates: {langs}"),
            )

    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


async def handle_video_embed_subtitles_submit(
    view: Optional[dict],
    context: RayContext,
    client: AsyncWebClient,
):
    """Handle embed subtitles form submission — post Quote1 before ASR."""
    if not await require_ray_client(context, prompt_login=True, allow_org_billing=True):
        return

    try:
        assert view is not None
        assert context["ray"] is not None

        metadata = json.loads(view["private_metadata"])
        form_values = view["state"]["values"]

        lang_selection = form_values.get("target_languages", {}).get(
            "language_mt_options", {}
        )
        selected_options = lang_selection.get("selected_options", [])

        if not selected_options:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one target language for translation."),
            )
            return

        target_language_codes = [opt["value"] for opt in selected_options]
        target_language_names = [opt["text"]["text"] for opt in selected_options]

        channel_id = (
            metadata.get("channel_id")
            or context.get("channel_id")
            or context["user_id"]
        )

        file_selection = form_values.get("selected_file", {}).get("file_display", {})
        selected_file_options = file_selection.get("selected_options", [])
        selected_file_ids = {opt["value"] for opt in selected_file_options}

        all_files = metadata["files"]
        files = [f for f in all_files if f["file_id"] in selected_file_ids]

        if not files:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Please select at least one file to process."),
            )
            return

        thread_ts = metadata.get("thread_ts")

        quotes_posted = 0
        all_duplicate_languages = []

        for file_info in files:
            duplicate_languages = []
            valid_languages = []
            submission_ids = []
            for lang_code, lang_name in zip(
                target_language_codes, target_language_names, strict=True
            ):
                (
                    is_dup,
                    submission_record,
                ) = await check_and_record_transcription_submission_async(
                    slack_file_id=file_info["file_id"],
                    file_name=file_info["file_name"],
                    user_id=context["user_id"],
                    team_id=context["team_id"],
                    channel_id=channel_id,
                    target_language=lang_code,
                )
                if is_dup:
                    duplicate_languages.append(lang_name)
                else:
                    valid_languages.append({"code": lang_code, "name": lang_name})
                    submission_ids.append(submission_record.id)

            if not valid_languages:
                all_duplicate_languages.extend(duplicate_languages)
                continue

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
            valid_language_codes = [lang["code"] for lang in valid_languages]
            valid_language_names_list = [lang["name"] for lang in valid_languages]

            session = await create_media_quote_session(
                pipeline_kind=PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
                stage=STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                file_info=file_info,
                download_url=download_url,
                target_languages=valid_language_codes,
                target_language_names=valid_language_names_list,
                submission_ids=submission_ids,
            )
            await post_or_auto_start_media_quote(
                client,
                context,
                session,
                accept_action_id=ACTION_MEDIA_QUOTE_ACCEPT,
                cancel_action_id=ACTION_MEDIA_QUOTE_CANCEL,
            )
            quotes_posted += 1

            if duplicate_languages:
                all_duplicate_languages.extend(duplicate_languages)

        if quotes_posted == 0:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription & embedding(s) to prevent duplicate submissions."
                ),
            )
            return

        if all_duplicate_languages:
            langs = ", ".join(list(set(all_duplicate_languages)))
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Some embeddings were skipped as duplicates: {langs}"),
            )

    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


async def handle_video_configure_media_submit(
    view: Optional[dict],
    context: RayContext,
    client: AsyncWebClient,
):
    """Create Quote 1 from the unified Configure media modal."""
    if not await require_ray_client(context, prompt_login=True, allow_org_billing=True):
        return

    try:
        assert view is not None
        assert context["ray"] is not None
        if not await media_configure_enabled_for_user(context["ray"]):
            # Guards a view reopened from a stale trigger, where the action-side
            # check in handle_video_configure_media never ran for this user.
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Selecting media services is still being rolled out. "
                    "Ask a workspace admin to start this request."
                ),
            )
            return
        try:
            selection = parse_video_configure_media_view(view)
        except VideoConfigureMediaError as err:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=str(err),
            )
            return

        channel_id = (
            selection.channel_id or context.get("channel_id") or context["user_id"]
        )
        quotes_posted = await _post_configure_media_quotes(
            client=client,
            context=context,
            selection=selection,
            channel_id=channel_id,
        )
        if quotes_posted == 0:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "Please allow the system to complete the ongoing transcription to prevent duplicate submissions."
                ),
            )
    except Exception as e:
        notify_exception(e)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error processing your video. Please try again."),
        )


async def _post_configure_media_quotes(
    *,
    client: AsyncWebClient,
    context: RayContext,
    selection: VideoConfigureMediaSelection,
    channel_id: str,
) -> int:
    fields = configure_media_quote_fields(selection)
    quotes_posted = 0
    transcribe_only = selection.workflow_type is MediaWorkflowType.TRANSCRIBE_ONLY
    for file_info in selection.files:
        submission_id = None
        submission_ids: list[int] = []
        target_languages = fields["target_languages"]
        target_language_names = fields["target_language_names"]
        if transcribe_only:
            (
                is_dup,
                submission_record,
            ) = await check_and_record_transcription_only_submission_async(
                slack_file_id=file_info["file_id"],
                file_name=file_info["file_name"],
                user_id=context["user_id"],
                team_id=context["team_id"],
                channel_id=channel_id,
            )
            if is_dup:
                continue
            submission_id = submission_record.id
        else:
            valid_languages = []
            for lang_code, lang_name in zip(
                target_languages, target_language_names, strict=True
            ):
                (
                    is_dup,
                    submission_record,
                ) = await check_and_record_transcription_submission_async(
                    slack_file_id=file_info["file_id"],
                    file_name=file_info["file_name"],
                    user_id=context["user_id"],
                    team_id=context["team_id"],
                    channel_id=channel_id,
                    target_language=lang_code,
                )
                if not is_dup:
                    valid_languages.append({"code": lang_code, "name": lang_name})
                    submission_ids.append(submission_record.id)
            if not valid_languages:
                continue
            target_languages = [lang["code"] for lang in valid_languages]
            target_language_names = [lang["name"] for lang in valid_languages]

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
            pipeline_kind=fields["pipeline_kind"],
            stage=STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=channel_id,
            thread_ts=selection.thread_ts,
            file_info=file_info,
            download_url=download_url,
            target_languages=target_languages,
            target_language_names=target_language_names,
            submission_id=submission_id,
            submission_ids=submission_ids,
            extra=fields["extra"],
        )
        await post_or_auto_start_media_quote(
            client,
            context,
            session,
            accept_action_id=ACTION_MEDIA_QUOTE_ACCEPT,
            cancel_action_id=ACTION_MEDIA_QUOTE_CANCEL,
        )
        quotes_posted += 1
    return quotes_posted

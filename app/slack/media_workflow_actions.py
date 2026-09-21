"""Execute Configure media workflow commands from Slack clicks and Ray callbacks."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from app.auth.connector import RayContext
from app.media.media_workflow import (
    MediaWorkflowCommand,
    MediaWorkflowDecision,
    MediaWorkflowEvent,
    MediaWorkflowStage,
    MediaWorkflowTransitionError,
    advance_media_workflow,
    media_workflow_session_from_quote,
)
from app.ray.settings import get_auto_translate_language_name
from app.ray.utils import download_from_file_server_async, upload_to_file_server
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
from app.slack.media_configure_embed import TranslatedSrtLanguageRequired
from app.slack.media_quote_actions import (
    auto_accept_media_translation_quote_if_needed,
    post_media_quote_message,
)
from app.slack.media_quote_adjustment import media_translation_quote_from_session
from app.slack.media_quotes import (
    ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
    ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    build_quote2_line_items,
    get_media_quote_session,
    media_quote_actor_may_continue,
    media_quote_lock_key,
    total_tokens_from_line_items,
    update_media_quote_session,
)
from app.slack.modal_trigger import (
    open_loading_modal,
    safe_views_update,
    status_modal,
)
from app.slack.templates.messages import (
    MediaSrtApproveContinueMessage,
    MediaSrtReviewMessage,
)
from app.slack.web import download_file, upload_file_to_slack_memory_efficient
from app.translate import _

logger = logging.getLogger(__name__)


class MediaSrtReplaceFailed(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__("Could not store the replacement SRT on the file server")
        self.cause = cause
        self.__cause__ = cause


async def post_deferred_word_transcript(
    client: AsyncWebClient,
    *,
    word_file_id: str,
    word_file_name: str,
    channel_id: str,
    thread_ts: str | None,
    initial_comment: str | None = None,
) -> None:
    """Upload a Word transcript withheld until its SRT was approved.

    Best-effort: a failure is logged and swallowed so the approval workflow
    never fails because of the Word extra.
    """
    word_path: str | None = None
    try:
        word_output = await download_from_file_server_async(word_file_id)
        word_path = word_output.get("file") if isinstance(word_output, dict) else None
        if not word_path or not os.path.exists(word_path):
            raise RuntimeError(
                f"File-server download returned no path for {word_file_id=}"
            )
        word_dir = os.path.dirname(word_path)
        renamed_word_path = os.path.join(word_dir, word_file_name)
        if word_path != renamed_word_path:
            os.rename(word_path, renamed_word_path)
            word_path = renamed_word_path
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=word_path,
            channel_id=channel_id,
            title=word_file_name,
            filename=word_file_name,
            initial_comment=initial_comment,
            thread_ts=thread_ts,
        )
    except Exception:
        logger.exception(
            "Deferred Word transcript upload failed; SRT already approved",
            extra={"word_file_id": word_file_id},
        )
    finally:
        try:
            if word_path and os.path.exists(word_path):
                os.unlink(word_path)
        except OSError:
            pass


def thread_srt_matches_review_file(
    *,
    uploaded_name: str,
    original_file_name: str,
    stage: MediaWorkflowStage,
    target_languages: tuple[str, ...],
) -> bool:
    uploaded_stem = Path(uploaded_name).stem.lower()
    original_stem = Path(original_file_name).stem.lower()
    if stage is MediaWorkflowStage.AWAITING_SOURCE_REVIEW:
        return uploaded_stem == original_stem
    if stage is MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW:
        if uploaded_stem == original_stem:
            return False
        if uploaded_stem.startswith(f"{original_stem}_"):
            return True
        return any(
            _stem_has_language_code_token(uploaded_stem, code)
            for code in target_languages
        )
    return False


def _stem_has_language_code_token(stem: str, code: str) -> bool:
    token = code.lower()
    if not token:
        return False
    return re.search(rf"(?:^|[-_.]){re.escape(token)}(?:[-_.]|$)", stem) is not None


def _translated_replace_language(
    uploaded_name: str, target_languages: tuple[str, ...]
) -> str | None:
    stem = Path(uploaded_name).stem.lower()
    suffix = stem.rsplit("_", 1)[-1] if "_" in stem else stem
    for code in target_languages:
        if _stem_has_language_code_token(stem, code):
            return code
        if suffix == code.lower():
            return code
        name = get_auto_translate_language_name(code)
        if name and suffix == name.casefold():
            return code
    if len(target_languages) == 1:
        return target_languages[0]
    return None


def parse_media_srt_review_value(value: str) -> tuple[str, str | None]:
    raw = (value or "").strip()
    if not raw:
        return "", None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw, None
        if isinstance(data, dict):
            quote_id = str(data.get("quote_id") or "")
            language = data.get("language")
            return quote_id, str(language) if language else None
    return raw, None


def _srt_approve_lock_key(quote_id: str) -> str:
    return media_quote_lock_key(f"srt-approve:{quote_id}")


async def apply_thread_srt_review_replace(
    *,
    client: AsyncWebClient,
    session: dict[str, Any],
    slack_file_id: str,
    uploaded_name: str,
    match_review_filename: bool = True,
    acting_user_id: str | None = None,
    language: str | None = None,
    context: RayContext | None = None,
) -> bool:
    workflow = media_workflow_session_from_quote(session)
    if workflow is None:
        return False
    if workflow.stage not in (
        MediaWorkflowStage.AWAITING_SOURCE_REVIEW,
        MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
    ):
        return False
    if acting_user_id is not None:
        allowed = (
            await media_quote_actor_may_continue(session, context)
            if context is not None
            else session.get("user_id") == acting_user_id
        )
        if not allowed:
            await client.chat_postMessage(
                channel=acting_user_id,
                text=_("You do not have permission to replace this file."),
            )
            return True
    if match_review_filename and not thread_srt_matches_review_file(
        uploaded_name=uploaded_name,
        original_file_name=str(session.get("file_name") or ""),
        stage=workflow.stage,
        target_languages=workflow.config.target_languages,
    ):
        return False

    event = (
        MediaWorkflowEvent.SOURCE_SRT_REPLACED
        if workflow.stage is MediaWorkflowStage.AWAITING_SOURCE_REVIEW
        else MediaWorkflowEvent.TRANSLATED_SRT_REPLACED
    )
    replaced_language = language
    if event is MediaWorkflowEvent.TRANSLATED_SRT_REPLACED:
        replaced_language = language or _translated_replace_language(
            uploaded_name, workflow.config.target_languages
        )
        if not replaced_language and len(workflow.config.target_languages) > 1:
            return False
    try:
        advance_media_workflow(workflow, event)
    except MediaWorkflowTransitionError as exc:
        notify_exception(exc)
        await _notify_srt_replace_failed(client, session)
        return True

    quote_id = str(session["quote_id"])
    lock_key = _srt_approve_lock_key(quote_id)
    lock_acquired = await redis_conn.set(lock_key, "1", ex=120, nx=True)
    if not lock_acquired:
        await client.chat_postMessage(
            channel=acting_user_id or str(session.get("channel_id") or ""),
            text=_(
                "A request is already in progress. Please try again in a few seconds."
            ),
            thread_ts=session.get("thread_ts"),
        )
        return True

    try:
        try:
            file_server_id = await _upload_slack_srt_to_file_server(
                client, slack_file_id
            )
        except Exception as exc:
            failed = (
                exc
                if isinstance(exc, MediaSrtReplaceFailed)
                else MediaSrtReplaceFailed(exc)
            )
            notify_exception(failed)
            await _notify_srt_replace_failed(client, session)
            return True
        updates: dict[str, Any] = {
            (
                "approved_source_srt_file_id"
                if event is MediaWorkflowEvent.SOURCE_SRT_REPLACED
                else "approved_translated_srt_file_id"
            ): file_server_id
        }
        if event is MediaWorkflowEvent.TRANSLATED_SRT_REPLACED:
            if replaced_language:
                updates["approved_translated_srt_language"] = replaced_language
                file_ids = dict(session.get("approved_translated_srt_file_ids") or {})
                file_ids[replaced_language] = file_server_id
                updates["approved_translated_srt_file_ids"] = file_ids
        await update_media_quote_session(quote_id, updates)
        if event is MediaWorkflowEvent.SOURCE_SRT_REPLACED:
            auto_workflow = media_workflow_session_from_quote({**session, **updates})
            if auto_workflow is None:
                notify_exception(
                    MediaWorkflowTransitionError(
                        workflow.stage, MediaWorkflowEvent.SOURCE_SRT_APPROVED
                    )
                )
            else:
                try:
                    auto_decision = advance_media_workflow(
                        auto_workflow, MediaWorkflowEvent.SOURCE_SRT_APPROVED
                    )
                except MediaWorkflowTransitionError as exc:
                    notify_exception(exc)
                    auto_decision = None
                if auto_decision is not None:
                    completed = await _complete_source_approval(
                        client, {**session, **updates}, auto_decision
                    )
                    if completed is not None:
                        return True
        await client.chat_postMessage(
            channel=str(session["channel_id"]),
            text=_("Replacement file received. Click *Proceed* when you are ready."),
            thread_ts=session.get("thread_ts"),
        )
        return True
    finally:
        await redis_conn.delete(lock_key)


async def _post_deferred_word_transcript_if_needed(
    client: AsyncWebClient,
    session: dict[str, Any],
    initial_comment: str | None = None,
) -> None:
    """Upload a Word transcript withheld for end-of-flow delivery."""
    word_file_id = session.get("deferred_word_file_id")
    word_file_name = session.get("deferred_word_file_name")
    if not word_file_id or not word_file_name:
        return
    await post_deferred_word_transcript(
        client,
        word_file_id=str(word_file_id),
        word_file_name=str(word_file_name),
        channel_id=str(session.get("channel_id") or ""),
        thread_ts=session.get("thread_ts"),
        initial_comment=initial_comment,
    )
    await update_media_quote_session(
        str(session["quote_id"]),
        {"deferred_word_file_id": None, "deferred_word_file_name": None},
    )


async def _complete_source_approval(
    client: AsyncWebClient,
    session: dict[str, Any],
    decision: MediaWorkflowDecision,
) -> dict[str, Any] | None:
    """Run the source-approval follow-on shared by Approve and auto-advance.

    Returns the updated session, or None when the follow-on posted its own
    error message and the caller should stop.
    """
    try:
        updated = await execute_media_workflow_decision(
            client=client,
            session=session,
            decision=decision,
        )
    except TranslatedSrtLanguageRequired:
        await client.chat_postMessage(
            channel=str(session.get("channel_id") or ""),
            text=_(
                "We couldn't tell which subtitle that replacement belongs to. "
                "Use *Edit and reupload* under the file you edited."
            ),
            thread_ts=session.get("thread_ts"),
        )
        return None
    await _clear_srt_review_actions(client, session)
    return updated


async def handle_media_srt_approve_continue(
    *,
    client: AsyncWebClient,
    action: dict[str, Any],
    context: RayContext,
) -> None:
    quote_id = str(action.get("value") or "")
    session = await get_media_quote_session(quote_id)
    if session is None:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This media review has expired. Please start a new request."),
        )
        return
    if not await media_quote_actor_may_continue(session, context):
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to approve this file."),
        )
        return

    lock_key = _srt_approve_lock_key(quote_id)
    lock_acquired = await redis_conn.set(lock_key, "1", ex=120, nx=True)
    if not lock_acquired:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "A request is already in progress. Please try again in a few seconds."
            ),
        )
        return

    try:
        session = await get_media_quote_session(quote_id)
        if session is None:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("This media review has expired. Please start a new request."),
            )
            return
        workflow = media_workflow_session_from_quote(session)
        if workflow is None:
            return
        translated = workflow.stage is MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW
        event = (
            MediaWorkflowEvent.SOURCE_SRT_APPROVED
            if workflow.stage is MediaWorkflowStage.AWAITING_SOURCE_REVIEW
            else MediaWorkflowEvent.TRANSLATED_SRT_APPROVED
        )
        try:
            decision = advance_media_workflow(workflow, event)
        except MediaWorkflowTransitionError as exc:
            notify_exception(exc)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "This file can no longer be approved. The media request has "
                    "already moved on — check the thread for the latest step."
                ),
            )
            return
        if event is MediaWorkflowEvent.SOURCE_SRT_APPROVED:
            await _complete_source_approval(client, session, decision)
            return
        try:
            await execute_media_workflow_decision(
                client=client,
                session=session,
                decision=decision,
            )
        except TranslatedSrtLanguageRequired:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "We couldn't tell which subtitle that replacement belongs to. "
                    "Use *Edit and reupload* under the file you edited."
                ),
            )
            return
        await _clear_srt_review_actions(client, session)
    finally:
        await redis_conn.delete(lock_key)


async def execute_media_workflow_decision(
    *,
    client: AsyncWebClient,
    session: dict[str, Any],
    decision: MediaWorkflowDecision,
    source_text_length: int | None = None,
    task_uuid: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {"stage": decision.session.stage.value}
    if source_text_length is not None:
        updates["source_text_length"] = source_text_length
    if task_uuid is not None:
        updates["task_uuid"] = task_uuid
    if duration_ms is not None:
        updates["duration_ms"] = duration_ms

    if MediaWorkflowCommand.POST_QUOTE2 in decision.commands:
        length = int(
            source_text_length
            if source_text_length is not None
            else session.get("source_text_length") or 0
        )
        targets = list(session.get("target_languages") or [])
        line_items = build_quote2_line_items(
            source_text_length=length,
            target_count=len(targets) or 1,
            duration_ms=int(duration_ms or session.get("duration_ms") or 0),
            embed_source=bool(session.get("embed_source")),
            embed_translated=bool(session.get("embed_translated")),
        )
        quote_seed = {
            **session,
            "source_text_length": length,
            "target_languages": targets,
        }
        updates.update(
            {
                "stage": STAGE_AWAITING_TRANSLATION_ACCEPT,
                "source_text_length": length,
                "line_items": line_items,
                "total_tokens": total_tokens_from_line_items(line_items),
                "quote": media_translation_quote_from_session(quote_seed),
            }
        )

    defer_stage = (
        MediaWorkflowCommand.START_SOURCE_EMBED in decision.commands
        or MediaWorkflowCommand.START_TRANSLATED_EMBED in decision.commands
    )
    persist = {key: value for key, value in updates.items() if key != "stage"}
    stage_changed = decision.session.stage.value != session.get("stage")
    if not defer_stage and (decision.commands or stage_changed):
        persist["stage"] = updates["stage"]
    if persist:
        updated = await update_media_quote_session(
            str(session["quote_id"]), persist
        ) or {**session, **persist}
    else:
        updated = dict(session)
    updated = {**updated, **updates}

    if MediaWorkflowCommand.POST_SOURCE_REVIEW in decision.commands:
        if updated.get("auto_proceed"):
            follow = advance_media_workflow(
                decision.session, MediaWorkflowEvent.SOURCE_SRT_APPROVED
            )
            return await execute_media_workflow_decision(
                client=client,
                session=updated,
                decision=follow,
                source_text_length=source_text_length,
                task_uuid=task_uuid,
                duration_ms=duration_ms,
            )
        if not updated.get("defer_source_review"):
            await _post_srt_review(client, updated)
    if MediaWorkflowCommand.POST_TRANSLATION_REVIEW in decision.commands:
        if updated.get("auto_proceed"):
            follow = advance_media_workflow(
                decision.session, MediaWorkflowEvent.TRANSLATED_SRT_APPROVED
            )
            return await execute_media_workflow_decision(
                client=client,
                session=updated,
                decision=follow,
                source_text_length=source_text_length,
                task_uuid=task_uuid,
                duration_ms=duration_ms,
            )
        await _post_srt_approve_continue(client, updated, translated=True)
    if MediaWorkflowCommand.START_TRANSLATE in decision.commands:
        from app.slack.media_quote_actions import _resume_translate_phase

        resume_uuid = str(task_uuid or updated.get("task_uuid") or "")
        if resume_uuid:
            await _resume_translate_phase(
                task_uuid=resume_uuid,
                pipeline_kind=str(updated.get("pipeline_kind") or ""),
                session=updated,
            )
    if MediaWorkflowCommand.START_SOURCE_EMBED in decision.commands:
        from app.slack.media_configure_embed import resume_configure_embed_phase

        await resume_configure_embed_phase(session=updated, translated=False)
    if MediaWorkflowCommand.START_TRANSLATED_EMBED in decision.commands:
        from app.slack.media_configure_embed import resume_configure_embed_phase

        await resume_configure_embed_phase(session=updated, translated=True)
    if defer_stage:
        updated = (
            await update_media_quote_session(
                str(session["quote_id"]), {"stage": updates["stage"]}
            )
            or updated
        )
    if MediaWorkflowCommand.POST_QUOTE2 in decision.commands:
        if not await auto_accept_media_translation_quote_if_needed(client, updated):
            await post_media_quote_message(
                client,
                updated,
                accept_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
                cancel_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
            )
    if MediaWorkflowCommand.MARK_DONE in decision.commands:
        from app.ray.events.media_pipeline_events import update_submission_status

        await update_submission_status(updated)
        await _post_deferred_word_transcript_if_needed(
            client, updated, initial_comment=_("Native transcript copy")
        )
    return updated


async def _record_srt_review_ts(
    client: AsyncWebClient,
    session: dict[str, Any],
    *,
    text: str,
    blocks: list[dict[str, Any]],
) -> None:
    response = await client.chat_postMessage(
        channel=str(session["channel_id"]),
        text=text,
        blocks=blocks,
        thread_ts=session.get("thread_ts"),
    )
    ts = None
    if isinstance(response, dict):
        ts = response.get("ts")
    elif isinstance(response, AsyncSlackResponse):
        ts = response.get("ts")
    if not isinstance(ts, str) or not ts:
        return
    existing = [
        item
        for item in (session.get("srt_review_message_ts") or [])
        if isinstance(item, str)
    ]
    existing.append(ts)
    session["srt_review_message_ts"] = existing
    await update_media_quote_session(
        str(session["quote_id"]), {"srt_review_message_ts": existing}
    )


async def _post_srt_file_replace(
    client: AsyncWebClient,
    session: dict[str, Any],
    *,
    language: str | None = None,
    file_label: str | None = None,
) -> None:
    review = MediaSrtReviewMessage(
        str(session["quote_id"]),
        language=language,
        file_label=file_label,
    )
    await _record_srt_review_ts(client, session, text=review.text, blocks=review.blocks)


async def _post_srt_approve_continue(
    client: AsyncWebClient,
    session: dict[str, Any],
    *,
    translated: bool = False,
    include_replace: bool = False,
) -> None:
    approve = MediaSrtApproveContinueMessage(
        str(session["quote_id"]),
        translated=translated,
        include_replace=include_replace,
    )
    await _record_srt_review_ts(
        client, session, text=approve.text, blocks=approve.blocks
    )


async def _post_srt_review(
    client: AsyncWebClient,
    session: dict[str, Any],
    *,
    language: str | None = None,
    file_label: str | None = None,
) -> None:
    if language is None:
        await _post_srt_approve_continue(client, session, include_replace=True)
        return
    await _post_srt_file_replace(
        client, session, language=language, file_label=file_label
    )
    await _post_srt_approve_continue(client, session)


async def _clear_srt_review_actions(
    client: AsyncWebClient, session: dict[str, Any]
) -> None:
    timestamps = [
        ts
        for ts in (session.get("srt_review_message_ts") or [])
        if isinstance(ts, str) and ts
    ]
    channel_id = session.get("channel_id")
    if timestamps and channel_id:
        for ts in timestamps:
            try:
                await client.chat_delete(channel=str(channel_id), ts=ts)
            except SlackApiError as exc:
                notify_exception(exc)
        await update_media_quote_session(
            str(session["quote_id"]), {"srt_review_message_ts": []}
        )
        session["srt_review_message_ts"] = []


async def _notify_srt_replace_failed(
    client: AsyncWebClient, session: dict[str, Any]
) -> None:
    await client.chat_postMessage(
        channel=str(session["channel_id"]),
        text=_(
            "Could not replace this file. Please try again from the review message."
        ),
        thread_ts=session.get("thread_ts"),
    )


async def _upload_slack_srt_to_file_server(
    client: AsyncWebClient, slack_file_id: str
) -> str:
    try:
        path = await download_file(client=client, file_id=slack_file_id, http=None)
        try:
            return await upload_to_file_server(path)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)
    except MediaSrtReplaceFailed:
        raise
    except Exception as exc:
        raise MediaSrtReplaceFailed(exc) from exc


async def handle_media_srt_replace_open(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
) -> None:
    from app.slack.templates.views import media_srt_replace_modal

    quote_id, language = parse_media_srt_review_value(str(action.get("value") or ""))
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await safe_views_update(
            client,
            view_id,
            media_srt_replace_modal(quote_id, language=language),
        )
    except SlackApiError as exc:
        notify_exception(exc)
        await safe_views_update(
            client,
            view_id,
            status_modal(
                _("Edit and reupload")[:24],
                _(
                    "Upload your edited subtitle file in this thread to replace "
                    "the file under review."
                ),
            ),
        )


async def handle_media_srt_replace_submit(
    *,
    view: dict[str, Any],
    client: AsyncWebClient,
    context: RayContext,
) -> None:
    quote_id, language = parse_media_srt_review_value(
        str(view.get("private_metadata") or "")
    )
    session = await get_media_quote_session(quote_id)
    if session is None:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This media review has expired. Please start a new request."),
        )
        return
    if not await media_quote_actor_may_continue(session, context):
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to replace this file."),
        )
        return
    values = (view.get("state") or {}).get("values") or {}
    files = ((values.get("srt_file") or {}).get("srt_file_input") or {}).get(
        "files"
    ) or []
    if not files:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("Please choose a transcript or subtitle file to replace."),
        )
        return
    slack_file = files[0]
    uploaded_name = str(slack_file.get("name") or slack_file.get("title") or "file.srt")
    if not uploaded_name.lower().endswith(".srt"):
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("Please upload a transcript or subtitle file."),
        )
        return
    replaced = await apply_thread_srt_review_replace(
        client=client,
        session=session,
        slack_file_id=str(slack_file.get("id") or ""),
        uploaded_name=uploaded_name,
        match_review_filename=False,
        acting_user_id=context["user_id"],
        language=language,
        context=context,
    )
    if not replaced:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "Could not replace this file. Please try again from the review message."
            ),
        )

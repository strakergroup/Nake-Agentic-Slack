"""Execute Configure media workflow commands from Slack clicks and Ray callbacks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

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
from app.ray.utils import upload_to_file_server
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
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
    media_quote_lock_key,
    total_tokens_from_line_items,
    update_media_quote_session,
)
from app.slack.templates.messages import MediaSrtReviewMessage
from app.slack.web import download_file
from app.translate import _


class MediaSrtReplaceFailed(Exception):
    def __init__(self, cause: BaseException) -> None:
        super().__init__("Could not store the replacement SRT on the file server")
        self.cause = cause
        self.__cause__ = cause


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


async def apply_thread_srt_review_replace(
    *,
    client: AsyncWebClient,
    session: dict[str, Any],
    slack_file_id: str,
    uploaded_name: str,
    match_review_filename: bool = True,
) -> bool:
    workflow = media_workflow_session_from_quote(session)
    if workflow is None:
        return False
    if workflow.stage not in (
        MediaWorkflowStage.AWAITING_SOURCE_REVIEW,
        MediaWorkflowStage.AWAITING_TRANSLATION_REVIEW,
    ):
        return False
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
    try:
        advance_media_workflow(workflow, event)
    except MediaWorkflowTransitionError as exc:
        notify_exception(exc)
        await _notify_srt_replace_failed(client, session)
        return True

    try:
        file_server_id = await _upload_slack_srt_to_file_server(client, slack_file_id)
    except Exception as exc:
        failed = (
            exc
            if isinstance(exc, MediaSrtReplaceFailed)
            else MediaSrtReplaceFailed(exc)
        )
        notify_exception(failed)
        await _notify_srt_replace_failed(client, session)
        return True
    field = (
        "approved_source_srt_file_id"
        if event is MediaWorkflowEvent.SOURCE_SRT_REPLACED
        else "approved_translated_srt_file_id"
    )
    await update_media_quote_session(str(session["quote_id"]), {field: file_server_id})
    await client.chat_postMessage(
        channel=str(session["channel_id"]),
        text=_(
            "Replacement SRT received. Click *Approve & Continue* when you are ready."
        ),
        thread_ts=session.get("thread_ts"),
    )
    return True


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
    if session.get("user_id") != context["user_id"]:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to approve this SRT."),
        )
        return

    lock_key = media_quote_lock_key(f"srt-approve:{quote_id}")
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
        workflow = media_workflow_session_from_quote(session)
        if workflow is None:
            return
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
                    "This SRT can no longer be approved. The media request has "
                    "already moved on — check the thread for the latest step."
                ),
            )
            return
        await execute_media_workflow_decision(
            client=client,
            session=session,
            decision=decision,
        )
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

    defer_stage = decision.session.stage in (
        MediaWorkflowStage.EMBEDDING_SOURCE,
        MediaWorkflowStage.EMBEDDING_TRANSLATED,
    )
    persist = {key: value for key, value in updates.items() if key != "stage"}
    if not defer_stage:
        persist["stage"] = updates["stage"]
    if persist:
        updated = await update_media_quote_session(
            str(session["quote_id"]), persist
        ) or {**session, **persist}
    else:
        updated = dict(session)
    updated = {**updated, **updates}

    if MediaWorkflowCommand.POST_SOURCE_REVIEW in decision.commands:
        if not updated.get("defer_source_review"):
            await _post_srt_review(client, updated)
    if MediaWorkflowCommand.POST_TRANSLATION_REVIEW in decision.commands:
        await _post_srt_review(client, updated)
    if MediaWorkflowCommand.POST_QUOTE2 in decision.commands:
        if not await auto_accept_media_translation_quote_if_needed(client, updated):
            await post_media_quote_message(
                client,
                updated,
                accept_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
                cancel_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
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
    if MediaWorkflowCommand.MARK_DONE in decision.commands:
        from app.ray.events.media_pipeline_events import update_submission_status

        await update_submission_status(updated)
    return updated


async def _post_srt_review(client: AsyncWebClient, session: dict[str, Any]) -> None:
    review = MediaSrtReviewMessage(str(session["quote_id"]))
    await client.chat_postMessage(
        channel=str(session["channel_id"]),
        text=review.text,
        blocks=review.blocks,
        thread_ts=session.get("thread_ts"),
    )


async def _notify_srt_replace_failed(
    client: AsyncWebClient, session: dict[str, Any]
) -> None:
    await client.chat_postMessage(
        channel=str(session["channel_id"]),
        text=_("Could not replace this SRT. Please try again from the review message."),
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

    await client.views_open(
        trigger_id=body["trigger_id"],
        view=media_srt_replace_modal(str(action.get("value") or "")),
    )


async def handle_media_srt_replace_submit(
    *,
    view: dict[str, Any],
    client: AsyncWebClient,
    context: RayContext,
) -> None:
    quote_id = str(view.get("private_metadata") or "")
    session = await get_media_quote_session(quote_id)
    if session is None:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This media review has expired. Please start a new request."),
        )
        return
    values = (view.get("state") or {}).get("values") or {}
    files = ((values.get("srt_file") or {}).get("srt_file_input") or {}).get(
        "files"
    ) or []
    if not files:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("Please choose an SRT file to replace."),
        )
        return
    slack_file = files[0]
    replaced = await apply_thread_srt_review_replace(
        client=client,
        session=session,
        slack_file_id=str(slack_file.get("id") or ""),
        uploaded_name=str(
            slack_file.get("name") or slack_file.get("title") or "file.srt"
        ),
        match_review_filename=False,
    )
    if not replaced:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "Could not replace this SRT. Please try again from the review message."
            ),
        )

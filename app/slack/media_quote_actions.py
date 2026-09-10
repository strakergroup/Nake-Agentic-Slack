"""Accept / cancel handlers for media transcription and translation quotes."""

from __future__ import annotations

from typing import Any, cast

import httpx
from slack_bolt.context.async_context import AsyncBoltContext
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.connector import (
    RayContext,
    get_client_tokens,
    get_client_type,
    get_group_tokens,
    mt_billing_client_id,
    mt_bills_workspace_org,
    resolve_slack_user_email,
    suppress_ibm_mt_token_prompt,
    user_may_receive_quotes,
)
from app.config import domains
from app.database import async_engines
from app.ibm_ht_service_account import resolve_slack_poster_email
from app.media.media_workflow import (
    MediaWorkflowEvent,
    MediaWorkflowTransitionError,
    advance_media_workflow,
    media_workflow_session_from_quote,
)
from app.models import ASRTask, TranscriptionTask, TranscriptionTaskData
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
from app.slack.document_mt_quote_adjustment import document_mt_tokens_for_pairs
from app.slack.media_configure_embed import resume_configure_embed_phase
from app.slack.media_quote_adjustment import (
    media_selected_target_language_names,
    media_selected_target_languages,
    media_translation_quote_from_session,
    media_translation_quote_uses_adjust_layout,
)
from app.slack.media_quotes import (
    ACTION_MEDIA_QUOTE_ACCEPT,
    ACTION_MEDIA_QUOTE_CANCEL,
    ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
    ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
    PIPELINE_EMBED,
    PIPELINE_TRANSCRIBE,
    PIPELINE_TRANSCRIBE_TRANSLATE,
    PIPELINE_TRANSCRIBE_TRANSLATE_EMBED,
    STAGE_AWAITING_TRANSCRIPTION_ACCEPT,
    STAGE_AWAITING_TRANSLATION_ACCEPT,
    STAGE_CANCELLED,
    STAGE_EMBEDDING,
    STAGE_TRANSCRIBING,
    STAGE_TRANSLATING,
    delete_media_quote_session,
    get_media_quote_session,
    media_quote_blocks,
    media_quote_lock_key,
    translate_resume_pipeline_type,
    update_media_quote_session,
)
from app.slack.middleware import require_ray_client
from app.slack.templates.messages import (
    MediaTranslationQuoteMessage,
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
)
from app.transcriber_tasks.tasks import create_asr_task
from app.translate import _


async def _require_ai_token_balance(
    context: RayContext,
    client: AsyncWebClient,
    required_tokens: int,
) -> bool:
    """Gate on AI token balance using already-priced token counts (not char→token)."""
    if required_tokens <= 0:
        return True

    ai_tokens = 0
    ray = context.get("ray")
    if ray and mt_bills_workspace_org(ray):
        org_uuid = ray.super_group[0].verify_organization_uuid
        client_tokens = await get_group_tokens(org_uuid)
        if client_tokens is None:
            return False
        ai_tokens = client_tokens.ai_token
        if ai_tokens and ai_tokens >= required_tokens:
            return True
        if suppress_ibm_mt_token_prompt(
            context.enterprise_id,
            organization_uuid=org_uuid,
            balance=ai_tokens,
            required=required_tokens,
            extra={"slack_user_id": context.get("user_id")},
        ):
            return False
    elif ray and ray.client is not None:
        user_tokens = await get_client_tokens(ray.client.id_token)
        if user_tokens is None:
            return False
        ai_tokens = user_tokens.ai_token
        if ai_tokens >= required_tokens:
            return True
    if suppress_ibm_mt_token_prompt(
        context.enterprise_id,
        organization_uuid=(
            ray.super_group[0].verify_organization_uuid
            if ray and ray.super_group
            else ""
        ),
        balance=ai_tokens,
        required=required_tokens,
        extra={
            "source": "media_quote",
            "slack_user_id": context.get("user_id"),
        },
    ):
        return False

    client_type = None
    if ray and ray.client is not None:
        client_type = await get_client_type(ray.client.id, ray.client.user_group_id)

    message = (
        RequiresMtTokenMessage(ai_tokens, required_tokens)
        if client_type in ["Admin", "Owner"]
        and not is_ibm_enterprise(enterprise_id=context.enterprise_id)
        else RequiresMtTokenAdminMessage(ai_tokens, required_tokens)
    )
    await client.chat_postMessage(
        channel=context["user_id"],
        text=message.text,
        blocks=message.blocks,
    )
    return False


async def _update_quote_message(
    client: AsyncWebClient,
    *,
    channel_id: str | None,
    message_ts: str | None,
    session: dict[str, Any],
    accept_action_id: str,
    cancel_action_id: str,
    actions: bool,
    status_message: str | None = None,
) -> None:
    if not channel_id or not message_ts:
        return
    if media_translation_quote_uses_adjust_layout(session):
        message = MediaTranslationQuoteMessage(
            session,
            actions=actions,
            status_message=status_message,
        )
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=message.text,
            blocks=message.blocks,
        )
        return
    blocks = media_quote_blocks(
        session,
        accept_action_id=accept_action_id,
        cancel_action_id=cancel_action_id,
        actions=actions,
        status_message=status_message,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=_("Service Quote"),
        blocks=blocks,
    )


async def _create_asr_from_quote_session(
    *,
    client: AsyncWebClient,
    context: RayContext,
    session: dict[str, Any],
    pipeline_type_for_db: str,
    extra_data: dict[str, Any],
) -> str:
    ray = context.get("ray")
    if ray is None:
        raise ValueError("Ray connection is required to start media processing")

    # Workspace org on slack_super_group_link always bills MT / media.
    billing_client_id = mt_billing_client_id(ray)
    if not billing_client_id:
        raise ValueError(
            "A LanguageCloud member or workspace organisation is required "
            "to start media processing"
        )

    task_data = TranscriptionTaskData(
        client_id=billing_client_id,
        file_name=session["file_name"],
        download_url=session["download_url"],
        app_token=client.token or "",
        out_stream_name=f"{domains.stream_proxy}/events/transcription:slack:media:results",
        service="azure",
        model="whisper-1",
        embed_subtitles=pipeline_type_for_db
        in (PIPELINE_TRANSCRIBE_TRANSLATE_EMBED, PIPELINE_EMBED),
        sandbox=False,
    )
    asr_task = ASRTask(
        member_uuid=billing_client_id,
        event_name="sup-subtitle-ai:media:asr",
        app_source="slack",
        service="azure",
        model="whisper-1",
        extra_data=extra_data,
        task_data=task_data,
    )
    return await create_asr_task(asr_task)


async def _resume_translate_phase(
    *,
    task_uuid: str,
    pipeline_kind: str,
    session: dict[str, Any],
) -> None:
    """Update DB pipeline_type for phase-2 and re-trigger the consumer."""
    next_pipeline = translate_resume_pipeline_type(session)

    async with AsyncSession(async_engines["sitecommons"]) as db_session:
        task = await db_session.get(TranscriptionTask, task_uuid)
        if not task:
            raise ValueError(f"Transcription task {task_uuid} not found")
        extra_data = dict(task.extra_data or {})
        extra_data["media_quote_id"] = session["quote_id"]
        extra_data["pipeline_kind"] = pipeline_kind
        if session.get("workflow_type"):
            extra_data["workflow_type"] = session["workflow_type"]
            extra_data["embed_translated"] = bool(session.get("embed_translated"))
            extra_data["embed_source"] = bool(session.get("embed_source"))
        selected_languages = media_selected_target_languages(session)
        extra_data["target_languages"] = selected_languages
        extra_data["target_language_names"] = media_selected_target_language_names(
            session, selected_languages
        )
        if session.get("submission_ids"):
            extra_data["submission_ids"] = session["submission_ids"]
        await db_session.execute(
            update(TranscriptionTask)
            .where(TranscriptionTask.task_uuid == task_uuid)
            .values(
                pipeline_type=next_pipeline,
                status="pending",
                stage=None,
                error_message=None,
                extra_data=extra_data,
            )
        )
        await db_session.commit()

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/sup-subtitle-ai:media:asr",
            json={
                "data": {"task_uuid": task_uuid},
                "source": "Straker Translate for Slack",
            },
        )


def _configure_quote_decision(session: dict[str, Any], event: MediaWorkflowEvent):
    workflow = media_workflow_session_from_quote(session)
    if workflow is None:
        return None
    return advance_media_workflow(workflow, event)


async def accept_media_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> bool:
    """Accept Quote1 (transcription / embedding) and start the first pipeline phase.

    Returns True when processing started; False on early-exit / failure so callers
    (non-admin auto-start) can fall back to posting a retryable quote UI.
    """
    quote_id = action["value"]
    session = await get_media_quote_session(quote_id)
    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")

    if session is None:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This media quote has expired. Please request a new quote."),
        )
        return False
    if session.get("user_id") != context["user_id"]:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to accept this media quote."),
        )
        return False
    if session.get("stage") != STAGE_AWAITING_TRANSCRIPTION_ACCEPT:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This media quote is not ready to accept."),
        )
        return False

    lock_key = media_quote_lock_key(quote_id)
    lock_acquired = await redis_conn.set(lock_key, "1", ex=120, nx=True)
    if not lock_acquired:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "A request is already in progress. Please try again in a few seconds."
            ),
        )
        return False

    try:
        required_tokens = int(session.get("total_tokens") or 0)
        if not await _require_ai_token_balance(context, client, required_tokens):
            return False
        # Media processing can org-bill like AI Translate when no personal member.
        if not await require_ray_client(context, allow_org_billing=True):
            return False

        try:
            configure_decision = _configure_quote_decision(
                session, MediaWorkflowEvent.QUOTE1_ACCEPTED
            )
        except MediaWorkflowTransitionError as exc:
            notify_exception(exc)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("This media quote is not ready to accept."),
            )
            return False

        pipeline_kind = session["pipeline_kind"]
        extra_data: dict[str, Any] = {
            "slack_user_id": context["user_id"],
            "slack_team_id": context["team_id"],
            "slack_enterprise_id": context.enterprise_id,
            "slack_channel_id": session["channel_id"],
            "slack_thread_ts": session.get("thread_ts"),
            "media_quote_id": quote_id,
            "pipeline_kind": pipeline_kind,
        }
        if session.get("workflow_type"):
            extra_data["workflow_type"] = session["workflow_type"]
            extra_data["embed_source"] = bool(session.get("embed_source"))
            extra_data["embed_translated"] = bool(session.get("embed_translated"))
            extra_data["review_gate"] = bool(session.get("review_gate", True))
        # Stamp poster identity at accept so media spend usage rows always carry
        # Client Email/Name even when org-billed (RAY-81247) — same helpers as HT/channel.
        poster_email = await resolve_slack_poster_email(client, context["user_id"])
        if not poster_email:
            poster_email = await resolve_slack_user_email(
                context["user_id"],
                context["team_id"],
                context.enterprise_id,
            )
        poster_name = None
        user_info = getattr(context, "user_info", None) or context.get("user_info")
        if isinstance(user_info, dict):
            profile = (user_info.get("user") or {}).get("profile") or {}
            poster_name = (
                profile.get("real_name") or profile.get("real_name_normalized") or None
            )
        if poster_email:
            extra_data["requester_email"] = poster_email
        if poster_name:
            extra_data["client_name"] = poster_name
        # Super-group uuid for org-billed subtitle MT /mt/transaction (ISVC
        # forwards this as group_uuid so the ledger does not collapse to the
        # org uuid — same as channel/document MT).
        ray = context.get("ray")
        if ray and ray.super_group:
            billing_group = (ray.super_group[0].id or "").strip()
            if billing_group:
                extra_data["billing_group_uuid"] = billing_group

        if pipeline_kind == PIPELINE_TRANSCRIBE:
            if session.get("submission_id") is not None:
                extra_data["submission_id"] = session["submission_id"]
            # First phase is always ASR-only; intended pipeline stored in quote session.
            db_pipeline = PIPELINE_TRANSCRIBE
            wait_text = _(
                ":stopwatch: Please wait a moment while we transcribe your file."
            )
        elif pipeline_kind == PIPELINE_TRANSCRIBE_TRANSLATE:
            extra_data["target_languages"] = session.get("target_languages") or []
            extra_data["target_language_names"] = (
                session.get("target_language_names") or []
            )
            if session.get("submission_ids"):
                extra_data["submission_ids"] = session["submission_ids"]
            db_pipeline = PIPELINE_TRANSCRIBE
            wait_text = _(
                ":stopwatch: Please wait a moment while we transcribe your file. "
                "You will receive an AI Translation quote when transcription completes."
            )
        elif pipeline_kind == PIPELINE_TRANSCRIBE_TRANSLATE_EMBED:
            extra_data["target_languages"] = session.get("target_languages") or []
            extra_data["target_language_names"] = (
                session.get("target_language_names") or []
            )
            if session.get("submission_ids"):
                extra_data["submission_ids"] = session["submission_ids"]
            extra_data["original_video_file_id"] = session["file_id"]
            extra_data["original_video_download_url"] = session["download_url"]
            extra_data["original_video_file_name"] = session["file_name"]
            db_pipeline = PIPELINE_TRANSCRIBE
            wait_text = _(
                ":stopwatch: Please wait a moment while we transcribe your file. "
                "You will receive an AI Translation quote when transcription completes."
            )
        elif pipeline_kind == PIPELINE_EMBED:
            for key in (
                "original_video_file_id",
                "original_video_download_url",
                "original_video_file_name",
                "srt_file_ids",
                "language_codes",
                "target_languages",
            ):
                if session.get(key) is not None:
                    extra_data[key] = session[key]
            if session.get("submission_ids"):
                extra_data["submission_ids"] = session["submission_ids"]
            elif session.get("submission_id") is not None:
                extra_data["submission_ids"] = [session["submission_id"]]
            db_pipeline = PIPELINE_EMBED
            wait_text = _(
                ":stopwatch: Please wait a moment while we embed the uploaded "
                "subtitles into your video."
            )
        else:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("Unsupported media quote type."),
            )
            return False

        extra_data["pipeline_type"] = db_pipeline
        task_uuid = await _create_asr_from_quote_session(
            client=client,
            context=context,
            session=session,
            pipeline_type_for_db=db_pipeline,
            extra_data=extra_data,
        )

        if configure_decision is not None:
            from app.slack.media_workflow_actions import execute_media_workflow_decision

            updated = await execute_media_workflow_decision(
                client=client,
                session=session,
                decision=configure_decision,
                task_uuid=task_uuid,
            )
        else:
            next_stage = (
                STAGE_EMBEDDING
                if pipeline_kind == PIPELINE_EMBED
                else STAGE_TRANSCRIBING
            )
            updated = await update_media_quote_session(
                quote_id,
                {"stage": next_stage, "task_uuid": task_uuid},
            )
        await _update_quote_message(
            client,
            channel_id=channel_id or session.get("channel_id"),
            message_ts=message_ts or (updated or {}).get("quote_message_ts"),
            session=updated or session,
            accept_action_id=ACTION_MEDIA_QUOTE_ACCEPT,
            cancel_action_id=ACTION_MEDIA_QUOTE_CANCEL,
            actions=False,
            status_message=_("Quote accepted. Processing has started."),
        )
        await client.chat_postMessage(
            channel=str(session["channel_id"]),
            text=wait_text,
            thread_ts=session.get("thread_ts"),
        )
        return True
    except Exception as e:
        notify_exception(e)
        # Lazy import avoids circular dependency with media_pipeline_events.
        from app.ray.events.media_pipeline_events import fail_media_submissions

        await fail_media_submissions(session)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error accepting your quote, please try again."),
        )
        return False
    finally:
        await redis_conn.delete(lock_key)


async def accept_media_translation_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> bool:
    """Accept Quote2 (AI translation) and resume translate / translate+embed.

    Returns True when translation started; False on early-exit / failure.
    """
    quote_id = action["value"]
    session = await get_media_quote_session(quote_id)
    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")

    if session is None:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This translation quote has expired. Please request a new quote."),
        )
        return False
    if session.get("user_id") != context["user_id"]:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to accept this translation quote."),
        )
        return False
    if session.get("stage") != STAGE_AWAITING_TRANSLATION_ACCEPT:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This translation quote is not ready to accept."),
        )
        return False

    task_uuid = session.get("task_uuid")
    if not task_uuid:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("Missing transcription task for this quote. Please try again."),
        )
        return False

    lock_key = media_quote_lock_key(f"translate:{quote_id}")
    lock_acquired = await redis_conn.set(lock_key, "1", ex=120, nx=True)
    if not lock_acquired:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "A request is already in progress. Please try again in a few seconds."
            ),
        )
        return False

    try:
        required_tokens = int(session.get("total_tokens") or 0)
        if "selected_pairs" in session:
            required_tokens = document_mt_tokens_for_pairs(
                media_translation_quote_from_session(session),
                [str(pair) for pair in session.get("selected_pairs") or []],
            )
        if not await _require_ai_token_balance(context, client, required_tokens):
            return False

        try:
            configure_decision = _configure_quote_decision(
                session, MediaWorkflowEvent.QUOTE2_ACCEPTED
            )
        except MediaWorkflowTransitionError as exc:
            notify_exception(exc)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("This translation quote is not ready to accept."),
            )
            return False

        pipeline_kind = session["pipeline_kind"]
        if configure_decision is not None:
            from app.slack.media_workflow_actions import execute_media_workflow_decision

            updated = await execute_media_workflow_decision(
                client=client,
                session=session,
                decision=configure_decision,
                task_uuid=task_uuid,
            )
        else:
            await _resume_translate_phase(
                task_uuid=task_uuid,
                pipeline_kind=pipeline_kind,
                session=session,
            )
            updated = await update_media_quote_session(
                quote_id, {"stage": STAGE_TRANSLATING}
            )
        await _update_quote_message(
            client,
            channel_id=channel_id or session.get("channel_id"),
            message_ts=message_ts or (updated or {}).get("quote_message_ts"),
            session=updated or session,
            accept_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_ACCEPT,
            cancel_action_id=ACTION_MEDIA_TRANSLATION_QUOTE_CANCEL,
            actions=False,
            status_message=_("Quote accepted. AI translation has started."),
        )
        await client.chat_postMessage(
            channel=str(session["channel_id"]),
            text=_(
                ":stopwatch: Please wait a moment while we AI-translate your transcript."
            ),
            thread_ts=session.get("thread_ts"),
        )
        return True
    except Exception as e:
        notify_exception(e)
        # Lazy import avoids circular dependency with media_pipeline_events.
        from app.ray.events.media_pipeline_events import fail_media_submissions

        await fail_media_submissions(session)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error accepting your quote, please try again."),
        )
        return False
    finally:
        await redis_conn.delete(lock_key)


async def cancel_media_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
    is_translation_quote: bool = False,
) -> None:
    """Cancel a media Quote1 or Quote2 session."""
    quote_id = action["value"]
    session = await get_media_quote_session(quote_id)
    if session is not None and session.get("user_id") != context["user_id"]:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to cancel this media quote."),
        )
        return

    if session is not None:
        try:
            configure_decision = _configure_quote_decision(
                session,
                MediaWorkflowEvent.QUOTE2_CANCELLED
                if is_translation_quote
                else MediaWorkflowEvent.QUOTE1_CANCELLED,
            )
            cancel_stage = (
                configure_decision.session.stage.value
                if configure_decision is not None
                else STAGE_CANCELLED
            )
        except MediaWorkflowTransitionError as exc:
            notify_exception(exc)
            cancel_stage = STAGE_CANCELLED
        await update_media_quote_session(quote_id, {"stage": cancel_stage})
        # Unlock 24h dedupe so the user can resubmit after cancel.
        from app.ray.events.media_pipeline_events import fail_media_submissions

        await fail_media_submissions(session)
    await delete_media_quote_session(quote_id)

    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    cancel_text = (
        _("AI Translation quote cancelled.")
        if is_translation_quote
        else _("Media quote cancelled.")
    )
    if channel_id and message_ts:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=cancel_text,
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": cancel_text},
                }
            ],
        )


async def update_media_translation_quote_slack_message(
    client: AsyncWebClient,
    *,
    channel_id: str,
    message_ts: str,
    session: dict[str, Any],
    actions: bool = True,
    status_message: str | None = None,
) -> None:
    """Replace the original media Quote2 message in place (shared AI Translate grid)."""
    message = MediaTranslationQuoteMessage(
        session,
        actions=actions,
        status_message=status_message,
    )
    await client.chat_update(
        channel=channel_id,
        ts=message_ts,
        text=message.text,
        blocks=message.blocks,
    )


async def post_media_quote_message(
    client: AsyncWebClient,
    session: dict[str, Any],
    *,
    accept_action_id: str,
    cancel_action_id: str,
) -> str | None:
    """Post a Service Quote message and store its message_ts on the session."""
    if media_translation_quote_uses_adjust_layout(session):
        message = MediaTranslationQuoteMessage(session, actions=True)
        blocks = message.blocks
    else:
        blocks = media_quote_blocks(
            session,
            accept_action_id=accept_action_id,
            cancel_action_id=cancel_action_id,
            actions=True,
        )
    response = await client.chat_postMessage(
        channel=str(session["channel_id"]),
        text=_("Service Quote"),
        blocks=blocks,
        thread_ts=session.get("thread_ts"),
    )
    message_ts = response.get("ts")
    if message_ts:
        await update_media_quote_session(
            session["quote_id"], {"quote_message_ts": message_ts}
        )
    return message_ts


async def post_or_auto_start_media_quote(
    client: AsyncWebClient,
    context: RayContext,
    session: dict[str, Any],
    *,
    accept_action_id: str = ACTION_MEDIA_QUOTE_ACCEPT,
    cancel_action_id: str = ACTION_MEDIA_QUOTE_CANCEL,
) -> None:
    """Show Quote1 to admins; non-admins skip the quote and start processing.

    If auto-start exits early (balance/login), fall back to posting the quote so
    the submission is not stranded without a retry path.
    """
    if await user_may_receive_quotes(context.get("ray")):
        await post_media_quote_message(
            client,
            session,
            accept_action_id=accept_action_id,
            cancel_action_id=cancel_action_id,
        )
        return

    await update_media_quote_session(session["quote_id"], {"auto_proceed": True})
    refreshed = await get_media_quote_session(session["quote_id"]) or session
    started = await accept_media_quote(
        client=client,
        body={
            "channel": {"id": refreshed.get("channel_id")},
            "message": {},
            "user": {"id": refreshed.get("user_id")},
        },
        action={"value": refreshed["quote_id"]},
        context=context,
    )
    if not started:
        await post_media_quote_message(
            client,
            refreshed,
            accept_action_id=accept_action_id,
            cancel_action_id=cancel_action_id,
        )


async def auto_accept_media_translation_quote_if_needed(
    client: AsyncWebClient,
    session: dict[str, Any],
) -> bool:
    """When ``auto_proceed`` is set, start Quote2 without posting Accept UI.

    Returns True when auto-proceed started translation (caller should not post).
    Returns False when auto-proceed is unset or failed, so the caller posts Quote2.
    """
    if not session.get("auto_proceed"):
        return False

    from app.auth.connector import RayConnection, get_ray_client, get_ray_super_group

    # Prefer member client; fall back to workspace org for org-billed media.
    ray_client = await get_ray_client(
        session["user_id"],
        session["team_id"],
        session.get("enterprise_id"),
    )
    super_groups = (
        await get_ray_super_group(
            session["team_id"],
            session.get("enterprise_id"),
        )
        or []
    )
    if ray_client is None and not super_groups:
        return False

    # Build a minimal Bolt-like context; set ray after init for typing.
    context = RayContext(
        cast(
            AsyncBoltContext,
            {
                "user_id": session["user_id"],
                "team_id": session["team_id"],
                "enterprise_id": session.get("enterprise_id"),
                "channel_id": session.get("channel_id"),
            },
        )
    )
    context["ray"] = RayConnection(super_groups, ray_client)

    return await accept_media_translation_quote(
        client=client,
        body={
            "channel": {"id": session.get("channel_id")},
            "message": {},
            "user": {"id": session.get("user_id")},
        },
        action={"value": session["quote_id"]},
        context=context,
    )


# Re-export stage constants used by ray callback for Quote2.
__all__ = [
    "accept_media_quote",
    "accept_media_translation_quote",
    "auto_accept_media_translation_quote_if_needed",
    "cancel_media_quote",
    "post_media_quote_message",
    "post_or_auto_start_media_quote",
    "resume_configure_embed_phase",
    "update_media_translation_quote_slack_message",
]

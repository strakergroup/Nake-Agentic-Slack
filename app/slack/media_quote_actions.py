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
    user_may_receive_quotes,
)
from app.config import domains
from app.database import async_engines
from app.models import ASRTask, TranscriptionTask, TranscriptionTaskData
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
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
    update_media_quote_session,
)
from app.slack.middleware import require_ray_client
from app.slack.templates.messages import (
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
    if ray and ray.client is not None:
        user_tokens = await get_client_tokens(ray.client.id_token)
        if user_tokens is None:
            return False
        ai_tokens = user_tokens.ai_token
        if ai_tokens >= required_tokens:
            return True
    elif ray and ray.super_group is not None:
        client_tokens = await get_group_tokens(
            ray.super_group[0].verify_organization_uuid
        )
        if client_tokens is None:
            return False
        ai_tokens = client_tokens.ai_token
        if ai_tokens and ai_tokens >= required_tokens:
            return True

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

    # Prefer personal CRM member; otherwise org-bill like Document MT / AI Translate.
    if ray.client is not None:
        billing_client_id = ray.client.id
    elif ray.super_group:
        billing_client_id = ray.super_group[0].verify_organization_uuid
    else:
        raise ValueError(
            "A LanguageCloud member or workspace organisation is required "
            "to start media processing"
        )
    if not billing_client_id:
        raise ValueError("No billing client available for media processing")

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
    if pipeline_kind == PIPELINE_TRANSCRIBE_TRANSLATE_EMBED:
        next_pipeline = "translate_embed"
    else:
        next_pipeline = "translate_only"

    async with AsyncSession(async_engines["sitecommons"]) as db_session:
        task = await db_session.get(TranscriptionTask, task_uuid)
        if not task:
            raise ValueError(f"Transcription task {task_uuid} not found")
        extra_data = dict(task.extra_data or {})
        extra_data["media_quote_id"] = session["quote_id"]
        extra_data["pipeline_kind"] = pipeline_kind
        extra_data["target_languages"] = session.get("target_languages") or []
        extra_data["target_language_names"] = session.get("target_language_names") or []
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

        next_stage = (
            STAGE_EMBEDDING if pipeline_kind == PIPELINE_EMBED else STAGE_TRANSCRIBING
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
        if not await _require_ai_token_balance(context, client, required_tokens):
            return False

        pipeline_kind = session["pipeline_kind"]
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
        await update_media_quote_session(quote_id, {"stage": STAGE_CANCELLED})
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


async def post_media_quote_message(
    client: AsyncWebClient,
    session: dict[str, Any],
    *,
    accept_action_id: str,
    cancel_action_id: str,
) -> str | None:
    """Post a Service Quote message and store its message_ts on the session."""
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
]

from __future__ import annotations

from typing import Any

from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.redis import redis_conn
from app.saq_jobs import enqueue_document_mt_submission
from app.slack.buglog_notifier import notify_exception
from app.slack.document_mt_quotes import (
    QUOTE_STATUS_QUOTED,
    delete_document_mt_quote_session,
    document_mt_quote_lock_key,
    get_document_mt_quote_session,
    mark_document_mt_quote_accepted,
    update_document_mt_quote_session,
)
from app.slack.templates.messages import DocumentMtQuoteMessage
from app.translate import _


async def accept_document_mt_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> None:
    """Accept a cached document MT quote and enqueue the actual translation."""
    quote_id = action["value"]
    session = await get_document_mt_quote_session(quote_id)
    if session is None:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This translation quote has expired. Please request a new quote."),
        )
        return
    if session.get("user_id") != context["user_id"]:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to accept this translation quote."),
        )
        return
    if session.get("status") != QUOTE_STATUS_QUOTED:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This translation quote is not ready to accept."),
        )
        return

    lock_key = document_mt_quote_lock_key(quote_id)
    lock_acquired = await redis_conn.set(lock_key, "1", ex=60, nx=True)
    if not lock_acquired:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "A request is already in progress. Please try again in a few seconds."
            ),
        )
        return

    marked_accepted = False
    try:
        accepted_session = await mark_document_mt_quote_accepted(quote_id)
        if accepted_session is None:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "This translation quote has expired. Please request a new quote."
                ),
            )
            return
        marked_accepted = True
        await enqueue_document_mt_submission(
            user_id=context["user_id"],
            team_id=str(accepted_session.get("team_id") or context["team_id"]),
            enterprise_id=accepted_session.get("enterprise_id")
            or context.enterprise_id,
            channel_id=str(accepted_session["channel_id"]),
            files=[],
            source_language=accepted_session.get("source_language"),
            target_languages=[
                str(language) for language in accepted_session["target_languages"]
            ],
            quote_id=quote_id,
        )
        message = DocumentMtQuoteMessage(accepted_session, actions=False)
        channel_id = body.get("channel", {}).get("id") or accepted_session["channel_id"]
        message_ts = body.get("message", {}).get("ts")
        if channel_id and message_ts:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text=message.text,
                blocks=message.blocks,
            )
        await client.chat_postMessage(
            channel=str(accepted_session["channel_id"]),
            text=_(
                "Your document translation has been submitted. You will be notified when it is ready."
            ),
        )
    except Exception as e:
        notify_exception(e)
        if marked_accepted:
            # Nothing was submitted or billed, so put the quote back in a state
            # the Accept button can act on — otherwise the retry we ask for hits
            # the "not ready to accept" guard and the upload is stranded.
            await update_document_mt_quote_session(
                quote_id, {"status": QUOTE_STATUS_QUOTED}
            )
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("There was an error accepting your quote, please try again."),
        )
    finally:
        await redis_conn.delete(lock_key)


async def cancel_document_mt_quote(
    *,
    client: AsyncWebClient,
    body: dict[str, Any],
    action: dict[str, Any],
    context: RayContext,
) -> None:
    """Cancel a cached document MT quote."""
    quote_id = action["value"]
    session = await get_document_mt_quote_session(quote_id)
    if session is not None and session.get("user_id") != context["user_id"]:
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("You do not have permission to cancel this translation quote."),
        )
        return
    if session is not None and session.get("status") != QUOTE_STATUS_QUOTED:
        # A stale Cancel click after Accept would delete a session whose
        # submission is already queued, and the worker would then abort with
        # "this quote has expired".
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("This translation quote can no longer be cancelled."),
        )
        return

    await delete_document_mt_quote_session(quote_id)
    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    if channel_id and message_ts:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text=_("AI Translate quote cancelled."),
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": _("AI Translate quote cancelled."),
                    },
                }
            ],
        )

"""Handlers for evaluate / human-translation quote flows."""

import json
from typing import Any, Dict, Optional

from pydantic import ValidationError
from slack_bolt.kwargs_injection.async_args import AsyncAck
from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_client_evaluation_job,
    get_job_pricing,
)
from app.auth.connector import RayContext
from app.ray.utils import is_ibm_enterprise
from app.redis import redis_conn
from app.saq_jobs import enqueue_evaluation_submission
from app.slack.buglog_notifier import notify_exception
from app.slack.document_mt_quote_actions import accept_document_mt_quote
from app.slack.document_mt_quote_adjustment import (
    DOCUMENT_MT_QUOTE_ADJUST_ACTION_ID,
    DOCUMENT_MT_QUOTE_KIND,
)
from app.slack.evaluation_ai_adjustment import (
    ai_scope_from_job,
    filter_job_to_pairs,
    quote_message_context_from_body,
    selected_pairs_from_view,
)
from app.slack.evaluation_ai_quote_modal_service import (
    populate_ai_quote_adjustment_modal,
    refresh_ai_quote_adjustment_cost,
)
from app.slack.evaluation_ai_quote_submit_service import persist_ai_quote_adjustment
from app.slack.evaluation_combined_quotes import (
    STANDALONE_HT_TOTAL_COST_LABEL,
    WORST_CASE_QE_QUALITY_TIER,
    active_quote_cost_rows,
)
from app.slack.evaluation_pdf_quote_actions import accept_pdf_evaluate_quote
from app.slack.evaluation_quote_actions import accept_ai_translation_quote
from app.slack.evaluation_quotes import get_evaluate_quote_session
from app.slack.file_submissions import slack_file_submission_payload
from app.slack.language_validation import get_conflicting_target_language_labels
from app.slack.listener_actions import (
    get_accessible_slack_files,
    notify_missing_slack_files,
    verify_job_submission_lock_key,
)
from app.slack.middleware import populate_ray_connection, require_ray_client
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from app.slack.pdf_evaluate_quotes import PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID
from app.slack.templates.messages import LoginMessage
from app.slack.templates.models import (
    EVALUATE_JOB_REFERENCE_FALLBACK,
    EvaluateJobForm,
    convert_pydantic_to_slack_error,
)
from app.slack.templates.views import (
    human_job_modal,
    verify_job_modal,
    verify_quote_summary_modal,
)
from app.translate import _


async def handle_evaluate_job_submit(
    view: Optional[Dict[str, Any]],
    client: AsyncWebClient,
    ack: AsyncAck,
    context: RayContext,
):
    """Evaluate job. Triggered from the Evaluate form view."""
    if not view:
        await ack(response_action="clear")
        return
    channel_id = view.get("private_metadata")
    if not channel_id:
        await ack(response_action="clear")
        return
    if view.get("callback_id") == "evaluate_job" and is_ibm_enterprise(
        context.enterprise_id
    ):
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "Quality Evaluation is now available through the Human Translation quote flow, not as a standalone Slack submission."
            ),
        )
        return

    form_data = view["state"]["values"]
    try:
        form = EvaluateJobForm.parse_human_job_form(form_data, view["callback_id"])
    except ValidationError as e:
        errors = convert_pydantic_to_slack_error(e)
        field_to_block = {"target_langs_uuid": "target_langs"}
        errors = {field_to_block.get(k, k): v for k, v in errors.items()}
        await ack(response_action="errors", errors=errors)
        return

    conflicting_target_labels = await get_conflicting_target_language_labels(
        form.source_lang_uuid, form.target_langs_uuid
    )
    if conflicting_target_labels:
        languages = ", ".join(conflicting_target_labels)
        await ack(
            response_action="errors",
            errors={
                "target_langs": _(
                    "The source language cannot be the same language or regional variant as a target language. Please remove: {languages}."
                )
            },
        )
        return

    await ack(response_action="clear")
    if await require_ray_client(context, prompt_login=True):
        if view["callback_id"] == "evaluate_job":
            msg = _(
                "Analyzing your content. You will receive an AI Translation quote shortly."
            )
        elif form.workflow_options:
            msg = _(
                "Your request is being processed. You will receive a summary to review before you finalise the order."
            )
        else:
            msg = _(
                "You've successfully submitted your document(s) for quality evaluation."
            )
        try:
            file_payloads = [
                slack_file_submission_payload(
                    file_id=file.id,
                    title=file.title,
                    size=file.size,
                )
                for file in form.files
            ]
            accessible_files, missing_files = await get_accessible_slack_files(
                client, file_payloads
            )
            if missing_files:
                await notify_missing_slack_files(
                    client, context["user_id"], missing_files
                )
            accessible_file_ids = {
                str(file["id"]) for file in accessible_files if file.get("id")
            }
            file_payloads = [
                file for file in file_payloads if str(file["id"]) in accessible_file_ids
            ]
            if not file_payloads:
                return

            verify_reference = (
                EVALUATE_JOB_REFERENCE_FALLBACK
                if view["callback_id"] == "evaluate_job_human"
                else form.reference
            )
            await client.chat_postMessage(channel=channel_id, text=msg)
            await enqueue_evaluation_submission(
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=channel_id,
                files=file_payloads,
                target_langs_uuid=form.target_langs_uuid,
                reference=verify_reference,
                source_lang_uuid=form.source_lang_uuid,
                workflow_uuid=form.workflow_options,
                job_notes=form.job_notes or "",
            )
        except VerifyAPIError:
            await client.chat_postMessage(
                channel=channel_id,
                text=_(
                    "There was an error processing your request. You do not have permission to perform this action. Please contact your team administrator."
                ),
            )
        except Exception as e:
            notify_exception(e)
            if form.workflow_options:
                error_msg = _(
                    "There was an error submitting your human translation request, please try again."
                )
            else:
                error_msg = _(
                    "There was an error submitting your quality evaluation request, please try again."
                )
            await client.chat_postMessage(
                channel=channel_id,
                text=error_msg,
            )


async def handle_evaluate_job_action(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Evaluate job. Triggered from the Evaluate Job button."""
    action_data = json.loads(action.get("value", ""))
    job_type = action_data.get("job_type", "evaluate")
    login_variation = (
        LoginMessage.HUMAN_TRANSLATION
        if job_type == "human"
        else LoginMessage.QUALITY_EVALUATION
    )
    files = action_data.get("files", [])
    channel_id = action_data.get("channel_id")
    if not files:
        await populate_ray_connection(context)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_(
                "No files found in the message. Please try reupload files to translate."
            ),
        )
        return

    # Open before Ray middleware I/O — same pattern as RAY-72999 quote modals.
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, variation=login_variation):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        if job_type != "human" and is_ibm_enterprise(context.enterprise_id):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Quality Evaluation"),
                    _(
                        "Quality Evaluation is now available through the Human Translation quote flow, not as a standalone Slack submission."
                    ),
                ),
            )
            return
        await safe_views_update(
            client,
            view_id,
            human_job_modal(
                channel_id,
                files,
                is_ibm_enterprise(context.enterprise_id),
                job_type,
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_verify_job_modal_open(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Open modal for human translation. Triggered from the Send for human translation button."""
    job_uuid = action["value"]
    message_ts = body.get("message", {}).get("ts")
    quote_channel_id = (
        body.get("channel", {}).get("id")
        or body.get("message", {}).get("channel")
        or context.get("channel_id")
    )
    # Open before Redis/Ray I/O — RAY-72999 loading-modal pattern.
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        lock_key = verify_job_submission_lock_key(job_uuid)
        if await redis_conn.get(lock_key):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Request in progress"),
                    _(
                        "A request is already in progress. Please try again in a few seconds."
                    ),
                ),
            )
            return
        if not await require_ray_client(context, prompt_login=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        ray_client = context["ray"].client if context["ray"] is not None else None
        if ray_client is None:
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        job = await get_client_evaluation_job(ray_client, job_uuid)
        session = await get_evaluate_quote_session(job_uuid)
        quote_snapshot = (session or {}).get("quote_snapshot") or {}
        is_combined_qe_human_quote = bool(
            quote_snapshot.get("auto_submit_human_job")
            and action["action_id"] == "quote_summary_modal_open"
        )
        if is_combined_qe_human_quote:
            ai_scope = [
                str(value)
                for value in quote_snapshot.get("ai_translation_file_and_languages")
                or ai_scope_from_job(job["data"])
            ]
            job["data"] = filter_job_to_pairs(job["data"], ai_scope)
        langs = [lang["uuid"] for lang in job["data"]["target_languages"]]
        qe_token_cost = int(quote_snapshot.get("token_cost") or 0)
        costs = await get_job_pricing(
            ray_client,
            job_uuid,
            [file["file_uuid"] for file in job["data"]["source_files"]],
            langs,
            assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER
            if is_combined_qe_human_quote
            else None,
        )
        pricing_costs = (
            active_quote_cost_rows(costs["data"], ai_scope)
            if is_combined_qe_human_quote
            else costs["data"]
        )
        qe_costs = (
            active_quote_cost_rows(
                quote_snapshot.get("qe_additional_costs") or [],
                ai_scope,
            )
            if is_combined_qe_human_quote
            else None
        )
        final_view = (
            verify_quote_summary_modal(
                job["data"],
                pricing_costs,
                message_ts,
                quote_channel_id,
                additional_costs=qe_costs,
                metadata={
                    "combined_qe_human_quote": True,
                    "qe_token_cost": qe_token_cost,
                }
                if is_combined_qe_human_quote
                else None,
                # HT Adjust mirrors its quote: no tiers, and no savings suffix.
                show_quality_discount=False,
                show_savings=False,
                total_cost_label=None
                if is_combined_qe_human_quote
                else STANDALONE_HT_TOTAL_COST_LABEL,
                embed_additional_costs_in_line_price=is_combined_qe_human_quote,
            )
            if action["action_id"] == "quote_summary_modal_open"
            else verify_job_modal(
                job["data"], pricing_costs, message_ts, quote_channel_id
            )
        )
        await safe_views_update(client, view_id, final_view)
    except VerifyAPIError:
        await safe_views_update(
            client,
            view_id,
            status_modal(
                _("Unauthorized"),
                _(
                    "You do not have permission to access this verification job. Please contact your team administrator."
                ),
            ),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_ai_quote_adjust(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Open the staged AI quote adjustment modal before any slow I/O."""
    view_id = await open_loading_modal(client, body["trigger_id"])
    quote_id = str(action["value"])
    quote_kind = "evaluate"
    if action["action_id"] == PDF_EVALUATE_QUOTE_ADJUST_ACTION_ID:
        quote_kind = "pdf_prequote"
    elif action["action_id"] == DOCUMENT_MT_QUOTE_ADJUST_ACTION_ID:
        quote_kind = DOCUMENT_MT_QUOTE_KIND
    channel_id, message_ts = quote_message_context_from_body(body)
    try:
        await populate_ai_quote_adjustment_modal(
            client,
            view_id=view_id,
            quote_id=quote_id,
            quote_kind=quote_kind,
            user_id=body["user"]["id"],
            context=context,
            channel_id=channel_id,
            message_ts=message_ts,
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_ai_quote_adjust_selection(
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    """Refresh the modal total from the currently selected file/language pairs."""
    view = body["view"]
    try:
        metadata = json.loads(view.get("private_metadata") or "{}")
        await refresh_ai_quote_adjustment_cost(
            client,
            view=view,
            quote_id=str(metadata.get("quote_id") or ""),
            quote_kind=str(metadata.get("quote_kind") or ""),
        )
    except Exception as e:
        notify_exception(e)


async def handle_ai_quote_adjust_submit(
    ack: AsyncAck,
    body: Dict[str, Any],
    client: AsyncWebClient,
    context: RayContext,
):
    """Persist an AI quote adjustment and refresh the original quote.

    Deselecting every file/language pair is allowed: the quote message is
    updated to show all rows as cancelled and acceptance is skipped.
    """
    view = body["view"]
    selected_pairs = selected_pairs_from_view(view)
    await ack(response_action="clear")

    metadata = json.loads(view.get("private_metadata") or "{}")
    quote_id = str(metadata.get("quote_id") or "")
    quote_kind = str(metadata.get("quote_kind") or "")
    channel_id = str(metadata.get("channel_id") or context.get("channel_id") or "")
    message_ts = str(metadata.get("message_ts") or "") or None
    persisted = await persist_ai_quote_adjustment(
        client,
        quote_id=quote_id,
        quote_kind=quote_kind,
        selected_pairs=selected_pairs,
        user_id=body["user"]["id"],
        context=context,
        channel_id=channel_id or None,
        message_ts=message_ts,
    )
    if not persisted or not selected_pairs:
        return
    if quote_kind == "pdf_prequote":
        await accept_pdf_evaluate_quote(
            client,
            body,
            quote_id,
            context,
            selected_pairs_override=selected_pairs,
        )
    elif quote_kind == DOCUMENT_MT_QUOTE_KIND:
        await accept_document_mt_quote(
            client=client,
            body=body,
            action={"value": quote_id},
            context=context,
        )
    else:
        await accept_ai_translation_quote(
            client=client,
            body=body,
            action={"value": quote_id},
            context=context,
            selected_pairs_override=selected_pairs,
        )

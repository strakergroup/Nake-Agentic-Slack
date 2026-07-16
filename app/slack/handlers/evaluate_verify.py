"""Handlers for verification quote acceptance and checkbox recalculation."""

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict

from slack_sdk.web.async_client import AsyncWebClient

from app.api.verify import (
    VerifyAPIError,
    get_client_evaluation_job,
    get_evaluation_job_quote,
    get_job_pricing,
    proceed_quality_evaluation,
)
from app.auth.connector import RayContext
from app.constants import (
    EVALUATE_SERVICE_QUALITY_EVALUATION,
    HUMAN_EVALUATION_WORKFLOW_UUID,
)
from app.redis import redis_conn
from app.slack.buglog_notifier import notify_exception
from app.slack.evaluation_ai_adjustment import mark_out_of_scope_pairs_cancelled
from app.slack.evaluation_combined_quotes import (
    COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID,
    PRE_QE_QUOTE_DISPLAY,
    WORST_CASE_QE_QUALITY_TIER,
    active_quote_cost_rows,
    combined_human_job_quote_message,
    job_file_uuids,
    job_target_language_uuids,
    qe_additional_cost,
    qe_additional_costs_from_quote,
)
from app.slack.evaluation_quotes import (
    QE_TERMINAL_STAGES,
    STAGE_ACCEPTED_QE,
    STAGE_AWAITING_QE,
    STAGE_PROCESSING_QE,
    get_evaluate_quote_session,
    save_evaluate_quote_session,
    update_evaluate_quote_stage,
)
from app.slack.listener_actions import (
    VERIFY_JOB_SUBMISSION_LOCK_TTL_SECONDS,
    submit_verification_job,
    update_human_job_quote_message,
    verify_job_submission_lock_key,
)
from app.slack.utils import calculate_total_estimated_days
from app.translate import _


async def handle_quote_accept_all(
    client: AsyncWebClient,
    body: Dict[str, Any],
    action: Dict[str, Any],
    context: RayContext,
):
    """Accept all available language/file combinations for verification."""
    timestamp = body.get("message", {}).get("ts")
    job_uuid = action["value"]
    channel_id = context["channel_id"]
    lock_key = verify_job_submission_lock_key(job_uuid)

    lock_acquired = await redis_conn.set(
        lock_key,
        "1",
        ex=VERIFY_JOB_SUBMISSION_LOCK_TTL_SECONDS,
        nx=True,
    )
    if not lock_acquired:
        return

    try:
        assert context["ray"] is not None
        assert context["ray"].client is not None
        job = await get_client_evaluation_job(context["ray"].client, job_uuid)
    except VerifyAPIError:
        await redis_conn.delete(lock_key)
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "You do not have permission to access this verification job. You do not have permission to perform this action. Please contact your team administrator."
            ),
        )
        return
    except Exception as e:
        notify_exception(e)
        await redis_conn.delete(lock_key)
        await client.chat_postMessage(
            channel=channel_id,
            text=_("There was an error processing your request. Please try again."),
        )
        return

    # Get all available language/file combinations that are not in progress
    selected_languages = []
    for source_file in job["data"]["source_files"]:
        # Skip if already has a human job status
        for target_file in source_file.get("target_files", []):
            if not target_file.get("human_job_status"):
                selected_languages.append(
                    f"{source_file['file_uuid']}:{target_file['language_uuid']}"
                )
            if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
                target_file["human_job_status"] = "Submitted"

    if timestamp and job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
        costs = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            [file["file_uuid"] for file in job["data"]["source_files"]],
            [lang["uuid"] for lang in job["data"]["target_languages"]],
        )
        await update_human_job_quote_message(
            client,
            channel_id=channel_id,
            message_ts=timestamp,
            job=job["data"],
            costs=costs["data"],
            actions=False,
            status_message=_("Submitting quote..."),
        )

    await submit_verification_job(
        client=client,
        context=context,
        job_uuid=job_uuid,
        selected_languages=selected_languages,
        user_id=body["user"]["id"],
        timestamp=timestamp,
        job=job,
        channel_id=channel_id,
    )


async def handle_verify_job_submission(
    body: Dict[str, Any], client: AsyncWebClient, context: RayContext
):
    # Extract the private metadata (job UUID and message timestamp)
    private_metadata = json.loads(body["view"]["private_metadata"])
    job_uuid = private_metadata.get("job_uuid")
    message_ts = private_metadata.get("timestamp", None)
    quote_channel_id = private_metadata.get("channel_id") or context.get("channel_id")
    # lock so that if submission is in progress, it will not be submitted again
    lock_key = verify_job_submission_lock_key(job_uuid)
    lock_acquired = await redis_conn.set(
        lock_key,
        "1",
        ex=VERIFY_JOB_SUBMISSION_LOCK_TTL_SECONDS,
        nx=True,
    )
    if not lock_acquired:
        await client.chat_postMessage(
            channel=body["user"]["id"],
            text=_(
                "This request is no longer available. Please resubmit your documents in the message pane below"
            ),
        )
        return
    assert context["ray"] is not None
    assert context["ray"].client is not None

    job = await get_client_evaluation_job(context["ray"].client, job_uuid)
    target_languages = job["data"]["target_languages"]

    # Extract the selected checkbox values from input blocks
    selected_languages = []
    for source_file in job["data"]["source_files"]:
        for lang in target_languages:
            block_id = (
                f"verification_checkbox_{lang['uuid']}_{source_file['file_uuid']}"
            )
            if block_id in body["view"]["state"]["values"]:
                selected_options = body["view"]["state"]["values"][block_id][
                    "verification_checkbox_action"
                ]["selected_options"]
                selected_languages.extend(
                    [
                        ":".join(option["value"].split(":")[:2])
                        for option in selected_options
                    ]
                )
                if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
                    for target_lang_option in selected_languages:
                        parts = target_lang_option.rsplit(":", 1)
                        file_uuid, lang_uuid = parts[0], parts[1]
                        # Only mark if this selection is for the current source file
                        if file_uuid == source_file["file_uuid"]:
                            for target_file in source_file["target_files"]:
                                if target_file["language_uuid"] == lang_uuid:
                                    target_file["human_job_status"] = "Submitted"
                                    break

    if private_metadata.get("combined_qe_human_quote"):
        if not selected_languages:
            await redis_conn.delete(lock_key)
            await client.chat_postMessage(
                channel=body["user"]["id"],
                text=_("Please select at least one file and target language."),
            )
            return

        session = await get_evaluate_quote_session(job_uuid)
        if session and session.get("stage") in QE_TERMINAL_STAGES:
            await redis_conn.delete(lock_key)
            return

        quote = await get_evaluation_job_quote(
            context["ray"].client,
            job_uuid,
            [EVALUATE_SERVICE_QUALITY_EVALUATION],
            file_and_languages=selected_languages,
        )
        services_costs = quote.get("services_costs") or {}
        qe_token_cost = int(
            services_costs.get(
                EVALUATE_SERVICE_QUALITY_EVALUATION, quote.get("token", 0)
            )
        )
        costs = await get_job_pricing(
            context["ray"].client,
            job_uuid,
            job_file_uuids(job["data"]),
            job_target_language_uuids(job["data"]),
            assumed_quality_tier=WORST_CASE_QE_QUALITY_TIER,
        )
        selected_costs = active_quote_cost_rows(costs["data"], selected_languages)
        qe_costs = qe_additional_costs_from_quote(
            quote,
            selected_pairs=selected_languages,
        )
        if not qe_costs:
            qe_costs = qe_additional_cost(qe_token_cost, selected_costs)
        # Keep full language grid with Cancelled rows. Filtering pairs out here
        # leaves union target_languages and produces phantom USD$0.00 lines when
        # each file keeps a different language.
        selected_job_data = mark_out_of_scope_pairs_cancelled(
            job["data"],
            selected_languages,
        )
        quote_snapshot = dict((session or {}).get("quote_snapshot") or {})
        quote_snapshot.update(
            {
                "service": EVALUATE_SERVICE_QUALITY_EVALUATION,
                "token_cost": qe_token_cost,
                "service_label": _("Quality Evaluation + Human Translation"),
                "accept_action_id": COMBINED_QE_HUMAN_QUOTE_ACCEPT_ACTION_ID,
                "assumed_quality_tier": WORST_CASE_QE_QUALITY_TIER,
                "auto_submit_human_job": True,
                "selected_languages": selected_languages,
                "quality_evaluation_file_and_languages": selected_languages,
                "human_translation_file_and_languages": selected_languages,
                "qe_additional_costs": qe_costs,
            }
        )
        await save_evaluate_quote_session(
            job_uuid,
            channel_id=quote_channel_id or context["channel_id"],
            user_id=body["user"]["id"],
            team_id=context["team_id"],
            stage=STAGE_PROCESSING_QE,
            quote_snapshot=quote_snapshot,
            message_ts=message_ts,
        )
        if message_ts and quote_channel_id:
            message = combined_human_job_quote_message(
                selected_job_data,
                selected_costs,
                qe_token_cost=qe_token_cost,
                qe_additional_costs=qe_costs,
                actions=False,
                status_message=_("Accepting quote..."),
                allow_adjust=False,
                download_translations_job_uuid=job_uuid,
                **PRE_QE_QUOTE_DISPLAY,
            )
            await client.chat_update(
                channel=quote_channel_id,
                ts=message_ts,
                text=message.text,
                blocks=message.blocks,
            )

        try:
            await proceed_quality_evaluation(
                context["ray"].client,
                job_uuid,
                token_cost=qe_token_cost,
                human_translation_file_and_languages=selected_languages,
                quality_evaluation_file_and_languages=selected_languages,
            )
        except Exception:
            await update_evaluate_quote_stage(job_uuid, STAGE_AWAITING_QE)
            await redis_conn.delete(lock_key)
            raise

        await update_evaluate_quote_stage(job_uuid, STAGE_ACCEPTED_QE)
        if message_ts and quote_channel_id:
            message = combined_human_job_quote_message(
                selected_job_data,
                selected_costs,
                qe_token_cost=qe_token_cost,
                qe_additional_costs=qe_costs,
                actions=False,
                status_message=_(
                    "Quote accepted! Submitting for human translation and "
                    "calculating your final discount based on AI quality..."
                ),
                allow_adjust=False,
                download_translations_job_uuid=job_uuid,
                **PRE_QE_QUOTE_DISPLAY,
            )
            await client.chat_update(
                channel=quote_channel_id,
                ts=message_ts,
                text=message.text,
                blocks=message.blocks,
            )
        return

    if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
        for source_file in job["data"]["source_files"]:
            for target_file in source_file["target_files"]:
                if target_file.get("human_job_status") != "Submitted":
                    target_file["human_job_status"] = "Cancelled"

    # update origial message ts to remove buttons
    # fetch original message
    await submit_verification_job(
        client=client,
        context=context,
        job_uuid=job_uuid,
        selected_languages=selected_languages,
        user_id=body["user"]["id"],
        timestamp=message_ts,
        job=job,
        channel_id=quote_channel_id,
    )


async def handle_verification_checkbox(body, client, action):
    try:
        view_id = body["view"]["id"]
        action_ts = action.get("action_ts", "0")
        # Check if this is the latest action for this view
        latest_action_key = f"latest_action_{view_id}"
        latest_action_ts = await redis_conn.get(latest_action_key)

        if latest_action_ts and float(latest_action_ts) > float(action_ts):
            # A more recent action is already being processed, skip this one
            return

        # Set this as the latest action
        await redis_conn.set(latest_action_key, action_ts, ex=2)  # 2 second expiry

        # Use Redis lock to ensure only one views_update happens at a time
        lock_key = f"view_update_lock_{view_id}"
        lock_acquired = await redis_conn.set(
            lock_key, action_ts, ex=2, nx=True
        )  # 2 second lock, only if not exists

        if not lock_acquired:
            # Another update is in progress, wait for it to complete
            # Wait up to 5 seconds for the lock to be released
            for _ in range(20):  # 20 * 0.1 = 2 seconds
                await asyncio.sleep(0.1)
                if await redis_conn.get(lock_key) is None:
                    break
            else:
                return

            # Re-check if this is still the latest action after lock acquisition
            latest_action_ts = await redis_conn.get(latest_action_key)
            if latest_action_ts and float(latest_action_ts) > float(action_ts):
                # A more recent action is already being processed, skip this one
                return
            else:
                # We are still the latest action, try to acquire the lock
                lock_acquired = await redis_conn.set(
                    lock_key, action_ts, ex=2, nx=True
                )  # 2 second lock, only if not exists

                if not lock_acquired:
                    # Still can't acquire lock, give up
                    return

        try:
            # Parse all selected options from the state values
            selected_options = []
            state_values = body["view"]["state"]["values"]
            # Iterate through all block IDs that contain verification_checkbox_action
            for block_id, block_data in state_values.items():
                if "verification_checkbox_action" in block_data:
                    checkbox_data = block_data["verification_checkbox_action"]
                    if checkbox_data.get("type") == "checkboxes":
                        selected_options.extend(
                            checkbox_data.get("selected_options", [])
                        )

            # Calculate total cost from selected options. The optional fifth value
            # is a displayed per-target extra such as the distributed QE fee.
            total_cost = 0.0
            for option in selected_options:
                match = re.search(r"USD\$([\d.]+)", option["text"]["text"])
                if match:
                    total_cost += float(match.group(1))
                parts = option["value"].split(":")
                if len(parts) > 4:
                    total_cost += float(parts[4])
            total_savings = sum(
                float(option["value"].split(":")[3])
                for option in selected_options
                if len(option["value"].split(":")) > 3
            )
            total_savings = sum(
                float(option["value"].split(":")[3])
                for option in selected_options
                if len(option["value"].split(":")) > 3
            )

            private_metadata_raw = body["view"].get("private_metadata") or "{}"
            try:
                private_metadata = json.loads(private_metadata_raw)
            except json.JSONDecodeError:
                private_metadata = {}
            if not isinstance(private_metadata, dict):
                private_metadata = {}
            if private_metadata.get("combined_qe_human_quote"):
                from app.slack.evaluation_combined_quotes import qe_total_usd

                qe_token_cost = int(private_metadata.get("qe_token_cost") or 0)
                total_savings = max(
                    total_savings - qe_total_usd(qe_token_cost),
                    0.0,
                )

            # Calculate total estimated time from selected options (Verify: global max)
            total_estimated_days = calculate_total_estimated_days(
                [float(option["value"].split(":")[2]) for option in selected_options]
            )

            # Calculate completion date
            completion_date = datetime.now() + timedelta(days=total_estimated_days)
            formatted_date = completion_date.strftime("%d %B %Y")

            # Update the view
            view = body["view"]
            blocks = view["blocks"]

            # Find and update the total cost block
            for block in blocks:
                if block.get("block_id") == "total_cost_block":
                    existing_text = block["text"]["text"]
                    localized_prefix = existing_text.split("USD")[0]
                    total_text = f"{localized_prefix}USD ${total_cost:.2f}"
                    if total_savings > 0:
                        total_text += f" (saved ${total_savings:.2f})"
                    block["text"]["text"] = total_text
                    break

            # Find and update the total estimated time block
            for block in blocks:
                if block.get("block_id") == "total_estimated_time_block":
                    existing_text = block["text"]["text"]
                    prefix, _ = existing_text.split(":", 1)
                    block["text"]["text"] = f"{prefix}: {formatted_date}"
                    break

            await client.views_update(
                view_id=view["id"],
                view={
                    "type": "modal",
                    "title": view["title"],
                    "blocks": blocks,
                    "close": view["close"],
                    "submit": view["submit"],
                    "private_metadata": view["private_metadata"],
                    "callback_id": view["callback_id"],
                },
            )
        except Exception as e:
            notify_exception(e)
        finally:
            # Always release the lock when done
            await redis_conn.delete(lock_key)

    except Exception as e:
        notify_exception(e)
        raise e

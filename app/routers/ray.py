import hashlib
import logging
from dataclasses import replace
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.api.models import MtTranslationExtraData
from app.api.verify import (
    get_evaluation_job,
    get_job_pricing,
)
from app.auth.connector import (
    build_spend_idempotency_key,
    get_ray_client,
    get_ray_connection,
)
from app.constants import (
    EVALUATE_SERVICE_AI_TRANSLATION,
    HUMAN_EVALUATION_WORKFLOW_UUID,
    HUMAN_VERIFICATION_WORKFLOW_UUID,
)
from app.ray.submissions import SubmissionStatus, updated_submission_status
from app.ray.utils import (
    is_ibm_enterprise,
    set_user_language,
)
from app.slack.buglog_notifier import notify_exception, notify_message
from app.transcriber_tasks.tasks import get_transcription_task
from app.translate import _

from ..auth.connector import (
    SlackUser,
    get_client_access_tokens,
    get_client_type,
    get_demo_link,
    get_group_admin_slack_users,
    get_job_group_quote_settings,
    get_slack_user,
    is_verify_job,
    validate_api_callback_signature,
)
from ..dependencies import RayEvent, RayEventAuth, get_ray_event_auth
from ..ray.events.evaluate_quote_events import (
    claim_evaluate_complete_notification,
    post_evaluate_service_quote,
)
from ..ray.events.logging import (
    post_channel_translation_notification,
    post_notification,
    post_notification_ephemeral,
)
from ..ray.events.media_pipeline_events import (
    format_callback_error,
    get_language_name_by_uuid,
    handle_transcribe_embed_pipeline,
    handle_transcription_complete,
    handle_translation_complete,
    mark_stage_processed,
    order_translations_by_target_language_order,
    resolve_event_thread_ts,
    spend_embedding_credits,
    spend_transcription_credits,
    spend_translation_credits,
    update_submission_status,
    update_tokens_consumed,
)
from ..ray.events.models import (
    Balance,
    ClientApprovedEvent,
    ClientGroup,
    ClientSignupEvent,
    DocumentMtQuoteResponseSchema,
    JobQuoteAcceptedEvent,
    JobQuoteCancelledEvent,
    JobQuoteCreatedEvent,
    JobStatusChangedEvent,
    JobTranscribedEvent,
    MtErrorResponseSchema,
    MtSuccessResponseSchema,
    SlackAccountConnectedEvent,
)
from ..saq_jobs.dispatch import (
    enqueue_inline_mt_billing,
    enqueue_mt_success_upload,
    enqueue_verify_complete_upload,
)
from ..slack.document_mt_quotes import apply_document_mt_quote_result
from ..slack.evaluation_combined_quotes import (
    handle_combined_qe_complete,
    job_file_uuids,
    job_target_language_uuids,
    post_combined_qe_human_quote,
)
from ..slack.templates.messages import (
    AutoTranslationMessage,
    ClientApprovedEventMessage,
    ClientSignupEventAdminMessage,
    ClientSignupEventMessage,
    DocComplexityErrorMessage,
    DocInvalidPdfErrorMessage,
    DocMtMessage,
    DocParseErrorMessage,
    DocumentMtQuoteMessage,
    EvaluateAiOnlyCompleteMessage,
    EvaluateErrorMessage,
    EvaluateSuccessMessage,
    HumanJobQuoteMessage,
    JobCancelledEventMessage,
    JobCompletedEventMessage,
    JobCreationMessage,
    JobQuoteAcceptedEventMessage,
    JobQuoteCancelledEventMessage,
    JobQuotedEventMessage,
    JobStatusChangedEventMessage,
    MachineTranslationMessage,
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
    SlackMessage,
    SuccessfulLoginMessage,
    VerifyCompleteMessage,
)

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/ray/events")
async def ray_events(
    event: RayEvent, auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)]
):
    """Receives and responds to an event from the RAY platform."""
    client = None
    user_info = None
    message: Optional[SlackMessage] = None
    if auth.slack_user:
        client = AsyncWebClient(token=auth.slack_user.bot_token)
        try:
            if auth.slack_user.user_id != auth.slack_user.ray_client_id:
                user_info = await client.users_info(
                    user=auth.slack_user.user_id, include_locale=True
                )
                set_user_language(user_info)
        except Exception as e:
            pass

        # Handle different event types directly
        is_ibm = (
            is_ibm_enterprise(auth.slack_user.enterprise_id)
            if auth.slack_user
            else False
        )

        if event.event == "ray:slack:account_connected":
            try:
                event_data = SlackAccountConnectedEvent.model_validate(event.data)
                # Get ray connection for the user
                ray_connection = await get_ray_connection(
                    event_data.user_id, event_data.team_id, event_data.enterprise_id
                )
                if ray_connection is None:
                    raise ValueError(
                        "Could not get ray connection for successful login"
                    )
                assert ray_connection is not None
                login_message: SuccessfulLoginMessage = SuccessfulLoginMessage(
                    event_data.user_id,
                    event_data.username,
                    ray_connection,
                    event_data.enterprise_id,
                )
                # Send login message to the same conversation where it was prompted
                await post_notification_ephemeral(
                    client,
                    auth.slack_user.channel_id,
                    event,
                    auth.slack_user,
                    login_message,
                )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:client:signup":
            try:
                signup_event = ClientSignupEvent.model_validate(event.data)
                signup_message: ClientSignupEventMessage = ClientSignupEventMessage(
                    signup_event
                )
                # Send important messages regardless of subscribed status
                if auth.slack_user.is_subscribed or True:  # Always send signup messages
                    if auth.demo_slack_users:
                        for slack_user_id in auth.demo_slack_users:
                            new_slack_user = replace(
                                auth.slack_user, user_id=slack_user_id
                            )
                            try:
                                await post_notification(
                                    client, event, new_slack_user, signup_message
                                )
                            except Exception as e:
                                notify_exception(
                                    e,
                                    "Failed to send notification to send demo message",
                                )
                    else:
                        await post_notification(
                            client, event, auth.slack_user, signup_message
                        )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:client:approved":
            try:
                approved_event = ClientApprovedEvent.model_validate(event.data)
                group_names = [group.label for group in approved_event.groups]
                approved_message: ClientApprovedEventMessage = (
                    ClientApprovedEventMessage(group_names)
                )
                # Send important messages regardless of subscribed status
                if (
                    auth.slack_user.is_subscribed or True
                ):  # Always send approved messages
                    if auth.demo_slack_users:
                        for slack_user_id in auth.demo_slack_users:
                            new_slack_user = replace(
                                auth.slack_user, user_id=slack_user_id
                            )
                            try:
                                await post_notification(
                                    client, event, new_slack_user, approved_message
                                )
                            except Exception as e:
                                notify_exception(
                                    e,
                                    "Failed to send notification to send demo message",
                                )
                    else:
                        await post_notification(
                            client, event, auth.slack_user, approved_message
                        )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:status_changed":
            try:
                status_event = JobStatusChangedEvent.model_validate(event.data)
                status_event.status = status_event.status.strip().upper()
                status_event.previous_status = (
                    status_event.previous_status.strip().upper()
                    if status_event.previous_status
                    else None
                )
                # Do not send notification if quote is accepted or cancelled,
                # send those notifications instead.
                if status_event.status in (
                    "LEAD",
                    "IN_PROGRESS",
                    "VALIDATION",
                    "REFUNDED",
                ):
                    message = JobStatusChangedEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                        status=status_event.status,
                        is_ibm=is_ibm,
                    )
                elif status_event.status == "COMPLETED":
                    message = JobCompletedEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                        target_languages=[lang.label for lang in status_event.tl],
                        is_ibm=is_ibm,
                    )
                elif status_event.status == "CANCELLED":
                    message = JobCancelledEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                    )

                # Send message if we have one and user is subscribed
                if message is not None and auth.slack_user.is_subscribed:
                    if isinstance(message, JobCompletedEventMessage):
                        if not is_verify_job(event.data["uuid"]):
                            await post_notification(
                                client, event, auth.slack_user, message
                            )
                    else:
                        await post_notification(client, event, auth.slack_user, message)
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:quote_created":
            try:
                quote_event = JobQuoteCreatedEvent.model_validate(event.data)
                quote_message: JobQuotedEventMessage = JobQuotedEventMessage(
                    quote_event, is_ibm
                )
                # Send message if user is subscribed
                if auth.slack_user.is_subscribed:
                    await post_notification(
                        client, event, auth.slack_user, quote_message
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:quote_accepted":
            try:
                quote_accepted_event = JobQuoteAcceptedEvent.model_validate(event.data)
                accepted_message: JobQuoteAcceptedEventMessage = (
                    JobQuoteAcceptedEventMessage(quote_accepted_event, is_ibm)
                )
                # Send message if user is subscribed
                if auth.slack_user.is_subscribed:
                    await post_notification(
                        client, event, auth.slack_user, accepted_message
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "ray:job:quote_cancelled":
            try:
                quote_cancelled_event = JobQuoteCancelledEvent.model_validate(
                    event.data
                )
                cancelled_message: JobQuoteCancelledEventMessage = (
                    JobQuoteCancelledEventMessage(
                        client_id=quote_cancelled_event.client_id,
                        job_uuid=quote_cancelled_event.uuid,
                        job_id=quote_cancelled_event.id,
                        is_ibm=is_ibm,
                    )
                )
                # Send message if user is subscribed
                if auth.slack_user.is_subscribed:
                    await post_notification(
                        client, event, auth.slack_user, cancelled_message
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "transcription:slack:media:transcription:results":
            # Handle transcription completion events
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get task info from database
                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Handle errors
                extra_data = task_info.extra_data or {}
                if transcribed_event.error or event.data.get("error"):
                    thread_ts = resolve_event_thread_ts(extra_data, event.data)
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=format_callback_error("transcription", error_msg),
                        thread_ts=thread_ts,
                    )
                    return

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = resolve_event_thread_ts(extra_data, event.data)

                # Track processed stages for reference
                processed_stages = extra_data.get("_processed_stages", [])
                if "transcription" not in processed_stages:
                    # Mark as processed FIRST to prevent race condition
                    await mark_stage_processed(
                        transcribed_event.task_uuid, "transcription"
                    )
                    await handle_transcription_complete(
                        client,
                        task_info.result_file_id,
                        task_info.result_file_name,
                        task_info,
                        is_ibm,
                        str(channel_id),
                        thread_ts,
                        event,
                        auth,
                        auth.slack_user,
                    )
                    # Spend credits for transcription
                    transcription_tokens = await spend_transcription_credits(
                        task_info, auth
                    )
                    # Track total tokens (message will be shown after file upload completes)
                    if transcription_tokens > 0:
                        await update_tokens_consumed(
                            task_info.task_uuid, transcription_tokens
                        )

            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "transcription:slack:media:translation:results":
            # Handle translation completion events
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get task info from database
                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Handle errors
                extra_data = task_info.extra_data or {}
                if transcribed_event.error or event.data.get("error"):
                    thread_ts = resolve_event_thread_ts(extra_data, event.data)
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=format_callback_error("translation", error_msg),
                        thread_ts=thread_ts,
                    )
                    return

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = resolve_event_thread_ts(extra_data, event.data)

                # Track processed stages for reference
                processed_stages = extra_data.get("_processed_stages", [])
                if (
                    "translation" not in processed_stages
                    and task_info.translated_file_ids
                ):
                    # Mark as processed FIRST to prevent race condition
                    await mark_stage_processed(
                        transcribed_event.task_uuid, "translation"
                    )
                    await handle_translation_complete(
                        client,
                        str(channel_id),
                        thread_ts,
                        task_info,
                        auth,
                    )
                    # Spend credits for translation
                    translation_tokens = await spend_translation_credits(
                        task_info, auth
                    )
                    # Track total tokens (message will be shown after file upload completes)
                    if translation_tokens > 0:
                        await update_tokens_consumed(
                            transcribed_event.task_uuid, translation_tokens
                        )

            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "transcription:slack:media:embedding:results":
            # Handle embedding completion events
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get task info from database
                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Handle errors
                extra_data = task_info.extra_data or {}
                if transcribed_event.error or event.data.get("error"):
                    thread_ts = resolve_event_thread_ts(extra_data, event.data)
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=format_callback_error("embedding", error_msg),
                        thread_ts=thread_ts,
                    )
                    return

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = resolve_event_thread_ts(extra_data, event.data)

                # Track processed stages for reference
                processed_stages = extra_data.get("_processed_stages", [])
                if "embedding" not in processed_stages:
                    # Mark as processed FIRST to prevent race condition
                    await mark_stage_processed(transcribed_event.task_uuid, "embedding")
                    await handle_transcribe_embed_pipeline(
                        client,
                        task_info.result_file_id,
                        task_info.result_file_name,
                        task_info,
                        str(channel_id),
                        thread_ts,
                        auth,
                    )
                    # Spend credits for embedding
                    embedding_tokens = await spend_embedding_credits(task_info, auth)
                    # Track total tokens (message will be shown after file upload completes)
                    if embedding_tokens > 0:
                        await update_tokens_consumed(
                            transcribed_event.task_uuid, embedding_tokens
                        )
                    await update_submission_status(extra_data)

            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "verify:slack:document:quote":
            try:
                quote_data = DocumentMtQuoteResponseSchema.model_validate(event.data)
                if quote_data.error:
                    error_data = MtErrorResponseSchema.model_validate(
                        {
                            "error": True,
                            "client_id": quote_data.client_id,
                            "channel_id": quote_data.channel_id,
                            "error_type": quote_data.error_type or "other",
                            "error_data": quote_data.error_data,
                        }
                    )
                    if error_data.error_type == "insufficient_balance":
                        client_type = await get_client_type(
                            auth.slack_user.ray_client_id,
                            auth.slack_user.ray_user_group_id,
                        )
                        balance = Balance.model_validate(error_data.error_data)
                        message = (
                            RequiresMtTokenMessage(balance.balance, balance.required)
                            if client_type in ["Admin", "Owner"]
                            and not is_ibm_enterprise(auth.slack_user.enterprise_id)
                            else RequiresMtTokenAdminMessage(
                                balance.balance, balance.required
                            )
                        )
                    elif error_data.error_type == "conversion_error":
                        message = DocParseErrorMessage(
                            error_data.error_data.get("ext", ""),
                            error_data.error_data.get("file_expected", ""),
                            error_data.error_data.get("message", ""),
                        )
                    elif error_data.error_type == "file_complexity_error":
                        message = DocComplexityErrorMessage(
                            error_data.error_data.get("ext", ""),
                        )
                    elif error_data.error_type == "invalid_pdf":
                        message = DocInvalidPdfErrorMessage(
                            error_data.error_data.get("message", ""),
                        )
                    else:
                        message = DocMtMessage()
                    await post_notification_ephemeral(
                        client,
                        quote_data.channel_id,
                        event,
                        auth.slack_user,
                        message,
                    )
                else:
                    session = await apply_document_mt_quote_result(
                        quote_data.quote_id,
                        quote_data.model_dump(),
                    )
                    if session is None:
                        raise HTTPException(
                            404,
                            {"message": "Document MT quote session expired or missing"},
                        )
                    await post_notification(
                        client,
                        event,
                        auth.slack_user,
                        DocumentMtQuoteMessage(session),
                        channel_id=session.get("channel_id"),
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "verify:slack:document:translated":
            # Handle MT success/error
            try:
                document_translated_data = MtErrorResponseSchema.model_validate(
                    event.data
                )
                # Update submission status to FAILED if submission_id is present
                if document_translated_data.submission_id:
                    updated_submission_status(
                        submission_id=document_translated_data.submission_id,
                        processing_status=SubmissionStatus.FAILED,
                    )
                document_message: Optional[SlackMessage] = None
                if document_translated_data.error_type == "insufficient_balance":
                    # Send message to user that they need to purchase tokens
                    client_type = None
                    if (
                        auth.slack_user
                        and auth.slack_user.ray_user_group_id
                        and auth.slack_user.ray_client_id
                        != auth.slack_user.ray_user_group_id
                    ):
                        client_type = await get_client_type(
                            auth.slack_user.ray_client_id,
                            auth.slack_user.ray_user_group_id,
                        )
                    balance = Balance.model_validate(
                        document_translated_data.error_data
                    )

                    if client_type in ["Admin", "Owner"] and not is_ibm_enterprise(
                        auth.slack_user.enterprise_id
                    ):
                        document_message = RequiresMtTokenMessage(
                            balance.balance, balance.required
                        )
                    else:
                        document_message = RequiresMtTokenAdminMessage(
                            balance.balance, balance.required
                        )
                elif document_translated_data.error_type == "conversion_error":
                    document_message = DocParseErrorMessage(
                        document_translated_data.error_data.get("ext", ""),
                        document_translated_data.error_data.get("file_expected", ""),
                        document_translated_data.error_data.get("message", ""),
                    )
                elif document_translated_data.error_type == "file_complexity_error":
                    document_message = DocComplexityErrorMessage(
                        document_translated_data.error_data.get("ext", ""),
                    )
                elif document_translated_data.error_type == "invalid_pdf":
                    document_message = DocInvalidPdfErrorMessage(
                        document_translated_data.error_data.get("message", ""),
                    )
                else:
                    document_message = DocMtMessage()
                if document_message is not None and auth.slack_user is not None:
                    await post_notification_ephemeral(
                        client,
                        document_translated_data.channel_id
                        or auth.slack_user.channel_id,
                        event,
                        auth.slack_user,
                        document_message,
                    )
            except ValidationError:
                success_data = MtSuccessResponseSchema.model_validate(event.data)
                await enqueue_mt_success_upload(success_data)

        elif event.event == "verify:slack:evaluate:ready_for_ai_quote":
            try:
                job_uuid = event.data["job_uuid"]
                await post_evaluate_service_quote(
                    client,
                    event,
                    auth,
                    job_uuid=job_uuid,
                    service=EVALUATE_SERVICE_AI_TRANSLATION,
                    service_label=_("AI Translation"),
                    accept_action_id="evaluation_ai_quote_accept",
                    include_pdf_fee=True,
                )
            except Exception as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing ready_for_ai_quote event: {str(e)}",
                    },
                ) from e

        elif event.event == "verify:slack:evaluate:ready_for_qe_quote":
            try:
                job_uuid = event.data["job_uuid"]
                await post_combined_qe_human_quote(
                    client,
                    event,
                    auth,
                    job_uuid=job_uuid,
                )
            except Exception as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing ready_for_qe_quote event: {str(e)}",
                    },
                ) from e

        elif event.event == "verify:slack:evaluate:complete":
            if not await claim_evaluate_complete_notification(event):
                logger.info(
                    "Skipping duplicate evaluate-complete notification for job %s",
                    event.data.get("job_uuid"),
                )
                return {"message": "Duplicate evaluate-complete event skipped"}

            if event.data.get("error"):
                try:
                    error_data = MtErrorResponseSchema.model_validate(event.data)
                    if error_data.error_type == "insufficient_balance":
                        # Send message to user that they need to purchase tokens
                        client_type = await get_client_type(
                            auth.slack_user.ray_client_id,
                            auth.slack_user.ray_user_group_id,
                        )
                        balance = Balance.model_validate(error_data.error_data)

                        if client_type in ["Admin", "Owner"] and not is_ibm_enterprise(
                            auth.slack_user.enterprise_id
                        ):
                            message = RequiresMtTokenMessage(
                                balance.balance, balance.required
                            )
                        else:
                            message = RequiresMtTokenAdminMessage(
                                balance.balance, balance.required
                            )
                    elif error_data.error_type == "conversion_error":
                        message = DocParseErrorMessage(
                            error_data.error_data.get("ext", ""),
                            error_data.error_data.get("file_expected", ""),
                            error_data.error_data.get("message", ""),
                        )
                    elif error_data.error_type == "file_complexity_error":
                        message = DocComplexityErrorMessage(
                            error_data.error_data.get("ext", ""),
                        )
                    elif error_data.error_type == "invalid_pdf":
                        message = DocInvalidPdfErrorMessage(
                            error_data.error_data.get("message", ""),
                        )
                    else:
                        # For "other" or any other error type, use generic error message
                        message = EvaluateErrorMessage()
                except ValidationError:
                    # If validation fails, fall back to generic error message
                    message = EvaluateErrorMessage()
            else:
                try:
                    job = await get_evaluation_job(
                        auth.slack_user, event.data["job_uuid"]
                    )
                    if job["data"].get("human_job_in_progress", False):
                        raise ValueError(f"Invalid RAY event type: {event.event}")
                    workflow_uuid = job["data"].get("workflow_uuid")
                    if event.data.get("ai_only"):
                        message = EvaluateAiOnlyCompleteMessage(job["data"])
                    elif workflow_uuid in (
                        HUMAN_EVALUATION_WORKFLOW_UUID,
                        HUMAN_VERIFICATION_WORKFLOW_UUID,
                    ):
                        ray_client = await get_ray_client(
                            auth.slack_user.user_id, auth.slack_user.team_id
                        )
                        if ray_client is None:
                            raise ValueError("Could not get ray client for job pricing")
                        costs = await get_job_pricing(
                            ray_client,
                            job["data"]["uuid"],
                            job_file_uuids(job["data"]),
                            job_target_language_uuids(job["data"]),
                        )
                        if await handle_combined_qe_complete(
                            client,
                            event,
                            auth,
                            job_data=job["data"],
                            costs=costs["data"],
                        ):
                            message = None
                        else:
                            message = HumanJobQuoteMessage(job["data"], costs["data"])
                    else:
                        message = EvaluateSuccessMessage(
                            job["data"], is_ibm, event.data.get("tokens")
                        )
                except Exception as e:
                    raise HTTPException(
                        422,
                        {
                            "message": f"Error processing evaluation complete event: {str(e)}",
                        },
                    ) from e

            # Send message if we have one
            if message is not None:
                await post_notification(
                    client,
                    event,
                    auth.slack_user,
                    message,
                )
        elif event.event == "verify:human_verification:completed":
            try:
                lang_label = await get_language_name_by_uuid(event.data["lang_uuid"])
                verify_message: VerifyCompleteMessage = VerifyCompleteMessage(
                    event.data["job_title"], lang_label
                )
                # Send message and enqueue durable file upload (RAY-79638)
                response = await post_notification(
                    client,
                    event,
                    auth.slack_user,
                    verify_message,
                )
                response_data = getattr(response, "data", None)
                upload_channel_id = (
                    response_data["channel"] if isinstance(response_data, dict) else ""
                )
                if upload_channel_id and auth.slack_user is not None:
                    await enqueue_verify_complete_upload(
                        grid_file_id=event.data["grid_file_id"],
                        client_id=auth.slack_user.ray_client_id,
                        channel_id=upload_channel_id,
                    )
            except Exception as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing human verification complete event: {str(e)}",
                    },
                ) from e

        elif event.event == "slack:direct:mt:result":
            try:
                mt_result_extra_data = MtTranslationExtraData.model_validate(
                    event.data["extra_data"]
                )

                # Parse translations from the new service-based response format
                translations = event.data.get("translations", {})

                if (
                    mt_result_extra_data.usage_type == "direct_machine_translation"
                    or mt_result_extra_data.usage_type == "shortcut_translate"
                ):
                    # For direct translation, we need to get the first target language
                    # and combine all translations into a single string
                    first_target_lang = None
                    for (
                        lang_glossary_map
                    ) in mt_result_extra_data.service_language_mapping.values():
                        if lang_glossary_map:
                            first_target_lang = next(iter(lang_glossary_map.keys()))
                            break

                    # Combine all translations into a single string
                    # The translations dict should be {lang: [text1, text2, ...]}
                    combined_translations = " ".join(
                        [
                            text
                            for lang_translations in translations.values()
                            for text in (
                                lang_translations
                                if isinstance(lang_translations, list)
                                else [lang_translations]
                            )
                        ]
                    )
                    assert mt_result_extra_data.source_text
                    mt_result_message: MachineTranslationMessage = (
                        MachineTranslationMessage(
                            first_target_lang or "unknown",
                            mt_result_extra_data.source_language,
                            mt_result_extra_data.source_text,
                            combined_translations,
                        )
                    )
                    # Send message with response method configuration
                    await post_notification(
                        client,
                        event,
                        auth.slack_user,
                        mt_result_message,
                        channel_id=mt_result_extra_data.channel_id,
                        thread_ts=mt_result_extra_data.thread_ts,
                        is_edit=mt_result_extra_data.is_edit,
                        response_url=mt_result_extra_data.response_url,
                    )
                elif mt_result_extra_data.usage_type == "channel_translation":
                    from app.slack.bot_translation import (
                        is_channel_source_deleted,
                        is_stale_channel_mt_generation,
                    )

                    if (
                        mt_result_extra_data.message_ts
                        and mt_result_extra_data.edit_generation is not None
                        and await is_stale_channel_mt_generation(
                            mt_result_extra_data.message_ts,
                            mt_result_extra_data.edit_generation,
                        )
                    ):
                        return {
                            "message": "Stale channel translation skipped",
                            "data": {"event": event.event},
                        }

                    # Skip if the source was deleted while the translation was in
                    # flight; message_deleted cleanup runs before our reply exists.
                    if (
                        mt_result_extra_data.message_ts
                        and await is_channel_source_deleted(
                            mt_result_extra_data.message_ts
                        )
                    ):
                        return {
                            "message": "Deleted source channel translation skipped",
                            "data": {"event": event.event},
                        }
                    # For channel translation, pass the translations dict directly
                    # The AutoTranslationMessage expects {lang: [text1, text2, ...]} format
                    translations = order_translations_by_target_language_order(
                        translations,
                        mt_result_extra_data.target_language_order,
                    )
                    assert mt_result_extra_data.source_text
                    auto_translation_message: AutoTranslationMessage = (
                        AutoTranslationMessage(
                            mt_result_extra_data.source_text,
                            mt_result_extra_data.source_language,
                            translations=translations,
                        )
                    )
                    try:
                        await post_channel_translation_notification(
                            client,
                            event,
                            auth.slack_user,
                            auto_translation_message,
                            channel_id=mt_result_extra_data.channel_id,
                            thread_ts=mt_result_extra_data.thread_ts,
                            is_edit=mt_result_extra_data.is_edit,
                            display_format=mt_result_extra_data.display_format,
                            message_ts=mt_result_extra_data.message_ts,
                        )
                    except SlackApiError as e:
                        notify_exception(
                            e, "Failed to post channel translation notification"
                        )
                        raise HTTPException(
                            422,
                            {
                                "message": f"Failed to post channel translation notification: {e.response.get('error', 'unknown error')}",
                            },
                        ) from e
                else:
                    raise HTTPException(
                        422,
                        {
                            "message": f"Invalid usage type: {mt_result_extra_data.usage_type}",
                        },
                    )
                # The poster's default group (`obj_m_member.groupid`) is NOT a
                # requirement here: channel auto-translate is org-billed and needs
                # no login, so a NULL default groupid must not fail the callback
                # (RAY-80199). Resolve a group uuid for the usage report only,
                # falling back to the submission's group_id (org/group context
                # from get_group_id, colon-joined) when the member has no default
                # group. Billing itself goes through the gateway by client_id and
                # does not use this value.
                usage_group_uuid = auth.slack_user.ray_user_group_id or (
                    mt_result_extra_data.group_id.split(":")[0]
                    if mt_result_extra_data.group_id
                    else None
                )
                # Resolve a friendly channel name first so it can be persisted on
                # the usage row as well as the Google API usage log.
                channel_name = None
                if mt_result_extra_data.channel_id:
                    if not mt_result_extra_data.channel_id.startswith("C"):
                        channel_name = "direct message"
                    elif mt_result_extra_data.usage_type == "shortcut_translate":
                        channel_name = "shortcut translation"
                    else:
                        try:
                            channel_info = await client.conversations_info(
                                channel=mt_result_extra_data.channel_id
                            )
                            channel = channel_info.get("channel")
                            channel_name = (
                                channel["name"]
                                if channel and "name" in channel
                                else None
                            )
                        except Exception as e:
                            notify_exception(e, "Failed to get channel info")

                # One billed entry per (service, language) pair so the gateway's
                # ceil(text_length * len(target_languages) * 0.1) reproduces the
                # prior calculate_cost(text_length * total_languages) amount.
                billed_target_languages = [
                    lang
                    for lang_glossary_map in (
                        mt_result_extra_data.service_language_mapping.values()
                    )
                    for lang in lang_glossary_map
                ]
                # MT services contributing to this debit, recorded as context.
                engine = (
                    ",".join(sorted(mt_result_extra_data.service_language_mapping))
                    or None
                )
                # Per-message key so a redelivered MT result replays to a single
                # debit. The submission id combines the message ts with a
                # fingerprint of the translated text so a genuine *edit* (same ts,
                # new content) is still charged, while a pure redelivery (same ts,
                # same content) dedupes. Omitted when no message ts is available so
                # the charge stays back-compatible (RAY-80000 §3.4).
                content_fingerprint = hashlib.sha256(
                    (mt_result_extra_data.source_text or "").encode("utf-8")
                ).hexdigest()[:16]
                if mt_result_extra_data.message_ts:
                    inline_idempotency_key = build_spend_idempotency_key(
                        app_source="slack",
                        submission_id=(
                            f"{mt_result_extra_data.message_ts}:{content_fingerprint}"
                        ),
                        service=mt_result_extra_data.usage_type,
                        unit_type="characters",
                    )
                else:
                    # No message ts (e.g. slash-command direct MT). Billing now
                    # runs in a SAQ job that can be retried/redelivered, so the
                    # charge MUST be idempotent even without a ts. Derive a
                    # stable key from the channel + content fingerprint so a
                    # replayed stream entry dedupes at the gateway and the SAQ
                    # job instead of double-charging on retry (RAY-80258).
                    inline_idempotency_key = build_spend_idempotency_key(
                        app_source="slack",
                        submission_id=(
                            f"{mt_result_extra_data.channel_id}:{content_fingerprint}"
                        ),
                        service=mt_result_extra_data.usage_type,
                        unit_type="characters",
                    )
                # Channel/shortcut MT is billed against the group, so the usage
                # report cannot resolve the poster from client_uuid. Send the
                # Slack user identity so the usage row carries it (RAY-80000).
                slack_profile = user_info["user"]["profile"] if user_info else {}
                user_email = slack_profile.get("email") or None
                user_name = (
                    slack_profile.get("real_name")
                    or slack_profile.get("real_name_normalized")
                    or mt_result_extra_data.slack_user_name
                    or None
                )

                # Words in the source message -- a typed report column on the
                # usage row (RAY-80000). Billing stays character-based; this is
                # recorded for the report only.
                source_word_count = (
                    len(mt_result_extra_data.source_text.split())
                    if mt_result_extra_data.source_text
                    else None
                )

                # Convert translations from dict[lang, list[str]] to dict[lang, str]
                translations_for_log = {
                    lang: " ".join(texts) if isinstance(texts, list) else texts
                    for lang, texts in translations.items()
                }

                # Defer the LanguageCloud charge to a durable SAQ job so a
                # transient ConnectTimeout to /mt/inline-usage no longer returns
                # a 422 to redis-slack-consumer after the Slack message was
                # posted (RAY-80258). The job charges the gateway (writing the
                # self-describing credit_transaction_usage row, RAY-80000 §3.4)
                # with retry + idempotency, then writes the Google API usage row
                # against the resulting transaction_uuid. User delivery above is
                # now independent of billing durability.
                submission_anchor = (
                    mt_result_extra_data.message_ts
                    if mt_result_extra_data.message_ts
                    else f"{mt_result_extra_data.channel_id}:{content_fingerprint}"
                )
                billing_payload = {
                    "client_id": auth.slack_user.ray_client_id,
                    "text_length": mt_result_extra_data.text_length,
                    "target_languages": billed_target_languages,
                    "usage_type": mt_result_extra_data.usage_type,
                    "source_language": mt_result_extra_data.source_language,
                    "engine": engine,
                    "channel_name": channel_name,
                    "word_count": source_word_count,
                    "idempotency_key": inline_idempotency_key,
                    "email": user_email,
                    "client_name": user_name,
                    "is_bot": mt_result_extra_data.is_bot,
                    # Send the billing group so the gateway records the ledger
                    # group_uuid as the real group (e.g. the IBM super group)
                    # instead of collapsing to the org uuid for org-billed
                    # channel/shortcut MT (RAY-80000 hotfix).
                    "group_uuid": auth.slack_user.ray_user_group_id,
                    # Stable per-message anchor for IBM usage report grouping
                    # (RAY-80492 Phase 2).
                    "submission_group_uuid": submission_anchor,
                }
                usage_log_payload = {
                    "user_uuid": auth.slack_user.ray_client_id,
                    "group_uuid": usage_group_uuid,
                    "organization_uuid": mt_result_extra_data.organization_uuid,
                    "input_text": mt_result_extra_data.source_text
                    or "[Source text not available]",
                    "source_lang": mt_result_extra_data.source_language,
                    "translations": translations_for_log,
                    "app_name": "slack",
                    "usage_type": mt_result_extra_data.usage_type,
                    "text_length": mt_result_extra_data.text_length,
                    "channel_name": channel_name,
                    "email": user_email,
                }
                await enqueue_inline_mt_billing(
                    idempotency_key=inline_idempotency_key,
                    billing=billing_payload,
                    usage_log=usage_log_payload,
                )
            except Exception as e:
                # Some exceptions (e.g. bare AssertionError) stringify to "",
                # which previously produced an opaque "...: " message. Fall back
                # to the exception class name so the cause is always visible.
                error_detail = str(e) or type(e).__name__
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing MT result event: {error_detail}",
                    },
                ) from e

        else:
            raise HTTPException(
                400, f"The event type is invalid: {event.event}"
            ) from None

    # Send notifications to group admins when a new client signs up.
    if (
        event.event == "ray:client:signup"
        and auth.slack_user is not None
        and client is not None
    ):
        try:
            signup_event = ClientSignupEvent.model_validate(event.data)
            admins: dict[str, tuple[SlackUser, list[ClientGroup]]] = {}
            for group in signup_event.groups:
                admin_slack_users = await get_group_admin_slack_users(group.uuid)
                for admin in admin_slack_users:
                    if admin.ray_client_id not in admins:
                        admins[admin.ray_client_id] = (admin, [])
                    admins[admin.ray_client_id][1].append(group)

            for user, groups in admins.values():
                admin_message = ClientSignupEventAdminMessage(
                    event=signup_event,
                    groups=groups,
                )
                assert client is not None
                await post_notification(client, event, user, admin_message)
        except ValidationError:
            # If validation fails, skip admin notifications
            pass

    return {"message": "success", "data": {"event": event.event}}


class RayCallback(BaseModel):
    """The expected body format for the RAY callback endpoint."""

    event_types: list[str]
    job: list[dict[str, Any]]


@router.post("/ray/callback")
async def api_job_callback(
    request: Request,
    client_id: str,
    body: RayCallback,
    x_straker_signature: Annotated[str, Header()],
):
    """Callback endpoint for API jobs."""
    # Check if the callback can be linked to a Slack user.
    slack_user = await get_slack_user(client_id)
    demo_slack_users = await get_demo_link(client_id)
    if slack_user is None:
        notify_message("Slack user not found in callback endpoint", severity="WARNING")
        raise HTTPException(401)
    client = AsyncWebClient(token=slack_user.bot_token)
    user_info = await client.users_info(user=slack_user.user_id, include_locale=True)
    set_user_language(user_info)
    # Validate X-Straker-Signature.
    raw_body = await request.body()
    access_tokens = await get_client_access_tokens(slack_user.ray_client_id)
    is_header_valid = any(
        validate_api_callback_signature(raw_body, token, x_straker_signature)
        for token in access_tokens
    )
    if not is_header_valid:
        notify_message("Callback X-Straker-Signature is invalid", severity="WARNING")
        raise HTTPException(401)

    # Handle job creation and job completed callbacks.
    if "JOB_NUMBER" in body.event_types:
        try:
            job_data = body.job[0]
            is_auto_quote = True
            if is_ibm_enterprise(slack_user.enterprise_id):
                is_auto_quote = False
                is_auto_quote = await get_job_group_quote_settings(
                    job_data["tj_number"][2:]
                )
            if is_auto_quote:
                message = JobCreationMessage(job_data["tj_number"], True)
        except (KeyError, IndexError):
            raise HTTPException(422, "The callback payload format is invalid") from None
        if demo_slack_users:
            for slack_user_id in demo_slack_users:
                await client.chat_postMessage(
                    channel=slack_user_id,
                    text=message.text,
                    blocks=message.blocks,
                )
        else:
            if is_auto_quote:
                await client.chat_postMessage(
                    channel=slack_user.user_id,
                    text=message.text,
                    blocks=message.blocks,
                )
        return {
            "message": "success",
            "detail": "Slack user notified of event: JOB_NUMBER",
        }
    else:
        return {
            "message": "success",
            "detail": f"Unhandled callback event: {body.event_types}",
        }

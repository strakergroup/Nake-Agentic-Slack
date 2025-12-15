import asyncio
import logging
import os
from dataclasses import replace
from typing import Annotated, Any, Optional, Union, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse
from slack_sdk.webhook import WebhookResponse
from straker_utils.credits import calculate_cost, spend_credits

from app.api.models import MtTranslationExtraData
from app.api.verify import get_evaluation_job, get_job_pricing
from app.auth.connector import get_ray_client, get_ray_connection
from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.database import async_engines
from app.models import TranscriptionTaskInfo
from app.mt.logs import log_google_api_usage
from app.ray.submissions import SubmissionStatus, updated_submission_status
from app.ray.utils import (
    delete_from_file_server,
    download_from_file_server_async,
    is_ibm_enterprise,
    set_user_language,
)
from app.slack.buglog_notifier import notify_exception, notify_message
from app.slack.select_options import _get_languages_cached
from app.slack_job import update_slack_job
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
from ..ray.events.logging import (
    post_channel_translation_notification,
    post_notification,
    post_notification_ephemeral,
)
from ..ray.events.models import (
    Balance,
    ClientApprovedEvent,
    ClientGroup,
    ClientSignupEvent,
    JobQuoteAcceptedEvent,
    JobQuoteCancelledEvent,
    JobQuoteCreatedEvent,
    JobStatusChangedEvent,
    JobTranscribedEvent,
    MtErrorResponseSchema,
    MtSuccessResponseSchema,
    SlackAccountConnectedEvent,
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
    JobTranscribedEventMessage,
    MachineTranslationMessage,
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
    SlackMessage,
    SuccessfulLoginMessage,
    VerifyCompleteMessage,
)
from ..slack.web import upload_file_to_slack_memory_efficient

router = APIRouter()

# Track background tasks for potential cleanup
_background_tasks = set()


def _create_background_task(coro):
    """Create a background task with proper cleanup and error handling."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _cleanup_task(task):
        try:
            _background_tasks.discard(task)
        except Exception:
            pass

    task.add_done_callback(_cleanup_task)
    return task


def get_background_task_info():
    """Get information about running background tasks."""
    running_tasks = [task for task in _background_tasks if not task.done()]
    return {
        "total_tasks": len(_background_tasks),
        "running_tasks": len(running_tasks),
        "completed_tasks": len(_background_tasks) - len(running_tasks),
    }


async def _handle_mt_success_background(
    success_data: MtSuccessResponseSchema,
    auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)],
):
    """Background task to handle MT success file download and upload."""
    try:
        # Update submission status if submission_id is present
        if success_data.submission_id:
            updated_submission_status(
                submission_id=success_data.submission_id,
                processing_status=SubmissionStatus.COMPLETED,
            )
        await update_slack_job(
            task_uuid=success_data.task_uuid,
            status="slack_uploading",
        )
        # Create a new client instance with the correct token for this user
        if auth.slack_user is None:
            return
        client = AsyncWebClient(token=auth.slack_user.bot_token)

        # Download file from server
        output_file = await download_from_file_server_async(success_data.file_id)
        file_path = output_file.get("file")
        title = output_file.get("file_name")
        initial_comment = _(
            f"Your file is AI translated to *{success_data.target_language}* and can be downloaded below."
        )
        try:
            # Upload file using memory-efficient method
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file_path,
                channel_id=success_data.channel_id,
                title=title,
                filename=title,
                initial_comment=initial_comment,
            )

            await update_slack_job(
                task_uuid=success_data.task_uuid,
                status="delivered",
            )
            await delete_from_file_server(success_data.file_id)
        finally:
            # Clean up temporary file
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
    except Exception as e:
        notify_exception(e, "Background MT success file handling failed")
        await update_slack_job(
            task_uuid=success_data.task_uuid,
            status="failed_delivery",
        )


async def _handle_transcribe_embed_pipeline(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    task_info: TranscriptionTaskInfo,
    channel_id: str,
    thread_ts: str | None,
) -> None:
    """Handle transcription + translation + embed pipeline result."""
    if not result_file_id:
        return

    from ..ray.utils import download_from_file_server_async
    from ..slack.web import upload_file_to_slack_memory_efficient

    try:
        output_file = await download_from_file_server_async(result_file_id)
        file_path = output_file.get("file")
        if file_path and os.path.exists(file_path):
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file_path,
                channel_id=channel_id,
                thread_ts=thread_ts,
                title=result_file_name or task_info.file_name,
            )
            os.unlink(file_path)

        await client.chat_postMessage(
            channel=channel_id,
            text=_("Your video with embedded subtitles is ready!"),
            thread_ts=thread_ts,
        )
    except Exception as e:
        notify_exception(e, "Error handling embedded video")
        await client.chat_postMessage(
            channel=channel_id,
            text=_(
                "An error occurred while processing your embedded video. Please try again."
            ),
            thread_ts=thread_ts,
        )


async def _handle_transcribe_translate_pipeline(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    channel_id: str,
    thread_ts: str | None,
    auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)],
) -> None:
    """Handle transcription + translation pipeline result."""
    await client.chat_postMessage(
        channel=channel_id,
        text=_(
            "We have transcribed your file. Translations will be delivered separately."
        ),
        thread_ts=thread_ts,
    )

    if result_file_id and result_file_name:
        _create_background_task(
            _handle_transcribe_success_background(
                {"file_id": result_file_id, "file_name": result_file_name},
                auth,
                None,
                override_channel_id=channel_id,
            )
        )


async def _handle_transcribe_only_pipeline(
    client: AsyncWebClient,
    result_file_id: str | None,
    result_file_name: str | None,
    task_info: TranscriptionTaskInfo,
    is_ibm: bool,
    channel_id: str,
    event: RayEvent,
    auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)],
    auth_slack_user: Any,
) -> None:
    """Handle transcription-only pipeline result."""
    task_uuid = task_info.task_uuid or "unknown"

    transcribed_message = JobTranscribedEventMessage(
        source_file_name=task_info.file_name or "",
        is_ibm_enterprise=is_ibm,
    )
    response = await post_notification(
        client, event, auth_slack_user, transcribed_message
    )

    # Always upload SRT file if available
    if result_file_id and result_file_name:
        # Ensure we have a channel_id - fallback to slack_user channel_id if not in extra_data
        upload_channel_id: str | None = channel_id or (
            auth_slack_user.channel_id if auth_slack_user else None
        )

        if upload_channel_id:
            _create_background_task(
                _handle_transcribe_success_background(
                    {"file_id": result_file_id, "file_name": result_file_name},
                    auth,
                    response,
                    override_channel_id=upload_channel_id,
                )
            )
        else:
            notify_exception(
                Exception(
                    f"Missing channel_id for transcription-only pipeline (task: {task_uuid})"
                ),
                "Cannot upload SRT file - missing channel_id",
            )


async def _handle_transcribe_success_background(
    event_data: dict[str, Any],
    auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)],
    response: Union[AsyncSlackResponse, WebhookResponse, None],
    override_channel_id: str | None = None,
):
    """Background task to handle transcription success file download and upload."""
    if not auth.slack_user:
        return

    file_id = event_data.get("file_id")
    file_name = event_data.get("file_name")
    if not file_id or not file_name:
        notify_exception(
            Exception(
                f"Missing file_id or file_name in event_data. file_id={file_id}, file_name={file_name}"
            ),
            "Transcription background task failed",
        )
        return

    try:
        client = AsyncWebClient(token=auth.slack_user.bot_token)
        output_file = await download_from_file_server_async(file_id)
        file_path = output_file.get("file")

        if not file_path:
            notify_exception(
                Exception(f"Failed to download file {file_id} from file server"),
                "Transcription background task failed",
            )
            return

        # Get channel_id from override or response
        channel_id = override_channel_id

        if (
            not channel_id
            and isinstance(response, AsyncSlackResponse)
            and isinstance(response.data, dict)
        ):
            channel_id = response.data.get("channel")

        if not channel_id:
            # Final fallback to slack_user channel_id
            channel_id = auth.slack_user.channel_id if auth.slack_user else None

        if not channel_id:
            notify_exception(
                Exception(
                    f"Missing channel_id for file upload. file_id={file_id}, override_channel_id={override_channel_id}"
                ),
                "Transcription background task failed",
            )
            return

        try:
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file_path,
                channel_id=channel_id,
                title=file_name,
                filename=output_file.get("file_name"),
            )
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)
    except Exception as e:
        notify_exception(e, "Background transcription file handling failed")


async def _handle_verify_complete_background(event_data, auth, response):
    """Background task to handle verify complete file download and upload."""
    try:
        # Create a new client instance with the correct token for this user
        if auth.slack_user is None:
            return
        client = AsyncWebClient(token=auth.slack_user.bot_token)

        output_file = await download_from_file_server_async(event_data["grid_file_id"])
        file_path = output_file.get("file")
        try:
            await upload_file_to_slack_memory_efficient(
                client=client,
                file_path=file_path,
                channel_id=response.data["channel"]
                if isinstance(response.data, dict)
                else "",
                title=output_file.get("file_name"),
                filename=output_file.get("file_name"),
            )
        finally:
            # Clean up temporary file
            if file_path and os.path.exists(file_path):
                os.unlink(file_path)
    except Exception as e:
        notify_exception(e, "Background verify complete file handling failed")


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
                    event_data.user_id, event_data.username, ray_connection
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
                    message: SlackMessage = JobStatusChangedEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                        status=status_event.status,
                        is_ibm=is_ibm,
                    )
                elif status_event.status == "COMPLETED":
                    message: SlackMessage = JobCompletedEventMessage(
                        client_id=status_event.client_id,
                        job_uuid=status_event.uuid,
                        job_id=status_event.id,
                        target_languages=[lang.label for lang in status_event.tl],
                        is_ibm=is_ibm,
                    )
                elif status_event.status == "CANCELLED":
                    message: SlackMessage = JobCancelledEventMessage(
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

        elif event.event == "transcription:slack:media:results":
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)

                # Get all task info from database
                from ..transcriber_tasks.tasks import get_transcription_task

                task_info = await get_transcription_task(transcribed_event.task_uuid)
                if not task_info:
                    notify_exception(
                        Exception(
                            f"Task {transcribed_event.task_uuid} not found in database"
                        ),
                        "Task lookup failed",
                    )
                    return

                # Extract info from database
                extra_data = task_info.extra_data or {}
                pipeline_type = task_info.pipeline_type or "transcribe"
                client_id = task_info.client_id
                result_file_id = task_info.result_file_id
                result_file_name = task_info.result_file_name

                # Handle errors
                if transcribed_event.error or event.data.get("error"):
                    error_msg = transcribed_event.error or event.data.get(
                        "error", "Unknown error"
                    )
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=_(f"Transcription failed: {error_msg}"),
                    )
                    return

                # Common success handling
                # Update submission status
                try:
                    from ..ray.submissions import (
                        SubmissionStatus,
                        updated_submission_status,
                    )

                    submission_id = (
                        extra_data.get("submission_id") if extra_data else None
                    )
                    submission_ids = (
                        extra_data.get("submission_ids") if extra_data else None
                    )

                    if submission_id is not None:
                        updated_submission_status(
                            submission_id=int(submission_id),
                            processing_status=SubmissionStatus.COMPLETED,
                        )
                    elif submission_ids:
                        for sid in submission_ids:
                            if sid is not None:
                                updated_submission_status(
                                    submission_id=int(sid),
                                    processing_status=SubmissionStatus.COMPLETED,
                                )
                except Exception as e:
                    notify_exception(e, "Failed to update submission status")

                # Get channel and thread info
                channel_id = (
                    extra_data.get("slack_channel_id")
                    if extra_data
                    else auth.slack_user.channel_id
                )
                thread_ts = extra_data.get("slack_thread_ts") if extra_data else None

                # Handle based on pipeline type
                if pipeline_type == "transcribe_translate_embed":
                    await _handle_transcribe_embed_pipeline(
                        client,
                        result_file_id,
                        result_file_name,
                        task_info,
                        str(channel_id),
                        thread_ts,
                    )
                elif pipeline_type == "transcribe_translate":
                    await _handle_transcribe_translate_pipeline(
                        client,
                        result_file_id,
                        result_file_name,
                        str(channel_id),
                        thread_ts,
                        auth,
                    )
                else:
                    await _handle_transcribe_only_pipeline(
                        client,
                        result_file_id,
                        result_file_name,
                        task_info,
                        is_ibm,
                        str(channel_id),
                        event,
                        auth,
                        auth.slack_user,
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
                        document_message: SlackMessage = RequiresMtTokenMessage(
                            balance.balance, balance.required
                        )
                    else:
                        document_message: SlackMessage = RequiresMtTokenAdminMessage(
                            balance.balance, balance.required
                        )
                elif document_translated_data.error_type == "conversion_error":
                    document_message: SlackMessage = DocParseErrorMessage(
                        document_translated_data.error_data["ext"],
                        document_translated_data.error_data["file_expected"],
                    )
                elif document_translated_data.error_type == "file_complexity_error":
                    document_message: SlackMessage = DocComplexityErrorMessage(
                        document_translated_data.error_data["ext"],
                    )
                elif document_translated_data.error_type == "invalid_pdf":
                    document_message: SlackMessage = DocInvalidPdfErrorMessage(
                        document_translated_data.error_data["message"],
                    )
                else:
                    document_message: SlackMessage = DocMtMessage()
                if document_message is not None:
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
                _create_background_task(
                    _handle_mt_success_background(success_data, auth)
                )

        elif event.event == "verify:slack:evaluate:complete":
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
                            message: SlackMessage = RequiresMtTokenMessage(
                                balance.balance, balance.required
                            )
                        else:
                            message: SlackMessage = RequiresMtTokenAdminMessage(
                                balance.balance, balance.required
                            )
                    elif error_data.error_type == "conversion_error":
                        message: SlackMessage = DocParseErrorMessage(
                            error_data.error_data.get("ext", ""),
                            error_data.error_data.get("file_expected", ""),
                        )
                    elif error_data.error_type == "file_complexity_error":
                        message: SlackMessage = DocComplexityErrorMessage(
                            error_data.error_data.get("ext", ""),
                        )
                    elif error_data.error_type == "invalid_pdf":
                        message: SlackMessage = DocInvalidPdfErrorMessage(
                            error_data.error_data.get("message", ""),
                        )
                    else:
                        # For "other" or any other error type, use generic error message
                        message: SlackMessage = EvaluateErrorMessage()
                except ValidationError:
                    # If validation fails, fall back to generic error message
                    message: SlackMessage = EvaluateErrorMessage()
            else:
                try:
                    job = await get_evaluation_job(
                        auth.slack_user, event.data["job_uuid"]
                    )
                    if job["data"].get("human_job_in_progress", False):
                        raise ValueError(f"Invalid RAY event type: {event.event}")
                    all_langs = await _get_languages_cached()
                    if job["data"]["workflow_uuid"] == HUMAN_EVALUATION_WORKFLOW_UUID:
                        ray_client = await get_ray_client(
                            auth.slack_user.user_id, auth.slack_user.team_id
                        )
                        if ray_client is None:
                            raise ValueError("Could not get ray client for job pricing")
                        costs = await get_job_pricing(
                            ray_client,
                            job["data"]["uuid"],
                            [file["file_uuid"] for file in job["data"]["source_files"]],
                            [lang["uuid"] for lang in job["data"]["target_languages"]],
                        )
                        message: SlackMessage = HumanJobQuoteMessage(
                            job["data"], costs["data"]
                        )
                    else:
                        message: SlackMessage = EvaluateSuccessMessage(
                            job["data"], is_ibm, event.data["tokens"]
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
                all_langs = await _get_languages_cached()
                lang_label = ""
                for lang in all_langs:
                    if lang["uuid"] == event.data["lang_uuid"]:
                        lang_label = lang["name"]
                        break
                verify_message: VerifyCompleteMessage = VerifyCompleteMessage(
                    event.data["job_title"], lang_label
                )
                # Send message and handle background task
                response = await post_notification(
                    client,
                    event,
                    auth.slack_user,
                    verify_message,
                )
                _create_background_task(
                    _handle_verify_complete_background(event.data, auth, response)
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

                # Log the response for debugging
                notify_message(
                    f"MT Result - Service mapping: {mt_result_extra_data.service_language_mapping}, Translations: {translations}"
                )

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
                    # For channel translation, pass the translations dict directly
                    # The AutoTranslationMessage expects {lang: [text1, text2, ...]} format
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
                # Calculate total languages across all services
                total_languages = sum(
                    len(lang_glossary_map)
                    for lang_glossary_map in mt_result_extra_data.service_language_mapping.values()
                )
                amount = calculate_cost(
                    mt_result_extra_data.text_length * total_languages
                )
                assert auth.slack_user.ray_user_group_id is not None
                transaction_uuid = await spend_credits(
                    async_engines["sitemanager"],
                    auth.slack_user.ray_client_id,
                    auth.slack_user.ray_user_group_id,
                    amount,
                    "slack",
                    mt_result_extra_data.usage_type,
                    "Machine Translation",
                    mt_result_extra_data.organization_uuid,
                )

                # Log Google API usage
                # Convert translations from dict[lang, list[str]] to dict[lang, str]
                translations_for_log = {
                    lang: " ".join(texts) if isinstance(texts, list) else texts
                    for lang, texts in event.data["translations"].items()
                }

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
                user_email = (
                    user_info["user"]["profile"]["email"] if user_info else None
                )
                await log_google_api_usage(
                    user_uuid=auth.slack_user.ray_client_id,
                    group_uuid=auth.slack_user.ray_user_group_id,
                    organization_uuid=mt_result_extra_data.organization_uuid,
                    input_text=mt_result_extra_data.source_text
                    or "[Source text not available]",
                    source_lang=mt_result_extra_data.source_language,
                    translations=translations_for_log,
                    transaction_uuid=transaction_uuid,
                    app_name="slack",
                    usage_type=mt_result_extra_data.usage_type,
                    text_length=mt_result_extra_data.text_length,
                    channel_name=channel_name,
                    email=user_email,
                )
            except Exception as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"Error processing MT result event: {str(e)}",
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

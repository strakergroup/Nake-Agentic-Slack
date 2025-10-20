import asyncio
from dataclasses import replace
from typing import Annotated, Any

from buglog import notify_exception, notify_message
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ValidationError
from slack_sdk.web.async_client import AsyncWebClient
from straker_utils.credits import calculate_cost, spend_credits

from app.api.models import MtTranslationExtraData
from app.api.verify import get_evaluation_job, get_job_pricing
from app.auth.connector import get_ray_client, get_ray_connection
from app.constants import HUMAN_EVALUATION_WORKFLOW_UUID
from app.database import engines
from app.mt.logs import log_google_api_usage
from app.ray.utils import (
    delete_from_file_server,
    download_from_file_server_async,
    is_ibm_enterprise,
    set_user_language,
)
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
    MtFileReponseSchema,
    MtSuccessResponseSchema,
    SlackAccountConnectedEvent,
)
from ..slack.templates.messages import (
    AutoTranslationMessage,
    ClientApprovedEventMessage,
    ClientSignupEventAdminMessage,
    ClientSignupEventMessage,
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
        update_slack_job(
            task_uuid=success_data.task_uuid,
            status="slack_uploading",
        )
        # Create a new client instance with the correct token for this user
        if auth.slack_user is None:
            return
        client = AsyncWebClient(token=auth.slack_user.bot_token)

        # Download file from server
        output_file = await download_from_file_server_async(success_data.file_id)
        token_count = success_data.tokens
        title = output_file.get("file_name")
        token_consumption_message = (
            _("You have used {token_count} AI tokens.")
            if not is_ibm_enterprise(auth.slack_user.enterprise_id)
            else ""
        )
        # Upload file using memory-efficient method
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=output_file.get("file"),
            channel_id=success_data.channel_id,
            title=title,
            filename=title,
            initial_comment=token_consumption_message,
        )

        update_slack_job(
            task_uuid=success_data.task_uuid,
            status="delivered",
        )
        delete_from_file_server(success_data.file_id)
    except Exception as e:
        notify_exception(e, "Background MT success file handling failed")
        update_slack_job(
            task_uuid=success_data.task_uuid,
            status="failed_delivery",
        )


async def _handle_transcribe_success_background(
    event_data: dict[str, Any],
    auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)],
    response: dict[str, Any],
):
    """Background task to handle transcription success file download and upload."""
    try:
        # Create a new client instance with the correct token for this user
        if auth.slack_user is None:
            return
        client = AsyncWebClient(token=auth.slack_user.bot_token)

        # Download file from server
        file_id = event_data.get("file_id")
        file_name = event_data.get("file_name")
        if not file_id or not file_name:
            notify_exception(
                Exception("Missing file_id or file_name in event_data"),
                "Transcription background task failed",
            )
            return
        output_file = await download_from_file_server_async(file_id)
        # Upload file using memory-efficient method
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=output_file.get("file"),
            channel_id=response["channel"],
            title=file_name,
            filename=output_file.get("file_name"),
        )
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
        await upload_file_to_slack_memory_efficient(
            client=client,
            file_path=output_file.get("file"),
            channel_id=response["channel"],
            title=output_file.get("file_name"),
            filename=output_file.get("file_name"),
        )
    except Exception as e:
        notify_exception(e, "Background verify complete file handling failed")


@router.post("/ray/events")
async def ray_events(
    event: RayEvent, auth: Annotated[RayEventAuth, Depends(get_ray_event_auth)]
):
    """Receives and responds to an event from the RAY platform."""
    client = None

    if auth.slack_user:
        client = AsyncWebClient(token=auth.slack_user.bot_token)
        user_info = await client.users_info(
            user=auth.slack_user.user_id, include_locale=True
        )
        set_user_language(user_info)

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
                if (
                    status_event.status in ("IN_PROGRESS", "CANCELLED")
                    and status_event.previous_status == "LEAD"
                ):
                    message = None
                else:
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

        elif event.event == "transcription:slack:media:results":
            try:
                transcribed_event = JobTranscribedEvent.model_validate(event.data)
                transcribed_message: JobTranscribedEventMessage = (
                    JobTranscribedEventMessage(
                        transcribed_event.task_uuid, transcribed_event.source_file_name
                    )
                )
                # Handle transcription success/error
                if not event.data.get("error"):
                    response = await post_notification(
                        client,
                        event,
                        auth.slack_user,
                        transcribed_message,
                    )
                    _create_background_task(
                        _handle_transcribe_success_background(
                            event.data, auth, response
                        )
                    )
                else:
                    await client.chat_postEphemeral(
                        channel=auth.slack_user.channel_id,
                        user=auth.slack_user.user_id,
                        text=_(
                            "This video cannot be sent for transcription as it doesn't have any sound"
                        ),
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
            try:
                event_data = MtFileReponseSchema.model_validate(event.data)
                mt_message: DocMtMessage = DocMtMessage()
                # Handle MT success/error
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
                        )

                    await post_notification_ephemeral(
                        client,
                        error_data.channel_id or auth.slack_user.channel_id,
                        event,
                        auth.slack_user,
                        mt_message,
                    )
                except ValidationError:
                    success_data = MtSuccessResponseSchema.model_validate(event.data)
                    _create_background_task(
                        _handle_mt_success_background(success_data, auth)
                    )
            except ValidationError as e:
                raise HTTPException(
                    422,
                    {
                        "message": f"The event data is invalid for the event type: {event.event}",
                        "detail": e.errors(),
                    },
                ) from e

        elif event.event == "verify:slack:evaluate:complete":
            if event.data.get("error"):
                message = EvaluateErrorMessage()
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
                        message = HumanJobQuoteMessage(job["data"], costs["data"])
                    else:
                        message = EvaluateSuccessMessage(
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
                if isinstance(message, EvaluateSuccessMessage):
                    await post_notification(
                        client,
                        event,
                        auth.slack_user,
                        message,
                    )
                else:
                    # Handle DocMtMessage case
                    try:
                        error_data = MtErrorResponseSchema.model_validate(event.data)
                        if error_data.error_type == "insufficient_balance":
                            # Send message to user that they need to purchase tokens
                            client_type = await get_client_type(
                                auth.slack_user.ray_client_id,
                                auth.slack_user.ray_user_group_id,
                            )
                            balance = Balance.model_validate(error_data.error_data)

                            if client_type in [
                                "Admin",
                                "Owner",
                            ] and not is_ibm_enterprise(auth.slack_user.enterprise_id):
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
                            )

                        await post_notification_ephemeral(
                            client,
                            error_data.channel_id or auth.slack_user.channel_id,
                            event,
                            auth.slack_user,
                            message,
                        )
                    except ValidationError:
                        success_data = MtSuccessResponseSchema.model_validate(
                            event.data
                        )
                        _create_background_task(
                            _handle_mt_success_background(success_data, auth)
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
                extra_data = MtTranslationExtraData(**event.data["extra_data"])

                # Parse translations from the new service-based response format
                translations = event.data.get("translations", {})

                # Log the response for debugging
                notify_message(
                    f"MT Result - Service mapping: {extra_data.service_language_mapping}, Translations: {translations}"
                )

                if (
                    extra_data.usage_type == "direct_machine_translation"
                    or extra_data.usage_type == "shortcut_translate"
                ):
                    # For direct translation, we need to get the first target language
                    # and combine all translations into a single string
                    first_target_lang = None
                    for langs in extra_data.service_language_mapping.values():
                        if langs:
                            first_target_lang = langs[0]
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
                    assert extra_data.source_text
                    mt_result_message: MachineTranslationMessage = (
                        MachineTranslationMessage(
                            first_target_lang or "unknown",
                            extra_data.source_language,
                            extra_data.source_text,
                            combined_translations,
                        )
                    )
                    # Send message with response method configuration
                    await post_notification(
                        client,
                        event,
                        auth.slack_user,
                        mt_result_message,
                        channel_id=extra_data.channel_id,
                        thread_ts=extra_data.thread_ts,
                        is_edit=extra_data.is_edit,
                        response_url=extra_data.response_url,
                    )
                elif extra_data.usage_type == "channel_translation":
                    # For channel translation, pass the translations dict directly
                    # The AutoTranslationMessage expects {lang: [text1, text2, ...]} format
                    assert extra_data.source_text
                    auto_translation_message: AutoTranslationMessage = (
                        AutoTranslationMessage(
                            extra_data.source_text,
                            extra_data.source_language,
                            translations=translations,
                        )
                    )
                    await post_channel_translation_notification(
                        client,
                        event,
                        auth.slack_user,
                        auto_translation_message,
                        channel_id=extra_data.channel_id,
                        thread_ts=extra_data.thread_ts,
                        is_edit=extra_data.is_edit,
                        display_format=extra_data.display_format,
                        message_ts=extra_data.message_ts,
                    )
                else:
                    raise HTTPException(
                        422,
                        {
                            "message": f"Invalid usage type: {extra_data.usage_type}",
                        },
                    )
                database_engine = engines["sitemanager"]
                # Calculate total languages across all services
                total_languages = sum(
                    len(langs) for langs in extra_data.service_language_mapping.values()
                )
                amount = calculate_cost(extra_data.text_length * total_languages)
                assert auth.slack_user.ray_user_group_id is not None
                transaction_uuid = spend_credits(
                    database_engine,
                    auth.slack_user.ray_client_id,
                    auth.slack_user.ray_user_group_id,
                    amount,
                    "slack",
                    extra_data.usage_type,
                    "Machine Translation",
                    extra_data.organization_uuid,
                )

                # Log Google API usage
                # Convert translations from dict[lang, list[str]] to dict[lang, str]
                translations_for_log = {
                    lang: " ".join(texts) if isinstance(texts, list) else texts
                    for lang, texts in event.data["translations"].items()
                }

                channel_name = None
                if extra_data.channel_id:
                    if not extra_data.channel_id.startswith("C"):
                        channel_name = "direct message"
                    else:
                        try:
                            channel_info = await client.conversations_info(
                                channel=extra_data.channel_id
                            )
                            channel = channel_info.get("channel")
                            channel_name = (
                                channel["name"]
                                if channel and "name" in channel
                                else None
                            )
                        except Exception as e:
                            notify_exception(e, "Failed to get channel info")

                await log_google_api_usage(
                    user_uuid=auth.slack_user.ray_client_id,
                    group_uuid=auth.slack_user.ray_user_group_id,
                    organization_uuid=extra_data.organization_uuid,
                    input_text=extra_data.source_text or "[Source text not available]",
                    source_lang=extra_data.source_language,
                    translations=translations_for_log,
                    transaction_uuid=transaction_uuid,
                    app_name="slack",
                    usage_type=extra_data.usage_type,
                    text_length=extra_data.text_length,
                    channel_name=channel_name,
                )
            except Exception as e:
                raise e
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
                is_auto_quote = get_job_group_quote_settings(job_data["tj_number"][2:])
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

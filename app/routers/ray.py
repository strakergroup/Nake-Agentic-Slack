import os
from typing import Any, Annotated
from buglog import notify_exception, notify_message
from fastapi import APIRouter, HTTPException, Depends, Header, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError
from app.translate import translator_var, Translator

from app.ray.utils import (
    download_from_file_server,
    is_ibm_enterprise,
    set_user_language,
)
from app.translate import _
from app.wb_tasks.tasks import get_task

from ..auth.connector import (
    SlackUser,
    get_client_type,
    get_demo_link,
    spend_mt_tokens,
    validate_api_callback_signature,
    get_slack_user,
    get_client_access_tokens,
    get_group_admin_slack_users,
)
from ..dependencies import RayEventAuth, RayEvent
from ..slack import app
from ..slack.templates.messages import (
    DocMtMessage,
    RequiresMtTokenAdminMessage,
    RequiresMtTokenMessage,
    SuccessfulLoginMessage,
    ClientSignupEventMessage,
    ClientSignupEventAdminMessage,
    ClientApprovedEventMessage,
    JobCreationMessage,
    JobTranscribedEventMessage,
)
from ..ray.events.parse import get_ray_event_message
from ..ray.events.models import (
    ClientGroup,
    MtErrorResponseSchema,
    MtSuccessResponseSchema,
)
from ..ray.events.logging import post_notification, post_notification_ephemeral
from dataclasses import replace
from pathlib import Path
from ..config import config, domains


router = APIRouter()


@router.post("/ray/events")
async def ray_events(event: RayEvent, auth: Annotated[RayEventAuth, Depends()]):
    """Receives and responds to an event from the RAY platform."""
    try:
        message = None
        if auth.slack_user:
            app.client.token = auth.slack_user.bot_token
            user_info = await app.client.users_info(
                user=auth.slack_user.user_id, include_locale=True
            )
            set_user_language(user_info)
            message = get_ray_event_message(event.event, event.data, auth.slack_user)
    except ValidationError as e:
        raise HTTPException(
            422,
            {
                "message": f"The event data is invalid for the event type: {event.event}",
                "detail": e.errors(),
            },
        ) from e
    except ValueError:
        raise HTTPException(400, f"The event type is invalid: {event.event}") from None
    if auth.slack_user is not None and message is not None:
        # Send login message to the same conversation where it was prompted.
        if isinstance(message, SuccessfulLoginMessage):
            await post_notification_ephemeral(
                app.client, auth.slack_user.channel_id, event, auth.slack_user, message
            )
        elif isinstance(message, DocMtMessage):
            try:
                event_data = MtErrorResponseSchema.model_validate(event.data)
                if event_data.error_type == "insufficient_balance":
                    # Send message to user that they need to purchase tokens
                    client_type = await get_client_type(
                        auth.slack_user.ray_client_id,
                        auth.slack_user.ray_user_group_id,
                    )
                    token_balance = event_data.error_data.get("balance")
                    required = event_data.error_data.get("required")

                    if client_type in ["Admin", "Owner"] and not is_ibm_enterprise(
                        auth.slack_user.team_id, auth.slack_user.enterprise_id
                    ):
                        message = RequiresMtTokenMessage(token_balance, required)
                    else:
                        message = RequiresMtTokenAdminMessage(token_balance, required)

                await post_notification_ephemeral(
                    app.client,
                    auth.slack_user.channel_id,
                    event,
                    auth.slack_user,
                    message,
                )
            except ValidationError:
                app.client.token = auth.slack_user.bot_token
                event_data = MtSuccessResponseSchema.model_validate(event.data)
                output_file = download_from_file_server(event_data.file_id)
                token_count = event_data.tokens
                token_count = await spend_mt_tokens(
                    user=auth.slack_user, credits=token_count
                )
                target_lang = event_data.target_language
                title = target_lang + "_" + output_file.get("file_name")
                token_consumption_message = _("You have used {token_count} AI tokens.")
                await app.client.files_upload_v2(
                    channel=auth.slack_user.channel_id,
                    file=output_file.get("file"),
                    initial_comment=token_consumption_message,
                    title=title,
                    filename=title,
                )
        elif isinstance(message, JobTranscribedEventMessage):
            if not event.data.get("error"):
                await post_notification_ephemeral(
                    app.client,
                    auth.slack_user.channel_id,
                    event,
                    auth.slack_user,
                    message,
                )
            else:
                await app.client.chat_postEphemeral(
                    channel=auth.slack_user.channel_id,
                    user=auth.slack_user.user_id,
                    text=_(
                        "This video cannot be sent for transcription as it doesn't have any sound"
                    ),
                )
        elif (
            # Send important messages regardless of subscribed status.
            isinstance(message, (ClientSignupEventMessage, ClientApprovedEventMessage))
            # Send all other messages if the client is subscribed to notifications.
            or auth.slack_user.is_subscribed
        ):
            if auth.demo_slack_users:
                for slack_user_id in auth.demo_slack_users:
                    new_slack_user = replace(auth.slack_user, user_id=slack_user_id)
                    # auth.slack_user.user_id = slack_user_id
                    try:
                        await post_notification(
                            app.client, event, new_slack_user, message
                        )
                    except Exception as e:
                        notify_exception(
                            e, "Failed to send notification to send demo message"
                        )
            else:
                await post_notification(app.client, event, auth.slack_user, message)

    # Send notifications to group admins when a new client signs up.
    if isinstance(message, ClientSignupEventMessage):
        admins: dict[str, tuple[SlackUser, list[ClientGroup]]] = {}
        for group in message.event.groups:
            admin_slack_users = get_group_admin_slack_users(group.uuid)
            for admin in admin_slack_users:
                if admin.ray_client_id not in admins:
                    admins[admin.ray_client_id] = (admin, [])
                admins[admin.ray_client_id][1].append(group)

        for user, groups in admins.values():
            admin_message = ClientSignupEventAdminMessage(
                event=message.event,
                groups=groups,
            )
            await post_notification(app.client, event, user, admin_message)
    elif not auth.slack_user:
        notify_message("Slack user not found in events endpoint", severity="WARNING")
        raise HTTPException(401)

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
    slack_user = get_slack_user(client_id)
    demo_slack_users = get_demo_link(client_id)
    if slack_user is None:
        notify_message("Slack user not found in callback endpoint", severity="WARNING")
        raise HTTPException(401)
    app.client.token = slack_user.bot_token
    user_info = await app.client.users_info(
        user=slack_user.user_id, include_locale=True
    )
    set_user_language(user_info)
    # Validate X-Straker-Signature.
    raw_body = await request.body()
    access_tokens = get_client_access_tokens(slack_user.ray_client_id)
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
            message = JobCreationMessage(job_data["tj_number"])
        except (KeyError, IndexError):
            raise HTTPException(422, "The callback payload format is invalid") from None
        app.client.token = slack_user.bot_token
        if demo_slack_users:
            for slack_user_id in demo_slack_users:
                await app.client.chat_postMessage(
                    channel=slack_user_id,
                    text=message.text,
                    blocks=message.blocks,
                )
        else:
            await app.client.chat_postMessage(
                channel=slack_user.user_id,
                text=message.text,
                blocks=message.blocks,
            )
        return {
            "message": "success",
            "detail": "Slack user notified of event: JOB_NUMBER",
        }
    # RAY-58084: Send one notification when job is completed instead of multiple.
    # elif "JOB_COMPLETED" in body.event_types:
    #     try:
    #         job_data = body.job[0]
    #         message = FileTranslatedMessage(
    #             job_data["tj_number"],
    #             job_data["source_file"],
    #             job_data["sl"],
    #             job_data["translated_file"],
    #         )
    #     except (KeyError, IndexError):
    #         raise HTTPException(
    #             status.HTTP_422_UNPROCESSABLE_ENTITY,
    #             "The callback payload format is invalid",
    #         )
    #     app.client.token = slack_user.bot_token
    #     await app.client.chat_postMessage(
    #         channel=slack_user.user_id, text=message.text, blocks=message.blocks
    #     )
    #     return {
    #         "message": "success",
    #         "detail": "Slack user notified of event: JOB_COMPLETED",
    #     }
    else:
        return {
            "message": "success",
            "detail": f"Unhandled callback event: {body.event_types}",
        }

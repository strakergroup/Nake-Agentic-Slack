"""Handlers for job status, search, listing, quotes and account info."""

import json
import re
from typing import Any, Dict, Optional

from pydantic import ValidationError
from ray_sdk import RayAPIResponseError
from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncRespond
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import (
    RayContext,
    get_group_quote_settings,
)
from app.ray.utils import is_ibm_enterprise
from app.slack.buglog_notifier import notify_exception
from app.slack.listener_actions import (
    cancel_job_process,
    post_batch_list,
    post_file_list,
    post_job_details,
    post_job_list,
    post_job_status,
    post_job_summary,
    submit_job,
)
from app.slack.middleware import populate_ray_connection, require_ray_client
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from app.slack.templates.messages import (
    InfoMessage,
    JobCreationMessage,
    JobDelayMessage,
    JobSubmitMessage,
    LoginMessage,
    QuoteMessage,
)
from app.slack.templates.models import (
    JobSearchForm,
    NewJobForm,
    convert_pydantic_to_slack_error,
)
from app.slack.templates.views import (
    cancel_job_modal,
    job_search_modal,
)
from app.translate import _


async def handle_show_job_details(
    action: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    """Get job info. Triggered from the "View More Info" in the job list"""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        try:
            job_info = json.loads(payload["value"])
            job_id, status = job_info["id"], job_info["status"]
        except (KeyError, json.JSONDecodeError):
            pass
        else:
            await post_job_details(
                client, context, context["ray"].client, job_id, status
            )


async def handle_quote(context: RayContext, client: AsyncWebClient):
    """Get quote. Triggered from the Home View New Job button"""
    if await require_ray_client(context, variation=LoginMessage.NEW_JOB):
        msg = QuoteMessage()
        await client.chat_postMessage(
            channel=context["user_id"],
            text=msg.text,
            blocks=msg.blocks,
        )


async def handle_daily_summary(context: RayContext, client: AsyncWebClient):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await post_job_summary(client, context, context["ray"].client)


async def handle_all_summary(context: RayContext, client: AsyncWebClient):
    """Get daily summary. Triggered from the Home View Daily Summary button"""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await post_job_summary(
            client, context=context, ray_client=context["ray"].client, all_jobs=True
        )


async def handle_job_search_action(
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, variation=LoginMessage.NEW_JOB):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to continue."),
                ),
            )
            return
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await safe_views_update(
            client,
            view_id,
            job_search_modal(context["ray"].client.username),
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_job_list(
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    """Paginated job list. Triggered from the job summary dropdown."""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        if "selected_option" in payload:
            preset = payload["selected_option"].get("value")
            await post_job_list(client, context, context["ray"].client, preset=preset)
        else:
            preset = payload.get("value")
            await post_job_list(client, context, context["ray"].client, preset=preset)


async def handle_job_list_paginated(
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    """Paginated job list. Triggered from the job list "Show more" and
    "Show previous" buttons.
    """
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        try:
            settings = json.loads(payload["value"])
            preset = settings["preset"]
            client_ref = settings["client_reference"]
            page, page_size = settings["page"], settings["page_size"]
        except (KeyError, json.JSONDecodeError):
            pass
        else:
            await post_job_list(
                client,
                context,
                context["ray"].client,
                preset=preset,
                client_ref=client_ref,
                page=page,
                page_size=page_size,
                replace_original=True,
            )


async def handle_account_info(context: RayContext, respond: AsyncRespond):
    if await require_ray_client(context):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        msg = InfoMessage(
            ray_client=context["ray"].client,
            user_id=context["user_id"],
            team_id=context["team_id"],
            enterprise_id=context.enterprise_id,
            channel_id=context["channel_id"],
            is_ibm=is_ibm_enterprise(context.enterprise_id),
        )
        await respond(text=msg.text, blocks=msg.blocks, replace_original=False)


async def handle_connect_info(context: RayContext, respond: AsyncRespond):
    msg = LoginMessage(
        user_id=context["user_id"],
        team_id=context["team_id"],
        enterprise_id=context.enterprise_id,
        channel_id=context.get("channel_id", context["user_id"]),
        ray_client=context["ray"].client if context["ray"] is not None else None,
    )
    await respond(text=msg.text, blocks=msg.blocks, replace_original=False)


async def handle_delay_info(respond: AsyncRespond):
    await respond(JobDelayMessage().text, JobDelayMessage().blocks)


async def handle_new_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    if await require_ray_client(context, prompt_login=False):
        try:
            form_data = view["state"]["values"] if view else {}
            form = NewJobForm.parse_slack(form_data)
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")
        # The response is already returned at this point, can do long tasks here.
        # Process files and submit job.
        try:
            assert context["ray"] is not None
            assert context["ray"].client is not None
            responses = await submit_job(client, context["ray"].client, form)
            result = responses[0].response.json()["Message"]
            group_id = form.group_id or context["ray"].client.user_group_id
            if "job_id" in result:
                if not is_ibm_enterprise(
                    context.enterprise_id
                ) or get_group_quote_settings(group_id):
                    message = JobSubmitMessage(form)
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=message.text,
                        blocks=message.blocks,
                    )
                else:
                    job_msg = JobCreationMessage(result["job_id"], False)
                    await client.chat_postMessage(
                        channel=context["user_id"],
                        text=job_msg.text,
                        blocks=job_msg.blocks,
                    )
        except Exception as e:
            if isinstance(e, RayAPIResponseError):
                try:
                    notify_exception(e, extra={"response": e.response.json()})
                except Exception:
                    notify_exception(e, extra={"response": e.response.content.decode()})
            else:
                notify_exception(e)
            await client.chat_postMessage(
                channel=context["user_id"],
                text="There was an error submitting your translation request, please try again.",  # noqa: B950
            )
        else:
            for response in responses:
                context["log"].add_api_log(
                    status_code=response.response.status_code,
                    url=str(response.response.url),
                    payload=None,  # TODO: log payload without file
                    response=response.response.content.decode() or None,
                    headers=dict(response.response.headers.items()),
                    version="v3",
                )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


async def handle_job_search(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    if await require_ray_client(context, prompt_login=False):
        try:
            form_data = view.get("state", {}).get("values") if view else {}
            form = JobSearchForm.parse_slack(form_data)
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")
        # The response is already returned at this point, can do long tasks here.
        reference = form.reference.strip().replace(" ", "")
        # Try searching job by TJ number if the format is correct.
        if re.fullmatch(r"TJ\d+(,\s?TJ\d+)*", reference, re.IGNORECASE):
            await post_job_status(client, context, context["ray"].client, reference)
        elif re.fullmatch(r"\d+(,\s?\d+)*", reference, re.IGNORECASE):
            await post_job_status(
                client, context, context["ray"].client, "TJ" + reference
            )
        else:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("TJ Number is in incorrect format. E.g. TJ123456 or 123456"),
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


async def handle_cancel_job_action(
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
    body: Dict[str, Any],
):
    # Non-modal cancel paths do not need trigger_id; load Ray first.
    if "value" in payload:
        job_info = json.loads(payload["value"])
        if job_info.get("job_action") in {"list", "submit"}:
            await populate_ray_connection(context)
            if not await require_ray_client(context, variation=LoginMessage.NEW_JOB):
                return
            assert context["ray"] is not None
            assert context["ray"].client is not None
            if job_info.get("job_action") == "list":
                job_id = job_info["job_id"].split("TJ")[1]
                await cancel_job_process(
                    client, context, context["ray"].client, job_id=job_id
                )
            else:
                await cancel_job_process(
                    client, context, context["ray"].client, job_uuid=job_info["job_id"]
                )
            return

    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, variation=LoginMessage.NEW_JOB):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to continue."),
                ),
            )
            return
        assert context["ray"] is not None
        assert context["ray"].client is not None
        await safe_views_update(
            client, view_id, cancel_job_modal(context["ray"].client.username)
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_cancel_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    """Get job info. Triggered from the "View More Info" in the job list"""
    await ack()
    if await require_ray_client(context, prompt_login=False):
        assert context["ray"] is not None
        assert context["ray"].client is not None
        try:
            form_state = view["state"]["values"] if view else {}
            form = JobSearchForm.parse_slack(form_state)
        except ValidationError as e:
            errors = convert_pydantic_to_slack_error(e)
            await ack(response_action="errors", errors=errors)
            return
        await ack(response_action="clear")
        reference = form.reference.strip().lower()
        # Try searching job by TJ number if the format is correct.
        if re.fullmatch(r"tj\d+", reference, re.IGNORECASE):
            job_id = reference.split("tj")[1]
            await cancel_job_process(client, context, context["ray"].client, job_id)
        elif re.fullmatch(r"\d+", reference, re.IGNORECASE):
            await cancel_job_process(client, context, context["ray"].client, reference)
        else:
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_("TJ Number is in incorrect format. E.g. TJ123456 or 123456"),
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )


async def handle_batch_list(
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    """Paginated batch file list. Triggered from the Show In Progress Files button."""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        settings = json.loads(payload["value"])
        job_id = settings["id"]
        page = settings["page"]
        page_size = settings["page_size"]
        replace_original = settings["replace_original"]
        await post_batch_list(
            client,
            context,
            context["ray"].client,
            job_id=job_id,
            page=page,
            page_size=page_size,
            replace_original=replace_original,
        )


async def handle_file_list(
    payload: Dict[str, Any],
    context: RayContext,
    client: AsyncWebClient,
):
    """Paginated file list. Triggered from the Show Files button."""
    if await require_ray_client(context, variation=LoginMessage.GET_JOB):
        settings = json.loads(payload["value"])
        job_id = settings["id"]
        page = settings["page"]
        page_size = settings["page_size"]
        replace_original = settings["replace_original"]
        await post_file_list(
            client,
            context,
            context["ray"].client,
            job_id=job_id,
            page=page,
            page_size=page_size,
            replace_original=replace_original,
        )

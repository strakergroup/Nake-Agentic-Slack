"""Handlers for document machine-translation jobs."""

import json
import os
from typing import Any, Dict, Optional

from slack_bolt.kwargs_injection.async_args import AsyncAck, AsyncSay
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.auth.connector import RayContext
from app.ray.submissions import check_and_record_submission_async
from app.ray.utils import upload_to_file_server
from app.redis import redis_conn
from app.saq_jobs import enqueue_document_mt_quote_preflight
from app.slack.buglog_notifier import notify_exception
from app.slack.document_mt_quotes import new_document_mt_quote_id
from app.slack.file_submissions import slack_file_submission_payload_from_option
from app.slack.listener_actions import (
    document_machine_translate,
    document_mt_selected_languages,
    get_accessible_slack_files,
    is_slack_file_not_found,
    notify_missing_slack_files,
)
from app.slack.middleware import (
    populate_ray_connection,
    require_mt_tokens,
    require_ray_client,
)
from app.slack.modal_trigger import (
    open_loading_modal,
    request_error_modal,
    safe_views_update,
    status_modal,
)
from app.slack.templates.views import document_mt_job_modal
from app.slack.web import download_file
from app.translate import _


async def handle_document_mt_job_action(
    context: RayContext,
    action: Optional[Dict[str, Any]],
    body: Dict[str, Any],
    client: AsyncWebClient,
):
    assert action is not None
    action_data = json.loads(action.get("value", ""))
    files = action_data.get("files", [])
    channel_id = action_data.get("channel_id")
    if not files:
        await populate_ray_connection(context)
        await client.chat_postMessage(
            channel=context["user_id"],
            text=_("No files found in the message. Please upload files to translate."),
        )
        return

    view_id = await open_loading_modal(client, body["trigger_id"])
    try:
        await populate_ray_connection(context)
        if not await require_ray_client(context, allow_org_billing=True):
            await safe_views_update(
                client,
                view_id,
                status_modal(
                    _("Sign in required"),
                    _("Please sign in to LanguageCloud to continue."),
                ),
            )
            return
        await safe_views_update(
            client, view_id, document_mt_job_modal(channel_id, files)
        )
    except Exception as e:
        notify_exception(e)
        await safe_views_update(client, view_id, request_error_modal())


async def handle_document_mt_submit(
    action: Optional[Dict[str, Any]],
    context: RayContext,
    body: Dict[str, Any],
    say: AsyncSay,
    client: AsyncWebClient,
):
    if await require_ray_client(context, allow_org_billing=True):
        assert action is not None
        slack_file_ids = json.loads(action["value"])
        selected_language = await redis_conn.get(f"output_file_{action['value']}")
        selected_languages = document_mt_selected_languages(selected_language)
        # get uuid from output_file
        if await require_mt_tokens(context, 1):
            # get selected language from redis keyed on output_file
            # selected from get_auto_translate_language_options
            for slack_file_id in slack_file_ids:
                if selected_languages:
                    try:
                        input_file = await download_file(
                            client=client, file_id=slack_file_id, http=None
                        )
                    except SlackApiError as e:
                        if is_slack_file_not_found(e):
                            await notify_missing_slack_files(
                                client,
                                context["user_id"],
                                [{"id": slack_file_id, "title": slack_file_id}],
                            )
                            continue
                        raise
                    input_file_id = await upload_to_file_server(input_file)
                    # Dedupe check and record in DB
                    submitted_languages: list[str] = []
                    submission_ids: dict[str, int] = {}
                    for selected_language_code in selected_languages:
                        is_dup, _record = await check_and_record_submission_async(
                            path=input_file,
                            file_name=os.path.basename(input_file),
                            file_id=input_file_id,
                            user_id=context["user_id"],
                            team_id=context["team_id"],
                            channel_id=context.get("channel_id", context["user_id"]),
                            target_language=selected_language_code,
                        )
                        if is_dup:
                            await say(
                                _(
                                    f"This file has already been submitted for {selected_language_code}. Skipping duplicate."
                                )
                            )
                            continue
                        submitted_languages.append(selected_language_code)
                        submission_ids[selected_language_code] = _record.id

                    if submitted_languages:
                        await document_machine_translate(
                            context,
                            input_file_id,
                            None,
                            submitted_languages,
                            submission_ids,
                        )
                        await say(
                            _(
                                "The file is being translated. You will be notified when it is ready."
                            )
                        )
                else:
                    await say(_("Please select a language to translate to."))


async def handle_document_mt_job(
    ack: AsyncAck,
    view: Optional[Dict[str, Any]],
    context: RayContext,
    client: AsyncWebClient,
):
    """Handle document machine translation job submission."""
    if await require_ray_client(context, prompt_login=False, allow_org_billing=True):
        acked = False
        try:
            form_data = view["state"]["values"] if view else {}
            selected_source_language = (
                form_data.get("source_lang", {})
                .get("language_mt_options", {})
                .get("selected_option", {})
                .get("value")
            )
            selected_languages = (
                form_data.get("target_langs", {})
                .get("language_mt_options", {})
                .get("selected_options", [])
            )

            if not selected_source_language:
                await ack(
                    response_action="errors",
                    errors={
                        "source_lang": _(
                            "Please select a source language for translation."
                        )
                    },
                )
                return

            if any(
                str(lang.get("value", "")) == selected_source_language
                for lang in selected_languages
            ):
                await ack(
                    response_action="errors",
                    errors={
                        "target_langs": _(
                            "The source language cannot be the same as a target language. Please choose a different target language."
                        )
                    },
                )
                return

            await ack(response_action="clear")
            acked = True

            if not selected_languages:
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=_(
                        "Please select at least one target language for translation."
                    ),
                )
                return

            # Get the file IDs from the form state
            files = (
                form_data.get("files", {}).get("files", {}).get("selected_options", [])
            )

            if not files:
                await client.chat_postMessage(
                    channel=context["user_id"],
                    text=_("Please select at least one file to translate."),
                )
                return

            channel_id = (
                view["private_metadata"]
                if view and "private_metadata" in view
                else None
            )
            context["channel_id"] = channel_id or context["user_id"]
            file_payloads = [
                slack_file_submission_payload_from_option(file) for file in files
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

            selected_file_titles = [str(file["title"]) for file in file_payloads]
            quote_id = new_document_mt_quote_id()
            await enqueue_document_mt_quote_preflight(
                quote_id=quote_id,
                user_id=context["user_id"],
                team_id=context["team_id"],
                enterprise_id=context.enterprise_id,
                channel_id=context["channel_id"],
                files=file_payloads,
                source_language=selected_source_language,
                target_languages=[str(lang["value"]) for lang in selected_languages],
            )
            await client.chat_postMessage(
                channel=context["channel_id"],
                text=_(
                    f"Preparing an AI Translate quote for document(s) *({', '.join(selected_file_titles)})*. Please review the quote before translation starts."
                ),
            )

        except Exception as e:
            if not acked:
                try:
                    await ack(response_action="clear")
                except Exception:
                    pass
            notify_exception(e)
            await client.chat_postMessage(
                channel=context["user_id"],
                text=_(
                    "There was an error submitting your translation request, please try again."
                ),
            )
    else:
        await ack(response_action="clear")
        await client.chat_postMessage(
            channel=context["user_id"],
            blocks=context["login_prompt"].blocks,
            text=context["login_prompt"].text,
        )

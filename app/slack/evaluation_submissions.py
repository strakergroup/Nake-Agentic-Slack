from __future__ import annotations

import httpx

from app.config import domains
from app.ray.utils import upload_to_file_server


async def publish_pdf_evaluate_convert(
    *,
    ray_client,
    input_files: list[str],
    file_titles: list[str],
    target_langs_uuid: list[str],
    reference: str,
    channel_id: str,
    source_lang_uuid: str = "",
    workflow_uuid: str | None = None,
    job_notes: str = "",
    workflow_version: float = 3.0,
    docconverter_version: str = "m48",
    preaccepted_ai_translation_quote: bool = False,
    prequote_message_ts: str | None = None,
    ai_translation_filename_and_languages: list[str] | None = None,
    slack_ht_quote_after_qe: bool = False,
    confirmation_required: bool = True,
    slack_user_id: str = "",
    slack_team_id: str = "",
    slack_enterprise_id: str | None = None,
    requester_email: str = "",
) -> None:
    """Upload files to GridFS and publish to the PDF evaluate conversion stream."""
    file_ids = []
    for file_path in input_files:
        file_id = await upload_to_file_server(file_path)
        file_ids.append(file_id)

    payload = {
        "client_uuid": ray_client.id,
        "file_ids": file_ids,
        "file_names": file_titles,
        "target_languages_uuid": target_langs_uuid,
        "source_language_uuid": source_lang_uuid,
        "reference": reference,
        "workflow_uuid": workflow_uuid or "",
        "job_notes": job_notes,
        "workflow_version": workflow_version,
        "docconverter_version": docconverter_version,
        "channel_id": channel_id,
        "app_source": "slack",
        "confirmation_required": confirmation_required,
        "preaccepted_ai_translation_quote": preaccepted_ai_translation_quote,
        "slack_ht_quote_after_qe": slack_ht_quote_after_qe,
    }
    if prequote_message_ts:
        payload["prequote_message_ts"] = prequote_message_ts
    if ai_translation_filename_and_languages:
        payload["ai_translation_filename_and_languages"] = (
            ai_translation_filename_and_languages
        )
    if slack_user_id:
        payload["slack_user_id"] = slack_user_id
    if slack_team_id:
        payload["slack_team_id"] = slack_team_id
    if slack_enterprise_id:
        payload["slack_enterprise_id"] = slack_enterprise_id
    if requester_email:
        payload["requester_email"] = requester_email

    async with httpx.AsyncClient() as http:
        await http.post(
            f"{domains.stream_proxy}/events/slack:evaluate:pdf:convert",
            json={
                "data": payload,
                "source": "Straker Translate for Slack",
            },
        )

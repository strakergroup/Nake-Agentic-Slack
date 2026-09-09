"""Parse Slack Configure-media modal submissions into workflow flags."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.media.media_workflow import MediaWorkflowType
from app.slack.media_quotes import PIPELINE_TRANSCRIBE, PIPELINE_TRANSCRIBE_TRANSLATE
from app.translate import _


class VideoConfigureMediaError(Exception):
    """Raised when the Configure media modal is missing required selections."""


class VideoConfigureMediaSelection(BaseModel):
    workflow_type: MediaWorkflowType
    embed_source: bool
    embed_translated: bool
    review_gate: bool
    target_languages: list[str]
    target_language_names: list[str]
    files: list[dict[str, Any]]
    channel_id: str
    thread_ts: str | None = None
    show_embed_option: bool = True


def _selected_options(
    values: dict[str, Any], block_id: str, action_id: str
) -> list[dict]:
    block = values.get(block_id) or {}
    action = block.get(action_id) or {}
    return list(action.get("selected_options") or [])


def _checkbox_selected(
    values: dict[str, Any], block_id: str, action_id: str, value: str
) -> bool:
    return any(
        opt.get("value") == value
        for opt in _selected_options(values, block_id, action_id)
    )


def parse_video_configure_media_view(
    view: dict[str, Any],
) -> VideoConfigureMediaSelection:
    metadata = json.loads(view["private_metadata"])
    values = view["state"]["values"]
    workflow_block = values.get("workflow_type") or {}
    workflow_action = workflow_block.get("video_configure_workflow_type") or {}
    selected_workflow = (workflow_action.get("selected_option") or {}).get("value")
    try:
        workflow_type = MediaWorkflowType(selected_workflow)
    except ValueError as exc:
        raise VideoConfigureMediaError(_("Please choose a media workflow.")) from exc

    all_files = list(metadata.get("files") or [])
    selected_file_ids = {
        opt["value"]
        for opt in _selected_options(values, "selected_file", "file_display")
    }
    files = [
        file_info
        for file_info in all_files
        if file_info["file_id"] in selected_file_ids
    ]
    if not files:
        raise VideoConfigureMediaError(_("Please select at least one file to process."))

    lang_options = _selected_options(values, "target_languages", "language_mt_options")
    target_languages = [opt["value"] for opt in lang_options]
    target_language_names = [
        opt.get("text", {}).get("text", opt["value"]) for opt in lang_options
    ]
    if workflow_type is MediaWorkflowType.TRANSCRIBE_TRANSLATE and not target_languages:
        raise VideoConfigureMediaError(
            _("Please select at least one target language for translation.")
        )

    show_embed_option = bool(metadata.get("show_embed_option", True))
    embed_source = show_embed_option and _checkbox_selected(
        values, "embed_source", "embed_source_options", "embed_source"
    )
    embed_translated = (
        show_embed_option
        and workflow_type is MediaWorkflowType.TRANSCRIBE_TRANSLATE
        and _checkbox_selected(
            values, "embed_translated", "embed_translated_options", "embed_translated"
        )
    )
    if workflow_type is MediaWorkflowType.TRANSCRIBE_ONLY:
        target_languages = []
        target_language_names = []
        embed_translated = False

    review_present = "review_gate" in values
    review_gate = (
        _checkbox_selected(values, "review_gate", "review_gate_options", "review_gate")
        if review_present
        else True
    )

    return VideoConfigureMediaSelection(
        workflow_type=workflow_type,
        embed_source=embed_source,
        embed_translated=embed_translated,
        review_gate=review_gate,
        target_languages=target_languages,
        target_language_names=target_language_names,
        files=files,
        channel_id=str(metadata.get("channel_id") or ""),
        thread_ts=metadata.get("thread_ts"),
        show_embed_option=show_embed_option,
    )


def configure_media_quote_fields(
    selection: VideoConfigureMediaSelection,
) -> dict[str, Any]:
    pipeline_kind = (
        PIPELINE_TRANSCRIBE
        if selection.workflow_type is MediaWorkflowType.TRANSCRIBE_ONLY
        else PIPELINE_TRANSCRIBE_TRANSLATE
    )
    return {
        "pipeline_kind": pipeline_kind,
        "target_languages": selection.target_languages,
        "target_language_names": selection.target_language_names,
        "extra": {
            "embed_source": selection.embed_source,
            "embed_translated": selection.embed_translated,
            "review_gate": selection.review_gate,
            "workflow_type": selection.workflow_type.value,
        },
    }

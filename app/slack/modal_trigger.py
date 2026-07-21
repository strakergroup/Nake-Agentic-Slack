"""Helpers for Slack modal opens that must beat the trigger_id TTL.

Slack ``trigger_id`` values expire in a few seconds. Handlers that open modals
must call ``views.open`` before awaited Ray/DB/Redis/Slack I/O. Prefer:

1. ``views_open`` with :func:`loading_modal`
2. slow work (Ray connection, pricing, settings, …)
3. ``views_update`` with the final (or error) view

See ``.cursor/rules/slack-modal-trigger-safety.mdc`` and RAY-72999.
"""

from __future__ import annotations

from typing import Any

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.translate import _

from .templates.views import loading_modal


async def open_loading_modal(client: AsyncWebClient, trigger_id: str) -> str:
    """Open the shared loading modal and return its view id."""
    response = await client.views_open(trigger_id=trigger_id, view=loading_modal())
    view = response.get("view") if response is not None else None
    if not isinstance(view, dict) or "id" not in view:
        raise RuntimeError("Slack views.open did not return a view id")
    return str(view["id"])


async def safe_views_update(
    client: AsyncWebClient, view_id: str, view: dict[str, Any]
) -> bool:
    """Update a modal, ignoring ``view_closed`` (user dismissed it).

    Returns:
        True if the update succeeded, False if the view was already closed.
    """
    try:
        await client.views_update(view_id=view_id, view=view)
        return True
    except SlackApiError as slack_e:
        if slack_e.response.get("error") == "view_closed":
            return False
        raise


def status_modal(title: str, message: str) -> dict[str, Any]:
    """Simple informational/error modal used after a loading open fails."""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": title, "emoji": True},
        "close": {"type": "plain_text", "text": _("Close"), "emoji": True},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message,
                    "verbatim": True,
                },
            }
        ],
    }


def request_error_modal() -> dict[str, Any]:
    """Standard modal when modal-open processing fails unexpectedly."""
    return status_modal(
        _("Error"),
        _("There was an error processing your request. Please try again."),
    )

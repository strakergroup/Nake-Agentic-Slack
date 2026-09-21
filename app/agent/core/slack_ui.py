"""Pure builders for the Slack payloads the agent sends. No Slack client here.

Payload shapes follow docs.slack.dev/ai (agent sessions and chat streaming), read
on 2026-09-21. The adapter sends them with `client.api_call` because the app's
locked slack-sdk predates these methods.
"""

from __future__ import annotations

import json
from typing import Any

from . import copy
from .types import AgentFacts, PendingApproval

TASK_STATUS = {
    "pending": "pending",
    "in_progress": "in_progress",
    "complete": "complete",
    "error": "error",
}

# action_ids of the app's existing form openers (app/slack/listeners.py). The
# hand-off button reuses them so the existing handlers, and their modal
# trigger-safety pattern, do the opening. None means no button opener exists.
FORM_ACTION_IDS: dict[str, str | None] = {
    "document_translation": "document_mt_job",
    "media": "video_transcribe_translate",
    "quality_evaluation": "evaluate_job",
    "human_translation": "evaluate_job",
    "channel_settings": "settings_auto_translate",
    "new_job": None,
}


def status_payload(
    facts: AgentFacts, status: str, title: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "channel_id": facts.channel_id,
        "thread_ts": facts.thread_ts,
        "status": status,
    }
    if title:
        payload["title"] = title[:100]
    return payload


def rename_payload(facts: AgentFacts, title: str) -> dict[str, Any]:
    return {
        "channel_id": facts.channel_id,
        "thread_ts": facts.thread_ts,
        "title": title[:100],
    }


def stream_start_payload(facts: AgentFacts, text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "channel": facts.channel_id,
        "thread_ts": facts.thread_ts,
        "task_display_mode": "plan",
    }
    if text:
        payload["chunks"] = [{"type": "markdown_text", "markdown_text": text}]
    if facts.surface != "dm":
        payload["recipient_user_id"] = facts.user_id
        payload["recipient_team_id"] = facts.team_id
    return payload


def task_chunks(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks = []
    for card in plan:
        task: dict[str, Any] = {
            "task_id": card["id"],
            "title": card["title"],
            "status": TASK_STATUS[card["state"]],
        }
        if card.get("detail"):
            task["output"] = {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [{"type": "text", "text": card["detail"]}],
                    }
                ],
            }
        chunks.append({"type": "task_update", "task": task})
    return chunks


def stream_append_payload(
    facts: AgentFacts, ts: str, plan: list[dict[str, Any]]
) -> dict[str, Any]:
    return {"channel": facts.channel_id, "ts": ts, "chunks": task_chunks(plan)}


def stream_stop_payload(
    facts: AgentFacts,
    ts: str,
    text: str,
    blocks: list[dict[str, Any]] | None,
    session_status: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "channel": facts.channel_id,
        "ts": ts,
        "session_status": session_status,
    }
    if text:
        payload["chunks"] = [{"type": "markdown_text", "markdown_text": text}]
    if blocks:
        payload["blocks"] = blocks
    return payload


def _button(
    text: str, action_id: str, value: str, primary: bool = False
) -> dict[str, Any]:
    button: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": False},
        "action_id": action_id,
        "value": value,
    }
    if primary:
        button["style"] = "primary"
    return button


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def approval_blocks(
    approval: PendingApproval, approve_label: str = copy.APPROVE
) -> list[dict[str, Any]]:
    """The button value is the approval id and nothing else: the action that runs is
    the one stored on the session, not anything a click can carry."""
    summary = approval.summary
    if approval.show_amount and approval.amount_text:
        summary = f"{summary}\n{approval.amount_text}"
    return [
        _section(summary),
        {
            "type": "actions",
            "block_id": f"agent_approval:{approval.id}",
            "elements": [
                _button(approve_label, "agent_approve", approval.id, primary=True),
                _button(copy.DECLINE, "agent_decline", approval.id),
            ],
        },
    ]


def handoff_blocks(
    form: str, files: list[dict[str, str]], channel_id: str, thread_ts: str | None
) -> list[dict[str, Any]]:
    action_id = FORM_ACTION_IDS[form]
    blocks = [_section(copy.HANDOFF_INTRO[form])]
    if action_id is None:
        return blocks
    value: dict[str, Any] = {
        "files": [{"id": f["id"], "title": f["title"]} for f in files],
        "channel_id": channel_id,
    }
    if thread_ts:
        value["thread_ts"] = thread_ts
    blocks.append(
        {
            "type": "actions",
            "elements": [
                _button(
                    copy.HANDOFF_BUTTON[form],
                    action_id,
                    json.dumps(value),
                    primary=True,
                )
            ],
        }
    )
    return blocks


def quick_action_blocks() -> list[dict[str, Any]]:
    return [
        {
            "type": "actions",
            "elements": [
                _button(copy.QUICK_ACTION_JOBS, "agent_quick_jobs", "jobs"),
                _button(copy.QUICK_ACTION_NEW, "agent_quick_new", "new"),
                _button(copy.QUICK_ACTION_HELP, "agent_quick_help", "help"),
            ],
        }
    ]


def connect_blocks() -> list[dict[str, Any]]:
    return [
        _section(copy.CONNECT_NEEDED),
        {
            "type": "actions",
            "elements": [
                _button(copy.CONNECT_BUTTON, "agent_connect", "connect", primary=True)
            ],
        },
    ]


def suggestion_blocks(
    suggestion_id: str, language_name: str, channel_name: str
) -> list[dict[str, Any]]:
    """Sent as a direct message to the author. Ephemeral messages vanish on reload
    and are unreliable on mobile; a direct message stays put and only they see it."""
    return [
        _section(
            copy.SUGGESTION_LANGUAGE_GAP.format(
                language=language_name, channel=channel_name
            )
        ),
        {
            "type": "actions",
            "block_id": f"agent_suggestion:{suggestion_id}",
            "elements": [
                _button(
                    copy.SUGGESTION_ACT.format(language=language_name),
                    "agent_suggestion_act",
                    suggestion_id,
                    primary=True,
                ),
                _button(
                    copy.SUGGESTION_NOT_NOW, "agent_suggestion_later", suggestion_id
                ),
                _button(copy.SUGGESTION_NEVER, "agent_suggestion_never", suggestion_id),
            ],
        },
    ]


def admin_only_blocks() -> list[dict[str, Any]]:
    return [
        _section(copy.ADMIN_ONLY_SUGGESTIONS),
        {
            "type": "actions",
            "elements": [_button(copy.ASK_AN_ADMIN, "agent_ask_admin", "ask")],
        },
    ]


def digest_blocks(lines: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": copy.DIGEST_TITLE, "emoji": False},
        },
        _section("\n".join(lines)),
    ]


def home_blocks(
    digest: str,
    muted: list[dict[str, str]],
    enabled_channels: list[dict[str, str]] | None,
) -> list[dict[str, Any]]:
    """Blocks appended to the app's existing Home tab. `enabled_channels` is None for non-admins."""
    blocks: list[dict[str, Any]] = [
        {"type": "divider"},
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": copy.HOME_DIGEST_TITLE,
                "emoji": False,
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "radio_buttons",
                    "action_id": "agent_home_digest",
                    "initial_option": _option(digest.capitalize()),
                    "options": [_option(o) for o in copy.HOME_DIGEST_OPTIONS],
                }
            ],
        },
        _context(copy.HOME_DIGEST_HELPER),
    ]
    if muted:
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": copy.HOME_MUTED_TITLE,
                    "emoji": False,
                },
            }
        )
        for m in muted:
            blocks.append(
                {
                    **_section(m["label"]),
                    "accessory": _button(
                        copy.HOME_UNMUTE, "agent_home_unmute", m["channel_id"]
                    ),
                }
            )
    if enabled_channels:
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": copy.HOME_CHANNELS_TITLE,
                    "emoji": False,
                },
            }
        )
        for c in enabled_channels:
            blocks.append(
                {
                    **_section(c["label"]),
                    "accessory": _button(
                        copy.HOME_TURN_OFF, "agent_home_channel_off", c["channel_id"]
                    ),
                }
            )
    return blocks


def _option(label: str) -> dict[str, Any]:
    return {
        "text": {"type": "plain_text", "text": label, "emoji": False},
        "value": label.lower(),
    }

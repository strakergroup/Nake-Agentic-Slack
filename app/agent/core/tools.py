"""The agent's menu of actions.

Nine tools. Eight are look-ups that run straight away. One is gated: the model
can only propose it, and the runner runs it after a verified click. The agent
never submits paid work: quoted work goes through the app's existing quote
message and Accept button, and people who cannot see quotes are handed the
existing document form.
"""

from __future__ import annotations

from typing import Any

from .types import ToolHandler, ToolSpec

FORMS = [
    "document_translation",
    "media",
    "quality_evaluation",
    "human_translation",
    "new_job",
    "channel_settings",
]


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_SPECS: list[ToolSpec] = [
    ToolSpec(
        "get_job",
        "Look up one translation job by its reference, for example TJ48213. Use when the person names a job.",
        _schema(
            {
                "job_id": {
                    "type": "string",
                    "description": "Job reference. Digits alone are accepted.",
                }
            },
            ["job_id"],
        ),
        "lookup",
    ),
    ToolSpec(
        "list_jobs",
        "List the person's translation jobs. Use for questions such as where is my job, what is waiting on me, "
        "or what is in progress.",
        _schema(
            {
                "filter": {
                    "type": "string",
                    "enum": ["open", "waiting_on_me", "delivered", "all"],
                }
            },
            ["filter"],
        ),
        "lookup",
    ),
    ToolSpec(
        "account_status",
        "Check whether the person's account is ready to use. Never call this to find a price.",
        _schema({}, []),
        "lookup",
    ),
    ToolSpec(
        "translate_text",
        "Translate text for the person, shown to them in this conversation. Use for text they typed or, with "
        "use_current_thread, the thread they are in. The translation service does the translating; you never "
        "translate the text yourself and you never see the thread contents.",
        _schema(
            {
                "target_language": {
                    "type": "string",
                    "description": "Language code such as ja, de, pt-BR.",
                },
                "text": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Text the person typed for translation.",
                },
                "use_current_thread": {"type": "boolean"},
            },
            ["target_language", "text", "use_current_thread"],
        ),
        "lookup",
    ),
    ToolSpec(
        "request_document_quote",
        "Ask for a quote to translate the files attached to the person's message. Only available when the facts "
        "say the person can see quotes. The quote arrives as its own message with an Accept button; you do not "
        "state the price and you do not accept it.",
        _schema(
            {
                "target_languages": {"type": "array", "items": {"type": "string"}},
                "source_language": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            ["target_languages", "source_language"],
        ),
        "lookup",
    ),
    ToolSpec(
        "post_translation_publicly",
        "Propose posting a translation of a channel message into its thread, visible to everyone. This is a "
        "proposal only: a person must approve it with a click before anything is posted. Never say it has been "
        "posted until you are told the approval was given.",
        _schema(
            {
                "target_language": {"type": "string"},
                "message_ts": {
                    "type": "string",
                    "description": "Timestamp of the channel message to translate.",
                },
            },
            ["target_language", "message_ts"],
        ),
        "gated",
    ),
    ToolSpec(
        "offer_form",
        "Show a button that opens one of the app's existing forms, with the person's files attached. Use "
        "document_translation when the person wants a document translated and the facts say they cannot see "
        "quotes. Use media for video, audio, subtitles and transcripts; quality_evaluation to check a "
        "translation; human_translation for a human translator or reviewer; new_job for a managed translation "
        "job; channel_settings for channel auto-translate settings.",
        _schema({"form": {"type": "string", "enum": FORMS}}, ["form"]),
        "lookup",
    ),
    ToolSpec(
        "set_digest",
        "Turn the person's job digest on or off.",
        _schema(
            {"frequency": {"type": "string", "enum": ["off", "daily", "weekly"]}},
            ["frequency"],
        ),
        "lookup",
    ),
    ToolSpec(
        "explain",
        "Fetch the app's approved help text on a topic. Use it before answering questions about what Arbitr "
        "can do or how to get started.",
        _schema(
            {
                "topic": {
                    "type": "string",
                    "enum": ["capabilities", "getting_started", "pricing", "privacy"],
                }
            },
            ["topic"],
        ),
        "lookup",
    ),
]


class ToolRegistry:
    def __init__(self, native_document_quotes: bool = True) -> None:
        """`native_document_quotes=False` removes the quote tool for everyone, so
        documents go through the app's existing form (the adapter's default)."""
        self._native_quotes = native_document_quotes
        self._specs = {spec.name: spec for spec in _SPECS}
        self._handlers: dict[str, ToolHandler] = {}

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def specs_for(self, can_see_quotes: bool) -> list[ToolSpec]:
        """Tools offered to the model. A person who cannot see quotes is never offered the quote tool."""
        offer_quotes = can_see_quotes and self._native_quotes
        return [
            s
            for s in self._specs.values()
            if offer_quotes or s.name != "request_document_quote"
        ]

    def as_anthropic_tools(
        self, specs: list[ToolSpec] | None = None
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.input_schema,
                "strict": True,
            }
            for s in (specs if specs is not None else self.specs())
        ]

    def bind(self, name: str, handler: ToolHandler) -> None:
        if name not in self._specs:
            raise KeyError(f"unknown tool: {name}")
        self._handlers[name] = handler

    def handler_for(self, name: str) -> ToolHandler:
        if name not in self._specs:
            raise KeyError(f"unknown tool: {name}")
        if name not in self._handlers:
            raise KeyError(f"no handler bound for tool: {name}")
        return self._handlers[name]

    def is_known(self, name: str) -> bool:
        return name in self._specs

    def is_gated(self, name: str) -> bool:
        spec = self._specs.get(name)
        return spec is not None and spec.kind == "gated"

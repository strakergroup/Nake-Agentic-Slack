"""The agent's menu of actions.

Eleven tools. Nine are look-ups that run straight away. Two are gated: the model
can only propose them, and the runner runs them after a verified click.

Money: quoted work goes through the app's existing quote message and Accept
button, which the agent never touches. People who cannot see quotes confirm a
document translation with one click and no price (`submit_document_translation`),
which calls the same function the document form's Submit calls today. With
native documents switched off (the adapter's default) neither document tool is
offered and everyone is handed the existing form.

Posting: an explicit request made in a channel thread is its own record, so
`post_translation_in_thread` runs without a click, like the translate shortcut
today. Anywhere else, or when Arbitr spoke first, `post_translation_publicly`
needs a click.
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
        "submit_document_translation",
        "Propose translating the attached files for a person who cannot see quotes. This is a proposal only: "
        "the person confirms with one click, and no price is shown. Never say it has started until you are "
        "told the approval was given. Give target_language_names in the person's own language for the button text.",
        _schema(
            {
                "target_languages": {"type": "array", "items": {"type": "string"}},
                "target_language_names": {"type": "array", "items": {"type": "string"}},
                "source_language": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            ["target_languages", "target_language_names", "source_language"],
        ),
        "gated",
    ),
    ToolSpec(
        "post_translation_in_thread",
        "Post a translation into the channel thread you were mentioned in, visible to everyone. Use only when "
        "the person explicitly asked, in that thread, for a translation to be posted. Their request is the "
        "record, so no click is needed. message_ts is the message to translate; null means the first message "
        "of the thread.",
        _schema(
            {
                "target_language": {"type": "string"},
                "message_ts": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            ["target_language", "message_ts"],
        ),
        "lookup",
    ),
    ToolSpec(
        "post_translation_publicly",
        "Propose posting a translation into a channel thread when the request did NOT come from that thread "
        "(for example from a direct message). This is a "
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

    def specs_for(self, can_see_quotes: bool, surface: str = "dm") -> list[ToolSpec]:
        """Tools offered to the model for this person, here.

        Quote tool: only people who can see quotes. Confirm-and-submit tool: only people
        who cannot. Neither when native documents are off. In-thread posting: only when
        the agent was mentioned in a channel thread."""
        hidden = set()
        if not (self._native_quotes and can_see_quotes):
            hidden.add("request_document_quote")
        if not (self._native_quotes and not can_see_quotes):
            hidden.add("submit_document_translation")
        if surface != "mention":
            hidden.add("post_translation_in_thread")
        return [s for s in self._specs.values() if s.name not in hidden]

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

"""Every sentence the Arbitr agent can say that the model does not write.

English source strings. The adapter passes them through the app's `_()` helper
so they are localised like the rest of the app. Voice rules (Arbitr Design
System v2, with Nake's ruling of 2026-09-21): in conversation the agent speaks as
"I"; notices, buttons and attributions name Arbitr; never "we", which would blur
the automated actor into a human team. Never "users", no exclamation marks, no
emoji, no em dashes. A refusal has three parts in order: what happened, why,
what happens next. A wait for approval is the product working, never an error.
"""

from __future__ import annotations

AGENT_NAME = "Arbitr"
AGENT_TAG = "AI agent"

DISCLAIMER = "AI output can be inaccurate. Human review is available on any job."

# --- status and plan cards -------------------------------------------------
CARD_TRANSLATE_TEXT = "Translate the text"
CARD_PRICE = "Price the translation"
CARD_DELIVER = "Translate and deliver here"
CARD_POST = "Post the translation"
CARD_FORM = "Open the form"
CARD_DETAIL_REQUESTED = "Requested"
CARD_DETAIL_HANDED_OVER = "Handed to the translation service"
CARD_DETAIL_QUOTE_READY = "Quote ready"
CARD_DETAIL_DELIVERED = "Delivered"

# --- approvals -------------------------------------------------------------
APPROVE = "Approve"
DECLINE = "Decline"
POST_PUBLICLY_PROMPT = (
    "I can post this translation in the thread for everyone to see. "
    "Inline translation is metered against your organization's balance, as it is today."
)
POST_PUBLICLY_APPROVE = "Post it"
NOT_NOW = "Not now"
SUBMIT_DOCUMENT_PROMPT = "This will be charged to your organization. Translate the attached file into {languages}?"
SUBMIT_DOCUMENT_APPROVE = "Translate now"
SUBMIT_DOCUMENT_STARTED = "Started. I'll post the translated file here when the translation service delivers it."
WAITING_FOR_APPROVAL = (
    "Waiting for the person to approve. Do not claim the action has happened."
)

APPROVED = "Approved. I'm on it."
DECLINED = "Declined. Nothing was posted and nothing was charged. Ask again whenever you need it."

APPROVAL_ERRORS = {
    "wrong_user": (
        "Ask for it yourself and I'll prepare one for you. That approval belongs to the person who asked. "
        "I only accept their click."
    ),
    "expired": (
        "Ask again and I'll prepare a new one. That approval has expired. "
        "I don't act on old approvals in case things have changed."
    ),
    "unknown": (
        "Ask again and I'll prepare a new one. I couldn't find that approval. "
        "It may belong to an earlier conversation."
    ),
    "already_used": (
        "Nothing further has happened. That approval was already used. "
        "I run an approved action once only."
    ),
    "stopped": (
        "Ask again when you're ready. That request was stopped. "
        "I don't act on approvals from a stopped request."
    ),
}

# --- failure behaviour -----------------------------------------------------
FALLBACK_MODEL_DOWN = (
    "The buttons below still work. I can't answer in my own words right now, and your jobs are unaffected. "
    "Try me again in a moment."
)
FALLBACK_TOOL_FAILED = (
    "Try that again in a moment. The translation service did not respond as expected. "
    "Nothing was charged."
)
FALLBACK_TOO_MANY_STEPS = (
    "Tell me the one thing you need first. That request needed more steps than I take in one go. "
    "Nothing was ordered or posted."
)
FALLBACK_REFUSED = (
    "I can translate text and documents, check your jobs, and open the forms for media and review. "
    "That request is outside what this app does. Ask me for one of those instead."
)
STOPPED = (
    "Stopped. Nothing new will start, and anything waiting for your approval has been cancelled. "
    "Jobs already handed to the translation service carry on, and each can be cancelled from its own job message."
)
QUICK_ACTION_JOBS = "My jobs"
QUICK_ACTION_NEW = "New translation"
QUICK_ACTION_HELP = "Help"

# --- hand-offs to the existing forms ----------------------------------------
HANDOFF_INTRO = {
    "document_translation": (
        "Document translation runs through the document form. Open it and your file will already be attached."
    ),
    "media": (
        "Subtitles and transcripts run through the media form. Open it and your file will already be attached."
    ),
    "quality_evaluation": (
        "Quality evaluation runs through its own form. Open it and your file will already be attached."
    ),
    "human_translation": (
        "Human translation runs through its own form. Open it and your file will already be attached."
    ),
    "new_job": "A new translation job starts from the job form.",
    "channel_settings": "Channel translation settings are changed in the settings form.",
}
HANDOFF_BUTTON = {
    "document_translation": "Open document form",
    "media": "Open media form",
    "quality_evaluation": "Open evaluation form",
    "human_translation": "Open human translation form",
    "new_job": "Open job form",
    "channel_settings": "Open settings",
}

# --- help and onboarding ----------------------------------------------------
HELP_GENERAL = (
    "I translate text and documents in Slack, check on your translation jobs, "
    "and open the forms for subtitles, quality evaluation and human review. "
    "Say what you need in your own words."
)
HELP_IBM_GENERIC = (
    "I translate text and documents in Slack, check on your translation jobs, "
    "and open the forms for subtitles, quality evaluation and human review. "
    "Your workspace is already set up. Say what you need in your own words."
)
HELP_NO_PRICE = (
    "An administrator can share the quote with you. Pricing in this workspace is visible to administrators only. "
    "Everything else works as usual."
)
HELP_GETTING_STARTED = (
    "Type what you need, or attach a file and say which languages you want. "
    "I show my plan as I work and ask before anything is posted for others to see."
)
HELP_PRICING = (
    "Paid work starts with a quote. The quote arrives as its own message with an Accept button, "
    "and nothing is charged until someone with the right to accept it does so."
)
HELP_PRIVACY = (
    "Documents and messages go to the translation service, as they do today. "
    "The language model that runs this conversation sees your request and file names, not file contents. "
    "A suggestion is decided by language detection on Straker's own service, never by the language model."
)
CONNECT_NEEDED = (
    "To do that, I need your account. Connect it once and you won't be asked again."
)
CONNECT_BUTTON = "Connect account"

# Strings that may be shown in IBM workspaces. They must never mention account
# connection, balances or buying anything (tests/agent/test_copy.py).
IBM_SAFE = [
    HELP_IBM_GENERIC,
    HELP_NO_PRICE,
    FALLBACK_MODEL_DOWN,
    FALLBACK_TOOL_FAILED,
    STOPPED,
    DECLINED,
]

# --- suggestions (no model involved) -----------------------------------------
SUGGESTION_LANGUAGE_GAP = (
    "About your post in {channel}: most people there work in {language}. "
    "Want a {language} version posted in the thread? "
    "Inline translation is metered against your organization's balance, as it is today."
)
SUGGESTION_ACT = "Post in {language}"
SUGGESTION_NOT_NOW = "Not now"
SUGGESTION_NEVER = "Don't suggest this again"
SUGGESTION_DISMISSED = "No problem. Nothing was posted."
SUGGESTION_POSTED = "Posted in the thread under your message."
SUGGESTION_MUTED = (
    "Done. I won't suggest translations for your posts in that channel. "
    "You can change this in the Arbitr Home tab."
)
SUGGESTION_ATTRIBUTION = (
    "Translated from {source} by Arbitr (AI). Posted at {name}'s request."
)
SUGGESTIONS_ENABLED = (
    "Suggestions are now on in this channel. Anyone here can turn them off."
)
ADMIN_ONLY_SUGGESTIONS = (
    "I can send the request to your admins. In this workspace only an admin can turn on channel suggestions. "
    "That keeps administrators in control of where I speak first."
)
ASK_AN_ADMIN = "Ask an admin"

# --- follow-ups and digest ----------------------------------------------------
FOLLOWUP_QUOTE_WAITING = (
    "The quote for {name} has been waiting since {day}. I won't remind you again."
)
FOLLOWUP_DELIVERED = "{name} is ready. Need it in another language?"
DIGEST_TITLE = "Your translation jobs"
DIGEST_DELIVERED = "{count} delivered"
DIGEST_WAITING = "{count} waiting on you"
DIGEST_IN_PROGRESS = "{count} in progress"
DIGEST_TEAM = "Your team translated {words} words this week."
DIGEST_ON = "Digest is on. I'll send it by direct message."
DIGEST_OFF = "Digest is off."

# --- Home tab additions --------------------------------------------------------
HOME_DIGEST_TITLE = "Digest"
HOME_DIGEST_HELPER = "A summary of your jobs, sent by direct message."
HOME_DIGEST_OPTIONS = ["Off", "Daily", "Weekly"]
HOME_MUTED_TITLE = "Muted suggestions"
HOME_UNMUTE = "Unmute"
HOME_CHANNELS_TITLE = "Channel suggestions"
HOME_TURN_OFF = "Turn off"


def _flatten(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flatten(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _flatten(v)]
    return []


ALL: list[str] = [
    s
    for name, value in list(globals().items())
    if name.isupper() and name not in {"ALL", "IBM_SAFE"}
    for s in _flatten(value)
]

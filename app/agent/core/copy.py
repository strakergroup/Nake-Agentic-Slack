"""Every sentence the Arbitr agent can say that the model does not write.

English source strings. The adapter passes them through the app's `_()` helper
so they are localised like the rest of the app. Voice rules (Arbitr Design
System v2): Arbitr is the named actor, never "users", no exclamation marks, no
emoji, no em dashes. A refusal has three parts in order: what happened, why,
what happens next. A wait for approval is the product working, never an error.
"""

from __future__ import annotations

AGENT_NAME = "Arbitr"
AGENT_TAG = "AI agent"

DISCLAIMER = "AI output can be inaccurate. Human review is available on any job."

# --- status and plan cards -------------------------------------------------
CARD_LOOKUP_JOBS = "Look up your jobs"
CARD_ACCOUNT = "Check your account"
CARD_TRANSLATE_TEXT = "Translate the text"
CARD_PRICE = "Price the translation"
CARD_APPROVAL = "Your approval"
CARD_DELIVER = "Translate and deliver here"
CARD_POST = "Post the translation"
CARD_FORM = "Open the form"
CARD_DETAIL_WAITING = "Waiting on you"
CARD_DETAIL_DECLINED = "Declined"
CARD_DETAIL_REQUESTED = "Requested"
CARD_DETAIL_QUOTE_READY = "Quote ready"
CARD_DETAIL_DELIVERED = "Delivered"

# --- approvals -------------------------------------------------------------
APPROVE = "Approve"
DECLINE = "Decline"
POST_PUBLICLY_PROMPT = "Arbitr will post this translation in the thread, visible to everyone in the channel."
POST_PUBLICLY_APPROVE = "Post it"
NOT_NOW = "Not now"
WAITING_FOR_APPROVAL = (
    "Waiting for the person to approve. Do not claim the action has happened."
)

APPROVED = "Approved. Arbitr is on it."
DECLINED = "Declined. Nothing was posted and nothing was charged. Ask again whenever you need it."

APPROVAL_ERRORS = {
    "wrong_user": (
        "That approval belongs to someone else. Arbitr only accepts a click from the person who asked. "
        "Ask for it yourself and Arbitr will prepare a new one."
    ),
    "expired": (
        "That approval has expired. Arbitr does not act on old approvals in case things have changed. "
        "Ask again and Arbitr will prepare a new one."
    ),
    "unknown": (
        "Arbitr could not find that approval. It may belong to an earlier conversation. "
        "Ask again and Arbitr will prepare a new one."
    ),
    "already_used": (
        "That approval was already used. Arbitr runs an approved action once only. "
        "Nothing further has happened."
    ),
    "stopped": (
        "That request was stopped. Arbitr does not act on approvals from a stopped request. "
        "Ask again when you are ready."
    ),
}

# --- failure behaviour -----------------------------------------------------
FALLBACK_MODEL_DOWN = (
    "Arbitr could not think that through just now. The language model did not respond. "
    "The buttons below still work, or try again in a moment."
)
FALLBACK_TOOL_FAILED = (
    "Arbitr could not complete that step. The translation service did not respond as expected. "
    "Nothing was charged, and you can try again in a moment."
)
FALLBACK_TOO_MANY_STEPS = (
    "Arbitr stopped to avoid going in circles. The request needed more steps than expected. "
    "Tell Arbitr the one thing you need first."
)
FALLBACK_REFUSED = (
    "Arbitr cannot help with that request. It is outside what this app does. "
    "Arbitr can translate text and documents, check your jobs, and open the forms for media and review."
)
STOPPED = "Stopped. Anything not yet approved has been cancelled. Nothing further will happen."

QUICK_ACTION_JOBS = "My jobs"
QUICK_ACTION_NEW = "New translation"
QUICK_ACTION_HELP = "Help"

# --- hand-offs to the existing forms ----------------------------------------
HANDOFF_INTRO = {
    "document_translation": "Document translation runs through the document form. Open it with your file attached.",
    "media": "Subtitles and transcripts run through the media form. Open it with your file attached.",
    "quality_evaluation": "Quality evaluation runs through its own form. Open it with your file attached.",
    "human_translation": "Human translation runs through its own form. Open it with your file attached.",
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
    "Arbitr translates text and documents in Slack, checks on your translation jobs, "
    "and opens the forms for subtitles, quality evaluation and human review. "
    "Say what you need in your own words."
)
HELP_IBM_GENERIC = (
    "Arbitr translates text and documents in Slack, checks on your translation jobs, "
    "and opens the forms for subtitles, quality evaluation and human review. "
    "Your workspace is already set up. Say what you need in your own words."
)
HELP_NO_PRICE = (
    "Arbitr cannot show pricing here. Pricing in this workspace is visible to administrators. "
    "An administrator can share the quote with you."
)
CONNECT_NEEDED = "To do that, Arbitr needs your account. Connect it once and you will not be asked again."
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
SUGGESTION_PRIVATE_LABEL = "Only visible to you"
SUGGESTION_LANGUAGE_GAP = "Most people in this channel work in {language}. Want this posted in {language} as well?"
SUGGESTION_ACT = "Post in {language}"
SUGGESTION_NOT_NOW = "Not now"
SUGGESTION_NEVER = "Don't suggest this again"
SUGGESTION_DISMISSED = "No problem. Nothing was posted."
SUGGESTION_MUTED = (
    "Done. Arbitr will not suggest translations to you in this channel. "
    "You can change this in the Arbitr Home tab."
)
SUGGESTION_ATTRIBUTION = (
    "Translated from {source} by Arbitr (AI). Posted at {name}'s request."
)
SUGGESTIONS_ENABLED = (
    "Suggestions are now on in this channel. Anyone here can turn them off."
)
ADMIN_ONLY_SUGGESTIONS = (
    "In this workspace only an admin can turn on channel suggestions. "
    "This keeps administrators in control of where Arbitr speaks first. "
    "Arbitr can send the request to your admins."
)
ASK_AN_ADMIN = "Ask an admin"

# --- follow-ups and digest ----------------------------------------------------
FOLLOWUP_QUOTE_WAITING = "The quote for {name} has been waiting since {day}. Arbitr will not remind you again."
FOLLOWUP_DELIVERED = "{name} is ready. Need it in another language?"
DIGEST_TITLE = "Your translation jobs"
DIGEST_DELIVERED = "{count} delivered"
DIGEST_WAITING = "{count} waiting on you"
DIGEST_IN_PROGRESS = "{count} in progress"
DIGEST_TEAM = "Your team translated {words} words this week."
DIGEST_ON = "Digest is on. Arbitr will send it by direct message."
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

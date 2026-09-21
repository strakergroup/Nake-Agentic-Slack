"""When Arbitr may speak first in a channel. Rules only: no model is involved.

The five safety rules (spec section 5):
1. off until someone turns it on for that channel;
2. private to the person who triggered it;
3. every suggestion offers "Not now" and "Don't suggest this again";
4. hard caps on frequency;
5. no message text reaches a language model to decide. `decide` has no parameter
   that could carry the text: it receives a detected language code and a length.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import AgentFacts

MIN_LENGTH = 40
MAJORITY_SHARE = 0.40
AUTHOR_CHANNEL_WINDOW = 24 * 3600
CHANNEL_WINDOW = 3600
CHANNEL_MAX_PER_WINDOW = 3
NOT_NOW_SECONDS = 24 * 3600


@dataclass(frozen=True)
class MessageSignal:
    """What the adapter may pass in about a channel message. No text, by design."""

    author_id: str
    channel_id: str
    detected_language: str | None
    length: int
    is_bot: bool = False
    is_edit: bool = False
    is_thread_reply: bool = False
    is_only_links_or_code: bool = False


@dataclass
class ChannelState:
    enabled: bool = False
    member_languages: dict[str, int] = field(
        default_factory=dict
    )  # language -> member count
    recent_suggestions: list[float] = field(default_factory=list)  # timestamps


@dataclass
class AuthorState:
    muted_forever: bool = False
    snoozed_until: float = 0.0
    last_suggested: float = 0.0


@dataclass(frozen=True)
class Suggestion:
    author_id: str
    channel_id: str
    target_language: str


def _base(language: str) -> str:
    return language.split("-")[0].lower()


def majority_language(member_languages: dict[str, int]) -> tuple[str, float] | None:
    total = sum(member_languages.values())
    if total <= 0:
        return None
    merged: dict[str, int] = {}
    for language, count in member_languages.items():
        merged[_base(language)] = merged.get(_base(language), 0) + count
    language, count = max(merged.items(), key=lambda item: item[1])
    return language, count / total


def decide(
    signal: MessageSignal,
    channel: ChannelState | None,
    author: AuthorState | None,
    now: float,
) -> Suggestion | None:
    # Rule 9 of the test list: if state could not be read, stay silent.
    if channel is None or author is None:
        return None
    if not channel.enabled:
        return None
    if (
        signal.is_bot
        or signal.is_edit
        or signal.is_thread_reply
        or signal.is_only_links_or_code
    ):
        return None
    if signal.length < MIN_LENGTH or not signal.detected_language:
        return None
    if author.muted_forever or now < author.snoozed_until:
        return None
    if author.last_suggested and now - author.last_suggested < AUTHOR_CHANNEL_WINDOW:
        return None
    recent = [t for t in channel.recent_suggestions if now - t < CHANNEL_WINDOW]
    if len(recent) >= CHANNEL_MAX_PER_WINDOW:
        return None
    majority = majority_language(channel.member_languages)
    if majority is None:
        return None
    language, share = majority
    if share < MAJORITY_SHARE or language == _base(signal.detected_language):
        return None
    return Suggestion(signal.author_id, signal.channel_id, language)


def record_shown(channel: ChannelState, author: AuthorState, now: float) -> None:
    channel.recent_suggestions = [
        t for t in channel.recent_suggestions if now - t < CHANNEL_WINDOW
    ] + [now]
    author.last_suggested = now


def not_now(author: AuthorState, now: float) -> None:
    author.snoozed_until = now + NOT_NOW_SECONDS


def never_again(author: AuthorState) -> None:
    author.muted_forever = True


def unmute(author: AuthorState) -> None:
    author.muted_forever = False
    author.snoozed_until = 0.0


def can_enable(facts: AgentFacts) -> bool:
    """IBM workspaces: admins only. Elsewhere: anyone in the channel."""
    return facts.is_workspace_admin if facts.is_ibm else True

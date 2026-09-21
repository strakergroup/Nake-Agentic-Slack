import re

from app.agent.core import copy
from app.agent.core.voice import check_copy


def test_there_is_a_meaningful_amount_of_copy():
    assert len(copy.ALL) > 60


def test_every_fixed_string_passes_the_voice_rules():
    bad = [f"{v.rule}: {text}" for text in copy.ALL for v in check_copy(text)]
    assert bad == []


def test_disclaimer_matches_the_poc():
    assert (
        copy.DISCLAIMER
        == "AI output can be inaccurate. Human review is available on any job."
    )


def _sentences(text):
    return [s for s in re.split(r"(?<=[.?])\s+", text.strip()) if s]


def test_refusals_have_three_parts():
    refusals = [
        *copy.APPROVAL_ERRORS.values(),
        copy.FALLBACK_MODEL_DOWN,
        copy.FALLBACK_TOOL_FAILED,
        copy.FALLBACK_TOO_MANY_STEPS,
        copy.FALLBACK_REFUSED,
        copy.ADMIN_ONLY_SUGGESTIONS,
        copy.HELP_NO_PRICE,
    ]
    for text in refusals:
        assert len(_sentences(text)) == 3, text


def test_refusals_never_apologise():
    for text in copy.ALL:
        assert not re.search(r"\b(sorry|apolog|unfortunately)\b", text, re.I), text


def test_ibm_safe_strings_never_mention_connection_or_money():
    for text in copy.IBM_SAFE:
        assert not re.search(
            r"\b(connect|top up|purchase|balance|buy)\b", text, re.I
        ), text


def test_every_form_has_an_intro_and_a_button():
    assert set(copy.HANDOFF_INTRO) == set(copy.HANDOFF_BUTTON)
    assert "document_translation" in copy.HANDOFF_INTRO


def test_every_approval_error_reason_has_copy():
    assert set(copy.APPROVAL_ERRORS) == {
        "wrong_user",
        "expired",
        "unknown",
        "already_used",
        "stopped",
    }

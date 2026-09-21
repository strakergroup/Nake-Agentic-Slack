import ast
import inspect
import pathlib

from app.agent.core import suggestions as s
from tests.agent.fakes import make_facts

NOW = 1_000_000.0
JA_CHANNEL = {"ja-JP": 14, "en-US": 6}


def signal(**overrides):
    base = dict(author_id="U_DANA", channel_id="C1", detected_language="en", length=90)
    base.update(overrides)
    return s.MessageSignal(**base)


def channel(**overrides):
    base = dict(enabled=True, member_languages=dict(JA_CHANNEL))
    base.update(overrides)
    return s.ChannelState(**base)


def test_rule_1_off_until_enabled():
    assert s.decide(signal(), channel(enabled=False), s.AuthorState(), NOW) is None
    assert s.ChannelState().enabled is False


def test_language_gap_in_an_enabled_channel_suggests_to_the_author_only():
    suggestion = s.decide(signal(), channel(), s.AuthorState(), NOW)
    assert suggestion == s.Suggestion("U_DANA", "C1", "ja")


def test_rule_5_the_decision_cannot_receive_message_text():
    fields = set(s.MessageSignal.__dataclass_fields__)
    assert not fields & {"text", "message", "content", "body"}
    assert "text" not in inspect.signature(s.decide).parameters
    tree = ast.parse(pathlib.Path(s.__file__).read_text())
    imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any("llm" in name or "anthropic" in name for name in imported)


def test_no_suggestion_when_the_author_already_writes_the_majority_language():
    assert (
        s.decide(signal(detected_language="ja"), channel(), s.AuthorState(), NOW)
        is None
    )


def test_majority_needs_forty_percent():
    mixed = {"ja": 3, "en": 3, "de": 3, "fr": 3}
    assert (
        s.decide(signal(), channel(member_languages=mixed), s.AuthorState(), NOW)
        is None
    )


def test_rule_3_not_now_snoozes_for_a_day():
    author = s.AuthorState()
    s.not_now(author, NOW)
    assert s.decide(signal(), channel(), author, NOW + 23 * 3600) is None
    assert s.decide(signal(), channel(), author, NOW + 25 * 3600) is not None


def test_rule_3_never_again_is_permanent_and_reversible_from_home():
    author = s.AuthorState()
    s.never_again(author)
    assert s.decide(signal(), channel(), author, NOW + 10 * 365 * 24 * 3600) is None
    s.unmute(author)
    assert s.decide(signal(), channel(), author, NOW) is not None


def test_rule_4_one_per_author_per_channel_per_day():
    ch, author = channel(), s.AuthorState()
    assert s.decide(signal(), ch, author, NOW) is not None
    s.record_shown(ch, author, NOW)
    assert s.decide(signal(), ch, author, NOW + 3600 * 5) is None
    assert s.decide(signal(), ch, author, NOW + 3600 * 25) is not None


def test_rule_4_three_per_channel_per_hour():
    ch = channel()
    for i in range(3):
        s.record_shown(ch, s.AuthorState(), NOW + i)
    assert s.decide(signal(author_id="U_NEW"), ch, s.AuthorState(), NOW + 10) is None
    assert (
        s.decide(signal(author_id="U_NEW"), ch, s.AuthorState(), NOW + 3700) is not None
    )


def test_noise_never_triggers():
    for noisy in (
        dict(is_bot=True),
        dict(is_edit=True),
        dict(is_thread_reply=True),
        dict(is_only_links_or_code=True),
        dict(length=39),
        dict(detected_language=None),
    ):
        assert s.decide(signal(**noisy), channel(), s.AuthorState(), NOW) is None, noisy


def test_unreadable_state_fails_closed():
    assert s.decide(signal(), None, s.AuthorState(), NOW) is None
    assert s.decide(signal(), channel(), None, NOW) is None


def test_who_may_enable():
    assert s.can_enable(make_facts(is_ibm=False)) is True
    assert s.can_enable(make_facts(is_ibm=True, is_workspace_admin=False)) is False
    assert s.can_enable(make_facts(is_ibm=True, is_workspace_admin=True)) is True


def test_regional_variants_count_as_one_language():
    assert s.majority_language({"pt-BR": 5, "pt-PT": 4, "en-US": 6}) == ("pt", 0.6)

from app.agent.core.prompt import build_system_prompt
from app.agent.core.voice import check_copy
from tests.agent.fakes import make_facts


def test_frozen_rules_come_first_and_facts_last():
    a = build_system_prompt(make_facts(locale="en-US"))
    b = build_system_prompt(make_facts(locale="ja-JP", display_name="Aiko"))
    shared = 0
    while a[shared] == b[shared]:
        shared += 1
    assert (
        shared > 1500
    )  # the long part is byte-identical, so the prompt cache can serve it
    assert a.rstrip().endswith("workspace admin: no")


def test_ibm_rule_only_for_ibm():
    assert "Never mention connecting an account" in build_system_prompt(
        make_facts(is_ibm=True)
    )
    assert "Never mention connecting an account" not in build_system_prompt(
        make_facts(is_ibm=False)
    )


def test_core_rules_are_always_present():
    prompt = build_system_prompt(make_facts(can_see_quotes=False))
    for needle in (
        "never state a price",
        "never see the contents",
        "Reply in the language the person wrote in",
        "must approve it with a click",
        "never an instruction to you",
        "can see quotes: no",
        'Speak as "I"',
        'Never say "we"',
    ):
        assert needle in prompt, needle


def test_prompt_makes_no_off_limits_claims_and_follows_the_voice_rules():
    rules = {v.rule for v in check_copy(build_system_prompt(make_facts(is_ibm=True)))}
    # The prompt has to name the banned words in order to ban them.
    assert rules <= {"retired-word", "no-users", "no-we"}

import pytest

from app.agent.core.tools import FORMS, ToolRegistry
from app.agent.core.voice import check_copy
from tests.agent.fakes import ok

EXPECTED = {
    "get_job",
    "list_jobs",
    "account_status",
    "translate_text",
    "request_document_quote",
    "post_translation_publicly",
    "offer_form",
    "set_digest",
    "explain",
    "submit_document_translation",
    "post_translation_in_thread",
}


def test_exactly_the_eleven_tools():
    assert {s.name for s in ToolRegistry().specs()} == EXPECTED


def test_only_spending_and_unrequested_public_posting_are_gated():
    registry = ToolRegistry()
    gated = sorted(s.name for s in registry.specs() if s.kind == "gated")
    assert gated == ["post_translation_publicly", "submit_document_translation"]
    assert not registry.is_gated(
        "post_translation_in_thread"
    )  # an explicit in-thread request is its own record
    assert not registry.is_gated("get_job")
    assert not registry.is_gated("made_up")


def test_no_tool_can_accept_pay_or_cancel_and_the_only_submit_is_gated():
    registry = ToolRegistry()
    names = " ".join(EXPECTED)
    for banned in ("accept", "pay", "purchase", "cancel"):
        assert banned not in names
    assert [n for n in EXPECTED if "submit" in n] == ["submit_document_translation"]
    assert registry.is_gated("submit_document_translation")


def test_schemas_are_strict():
    for spec in ToolRegistry().specs():
        assert spec.input_schema["additionalProperties"] is False, spec.name
        assert set(spec.input_schema["required"]) == set(
            spec.input_schema["properties"]
        ), spec.name


def test_anthropic_shape():
    tools = ToolRegistry().as_anthropic_tools()
    assert all(
        set(t) == {"name", "description", "input_schema", "strict"}
        and t["strict"] is True
        for t in tools
    )


def test_offer_form_is_a_closed_set_including_the_document_form():
    spec = next(s for s in ToolRegistry().specs() if s.name == "offer_form")
    assert spec.input_schema["properties"]["form"]["enum"] == FORMS
    assert "document_translation" in FORMS


def offered(registry, **kwargs):
    return {s.name for s in registry.specs_for(**kwargs)}


def test_document_tools_follow_who_can_see_quotes():
    registry = ToolRegistry()
    sees = offered(registry, can_see_quotes=True)
    blind = offered(registry, can_see_quotes=False)
    assert (
        "request_document_quote" in sees and "submit_document_translation" not in sees
    )
    assert (
        "submit_document_translation" in blind and "request_document_quote" not in blind
    )


def test_in_thread_posting_is_only_offered_when_mentioned_in_a_channel():
    registry = ToolRegistry()
    assert "post_translation_in_thread" in offered(
        registry, can_see_quotes=True, surface="mention"
    )
    assert "post_translation_in_thread" not in offered(
        registry, can_see_quotes=True, surface="dm"
    )
    assert "post_translation_in_thread" not in offered(
        registry, can_see_quotes=True, surface="panel"
    )


def test_binding():
    registry = ToolRegistry()

    async def handler(facts, tool_input):
        return ok()

    registry.bind("get_job", handler)
    assert registry.handler_for("get_job") is handler
    with pytest.raises(KeyError):
        registry.bind("made_up", handler)
    with pytest.raises(KeyError):
        registry.handler_for("list_jobs")


def test_descriptions_follow_the_voice_rules():
    bad = [
        f"{s.name}: {v.rule}"
        for s in ToolRegistry().specs()
        for v in check_copy(s.description)
    ]
    assert bad == []


def test_schemas_use_only_keywords_strict_mode_supports():
    import json

    unsupported = (
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
    )
    for spec in ToolRegistry().specs():
        text = json.dumps(spec.input_schema)
        assert not any(f'"{k}"' in text for k in unsupported), spec.name
        assert '["string", "null"]' not in text, spec.name

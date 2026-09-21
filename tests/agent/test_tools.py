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
}


def test_exactly_the_nine_tools():
    assert {s.name for s in ToolRegistry().specs()} == EXPECTED


def test_only_public_posting_is_gated():
    registry = ToolRegistry()
    assert [s.name for s in registry.specs() if s.kind == "gated"] == [
        "post_translation_publicly"
    ]
    assert registry.is_gated("post_translation_publicly")
    assert not registry.is_gated("get_job")
    assert not registry.is_gated("made_up")


def test_no_tool_can_submit_or_accept_paid_work():
    names = " ".join(EXPECTED)
    for banned in ("submit", "accept", "pay", "purchase", "cancel"):
        assert banned not in names


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


def test_people_who_cannot_see_quotes_are_never_offered_the_quote_tool():
    registry = ToolRegistry()
    assert "request_document_quote" not in {
        s.name for s in registry.specs_for(can_see_quotes=False)
    }
    assert "request_document_quote" in {
        s.name for s in registry.specs_for(can_see_quotes=True)
    }


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

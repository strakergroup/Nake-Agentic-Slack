from app.agent.core.tools import ToolRegistry


def test_native_quotes_off_hides_the_quote_tool_from_everyone():
    registry = ToolRegistry(native_document_quotes=False)
    for can_see in (True, False):
        assert "request_document_quote" not in {
            s.name for s in registry.specs_for(can_see)
        }


def test_native_quotes_on_offers_it_only_to_people_who_can_see_quotes():
    registry = ToolRegistry(native_document_quotes=True)
    assert "request_document_quote" in {s.name for s in registry.specs_for(True)}
    assert "request_document_quote" not in {s.name for s in registry.specs_for(False)}

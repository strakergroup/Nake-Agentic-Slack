from app.agent.core.tools import ToolRegistry

DOCUMENT_TOOLS = {"request_document_quote", "submit_document_translation"}


def test_native_documents_off_hides_both_document_tools_from_everyone():
    registry = ToolRegistry(native_document_quotes=False)
    for can_see in (True, False):
        assert not DOCUMENT_TOOLS & {s.name for s in registry.specs_for(can_see)}


def test_native_documents_on_offers_exactly_one_of_them():
    registry = ToolRegistry(native_document_quotes=True)
    assert DOCUMENT_TOOLS & {s.name for s in registry.specs_for(True)} == {
        "request_document_quote"
    }
    assert DOCUMENT_TOOLS & {s.name for s in registry.specs_for(False)} == {
        "submit_document_translation"
    }

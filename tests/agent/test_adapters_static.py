"""Checks on the adapter layer that need none of the app's infrastructure: they
read the source rather than import it. They run with the core tests."""

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).parents[2]
ADAPTERS = ROOT / "app" / "agent" / "adapters"

# Functions that submit, accept or cancel paid work. The agent must never call them.
BANNED = (
    "document_machine_translate",
    "handle_document_mt_submit",
    "accept_document_mt_quote",
    "cancel_document_mt_quote",
    "new_job",
    "cancel_job",
    "submit_job",
    "create_human_job",
    "mark_document_mt_quote_accepted",
)


def _names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def test_adapters_never_reference_functions_that_spend_money():
    offenders = [
        f"{py.name}: {name}"
        for py in ADAPTERS.glob("*.py")
        for name in _names(py)
        if name in BANNED
    ]
    assert offenders == []


def test_every_tool_the_adapter_binds_exists_in_the_registry():
    from app.agent.core.tools import ToolRegistry

    source = (ADAPTERS / "straker_tools.py").read_text()
    bound = set(re.findall(r'registry\.bind\("([a-z_]+)"', source))
    known = {spec.name for spec in ToolRegistry().specs()}
    assert bound == known


def test_no_adapter_opens_a_modal():
    """Hand-offs reuse the app's existing openers, so its trigger-safety rule holds by construction."""
    for py in ADAPTERS.glob("*.py"):
        assert not {"views_open", "views_push", "open_loading_modal"} & _names(py), (
            py.name
        )


def test_the_edits_to_existing_files_are_guarded():
    seam = (ROOT / "app/slack/listener_actions.py").read_text()
    assert "if agent_enabled_for(context) and await respond_with_agent(" in seam
    assert seam.index("agent_enabled_for(context) and") < seam.index(
        'response = await watson_message(message["text"]'
    )
    listeners = (ROOT / "app/slack/listeners.py").read_text()
    assert listeners.index("register_agent(app)") < listeners.index(
        '@app.event(re.compile(r".+"))'
    )
    assert "if _agent_config.agent_enabled:" in listeners
    config = (ROOT / "app/config.py").read_text()
    assert "agent_enabled: bool = False" in config
    assert "agent_native_document_quotes: bool = False" in config


def test_the_event_bridge_cannot_raise_into_the_router():
    tree = ast.parse((ADAPTERS / "events.py").read_text())
    func = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "notify_backend_event"
    )
    body = [n for n in func.body if not isinstance(n, ast.Expr)]
    assert len(body) == 1 and isinstance(body[0], ast.Try)
    handler = body[0].handlers[0]
    assert isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def test_the_slash_command_pattern_accepts_arbitr_and_still_accepts_straker():
    import re

    listeners = (ROOT / "app/slack/listeners.py").read_text()
    pattern = re.search(r'@app\.command\(re\.compile\(r"(.+?)"\)\)', listeners).group(1)
    for command in ("/arbitr", "/straker", "/ray", "/lc"):
        assert re.fullmatch(pattern, command), command
    assert not re.fullmatch(pattern, "/giphy")


def test_the_submission_function_is_referenced_only_inside_the_gated_tool():
    """`enqueue_document_mt_submission` spends money. It may appear in one adapter file,
    inside one function, and that function's tool must be gated in the registry."""
    from app.agent.core.tools import ToolRegistry

    name = "enqueue_document_mt_submission"
    holders = [py.name for py in ADAPTERS.glob("*.py") if name in _names(py)]
    assert holders == ["straker_tools.py"]
    tree = ast.parse((ADAPTERS / "straker_tools.py").read_text())
    functions = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            (isinstance(n, ast.Name) and n.id == name)
            or (isinstance(n, ast.alias) and n.name == name)
            for n in ast.walk(node)
        )
    ]
    assert functions == ["submit_document_translation"]
    assert ToolRegistry().is_gated("submit_document_translation")


def test_both_document_tools_are_bound_only_behind_the_flag():
    source = (ADAPTERS / "straker_tools.py").read_text()
    before, after = source.split("if native_document_quotes:", 1)
    assert 'registry.bind("request_document_quote"' in after
    assert 'registry.bind("submit_document_translation"' in after
    assert 'registry.bind("submit_document_translation"' not in before

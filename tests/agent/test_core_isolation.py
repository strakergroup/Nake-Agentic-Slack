"""The agent core must install and test on a laptop with none of the app's
infrastructure, so it may not import the rest of the app or its private packages."""

import ast
import pathlib

CORE = pathlib.Path(__file__).parents[2] / "app" / "agent" / "core"
ALLOWED_PREFIX = "app.agent.core"
PRIVATE = ("straker_utils", "straker_auth", "ray_sdk", "ray_logger", "buglog", "ibm_watson")


def _imports(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: stays inside the core
                continue
            yield node.module or ""


def test_core_never_imports_the_rest_of_the_app():
    offenders = [
        f"{py.name}: {name}"
        for py in CORE.rglob("*.py")
        for name in _imports(py)
        if name == "app" or (name.startswith("app.") and not name.startswith(ALLOWED_PREFIX))
    ]
    assert offenders == []


def test_core_has_no_private_straker_packages():
    offenders = [
        f"{py.name}: {name}"
        for py in CORE.rglob("*.py")
        for name in _imports(py)
        if name.split(".")[0] in PRIVATE
    ]
    assert offenders == []

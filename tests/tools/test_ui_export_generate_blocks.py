import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "tools" / "ui-export" / "generate_blocks.py"
)
SPEC = importlib.util.spec_from_file_location("ui_export_generate_blocks", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
build_all_messages = MODULE.build_all_messages
build_all_views = MODULE.build_all_views


def test_build_all_messages_includes_ibm_new_job_variants():
    entries = build_all_messages()
    names = {entry["name"] for entry in entries}

    assert "NewJobMessage (IBM)" in names
    assert "NewJobMessage (IBM, verify)" in names


def test_build_all_messages_includes_other_ibm_sensitive_variants():
    entries = build_all_messages()
    names = {entry["name"] for entry in entries}

    assert "WelcomeBackMessage (IBM, admin)" in names
    assert "WelcomeBackMessage (IBM, non-admin)" in names
    assert "SuccessfulLoginMessage (IBM, admin)" in names
    assert "SuccessfulLoginMessage (IBM, non-admin)" in names
    assert "HelpMessage (IBM, admin)" in names
    assert "HelpMessage (IBM, non-admin)" in names
    assert "WelcomeBackMessage (IBM)" not in names
    assert "SuccessfulLoginMessage (IBM)" not in names
    assert "HelpMessage (IBM)" not in names


def test_build_all_views_includes_ibm_connected_home_variants():
    entries = build_all_views()
    names = {entry["name"] for entry in entries}

    assert "home_view (IBM, connected, admin)" in names
    assert "home_view (IBM, connected, non-admin)" in names
    assert "home_view (IBM, connected)" not in names

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
build_catalog = MODULE.build_catalog
configure_translation = MODULE.configure_translation
parse_languages = MODULE.parse_languages
_requested_translation_source = MODULE._requested_translation_source
_mock_translate = MODULE._mock_translate


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


def _collect_text_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _collect_text_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _collect_text_values(nested)
    elif isinstance(value, str):
        yield value


def test_home_view_static_copy_uses_catalog_translation_path():
    catalog = build_catalog(
        "de-DE",
        {
            "Get Started": "Loslegen",
            ":sunny: Daily Summary": ":sunny: Tageszusammenfassung",
            "{helpEmoji} AI Translate Help": "{helpEmoji} KI Translate Hilfe",
            "Translate Channels": "Kanaele uebersetzen",
            ":speech_balloon: Translation settings": ":speech_balloon: Einstellungen",
            "Give us your feedback": "Feedback geben",
            "Learn More": "Mehr erfahren",
            "{questionEmoji} Help Centre": "{questionEmoji} Hilfezentrum",
            "Visit Straker Verify": "Straker Verify besuchen",
        },
    )
    home_view = next(
        entry for entry in catalog["views"] if entry["name"] == "home_view (connected)"
    )
    texts = set(_collect_text_values(home_view["blocks"]))

    assert "Loslegen" in texts
    assert ":sunny: Tageszusammenfassung" in texts
    assert ":question: KI Translate Hilfe" in texts
    assert "Kanaele uebersetzen" in texts
    assert ":speech_balloon: Einstellungen" in texts
    assert "Feedback geben" in texts
    assert "Mehr erfahren" in texts
    assert ":question: Hilfezentrum" in texts
    assert "Straker Verify besuchen" in texts
    assert "Get Started" not in texts
    assert ":sunny: Daily Summary" not in texts


def test_parse_languages_deduplicates_and_defaults():
    assert parse_languages("fr, es, fr, ,de") == ["fr", "es", "de"]
    assert parse_languages("") == ["en"]


def test_requested_translation_source_prefers_cli(monkeypatch):
    monkeypatch.setenv("UI_EXPORT_TRANSLATION_SOURCE", "catalog")
    monkeypatch.setattr(
        MODULE.sys,
        "argv",
        ["generate_blocks.py", "--translation-source", "app"],
    )

    assert _requested_translation_source() == "app"


def test_requested_translation_source_reads_env(monkeypatch):
    monkeypatch.setenv("UI_EXPORT_TRANSLATION_SOURCE", "db")
    monkeypatch.setattr(MODULE.sys, "argv", ["generate_blocks.py"])

    assert _requested_translation_source() == "db"


def test_mock_translate_uses_configured_language_catalog():
    configure_translation("fr", {"Hello <x id=1>": "Bonjour <x id=1>"})

    try:
        translated = _mock_translate("Hello {name}")
    finally:
        configure_translation("en")

    assert translated == "Bonjour {name}"

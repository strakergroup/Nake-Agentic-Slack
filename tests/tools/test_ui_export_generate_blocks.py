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

QUOTE_FLOW_SPEC = importlib.util.spec_from_file_location(
    "ui_export_generate_quote_flow",
    Path(__file__).resolve().parents[2]
    / "tools"
    / "ui-export"
    / "generate_quote_flow.py",
)
assert QUOTE_FLOW_SPEC is not None
assert QUOTE_FLOW_SPEC.loader is not None
QUOTE_FLOW_MODULE = importlib.util.module_from_spec(QUOTE_FLOW_SPEC)
QUOTE_FLOW_SPEC.loader.exec_module(QUOTE_FLOW_MODULE)

AI_TRANSLATE_FLOW_SPEC = importlib.util.spec_from_file_location(
    "ui_export_generate_ai_translate_quote_flow",
    Path(__file__).resolve().parents[2]
    / "tools"
    / "ui-export"
    / "generate_ai_translate_quote_flow.py",
)
assert AI_TRANSLATE_FLOW_SPEC is not None
assert AI_TRANSLATE_FLOW_SPEC.loader is not None
AI_TRANSLATE_FLOW_MODULE = importlib.util.module_from_spec(AI_TRANSLATE_FLOW_SPEC)
AI_TRANSLATE_FLOW_SPEC.loader.exec_module(AI_TRANSLATE_FLOW_MODULE)


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


def test_build_all_messages_includes_staged_evaluate_quote_variants():
    entries = build_all_messages()
    names = {entry["name"] for entry in entries}

    assert "DocumentMtQuoteMessage (IBM AI Translate direct quote)" in names
    assert "DocumentMtQuoteMessage (IBM AI Translate accepted)" in names
    assert "EvaluationCreditsQuoteMessage (IBM HT AI quote with PDF)" in names
    assert "EvaluationCreditsQuoteMessage (IBM HT PDF prequote estimate)" in names


def test_ibm_quote_catalog_entry_uses_dollar_display():
    entries = build_all_messages()
    entry = next(
        item
        for item in entries
        if item["name"] == "EvaluationCreditsQuoteMessage (IBM HT AI quote with PDF)"
    )
    rendered = str(entry["blocks"])

    assert "Token cost" not in rendered
    assert "Total tokens" not in rendered
    assert "USD 25.00" in rendered
    assert "USD 2.00" in rendered
    assert "USD 27.00" in rendered


def test_human_job_quote_catalog_entry_shows_quality_discount():
    entries = build_all_messages()
    entry = next(item for item in entries if item["name"] == "HumanJobQuoteMessage")
    rendered = str(entry["blocks"])

    assert "USD 53.75" in rendered
    assert "Quality Evaluation: USD 8.00" in rendered
    assert "Quality: good" in rendered
    assert "-30% off" not in rendered
    assert "USD 46.50" in rendered
    assert "Quality: acceptable" in rendered
    assert "-20% off" not in rendered
    assert "Total Cost*: USD 100.25" in rendered
    assert "saved USD 29.24" in rendered


def test_quote_flow_html_contains_only_new_quote_steps():
    entries = QUOTE_FLOW_MODULE.build_flow_entries()

    assert len(entries) == 3
    assert [entry["entry_name"] for entry in entries] == [
        "EvaluationCreditsQuoteMessage (IBM HT PDF prequote estimate)",
        "EvaluationCreditsQuoteMessage (IBM HT AI quote with PDF)",
        "HumanJobQuoteMessage",
    ]
    payload = QUOTE_FLOW_MODULE.build_flow_payload()
    rendered = str(payload)
    assert "USD 27.00" in rendered
    assert "Quality: good" in rendered
    assert "-30% off" not in rendered
    assert "Quality: acceptable" in rendered
    assert "-20% off" not in rendered
    assert "saved USD 29.24" in rendered
    assert "JobStatusMessage" not in rendered


def test_ai_translate_quote_flow_contains_direct_quote_states():
    entries = AI_TRANSLATE_FLOW_MODULE.build_flow_entries()

    assert len(entries) == 2
    assert [entry["entry_name"] for entry in entries] == [
        "DocumentMtQuoteMessage (IBM AI Translate direct quote)",
        "DocumentMtQuoteMessage (IBM AI Translate accepted)",
    ]
    payload = AI_TRANSLATE_FLOW_MODULE.build_flow_payload()
    rendered = str(payload)
    assert "Direct AI Translate Quote Flow" in rendered
    assert "Service Quote" in rendered
    assert "USD 28.40" in rendered
    assert "Total AI Tokens" not in rendered
    assert "EvaluationCreditsQuoteMessage" not in rendered


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

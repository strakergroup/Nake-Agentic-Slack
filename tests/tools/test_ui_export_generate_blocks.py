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
filter_catalog = MODULE.filter_catalog
filter_entries = MODULE.filter_entries
parse_csv_values = MODULE.parse_csv_values
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


def test_build_all_messages_includes_staged_evaluate_quote_variants():
    entries = build_all_messages()
    names = {entry["name"] for entry in entries}

    assert "DocumentMtQuoteMessage (IBM AI Translate direct quote)" in names
    assert "DocumentMtQuoteMessage (IBM AI Translate accepted)" in names
    assert "EvaluationCreditsQuoteMessage (IBM HT AI quote with PDF)" in names
    assert "EvaluationCreditsQuoteMessage (IBM HT PDF prequote estimate)" in names
    assert "MediaQuoteMessage (Quote1 transcription before AI Translate)" in names
    assert "MediaQuoteMessage (Quote2 AI Translation after transcription)" in names


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
    assert "USD 15.00" in rendered
    assert "USD 10.00" in rendered
    assert "USD 2.00" in rendered
    assert "USD 27.00" in rendered
    assert "AI pre-translation before human review" in rendered
    assert "preparing your human translation quote" in rendered
    assert "Adjust Request" in rendered


def test_media_quote_catalog_entries_include_stage_copy():
    entries = build_all_messages()
    quote1 = next(
        item
        for item in entries
        if item["name"]
        == "MediaQuoteMessage (Quote1 transcription before AI Translate)"
    )
    quote2 = next(
        item
        for item in entries
        if item["name"]
        == "MediaQuoteMessage (Quote2 AI Translation after transcription)"
    )
    assert "must first be transcribed" in str(quote1["blocks"])
    assert "Running the AI translation will incur the following cost:" in str(
        quote2["blocks"]
    )


def test_human_job_quote_catalog_entry_embeds_qe_without_quality_tiers():
    """Catalog combined HT quote matches PRE_QE_QUOTE_DISPLAY (embedded QE)."""
    entries = build_all_messages()
    entry = next(item for item in entries if item["name"] == "HumanJobQuoteMessage")
    rendered = str(entry["blocks"])

    assert "USD 53.75" in rendered
    assert "USD 46.50" in rendered
    assert "Quality Evaluation: USD 8.00" not in rendered
    assert "Quality: " not in rendered
    assert "-30% off" not in rendered
    assert "-20% off" not in rendered
    assert "Maximum Total Cost*: USD 100.25" in rendered
    assert "saved USD" not in rendered
    assert "discount" in rendered.lower()
    assert "*Human Translation:*" in rendered


def test_standalone_ht_quote_catalog_entry_matches_prod_ht_only():
    entries = build_all_messages()
    entry = next(
        item
        for item in entries
        if item["name"] == "HumanJobQuoteMessage (standalone HT)"
    )
    rendered = str(entry["blocks"])

    assert "USD 45.75" in rendered
    assert "USD 38.50" in rendered
    assert "Quality Evaluation" not in rendered
    assert "Quality: " not in rendered
    assert "Total Cost*: USD 84.25" in rendered
    assert "saved USD" not in rendered
    assert "Maximum Total Cost" not in rendered


def test_filter_catalog_by_match_and_category():
    catalog = build_catalog("en")
    filtered = filter_catalog(
        catalog,
        match=r"MediaQuote|HumanJobQuote|EvaluationCredits|DocumentMtQuote|evaluation_ai_quote_adjust",
        categories=["Quotes", "Modals"],
    )
    names = {
        entry["name"] for kind in ("messages", "views") for entry in filtered[kind]
    }

    assert filtered["stats"]["total"] >= 6
    assert "MediaQuoteMessage (Quote1 transcription before AI Translate)" in names
    assert "HumanJobQuoteMessage" in names
    assert "evaluation_ai_quote_adjust_modal" in names
    assert "LoginMessage" not in names
    assert all(
        entry["category"] in {"Quotes", "Modals"}
        for kind in ("messages", "views")
        for entry in filtered[kind]
    )


def test_filter_entries_by_exact_names():
    entries = [
        {"name": "Alpha", "category": "Quotes"},
        {"name": "Beta", "category": "Quotes"},
        {"name": "Gamma", "category": "Auth"},
    ]
    assert [entry["name"] for entry in filter_entries(entries, names=["Beta"])] == [
        "Beta"
    ]
    assert parse_csv_values("Quotes, Modals, Quotes") == ["Quotes", "Modals"]


def test_build_all_views_includes_ibm_connected_home_variants():
    entries = build_all_views()
    names = {entry["name"] for entry in entries}

    assert "home_view (IBM, connected, admin)" in names
    assert "home_view (IBM, connected, non-admin)" in names
    assert "home_view (IBM, connected)" not in names


def test_build_all_messages_includes_previously_missing_templates():
    entries = build_all_messages()
    names = {entry["name"] for entry in entries}

    assert "MissingSlackFilesMessage (single)" in names
    assert "MissingSlackFilesMessage (multiple)" in names
    assert "JobFileListEmptyMessage (in-progress)" in names
    assert "JobFileListEmptyMessage (completed)" in names
    assert "MediaTranslationPartialMessage" in names
    assert "MediaEmbeddingPartialMessage" in names
    assert "EvaluateAiOnlyCompleteMessage" in names


def test_build_all_views_includes_evaluation_ai_quote_adjust_modal():
    entries = build_all_views()
    entry = next(
        item for item in entries if item["name"] == "evaluation_ai_quote_adjust_modal"
    )
    rendered = str(entry["blocks"])

    assert entry["type"] == "modal"
    assert entry["title"] == "Adjust Request"
    assert "independent per file" in rendered
    assert ":paperclip: *marketing-copy.docx*" in rendered
    assert ":paperclip: *pricing.xlsx*" in rendered
    assert "*Total cost:*" in rendered
    assert "PDF conversion" in rendered


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
    configure_translation("fr", {'Hello <x id="1"/>': 'Bonjour <x id="1"/>'})

    try:
        translated = _mock_translate("Hello {name}")
    finally:
        configure_translation("en")

    assert translated == "Bonjour {name}"

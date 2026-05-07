import ast
import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "translation-export"
    / "export_missing_strings.py"
)
APP_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "translation_export_missing_strings",
    MODULE_PATH,
)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

StringEntry = MODULE.StringEntry
MissingStringRow = MODULE.MissingStringRow
build_missing_rows = MODULE.build_missing_rows
collect_string_entries = MODULE.collect_string_entries
infer_format = MODULE.infer_format
normalize_db_label_for_lookup = MODULE.normalize_db_label_for_lookup
parse_languages = MODULE.parse_languages
resolve_locale_target = MODULE.resolve_locale_target
tag_placeholders = MODULE.tag_placeholders
ensure_repo_root_on_path = MODULE.ensure_repo_root_on_path
split_output_path = MODULE.split_output_path
write_rows_by_language = MODULE.write_rows_by_language


def _literal_translation_strings(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    strings: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            strings.add(node.args[0].value)
    return strings


def _translation_call_arg_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            names.add(node.args[0].id)
    return names


def test_parse_languages_deduplicates_case_insensitively():
    assert parse_languages("fr-FR, de-DE, fr-fr, ,ja-JP") == [
        "fr-FR",
        "de-DE",
        "ja-JP",
    ]


def test_tag_placeholders_matches_runtime_translator_pattern():
    assert (
        tag_placeholders("Hello {name}, use :white_check_mark: for {thing}.")
        == "Hello <x id=1/>, use <x id=2/> for <x id=3/>."
    )
    assert tag_placeholders("{name} invited {name}") == "<x id=1/> invited <x id=1/>"


def test_normalize_db_label_for_lookup_matches_observed_mysql_equality():
    assert normalize_db_label_for_lookup("Select Languages  ") == "select languages"
    assert normalize_db_label_for_lookup(" Select Languages") == " select languages"
    assert normalize_db_label_for_lookup("*Group:*\n") == "*group:*\n"


def test_collect_string_entries_extracts_literal_calls(tmp_path):
    source_dir = tmp_path / "app"
    source_dir.mkdir()
    source_file = source_dir / "messages.py"
    source_file.write_text(
        "\n".join(
            [
                "from app.translate import _",
                'title = _("Translate {count} files", max_length=40)',
                'dynamic = _("Ignored " + value)',
                'body = _("Ready :white_check_mark:")',
            ]
        ),
        encoding="utf-8",
    )

    entries = collect_string_entries(source_dir, root=tmp_path)

    assert [
        (entry.source_text, entry.db_label, entry.max_length) for entry in entries
    ] == [
        ("Translate {count} files", "Translate <x id=1/> files", 40),
        ("Ready :white_check_mark:", "Ready <x id=1/>", 0),
    ]
    assert entries[0].locations == ["app/messages.py:2"]


def test_ray_callback_error_templates_are_exportable_literals():
    strings = _literal_translation_strings(APP_ROOT / "app" / "routers" / "ray.py")

    assert "Transcription failed: {error_detail}" in strings
    assert "Translation failed: {error_detail}" in strings
    assert "Embedding failed: {error_detail}" in strings
    assert "Transcription failed: %s" not in strings
    assert "Translation failed: %s" not in strings
    assert "Embedding failed: %s" not in strings


def test_invalid_pdf_error_message_does_not_translate_payload_variable():
    path = APP_ROOT / "app" / "slack" / "templates" / "messages.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        assert not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "error_message"
        )

    strings = _literal_translation_strings(path)
    assert "Invalid PDF file: {error_detail}" in strings
    assert "Invalid PDF file. Please check the file and try again." in strings


def test_slack_message_ui_catalog_labels_are_exportable_literals():
    path = APP_ROOT / "app" / "slack" / "templates" / "messages.py"
    strings = _literal_translation_strings(path)

    assert {
        "Connect your account to view your jobs.",
        "Connect your account to submit a new translation job.",
        "Connect your account to cancel your job.",
        "Connect your account to evaluate the quality of your translation.",
        "Connect your account to perform human translation.",
        "Your connected account is: {user_details}. \nYou can connect a different account by clicking this button.",
        "{bookEmoji} Learn Quality Evaluation Help",
        "{bookEmoji} Learn Human Translation Help",
        "*<{job_url}|Straker Job Reference {quote.id}>*",
        "*Straker Job Reference {quote.id}*",
        "*Validation*\n{validation_status}",
        "{user_mention} has changed the translation settings. The bot will respond to messages sent in <#{channel_id}> which will be translated into {langs_string} through thread replies in real-time.",
        "{user_mention} has changed the translation settings. The bot will respond to messages sent in <#{channel_id}> which will be translated into {langs_string} through messages in real-time.",
    }.issubset(strings)

    dynamic_arg_names = _translation_call_arg_names(path)
    assert "block_text" not in dynamic_arg_names
    assert "formatted_url" not in dynamic_arg_names


def test_payload_language_and_service_values_are_not_translated_directly():
    message_path = APP_ROOT / "app" / "slack" / "templates" / "messages.py"
    block_path = APP_ROOT / "app" / "slack" / "templates" / "blocks.py"

    assert "lang_label" not in _translation_call_arg_names(message_path)

    block_tree = ast.parse(
        block_path.read_text(encoding="utf-8"), filename=str(block_path)
    )
    translated_attribute_names = {
        node.args[0].attr
        for node in ast.walk(block_tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
        )
    }
    assert "label" not in translated_attribute_names
    assert "service" not in translated_attribute_names


def test_format_strings_display_exports_known_conjunction_literals():
    strings = _literal_translation_strings(APP_ROOT / "app" / "slack" / "utils.py")

    assert "&" in strings
    assert "and" in strings


def test_build_missing_rows_uses_slack_locale_to_db_lang_mapping():
    entries = [
        StringEntry(
            source_text="Submit {count}",
            db_label="Submit <x id=1/>",
            max_length=12,
            locations=["app/example.py:10"],
        ),
        StringEntry(
            source_text="Cancel",
            db_label="Cancel",
            locations=["app/example.py:11"],
        ),
    ]

    rows = build_missing_rows(
        entries=entries,
        slack_locales=["fr-FR", "ja-JP", "en-US"],
        language_map={"fr-fr": "fr", "ja-jp": "jp"},
        existing_labels_by_lang={"fr": {"Cancel"}, "jp": set()},
    )

    assert [
        (row.source_language, row.target_language, row.source_text) for row in rows
    ] == [
        ("en", "fr", "Submit <x id=1/>"),
        ("en", "jp", "Submit <x id=1/>"),
        ("en", "jp", "Cancel"),
    ]
    assert rows[0].max_length == 12
    assert rows[0].target_text == ""


def test_build_missing_rows_matches_existing_labels_case_insensitively():
    rows = build_missing_rows(
        entries=[
            StringEntry(source_text="Select languages", db_label="Select languages")
        ],
        slack_locales=["fr-FR"],
        language_map={"fr-fr": "fr"},
        existing_labels_by_lang={"fr": {"Select Languages"}},
    )

    assert rows == []


def test_build_missing_rows_matches_existing_labels_with_trailing_space_variants():
    rows = build_missing_rows(
        entries=[
            StringEntry(
                source_text="<x id=1/> Search allows you to find specific Translation Jobs (TJs).",
                db_label="<x id=1/> Search allows you to find specific Translation Jobs (TJs).",
            ),
            StringEntry(source_text="Trailing DB label", db_label="Trailing DB label "),
        ],
        slack_locales=["fr-FR"],
        language_map={"fr-fr": "fr"},
        existing_labels_by_lang={
            "fr": {
                "<x id=1/> Search allows you to find specific Translation Jobs (TJs). ",
                "Trailing DB label",
            }
        },
    )

    assert rows == []


def test_build_missing_rows_still_exports_truly_different_strings():
    rows = build_missing_rows(
        entries=[
            StringEntry(source_text="Select languages", db_label="Select languages"),
            StringEntry(source_text=" Select languages", db_label=" Select languages"),
        ],
        slack_locales=["fr-FR"],
        language_map={"fr-fr": "fr"},
        existing_labels_by_lang={"fr": {"Select Languages"}},
    )

    assert [(row.target_language, row.source_text) for row in rows] == [
        ("fr", " Select languages")
    ]


def test_build_missing_rows_does_not_match_trailing_newline_variant():
    rows = build_missing_rows(
        entries=[StringEntry(source_text="*Group:*", db_label="*Group:*")],
        slack_locales=["ja-JP"],
        language_map={"ja-jp": "jp"},
        existing_labels_by_lang={"jp": {"*Group:*\n"}},
    )

    assert [(row.target_language, row.source_text) for row in rows] == [
        ("jp", "*Group:*")
    ]


def test_build_missing_rows_flags_unmapped_slack_locale():
    rows = build_missing_rows(
        entries=[StringEntry(source_text="Submit", db_label="Submit")],
        slack_locales=["pt-BR"],
        language_map={},
        existing_labels_by_lang={"pt-BR": set()},
    )

    assert rows[0].target_language == "pt-BR"


def test_resolve_locale_target_identifies_english_shortcut():
    target = resolve_locale_target("en-US", {})

    assert target.is_english is True
    assert target.db_lang == "en-US"


def test_infer_format_prefers_explicit_format():
    assert infer_format(Path("missing.xlsx"), None) == "xlsx"
    assert infer_format(Path("missing.unknown"), None) == "csv"
    assert infer_format(Path("missing.csv"), "json") == "json"


def test_split_output_path_appends_db_language_before_suffix():
    assert split_output_path(
        Path("output/missing_strings.xlsx"), "fr-ca", "xlsx"
    ) == Path("output/missing_strings_fr-ca.xlsx")


def test_write_rows_by_language_creates_one_file_per_language(tmp_path):
    rows = [
        MissingStringRow(
            source_language="en",
            target_language="fr",
            source_text="Submit",
            target_text="",
            max_length=0,
        )
    ]

    output_paths = write_rows_by_language(
        rows,
        tmp_path / "missing_strings.csv",
        "csv",
        ["fr", "de"],
    )

    assert output_paths == [
        tmp_path / "missing_strings_fr.csv",
        tmp_path / "missing_strings_de.csv",
    ]
    assert "Submit" in output_paths[0].read_text(encoding="utf-8")
    assert (
        output_paths[1]
        .read_text(encoding="utf-8")
        .startswith("source_language,target_language,source_text")
    )


def test_ensure_repo_root_on_path_adds_app_import_path(monkeypatch):
    root = str(MODULE.repo_root())
    monkeypatch.setattr(
        MODULE.sys, "path", [path for path in MODULE.sys.path if path != root]
    )

    ensure_repo_root_on_path()

    assert MODULE.sys.path[0] == root

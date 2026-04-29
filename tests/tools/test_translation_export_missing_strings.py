import importlib.util
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools"
    / "translation-export"
    / "export_missing_strings.py"
)
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
parse_languages = MODULE.parse_languages
resolve_locale_target = MODULE.resolve_locale_target
tag_placeholders = MODULE.tag_placeholders
ensure_repo_root_on_path = MODULE.ensure_repo_root_on_path
split_output_path = MODULE.split_output_path
write_rows_by_language = MODULE.write_rows_by_language


def test_parse_languages_deduplicates_case_insensitively():
    assert parse_languages("fr-FR, de-DE, fr-fr, ,ja-JP") == [
        "fr-FR",
        "de-DE",
        "ja-JP",
    ]


def test_tag_placeholders_matches_runtime_translator_pattern():
    assert (
        tag_placeholders("Hello {name}, use :white_check_mark: for {thing}.")
        == "Hello <x id=1>, use <x id=2> for <x id=3>."
    )
    assert tag_placeholders("{name} invited {name}") == "<x id=1> invited <x id=1>"


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
        ("Translate {count} files", "Translate <x id=1> files", 40),
        ("Ready :white_check_mark:", "Ready <x id=1>", 0),
    ]
    assert entries[0].locations == ["app/messages.py:2"]


def test_build_missing_rows_uses_slack_locale_to_db_lang_mapping():
    entries = [
        StringEntry(
            source_text="Submit {count}",
            db_label="Submit <x id=1>",
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
        ("en", "fr", "Submit <x id=1>"),
        ("en", "jp", "Submit <x id=1>"),
        ("en", "jp", "Cancel"),
    ]
    assert rows[0].max_length == 12
    assert rows[0].target_text == ""


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

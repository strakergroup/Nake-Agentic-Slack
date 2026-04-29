import importlib.util
import sys
from pathlib import Path

import openpyxl

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools" / "translation-export"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def load_tool_module(module_name: str):
    module_path = TOOLS_DIR / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MT_FILL = load_tool_module("mt_fill_translations")
IMPORT_SQL = load_tool_module("import_xlsx_translation")


def make_workbook(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "slack_locale",
            "db_lang",
            "source_text",
            "db_label",
            "translation",
            "max_length",
            "locations",
            "notes",
        ]
    )
    sheet.append(["fr-FR", "fr", "Submit {count}", "Submit <x id=1>", "", 0, "", ""])
    sheet.append(["de-DE", "de", "Cancel", "Cancel", "Abbrechen", 0, "", ""])
    workbook.save(path)
    workbook.close()


def test_fill_workbook_translations_populates_blank_translation(tmp_path, monkeypatch):
    workbook_path = tmp_path / "missing_strings.xlsx"
    make_workbook(workbook_path)
    monkeypatch.setattr(
        MT_FILL,
        "mt_translate_db_label",
        lambda text, target_lang: f"{target_lang}:{text}",
    )

    filled, total = MT_FILL.fill_workbook_translations(workbook_path)

    workbook = openpyxl.load_workbook(workbook_path)
    try:
        sheet = workbook.active
        assert filled == 1
        assert total == 2
        assert sheet["E2"].value == "fr:Submit <x id=1>"
        assert sheet["E3"].value == "Abbrechen"
    finally:
        workbook.close()


def test_write_import_sql_skips_empty_translations(tmp_path):
    workbook_path = tmp_path / "missing_strings.xlsx"
    output_path = tmp_path / "import.sql"
    make_workbook(workbook_path)

    count = IMPORT_SQL.write_import_sql([workbook_path], output_path)

    sql = output_path.read_text(encoding="utf-8")
    assert count == 1
    assert '"de"' in sql
    assert '"Cancel"' in sql
    assert '"Abbrechen"' in sql
    assert "Submit" not in sql


def test_validate_translation_tags_reports_missing_and_bad_tags():
    errors = IMPORT_SQL.validate_translation_tags(
        "Submit <x id=1>",
        "Soumettre <x id=two>",
    )

    assert "translation contains malformed tag(s): <x id=two>" in errors
    assert "translation is missing placeholder tag(s): <x id=1>" in errors


def test_write_import_sql_fails_and_reports_placeholder_validation_errors(tmp_path):
    workbook_path = tmp_path / "missing_strings.xlsx"
    output_path = tmp_path / "import.sql"
    report_path = tmp_path / "validation_errors.csv"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "slack_locale",
            "db_lang",
            "source_text",
            "db_label",
            "translation",
            "max_length",
            "locations",
            "notes",
        ]
    )
    sheet.append(
        [
            "fr-FR",
            "fr",
            "Submit {count}",
            "Submit <x id=1>",
            "Soumettre",
            0,
            "",
            "",
        ]
    )
    workbook.save(workbook_path)
    workbook.close()

    try:
        IMPORT_SQL.write_import_sql([workbook_path], output_path, report_path)
    except ValueError as exc:
        assert "Validation failed for 1 translation row(s)" in str(exc)
    else:
        raise AssertionError("Expected placeholder validation to fail")

    report = report_path.read_text(encoding="utf-8")
    assert "translation is missing placeholder tag(s): <x id=1>" in report
    assert not output_path.exists()


def test_fill_workbooks_accepts_glob_input(tmp_path, monkeypatch):
    first_path = tmp_path / "missing_strings_fr.xlsx"
    second_path = tmp_path / "missing_strings_de.xlsx"
    make_workbook(first_path)
    make_workbook(second_path)
    monkeypatch.setattr(
        MT_FILL,
        "mt_translate_db_label",
        lambda text, target_lang: f"{target_lang}:{text}",
    )

    paths = MT_FILL.resolve_workbook_paths(str(tmp_path / "missing_strings_*.xlsx"))

    assert paths == [second_path, first_path]


def test_format_insert_statement_escapes_sql_values():
    statement = IMPORT_SQL.format_insert_statement(
        'Label "quoted"',
        "fr",
        "Line 1\nLine 2",
        "2026-04-30 00:00:00",
        "2026-04-30 00:00:00",
    )

    assert 'Label \\"quoted\\"' in statement
    assert "Line 1\\nLine 2" in statement

import importlib.util
import sys
from pathlib import Path

import openpyxl
import pytest

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
            "source_language",
            "target_language",
            "source_text",
            "target_text",
            "max_length",
        ]
    )
    sheet.append(["en", "fr", "Submit <x id=1>", "", 0])
    sheet.append(["en", "de", "Cancel", "Abbrechen", 0])
    workbook.save(path)
    workbook.close()


def test_fill_workbook_translations_populates_blank_translation(tmp_path, monkeypatch):
    workbook_path = tmp_path / "missing_strings.xlsx"
    make_workbook(workbook_path)
    monkeypatch.setattr(MT_FILL, "fetch_mt_language_map", lambda: {})
    monkeypatch.setattr(
        MT_FILL,
        "mt_translate_db_label",
        lambda text, target_lang, client_login=None: f"{target_lang}:{text}",
    )

    filled, total = MT_FILL.fill_workbook_translations(workbook_path)

    workbook = openpyxl.load_workbook(workbook_path)
    try:
        sheet = workbook.active
        assert filled == 1
        assert total == 2
        assert sheet["D2"].value == "fr:Submit <x id=1>"
        assert sheet["D3"].value == "Abbrechen"
    finally:
        workbook.close()


def test_fill_workbook_translations_decodes_html_entities_and_preserves_tags(
    tmp_path, monkeypatch
):
    workbook_path = tmp_path / "missing_strings.xlsx"
    make_workbook(workbook_path)
    monkeypatch.setattr(MT_FILL, "fetch_mt_language_map", lambda: {})
    monkeypatch.setattr(
        MT_FILL,
        "mt_translate_db_label",
        lambda text, target_lang, client_login=None: (
            "L&#39;envoi &quot;OK&quot; &amp; <x id=1>&nbsp;"
        ),
    )

    filled, total = MT_FILL.fill_workbook_translations(workbook_path)

    workbook = openpyxl.load_workbook(workbook_path)
    try:
        sheet = workbook.active
        assert filled == 1
        assert total == 2
        assert sheet["D2"].value == 'L\'envoi "OK" & <x id=1>\xa0'
    finally:
        workbook.close()


def test_fill_workbook_maps_korean_db_lang_to_mt_code(tmp_path, monkeypatch):
    workbook_path = tmp_path / "missing_strings_kr.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "source_language",
            "target_language",
            "source_text",
            "target_text",
            "max_length",
        ]
    )
    sheet.append(["en", "kr", "Submit", "", 0])
    workbook.save(workbook_path)
    workbook.close()

    requested_targets = []

    def fake_translate(text, target_lang, client_login=None):
        requested_targets.append(target_lang)
        return "제출"

    monkeypatch.setattr(MT_FILL, "fetch_mt_language_map", lambda: {"kr": "ko"})
    monkeypatch.setattr(MT_FILL, "mt_translate_db_label", fake_translate)

    filled, total = MT_FILL.fill_workbook_translations(workbook_path)

    workbook = openpyxl.load_workbook(workbook_path)
    try:
        sheet = workbook.active
        assert filled == 1
        assert total == 1
        assert requested_targets == ["ko"]
        assert sheet["B2"].value == "kr"
        assert sheet["D2"].value == "제출"
    finally:
        workbook.close()


def test_validate_mt_translation_rejects_unchanged_non_english_output():
    with pytest.raises(ValueError, match="unchanged source text"):
        MT_FILL.validate_mt_translation("Submit", "kr", "Submit")


def test_fill_workbook_raises_and_notes_unchanged_non_english_output(
    tmp_path, monkeypatch
):
    workbook_path = tmp_path / "missing_strings.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "source_language",
            "target_language",
            "source_text",
            "target_text",
            "max_length",
        ]
    )
    for label in ["Submit", "Cancel", "Done", "Back", "Next"]:
        sheet.append(["en", "kr", label, "", 0])
    workbook.save(workbook_path)
    workbook.close()
    monkeypatch.setattr(MT_FILL, "fetch_mt_language_map", lambda: {})
    monkeypatch.setattr(
        MT_FILL,
        "mt_translate_db_label",
        lambda text, target_lang, client_login=None: text,
    )

    with pytest.raises(ValueError, match="MT fill failed for 1 row"):
        MT_FILL.fill_workbook_translations(workbook_path)

    workbook = openpyxl.load_workbook(workbook_path)
    try:
        sheet = workbook.active
        assert sheet["F1"].value == "notes"
        assert "suspicious share" in sheet["F2"].value
        assert sheet["D2"].value == "Submit"
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


def test_validate_translation_text_reports_matching_non_english_source():
    errors = IMPORT_SQL.validate_translation_text("kr", "Submit", "Submit")

    assert (
        "translation matches source text for non-English language; review MT output"
        in errors
    )
    assert IMPORT_SQL.validate_translation_text("en", "Submit", "Submit") == []


def test_write_import_sql_fails_and_reports_placeholder_validation_errors(tmp_path):
    workbook_path = tmp_path / "missing_strings.xlsx"
    output_path = tmp_path / "import.sql"
    report_path = tmp_path / "validation_errors.csv"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "source_language",
            "target_language",
            "source_text",
            "target_text",
            "max_length",
        ]
    )
    sheet.append(
        [
            "en",
            "fr",
            "Submit <x id=1>",
            "Soumettre",
            0,
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


def test_write_import_sql_fails_on_matching_non_english_source_text(tmp_path):
    workbook_path = tmp_path / "missing_strings.xlsx"
    output_path = tmp_path / "import.sql"
    report_path = tmp_path / "validation_errors.csv"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(
        [
            "source_language",
            "target_language",
            "source_text",
            "target_text",
            "max_length",
        ]
    )
    for label in ["Submit", "Cancel", "Done", "Back", "Next"]:
        sheet.append(["en", "kr", label, label, 0])
    workbook.save(workbook_path)
    workbook.close()

    with pytest.raises(ValueError, match="Validation failed for 5 translation row"):
        IMPORT_SQL.write_import_sql([workbook_path], output_path, report_path)

    report = report_path.read_text(encoding="utf-8")
    assert "suspicious share of this workbook" in report
    assert not output_path.exists()


def test_fill_workbooks_accepts_glob_input(tmp_path, monkeypatch):
    first_path = tmp_path / "missing_strings_fr.xlsx"
    second_path = tmp_path / "missing_strings_de.xlsx"
    make_workbook(first_path)
    make_workbook(second_path)
    monkeypatch.setattr(
        MT_FILL,
        "mt_translate_db_label",
        lambda text, target_lang, client_login=None: f"{target_lang}:{text}",
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


def test_build_auth_header_prefers_env_token(monkeypatch):
    monkeypatch.setenv("LANGUAGECLOUD_API_TOKEN", "env-token")
    monkeypatch.setattr(
        MT_FILL,
        "generate_languagecloud_api_token",
        lambda client_login: "generated-token",
    )

    assert MT_FILL.build_auth_header("Elanex-205317") == {
        "Authorization": "Bearer env-token"
    }


def test_build_auth_header_generates_token_for_client_id(monkeypatch):
    monkeypatch.delenv("LANGUAGECLOUD_API_TOKEN", raising=False)
    monkeypatch.setattr(
        MT_FILL,
        "generate_languagecloud_api_token",
        lambda client_login: f"generated:{client_login}",
    )

    assert MT_FILL.build_auth_header("Elanex-205317") == {
        "Authorization": "Bearer generated:Elanex-205317"
    }


def test_mt_translate_db_label_uses_languagecloud_api_schema(monkeypatch):
    class FakeTranslationResponse:
        @classmethod
        def model_validate(cls, data):
            raise AssertionError("translations dict should be read directly")

    class FakePayload:
        def __init__(self):
            self.data = {
                "translations": {
                    "fr": "Bonjour <x id=1>",
                }
            }

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def post(self, url, headers, json):
            assert url == "https://languagecloud.example.test/mt/translate"
            assert headers == {"Authorization": "Bearer token"}
            assert json == {
                "text": "Hello <x id=1>",
                "target_languages": ["fr"],
                "app_name": "slack",
                "usage_type": "translation_export_mt_fill",
            }
            return FakePayload()

    monkeypatch.setattr(
        MT_FILL,
        "load_app_mt_types",
        lambda: (FakeTranslationResponse, "https://languagecloud.example.test"),
    )
    monkeypatch.setattr(
        MT_FILL,
        "build_auth_header",
        lambda client_id: {"Authorization": "Bearer token"},
    )
    monkeypatch.setattr(MT_FILL.httpx, "Client", FakeClient)

    assert (
        MT_FILL.mt_translate_db_label("Hello <x id=1>", "fr", "client-id")
        == "Bonjour <x id=1>"
    )


def test_mt_translate_db_label_fails_english_fallback_for_non_english(monkeypatch):
    class FakeTranslationResponse:
        @classmethod
        def model_validate(cls, data):
            raise AssertionError("translations dict should be read directly")

    class FakePayload:
        def raise_for_status(self):
            return None

        def json(self):
            return {"translations": {"en": "Submit"}}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def post(self, url, headers, json):
            return FakePayload()

    monkeypatch.setattr(
        MT_FILL,
        "load_app_mt_types",
        lambda: (FakeTranslationResponse, "https://languagecloud.example.test"),
    )
    monkeypatch.setattr(
        MT_FILL,
        "build_auth_header",
        lambda client_id: {"Authorization": "Bearer token"},
    )
    monkeypatch.setattr(MT_FILL.httpx, "Client", FakeClient)

    with pytest.raises(ValueError, match="English fallback"):
        MT_FILL.mt_translate_db_label("Submit", "ko", "client-id")


def test_configured_client_id_prefers_client_id_env(monkeypatch):
    monkeypatch.setenv("LANGUAGECLOUD_API_CLIENT_ID", "client-id")
    monkeypatch.setenv("LANGUAGECLOUD_API_CLIENT_LOGIN", "old-login")

    assert MT_FILL.configured_client_id() == "client-id"


def test_configured_client_id_requires_env_or_cli(monkeypatch):
    monkeypatch.delenv("LANGUAGECLOUD_API_CLIENT_ID", raising=False)
    monkeypatch.delenv("LANGUAGECLOUD_API_CLIENT_LOGIN", raising=False)

    try:
        MT_FILL.configured_client_id()
    except ValueError as exc:
        assert "LANGUAGECLOUD_API_CLIENT_ID or --client-id must be set" in str(exc)
    else:
        raise AssertionError("Expected missing client id to fail")

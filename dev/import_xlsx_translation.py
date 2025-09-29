import uuid
from datetime import datetime
from pathlib import Path

import openpyxl

LANGS = ["fr", "de", "es", "ja", "fr-ca"]
WORKDIR = Path(__file__).resolve().parent
OUTPUT_PATH = WORKDIR / "import.sql"
CREATED_TIMESTAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
MODIFIED_TIMESTAMP = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def escape_sql_value(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def format_insert_statement(
    label: object,
    lang: str,
    translation: object,
    created: str,
    modified: str,
) -> str:
    uuid_str = str(uuid.uuid4())
    label_sql = escape_sql_value(label)
    lang_sql = escape_sql_value(lang)
    translation_sql = escape_sql_value(translation)
    return (
        "INSERT INTO `obj_stringtranslator` "
        "(`obj_uuid`, `created`, `modified`, `label`, `lang`, `langstring`, `active`) "
        f"VALUES ('{uuid_str}', '{created}', '{modified}', \"{label_sql}\", \"{lang_sql}\", \"{translation_sql}\", 1);\n"
    )


def main() -> None:
    with OUTPUT_PATH.open("w", encoding="utf-8") as sql_file:
        for lang in LANGS:
            workbook_path = WORKDIR / f"translations_{lang}.xlsx"
            workbook = openpyxl.load_workbook(workbook_path)
            try:
                sheet = workbook.active
                for label, translation in sheet.iter_rows(
                    min_row=2, min_col=1, max_col=2, values_only=True
                ):
                    statement = format_insert_statement(
                        label, lang, translation, CREATED_TIMESTAMP, MODIFIED_TIMESTAMP
                    )
                    sql_file.write(statement)
            finally:
                workbook.close()


if __name__ == "__main__":
    main()

"""Fill missing translation workbook rows with LanguageCloud MT."""

from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import openpyxl
from export_missing_strings import ensure_repo_root_on_path
from sqlalchemy import text

TRANSLATION_COLUMN = "target_text"
DB_LABEL_COLUMN = "source_text"
DB_LANG_COLUMN = "target_language"
NOTES_COLUMN = "notes"
COLUMN_ALIASES = {
    TRANSLATION_COLUMN: ("translation",),
    DB_LABEL_COLUMN: ("db_label",),
    DB_LANG_COLUMN: ("db_lang",),
}


def load_app_mt_types() -> tuple[Any, str]:
    ensure_repo_root_on_path()
    from app.config import domains
    from app.mt.schemas import TranslationResponse

    return TranslationResponse, str(domains.languagecloud_api)


def generate_languagecloud_api_token(client_id: str) -> str:
    ensure_repo_root_on_path()
    from straker_auth.languagecloud import create_languagecloud_id_token

    from app.config import config
    from app.database import engines

    with engines["sitemanager_readonly"].connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT obj_uuid, given_name, family_name, email_primary, active
                FROM obj_m_member
                WHERE obj_uuid = :client_id
                AND is_deleted = 0
                LIMIT 1
                """
            ).bindparams(client_id=client_id)
        ).first()

    if not row:
        raise ValueError(f"LanguageCloud client {client_id!r} was not found")

    return create_languagecloud_id_token(
        uuid=row.obj_uuid,
        given_name=row.given_name or "",
        family_name=row.family_name or "",
        email=row.email_primary or "",
        is_active=bool(row.active),
        aud="languagecloud-api",
        secret=config.languagecloud_api_key.get_secret_value(),
    )


def configured_client_id() -> str:
    client_id = os.getenv("LANGUAGECLOUD_API_CLIENT_ID") or os.getenv(
        "LANGUAGECLOUD_API_CLIENT_LOGIN"
    )
    if not client_id:
        raise ValueError(
            "LANGUAGECLOUD_API_CLIENT_ID or --client-id must be set when "
            "LANGUAGECLOUD_API_TOKEN is not set"
        )
    return client_id


def build_auth_header(client_id: str | None = None) -> dict[str, str]:
    env_token = os.getenv("LANGUAGECLOUD_API_TOKEN")
    token = env_token or generate_languagecloud_api_token(
        client_id or configured_client_id()
    )
    return {"Authorization": f"Bearer {token}"}


def mt_translate_db_label(
    text: str, target_lang: str, client_id: str | None = None
) -> str:
    TranslationResponse, default_base_url = load_app_mt_types()
    base_url = os.getenv("LANGUAGECLOUD_API_URL") or default_base_url
    url = f"{base_url.rstrip('/')}/mt/translate"
    payload = {
        "text": text,
        "target_languages": [target_lang],
        "app_name": "slack",
        "usage_type": "translation_export_mt_fill",
    }

    with httpx.Client(timeout=60.0) as client:
        response = client.post(url, headers=build_auth_header(client_id), json=payload)
        response.raise_for_status()
        data = response.json()

    translations: dict[str, str] = {}
    if isinstance(data, dict) and isinstance(data.get("translations"), dict):
        translations = data["translations"]
    else:
        translations = TranslationResponse.model_validate(data).translations or {}

    return translations.get(target_lang) or next(iter(translations.values()), text)


def header_indexes(sheet) -> dict[str, int]:
    headers = [cell.value for cell in sheet[1]]
    return {
        str(header): index + 1
        for index, header in enumerate(headers)
        if isinstance(header, str)
    }


def column_index(indexes: dict[str, int], column: str) -> int | None:
    if column in indexes:
        return indexes[column]
    return next(
        (
            indexes[alias]
            for alias in COLUMN_ALIASES.get(column, ())
            if alias in indexes
        ),
        None,
    )


def append_note(existing: object, note: str) -> str:
    existing_text = "" if existing is None else str(existing).strip()
    if not existing_text:
        return note
    return f"{existing_text}; {note}"


def decode_html_entities(value: str) -> str:
    return html.unescape(value)


def fill_workbook_translations(
    xlsx_path: Path,
    client_id: str | None = None,
) -> tuple[int, int]:
    workbook = openpyxl.load_workbook(xlsx_path)
    try:
        sheet = workbook.active
        indexes = header_indexes(sheet)
        label_index = column_index(indexes, DB_LABEL_COLUMN)
        lang_index = column_index(indexes, DB_LANG_COLUMN)
        translation_index = column_index(indexes, TRANSLATION_COLUMN)
        missing_columns = [
            column
            for column, index in (
                (DB_LABEL_COLUMN, label_index),
                (DB_LANG_COLUMN, lang_index),
                (TRANSLATION_COLUMN, translation_index),
            )
            if index is None
        ]
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Workbook is missing required columns: {missing}")
        assert label_index is not None
        assert lang_index is not None
        assert translation_index is not None

        notes_index = indexes.get(NOTES_COLUMN)
        filled = 0
        total = 0
        for row_number in range(2, sheet.max_row + 1):
            label_cell = sheet.cell(row=row_number, column=label_index)
            lang_cell = sheet.cell(row=row_number, column=lang_index)
            translation_cell = sheet.cell(
                row=row_number,
                column=translation_index,
            )
            if not label_cell.value or not lang_cell.value:
                continue
            total += 1
            if translation_cell.value and str(translation_cell.value).strip():
                continue
            try:
                translation_cell.value = decode_html_entities(
                    mt_translate_db_label(
                        str(label_cell.value),
                        str(lang_cell.value),
                        client_id,
                    )
                )
                filled += 1
            except Exception as exc:
                if notes_index:
                    notes_cell = sheet.cell(row=row_number, column=notes_index)
                    notes_cell.value = append_note(notes_cell.value, f"MT error: {exc}")

        workbook.save(xlsx_path)
        return filled, total
    finally:
        workbook.close()


def resolve_workbook_paths(input_pattern: str) -> list[Path]:
    matches = sorted(glob.glob(input_pattern))
    if matches:
        return [Path(match) for match in matches]
    return [Path(input_pattern)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fill translation-export workbook rows with LanguageCloud MT."
    )
    parser.add_argument(
        "--input",
        default=str(Path(__file__).parent / "output" / "missing_strings.xlsx"),
        help="Workbook generated by export_missing_strings.py. Globs are supported.",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("LANGUAGECLOUD_API_CLIENT_ID")
        or os.getenv("LANGUAGECLOUD_API_CLIENT_LOGIN"),
        help=(
            "LanguageCloud client UUID used to generate a JWT when "
            "LANGUAGECLOUD_API_TOKEN is not set."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = []
    for input_path in resolve_workbook_paths(args.input):
        filled, total = fill_workbook_translations(input_path, args.client_id)
        summary.append({"file": str(input_path), "filled": filled, "total": total})
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sys.exit(main())

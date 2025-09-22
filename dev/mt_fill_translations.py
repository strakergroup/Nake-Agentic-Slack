import glob
import json
import os
import re
import sys

import httpx
import openpyxl

try:
    from app.config import domains  # type: ignore
    from app.mt.schemas import TranslationRequest  # type: ignore
except Exception:
    # Ensure app imports work when running from the dev folder
    parent_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "slack-ray-translator")
    )
    sys.path.append(parent_dir)
    from app.config import domains  # type: ignore
    from app.mt.schemas import TranslationRequest  # type: ignore


def build_auth_header() -> dict:
    """Build the authentication header for the LanguageCloud API.
    You will need to get a JWT from the LanguageCloud API and set it as an environment variable.
    """
    env_token = os.getenv("LANGUAGECLOUD_API_TOKEN")
    if not env_token:
        raise ValueError("LANGUAGECLOUD_API_TOKEN is not set")
    return {"Authorization": f"Bearer {env_token}"}


def protect_placeholders(text: str) -> tuple[str, dict[str, str]]:
    """Replace emoji and Python-format placeholders with xml-like tags to avoid MT corruption.

    Replaces occurrences of Slack emoji :emoji: and python format placeholders {var}
    with <x id=n> tags. Returns the modified text and a map of original->tag to allow restore.
    """
    replacements: dict[str, str] = {}
    # Match :emoji: or {anything}
    pattern = r":\w+:|\{.*?\}"
    protected = text
    for i, match in enumerate(re.finditer(pattern, text)):
        original = match.group()
        tag = f"<x id={i + 1}>"
        replacements[original] = tag
        protected = protected.replace(original, tag)
    return protected, replacements


def restore_placeholders(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for original, tag in replacements.items():
        restored = restored.replace(tag, original)
    return restored


def mt_translate(text: str, target_lang: str) -> str:
    prepared, replacements = protect_placeholders(text)
    base_url = os.getenv("LANGUAGECLOUD_API_URL") or f"{domains.languagecloud_api}"
    url = f"{base_url.rstrip('/')}/mt/translate"
    headers = build_auth_header()
    payload = TranslationRequest(
        text=prepared,
        target_languages=[target_lang],
        app_name="slack-dev",
        usage_type="dev_machine_translation",
    )
    # Allow running without group UUID; API may accept token without claims
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=headers, json=payload.model_dump())
        resp.raise_for_status()
        data = resp.json()
    translations = data.translations or {}
    # Prefer the requested language; otherwise fall back to first value
    mt_text = translations.get(target_lang)
    if mt_text is None and translations:
        # take arbitrary first
        mt_text = next(iter(translations.values()))
    if not mt_text:
        # If API returned nothing, fall back to source
        mt_text = text
    return restore_placeholders(mt_text, replacements)


def fill_workbook_translations(xlsx_path: str, target_lang: str) -> tuple[int, int]:
    wb = openpyxl.load_workbook(xlsx_path)
    sheet = wb.active
    filled = 0
    total = 0
    # Expect headers: source_text | translation | notes
    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row, min_col=1, max_col=3):
        source_cell, translation_cell, _notes_cell = row
        source_text = source_cell.value
        if not source_text:
            continue
        total += 1
        if translation_cell.value and str(translation_cell.value).strip():
            continue
        try:
            translation = mt_translate(str(source_text), target_lang)
            translation_cell.value = translation
            filled += 1
        except Exception as e:
            # Write error message into notes cell if present
            try:
                _notes_cell.value = f"MT error: {e}"
            except Exception:
                pass
    wb.save(xlsx_path)
    return filled, total


def discover_target_lang(filename: str) -> str:
    # translations_{lang}.xlsx -> {lang}
    base = os.path.basename(filename)
    lang = base.removeprefix("translations_").removesuffix(".xlsx")
    return lang


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    xlsx_files = sorted(glob.glob(os.path.join(script_dir, "translations_*.xlsx")))
    if not xlsx_files:
        print("No translations_*.xlsx files found in dev/.")
        return
    summary: list[dict[str, str | int]] = []
    for xlsx in xlsx_files:
        lang = discover_target_lang(xlsx)
        print(
            f"Translating missing cells in {os.path.basename(xlsx)} for lang '{lang}' ..."
        )
        filled, total = fill_workbook_translations(xlsx, lang)
        print(f"  Filled {filled} of {total} rows")
        summary.append(
            {
                "file": os.path.basename(xlsx),
                "lang": lang,
                "filled": filled,
                "total": total,
            }
        )
    print("\nSummary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

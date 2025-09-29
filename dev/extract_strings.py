# Dev script to extract strings from the app source code and write them to an Excel file for translation
import ast
import itertools
import os
import re
import sys
from dataclasses import dataclass

import openpyxl

# Add the parent directory to sys.path
parent_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "slack-ray-translator")
)
sys.path.append(parent_dir)

from app.translate import Translator, translator_var  # type: ignore

langs = ["fr", "de", "es", "fr-ca", "ja"]

PLACEHOLDER_PATTERN = re.compile(r":\w+:|\{.*?\}")


@dataclass(frozen=True)
class StringEntry:
    text: str
    max_length: int


def tag_placeholders(text: str) -> str:
    if not text:
        return ""
    counter = itertools.count(1)
    return PLACEHOLDER_PATTERN.sub(lambda _: f"<x id={next(counter)}>", text)


def extract_max_length(node: ast.Call) -> int:
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        value = node.args[1].value
        if isinstance(value, int):
            return value
    for keyword in node.keywords:
        if keyword.arg == "max_length" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            if isinstance(value, int):
                return value
    return 0


def extract_entries_from_file(filepath: str) -> list[StringEntry]:
    with open(filepath, "r", encoding="utf-8") as file:
        source = file.read()
    tree = ast.parse(source, filename=filepath)
    entries: list[StringEntry] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
        ):
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.JoinedStr):
                print(f"Extracted f-string: {filepath}:{node.lineno}")
                continue
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                max_length = extract_max_length(node)
                entries.append(StringEntry(first_arg.value, max_length))
                continue
            if isinstance(first_arg, ast.Name):
                variable_name = first_arg.id
                print(
                    f"Found variable '{variable_name}' in file {filepath}:{node.lineno}"
                )
    return entries


def collect_unique_entries(directory: str) -> list[StringEntry]:
    unique_entries: dict[tuple[str, int], StringEntry] = {}
    for root, _, files in os.walk(directory):
        files.sort()
        for filename in files:
            if not filename.endswith(".py"):
                continue
            filepath = os.path.join(root, filename)
            for entry in extract_entries_from_file(filepath):
                key = (entry.text, entry.max_length)
                if key not in unique_entries:
                    unique_entries[key] = entry
    return list(unique_entries.values())


def append_missing_translations(
    entries: list[StringEntry], translator: Translator, sheet
) -> None:
    for entry in entries:
        result, success = translator.translate(entry.text, entry.max_length)
        if success:
            continue
        source_cell = tag_placeholders(entry.text)
        translation_cell = ""
        if result != entry.text:
            translation_cell = tag_placeholders(result)
        notes: list[str] = []
        if entry.max_length:
            notes.append(f"Needs maximum length: {entry.max_length}")
        sheet.append([source_cell, translation_cell, " ".join(notes)])


src_directory = os.path.join(os.path.dirname(__file__), "../app")
entries = collect_unique_entries(src_directory)

for lang in langs:
    translator_var.set(Translator(lang))
    translator = translator_var.get()

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Translations"
    sheet.append(["source_text", "translation", "notes"])

    print(f"Extracting strings for language: {lang}")
    append_missing_translations(entries, translator, sheet)

    workbook.save(f"translations_{lang}.xlsx")

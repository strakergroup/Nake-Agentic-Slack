# Dev script to extract strings from the app source code and write them to an Excel file for translation
import os
import re
import sys
import ast
import openpyxl

# Add the parent directory to sys.path
parent_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "slack-ray-translator")
)
sys.path.append(parent_dir)

from app.translate import Translator, translator_var

langs = ["fr", "de", "es", "fr-ca", "jp"]


def extract_strings_from_file(filepath, translator, sheet):
    with open(filepath, "r", encoding="utf-8") as file:
        tree = ast.parse(file.read(), filename=filepath)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_"
            ):
                if node.args:
                    if isinstance(node.args[0], ast.JoinedStr):
                        print(f"Extracted f-string: {filepath}:{node.lineno}")
                    if isinstance(node.args[0], ast.Constant):
                        source_text = node.args[0].s

                        result = translator.translate(source_text)
                        translation = result
                        if result == source_text:
                            for i, match in enumerate(
                                re.finditer(r":\w+:|\{.*?\}", result)
                            ):
                                tag = f"<x id={i+1}>"
                                translation = translation.replace(match.group(), tag)
                            # Write the source text and translation to the Excel sheet
                            sheet.append([translation])
                    elif isinstance(node.args[0], ast.Name):
                        variable_name = node.args[0].id
                        line_number = node.lineno
                        print(
                            f"Found variable '{variable_name}' in file {filepath}:{line_number}"
                        )
                        # Handle the variable case if needed


def extract_strings_from_directory(directory, translator, sheet):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                extract_strings_from_file(os.path.join(root, file), translator, sheet)


for lang in langs:
    translator_var.set(Translator(lang))
    translator = translator_var.get()

    # Create a new Excel workbook and sheet
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Translations"
    sheet.append(["source_text"])

    print(f"Extracting strings for language: {lang}")
    src_directory = os.path.join(os.path.dirname(__file__), "../app")
    extract_strings_from_directory(src_directory, translator, sheet)

    # Save the workbook with the language code in the file name
    workbook.save(f"translations_{lang}.xlsx")

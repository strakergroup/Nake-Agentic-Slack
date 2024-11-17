import os
import uuid
import openpyxl

# UPDATE this to required lang code
langs = ["fr", "de", "es", "fr-ca", "jp"]
# Get the directory where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))


# Open the file in write mode
with open(os.path.join(script_dir, "import.sql"), "w") as sql_file:
    for lang_code in langs:
        file_path = os.path.join(script_dir, f"translations_{lang_code}.xlsx")
        # Load the Excel file
        wb = openpyxl.load_workbook(file_path)
        sheet = wb.active

        # Loop through the rows and print the values in the first and second columns
        for row in sheet.iter_rows(
            min_row=2, max_row=sheet.max_row, min_col=1, max_col=2
        ):
            first_column_value = row[0].value
            second_column_value = row[1].value
            string_uuid = uuid.uuid4()
            sql_file.write(
                f"""
                INSERT INTO `obj_stringtranslator` (`obj_uuid`, `created`, `modified`, `label`, `lang`, `langstring`, `active`) VALUES
                    ('{string_uuid}', '2024-11-15 00:00:00', '2024-11-15 00:00:00', "{first_column_value}", "{lang_code}", "{second_column_value}", 1);
                """
            )

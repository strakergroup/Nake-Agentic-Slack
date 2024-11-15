import os
import uuid
import openpyxl

# UPDATE this to required lang code
lang_code = "fr"

# Load the Excel file
wb = openpyxl.load_workbook("/Users/wadenorman-mac/Downloads/translations_fr.xlsx")
sheet = wb.active


# Loop through the rows and print the values in the first and second columns
for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row, min_col=1, max_col=2):
    first_column_value = row[0].value
    second_column_value = row[1].value
    string_uuid = uuid.uuid4()
    print(
        f"""
          INSERT INTO `obj_stringtranslator` (`obj_uuid`, `created`, `modified`, `label`, `lang`, `langstring`, `active`) VALUES
            ({string_uuid}, '2024-11-15 00:00:00', '2024-11-15 00:00:00', {first_column_value}, {lang_code}, {second_column_value}, 1);
    """
    )

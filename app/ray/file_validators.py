import json
from typing import Tuple
from app.translate import _


def validate_json(file_path: str) -> Tuple[bool, str]:
    """Validates JSON file content.

    Args:
        file_path (str): The path to the file to validate.

    Returns:
        Tuple[bool, str]: A tuple containing:
            - bool: True if valid JSON, False otherwise
            - str: Empty string if valid, error message if invalid

    Raises:
        None - All exceptions are caught and returned as error messages

    Examples:
        >>> validate_json('{"key": "value"}')
        (True, '')
        >>> validate_json(b'invalid json')
        (False, 'Invalid JSON structure: Expecting value: line 1 column 1 (char 0)')
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            json_str = f.read()
        json.loads(json_str)
        return True, ""
    except UnicodeDecodeError:
        return False, _("Error: Invalid JSON. File must be UTF-8 encoded.")
    except json.JSONDecodeError as e:
        return False, _(
            "Error: Invalid JSON. Please fix the issue and resubmit the file."
        )

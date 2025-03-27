import json
from typing import Union, Tuple
from app.translate import _


def validate_json(content: Union[bytes, str]) -> Tuple[bool, str]:
    """Validates JSON file content.

    Args:
        content (Union[bytes, str]): The content to validate, either as bytes or string.

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
        json_str = content.decode("utf-8") if isinstance(content, bytes) else content
        json.loads(json_str)
        return True, ""
    except UnicodeDecodeError:
        return False, _("Invalid JSON: File must be UTF-8 encoded.")
    except json.JSONDecodeError as e:
        return False, _("Invalid JSON. Please fix the issue and resubmit the file.")

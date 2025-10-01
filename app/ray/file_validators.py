import json
from typing import Tuple

from PyPDF2 import PdfReader

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


def validate_pdf(file_path: str) -> Tuple[bool, str]:
    """Validates that a PDF is not exported from Google Slides."""

    try:
        reader = PdfReader(file_path)
        metadata = reader.metadata or {}  # type: ignore

        creator = _normalize_metadata_string(metadata.get("/Creator"))
        if _is_generated_by_google(creator):
            ratio_label, ratio_value = _identify_slide_ratio(reader)
            if _is_slide_layout(ratio_label, ratio_value):
                ratio_text = ratio_label or f"{ratio_value:.2f}"
                return False, _(
                    "Error: This PDF looks like a Google Slides export (detected %(ratio)s layout). "
                    "Please upload the original Slides file or export it as PPTX and try again."
                ) % {"ratio": ratio_text}

        return True, ""

    except Exception:
        return False, _(
            "Error: Unable to read PDF metadata. Please verify the file and try again."
        )


SLIDE_RATIO_TOLERANCE = 0.05
WIDESCREEN_MIN_RATIO = 1.2
SLIDE_RATIOS: dict[str, float] = {
    "16:9": 16 / 9,
    "4:3": 4 / 3,
    "16:10": 16 / 10,
}


def _is_generated_by_google(creator: str) -> bool:
    return "google" in creator


def _identify_slide_ratio(reader: PdfReader) -> tuple[str | None, float | None]:
    ratio = _calculate_primary_page_ratio(reader)
    if ratio is None:
        return None, None

    for label, target in SLIDE_RATIOS.items():
        if abs(ratio - target) <= SLIDE_RATIO_TOLERANCE:
            return label, ratio

    return None, ratio


def _calculate_primary_page_ratio(reader: PdfReader) -> float | None:
    try:
        if not reader.pages:
            return None
        page = reader.pages[0]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        if width <= 0 or height <= 0:
            return None
        return width / height
    except Exception:
        return None


def _is_slide_layout(label: str | None, ratio: float | None) -> bool:
    if label:
        return True
    if ratio is None:
        return False
    return ratio >= WIDESCREEN_MIN_RATIO


def _normalize_metadata_string(value: object | None) -> str:
    if value is None:
        return ""
    if hasattr(value, "get_object"):
        try:
            value = value.get_object()
        except Exception:
            return ""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "ignore")
        except Exception:
            return ""
    if not isinstance(value, str):
        value = str(value)
    return value.lower()

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
                    ":warning: Error: PDF is a Google Slides export (detected {ratio_text} layout).\n"
                    "We recommend uploading the original file or exporting as PPTX for better results."
                )

        return True, ""

    except Exception:
        return False, _(
            ":warning: Error: Unable to read PDF metadata. Please verify the file and try again."
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
    """Determine the aspect ratio of the primary page in a PDF and match it to known slide ratios.

    This function calculates the aspect ratio of the first page in a PDF document. It then compares this calculated ratio
    against a predefined set of common slide ratios (e.g., "16:9", "4:3"). If the calculated ratio closely matches one of
    these known ratios within a specified tolerance, the function returns the label of the matched ratio along with the
    calculated ratio. If no match is found, it returns None for the label and the calculated ratio.

    Args:
        reader (PdfReader): The PDF reader object containing the PDF data.

    Returns:
        tuple[str | None, float | None]: A tuple containing:
            - str: The label of the identified slide ratio (e.g., "16:9") or None if no match is found.
            - float: The calculated ratio of the primary page or None if it cannot be determined.
    """
    # Calculate the aspect ratio of the primary page
    ratio = _calculate_primary_page_ratio(reader)
    if ratio is None:
        # If the ratio cannot be determined, return None for both values
        return None, None

    # Compare the calculated ratio with known slide ratios
    for label, target in SLIDE_RATIOS.items():
        # Check if the calculated ratio is within the tolerance of a known ratio
        if abs(ratio - target) <= SLIDE_RATIO_TOLERANCE:
            # Return the label of the matched ratio and the calculated ratio
            return label, ratio

    # If no known ratio matches, return None for the label and the calculated ratio
    return None, ratio


def _calculate_primary_page_ratio(reader: PdfReader) -> float | None:
    """Calculate the aspect ratio of the primary page in a PDF.

    Args:
        reader (PdfReader): The PDF reader object containing the PDF data.

    Returns:
        float | None: The aspect ratio of the primary page or None if it cannot be determined.
    """
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
    """Determine if a given ratio or label corresponds to a slide layout.

    Args:
        label (str | None): The label of the slide ratio (e.g., "16:9").
        ratio (float | None): The calculated ratio of the primary page.

    Returns:
        bool: True if the label indicates a slide layout or if the ratio is above the widescreen minimum, False otherwise.
    """
    if label:
        return True
    if ratio is None:
        return False
    return ratio >= WIDESCREEN_MIN_RATIO


def _normalize_metadata_string(value: object | None) -> str:
    """Normalize a metadata value to a lowercase string.

    Args:
        value (object | None): The metadata value to normalize.

    Returns:
        str: The normalized string representation of the metadata value.
    """
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

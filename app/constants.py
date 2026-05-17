"""
Constants used throughout the application.
"""

import httpx

# Application version
APP_VERSION = "1.0.0"

# Timeout for file upload/download operations.
#
# These calls move Slack/Verify documents through internal services and should
# comfortably handle files up to 500 MB under normal production load.
FILE_TRANSFER_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=30.0)
SMALL_FILE_TRANSFER_TIMEOUT = httpx.Timeout(
    connect=10.0, read=180.0, write=180.0, pool=30.0
)


def file_transfer_timeout_for_size(
    file_size_bytes: int | None,
    *,
    small_file_threshold_bytes: int,
) -> httpx.Timeout:
    """Return an HTTP timeout sized for known-small vs large/unknown files."""
    if file_size_bytes is not None and file_size_bytes < small_file_threshold_bytes:
        return SMALL_FILE_TRANSFER_TIMEOUT
    return FILE_TRANSFER_TIMEOUT


# Default expiry (days) for temporary files uploaded to sup-file-api GridFS.
# These files are short-lived intermediaries for document translation / OCR processing.
DEFAULT_UPLOAD_EXPIRY_DAYS = 30

# Workflow UUIDs
HUMAN_EVALUATION_WORKFLOW_UUID = "069cfa54-609b-46d3-830b-575168f4ef19"
VERIFY_LOOP_WORKFLOW_UUID = "d3d6e7b3-8239-44b0-a448-fb8691dc6d84"

# Language UUIDs
ENGLISH_US_LANGUAGE_UUID = "917FF4B9-0808-A1F1-BCBF-A24974373286"

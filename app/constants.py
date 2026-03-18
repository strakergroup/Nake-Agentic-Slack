"""
Constants used throughout the application.
"""

import httpx

# Application version
APP_VERSION = "1.0.0"

# Timeout for file upload/download operations - generous write timeout for large files
FILE_TRANSFER_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=120.0, pool=10.0)

# Default expiry (days) for temporary files uploaded to sup-file-api GridFS.
# These files are short-lived intermediaries for document translation / OCR processing.
DEFAULT_UPLOAD_EXPIRY_DAYS = 30

# Workflow UUIDs
HUMAN_EVALUATION_WORKFLOW_UUID = "069cfa54-609b-46d3-830b-575168f4ef19"
VERIFY_LOOP_WORKFLOW_UUID = "d3d6e7b3-8239-44b0-a448-fb8691dc6d84"

# Language UUIDs
ENGLISH_US_LANGUAGE_UUID = "917FF4B9-0808-A1F1-BCBF-A24974373286"

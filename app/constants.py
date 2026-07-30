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
HUMAN_VERIFICATION_WORKFLOW_UUID = "06294ecf-85a8-453a-b98e-20ee4ac629b5"

# Evaluate quote confirmation services (Verify API /quote/credits)
EVALUATE_SERVICE_AI_TRANSLATION = "ai_translation"
EVALUATE_SERVICE_QUALITY_EVALUATION = "quality_evaluation"

# Job extra_info flag: non-admin evaluate path auto-runs AI+QE, then quotes HT.
# CVC synthetic workflows omit human-verification when this is set.
SLACK_HT_QUOTE_AFTER_QE_KEY = "slack_ht_quote_after_qe"

# PDF conversion fee aligned with Document MT (tokens per page)
EVALUATE_PDF_CONVERSION_TOKENS_PER_PAGE = 25

# HT/evaluate PDF pre-quote uses the same consumer extract path as Document MT,
# but returns on a distinct callback so Slack renders EvaluationCreditsQuoteMessage.
EVALUATE_PDF_QUOTE_OUTPUT_STREAM = "verify:slack:evaluate:pdf:quote"
DOCUMENT_MT_QUOTE_OUTPUT_STREAM = "verify:slack:document:quote"

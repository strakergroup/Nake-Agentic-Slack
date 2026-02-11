"""
Constants used throughout the application.
"""

import httpx

# Application version
APP_VERSION = "1.0.0"

# Timeout for file upload/download operations - generous write timeout for large files
FILE_TRANSFER_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=120.0, pool=10.0)

# Workflow UUIDs
HUMAN_EVALUATION_WORKFLOW_UUID = "069cfa54-609b-46d3-830b-575168f4ef19"

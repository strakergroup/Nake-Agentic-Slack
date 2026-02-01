# Changelog

All notable changes to this project will be documented in this file.

## Changes

- Added: New `app/api/http_client.py` module providing shared HTTP client utilities with consistent timeout configuration (`INTERNAL_SERVICE_TIMEOUT`), retry logic with exponential backoff for transient failures (`retry_on_timeout`), and connection pooling via `get_shared_client()` for improved performance on internal service calls (Wade Norman, 2026-01-29)
- Fixed: Added timeout and retry logic to `app/api/stream_proxy.py` to prevent `httpx.ReadTimeout` errors when calling stream proxy service; now uses shared HTTP client with connection pooling (Wade Norman, 2026-01-29)
- Fixed: Added retry logic to `app/api/language_cloud.py` `detect_language()` function and increased timeout from 10s to 30s to handle `httpx.ConnectTimeout` errors on language detection calls (Wade Norman, 2026-01-29)
- Changed: Updated `app/main.py` to close shared HTTP client on application shutdown to properly release connection pool resources (Wade Norman, 2026-01-29)
- Fixed: Refactored `upload_file_to_slack_memory_efficient` in `app/slack/web.py` to use streaming file upload instead of loading entire file into memory, preventing OOM errors for large video files (800MB+) with limited container memory (Justin Cole, 2026-02-02)
- Changed: Removed credit spending from `_spend_translation_credits` in `app/routers/ray.py` - translation credits and API usage logging are now handled by cloud-verify-consumer during SRT translation (RAY-77835) (Justin, 2026-01-28)
- Fixed: Channel name in brackets in `home_view` in `app/slack/templates/views.py` now only displays for private channels or when channel name is unavailable, and does not display for public channels. Removed unnecessary string conversion of `is_private` boolean to keep it as a boolean type (Wade Norman, 2026-01-14)
- Fixed: Updated `escape_slack_emoji` and `unescape_slack_emoji` in `app/slack/utils.py` to capture and preserve surrounding whitespace around emojis/tags, ensuring original spacing is restored after translation regardless of how translation engine modifies whitespace around placeholders (Wade Norman, 2026-01-15)
- Fixed: Consolidated file transfer timeout into shared `FILE_TRANSFER_TIMEOUT` constant in `app/constants.py`, used by both `app/ray/utils.py` and `app/slack/web.py` to prevent ReadTimeout errors when uploading/downloading large files (Wade Norman, 2026-01-13)
- Changed: Updated `escape_slack_emoji` and `unescape_slack_emoji` in `app/slack/utils.py` to use `<img id='N'/>` placeholder tags with single quotes instead of `<br id="N"/>` tags for Slack emoji and special tag escaping during translation (Wade Norman, 2026-01-13)

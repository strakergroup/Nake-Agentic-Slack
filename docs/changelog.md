# Changelog

All notable changes to this project will be documented in this file.

## Changes

- Changed: Updated `escape_slack_emoji` and `unescape_slack_emoji` to use indexed `<br id="N"/>` placeholder tags instead of `<span translate="no">` tags, which restores original content from source text to avoid HTML entity encoding issues with Google API (Wade Norman, 2026-01-06)

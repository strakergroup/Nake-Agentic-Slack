# Changelog

## [Unreleased]

- [Fixed]: Race condition causing duplicate "Your file is AI translated" messages when selecting multiple target languages - moved stage processing marker before handler execution (Justin Cole, 2026-02-02)
- [Added]: Implemented token calculation for subtitling feature in Slack - $0.60 per minute equals 30 tokens per minute (Justin Cole, 2026-01-20)
# Changelog

All notable changes to the Slack Ray Translator application.

## Changes

- Added: Store channel_name and is_private in slack_group_settings_translation table to reduce Slack API calls on Home tab render. Existing settings are lazily updated when viewed. (Wade Norman, 2026-01-11)

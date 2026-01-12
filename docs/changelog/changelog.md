# Changelog

All notable changes to the Slack Ray Translator application.

## Changes

- Added: Store channel_name and is_private in slack_group_settings_translation table to reduce Slack API calls on Home tab render. Existing settings are lazily updated when viewed. (Wade Norman, 2026-01-11)

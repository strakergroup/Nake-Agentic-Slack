# Changelog

All notable changes to this project will be documented in this file.

## Changes

- Changed: Updated `escape_slack_emoji` and `unescape_slack_emoji` in `app/slack/utils.py` to use `<img id='N'/>` placeholder tags with single quotes instead of `<br id="N"/>` tags for Slack emoji and special tag escaping during translation (Wade Norman, 2026-01-13)

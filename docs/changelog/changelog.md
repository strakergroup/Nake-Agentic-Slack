# Changelog

## [Unreleased]

- [Removed]: Deleted the repository GitHub Actions `Pyright` workflow so type checking remains local/pre-commit only until a reusable CI approach is in place for VPN-only dependencies (Wade Norman, 2026-03-26)
- [Removed]: Deleted unused Microsoft Translator environment settings and config validation from the app because this repository no longer reads those credentials (Wade Norman, 2026-03-26)
- [Changed]: Updated the GitHub `Pyright` workflow to install only `Pyright` on CI so PR type checks can run without VPN-only internal dependencies (Wade Norman, 2026-03-26)
- [Changed]: Replaced `mypy` with `Pyright` for repository type checking, added a GitHub PR typecheck workflow, and documented the staged ignored-module rollout for team adoption (Wade Norman, 2026-03-26)
- [Changed]: Document MT PDF size enforcement now checks the effective Verify session trial state before applying the limit, and `get_ray_client` no longer runs the unused subscription plan query (Wade Norman, 2026-03-06)
- [Fixed]: Added a configurable PDF size limit check to document MT submissions so oversized PDFs are rejected with a user-facing error before upload processing continues (Wade Norman, 2026-03-06)
- [Fixed]: Quality evaluation percentages now always sum to 100% — replaced independent rounding with Largest Remainder Method (Hamilton's method); extracted shared `calculate_evaluation_percentages` helper into `app/slack/utils.py` to eliminate duplicated logic in `verify_quote_blocks` and `evaluate_success_blocks` (Wade Norman, 2026-02-17)
- [Fixed]: Race condition causing duplicate "Your file is AI translated" messages when selecting multiple target languages - moved stage processing marker before handler execution (Justin Cole, 2026-02-02)
- [Added]: Implemented token calculation for subtitling feature in Slack - $0.60 per minute equals 30 tokens per minute (Justin Cole, 2026-01-20)
# Changelog

All notable changes to the Slack Ray Translator application.

## Changes

- Added: Store channel_name and is_private in slack_group_settings_translation table to reduce Slack API calls on Home tab render. Existing settings are lazily updated when viewed. (Wade Norman, 2026-01-11)

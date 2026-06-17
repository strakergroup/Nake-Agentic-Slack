# Changelog

## [Unreleased]

- [Changed]: Bot channel translation metadata now carries `is_bot`, a resolvable Slack bot user id, and the bot display name as the reporting `client_name` fallback for `/mt/inline-usage` (Wade Norman, 2026-06-15)
- [Fixed]: Preserved the Slack `message_not_found` exception as the cause when the channel-translation edit fallback receives a post response without a timestamp, clearing Ruff B904 for the repo lint check (Wade Norman, 2026-06-15)
- [Changed]: RAY-80133 enabled channel translation for Slack bot messages with a Redis-backed limit of 10 bot messages per bot, per channel, per 5-minute window to prevent bot loops (Wade Norman, 2026-06-15)
- [Fixed]: RAY-80199 — channel/direct MT result callback no longer fails with a 422 when the poster's default group (`obj_m_member.groupid`) is NULL; removed the bare `assert` and resolve the usage-report `group_uuid` from the submission's `group_id` (org/group context) so org-billed channel auto-translate succeeds without a default group. Also fall back to the exception class name when an error stringifies to empty so the 422 message is never blank. Adds a NULL-groupid channel-translation test (Wade Norman, 2026-06-11)
- [Fixed]: Inline/channel/shortcut MT charge now sends the billing group (`auth.slack_user.ray_user_group_id`) as `group_uuid` to `/mt/inline-usage`, so org-billed channel MT's ledger `group_uuid` stays the real group (e.g. IBM super group) instead of collapsing to the org uuid after the gateway migration; `log_inline_mt_usage_by_client_id` gains an optional `group_uuid` (RAY-80000 hotfix) (Wade Norman, 2026-06-11)
- [Fixed]: Updated `test_ray.py` direct-MT and channel-translation result tests to patch the gateway spend (`log_inline_mt_usage_by_client_id`) instead of the removed `calculate_cost`/`spend_credits`, which RAY-80000 replaced when spend moved to the LanguageCloud gateway; test-only, no app change (Wade Norman, 2026-06-11)
- [Fixed]: `log_inline_mt_usage_by_client_id` now mints a group token (via `create_languagecloud_group_token`) when the client_id has no `obj_m_member` row instead of raising — restores org-billed channel auto-translate charges for posters who never direct-logged-in; the gateway's `/mt/inline-usage` accepts the group principal (RAY-80000) (Wade Norman, 2026-06-07)
- [Added]: Inline/channel MT spend now sends `word_count` (computed from the source message text) to `/mt/inline-usage`, recorded as a typed report column on `credit_transaction_usage`; billing stays character-based (RAY-80000) (Wade Norman, 2026-06-05)
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

# Direct Login removed (RAY-81247)

Slack **Direct Login** (SSO mint/link via the Direct Login button / `login_sso` action)
has been removed.

- IBM HT and Media no longer require end-user CRM minting via Slack SSO.
- HT without an active CRM member uses the HT service account (Requester/Surrogate).
- Media org-bills like AI Translate when the workspace has a linked super group.
- Existing CRM members who are already Slack-linked continue to work via
  `slack_deltaray_link`.

Historical note (RAY-80562): inactive member reactivation on Direct Login is obsolete
with the SSO UI removed. See git history for `reactivate_member_for_direct_login` if
needed for ops.

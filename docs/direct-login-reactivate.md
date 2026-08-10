# Direct Login removed (RAY-81247)

Slack **Direct Login** (SSO mint/link via the Direct Login button / `login_sso` action)
has been removed.

- IBM HT and Media no longer require end-user CRM minting via Slack SSO.
- HT without a reusable CRM member uses the HT service account (Requester/Surrogate).
- Media org-bills like AI Translate when the workspace has a linked super group.
- Existing CRM members continue to work via Slack email → active CRM member
  resolve (`get_ray_client_ibm_by_email`), which upserts `slack_deltaray_link`.
- IBM logout / missing deltaray is irrelevant when the Slack email matches CRM.

Historical note (RAY-80562): inactive **member** reactivation on Direct Login
(`reactivate_member_for_direct_login`) still exists for leftover SSO link paths;
IBM UX no longer shows the Direct Login button.

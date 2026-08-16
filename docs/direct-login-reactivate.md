# Direct Login removed (RAY-81247)

Slack **Direct Login** (SSO mint/link via the Direct Login button / `login_sso` action)
has been removed from IBM UX.

- IBM HT and Media no longer require end-user CRM minting via Slack SSO.
- HT without a reusable CRM member uses the HT service account (Requester/Surrogate).
- Media org-bills like AI Translate when the workspace has a linked super group.
- Existing CRM members continue to work via Slack email → active CRM member
  resolve (`get_ray_client_ibm_by_email`), which upserts `slack_deltaray_link`.
- IBM logout / missing deltaray is irrelevant when the Slack email matches CRM.
- Slack must not mint new CRM People via leftover SSO (`connect_ray_account_sso`
  raises if no member exists for the email).

Leftover SSO link paths still reactivate inactive **members** (RAY-80562) and
inactive IBM Slack App **mglinks** (RAY-81311). IBM UX no longer shows the
Direct Login button.

## Problem (member active — RAY-80562)

CBN cleanup deactivated large numbers of auto-registered IBM Slack LC users that had
no HT jobs. Users who later clicked Direct Login got:

> Your connected account could not be determined.

Cause:

1. `connect_ray_account_sso` found the existing member by `login` and refreshed
   `slack_deltaray_link` (`is_active = 1`).
2. It did **not** set `obj_m_member.active = 1`.
3. `get_ray_client` requires `mem.active = 1` and `link.is_active = 1`, so the client
   resolved to `None` and `SsoConnectionInfoMessage` showed the fallback error.

## Problem (mglink inactive — RAY-81311)

CBN / CRM bulk cleanup also set many IBM Slack App `obj_m_mglink` rows to
`is_active = 0`. Direct Login called `add_client_to_slack_group`, which:

1. Selected any mglink row for (member, IBM Slack App group) **without** checking
   `is_active`.
2. Skipped insert when a row already existed — **including inactive rows**.
3. Never ran `UPDATE … SET is_active = 1`.

LC `getUserGroupByMemberid` only returns groups with `mgl.is_active = 1`. Users with
only inactive mglinks looked groupless, so `/signupgroup` / company signup was
allowed and created one-person Verify groups such as **IBM (45)** (see RAY-81311 /
Grace Kirchner triage).

```mermaid
sequenceDiagram
    participant User
    participant Slack as Slack App
    participant SSO as connect_ray_account_sso
    participant DB as obj_m_member
    participant MG as obj_m_mglink
    participant Link as slack_deltaray_link
    participant Get as get_ray_client

    User->>Slack: Direct Login
    Slack->>SSO: email from users.info
    SSO->>DB: SELECT by login
    alt member missing
        SSO-->>User: LookupError (RAY-81247: do not mint CRM)
    else member exists
        SSO->>DB: UPDATE active=1 (if not deleted)
        SSO->>MG: ensure IBM Slack App mglink is_active=1
    end
    SSO->>Link: upsert is_active=1, is_sso=1
    Slack->>Get: reload connection
    Get->>DB: require active=1
    Get-->>User: connected username
```

## Behaviour

| Case | Behaviour |
| --- | --- |
| No member for email | LookupError — do not mint CRM (RAY-81247) |
| Existing member, `active=0`, `is_deleted=0` | Set `active=1`, `email_active=1`, then link Slack |
| Existing member, already active | No-op update predicate; link Slack as before |
| Soft-deleted member (`is_deleted=1`) | Not reactivated |
| No mglink for Direct Login group | INSERT `obj_m_mglink` with `is_active=1` |
| Existing mglink, `is_active=0` (or NULL) | UPDATE `is_active=1` |
| Existing mglink, `is_active=1` | Leave mglink unchanged; still set empty `member.groupid` if needed |

Implementation:

- `reactivate_member_for_direct_login` — member row
- `add_client_to_slack_group` — group link row

Both are called from the existing-member branch of `connect_ray_account_sso`.

## Tests

`tests/auth/test_direct_login_reactivate.py` covers:

- UPDATE SQL issued by the member helper
- Existing-member SSO path awaits reactivation
- New-member path raises LookupError (does not mint CRM)
- Inactive mglink → UPDATE `is_active=1`
- Active mglink → no mglink UPDATE
- Missing mglink → INSERT with `is_active=1`

# Direct Login member reactivation (RAY-80562)

## Summary

When an IBM (or other SSO) Slack user completes **Direct Login**, and a LanguageCloud
member already exists for their email but was deactivated (`obj_m_member.active = 0`),
Slack Direct Login now sets that member **active again** before linking Slack and
returning the connected account.

## Problem

CBN cleanup deactivated large numbers of auto-registered IBM Slack LC users that had
no HT jobs. Users who later clicked Direct Login got:

> Your connected account could not be determined.

Cause:

1. `connect_ray_account_sso` found the existing member by `login` and refreshed
   `slack_deltaray_link` (`is_active = 1`).
2. It did **not** set `obj_m_member.active = 1`.
3. `get_ray_client` requires `mem.active = 1` and `link.is_active = 1`, so the client
   resolved to `None` and `SsoConnectionInfoMessage` showed the fallback error.

```mermaid
sequenceDiagram
    participant User
    participant Slack as Slack App
    participant SSO as connect_ray_account_sso
    participant DB as obj_m_member
    participant Link as slack_deltaray_link
    participant Get as get_ray_client

    User->>Slack: Direct Login
    Slack->>SSO: email from users.info
    SSO->>DB: SELECT by login
    alt member missing
        SSO->>DB: INSERT active=1
    else member exists
        SSO->>DB: UPDATE active=1 (if not deleted)
    end
    SSO->>Link: upsert is_active=1, is_sso=1
    Slack->>Get: reload connection
    Get->>DB: require active=1
    Get-->>User: connected username
```

## Behaviour

| Case | Behaviour |
| --- | --- |
| No member for email | Create member (already `active=1`) — unchanged |
| Existing member, `active=0`, `is_deleted=0` | Set `active=1`, `email_active=1`, then link Slack |
| Existing member, already active | No-op update predicate; link Slack as before |
| Soft-deleted member (`is_deleted=1`) | Not reactivated |

Implementation: `reactivate_member_for_direct_login` in `app/auth/connector.py`,
called from the existing-member branch of `connect_ray_account_sso`.

## Tests

`tests/auth/test_direct_login_reactivate.py` covers:

- UPDATE SQL issued by the helper
- Existing-member SSO path awaits reactivation
- New-member path does not call reactivation

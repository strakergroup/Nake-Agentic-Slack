# Arbitr agent: switching it on in dev

Nothing here has been run. It was written outside Straker's network, where this app
cannot start. This run-book is the test. Please record what happens at each step; every
"expected" below is a claim from reading the code, and some will be wrong.

## Before you start

1. Check out branch `arbitr-agent`.
2. Regenerate the lock file against the private index: `pipenv lock && pipenv install --dev`.
   `anthropic` 1.x uses `httpx2`, a separate package, so it does not collide with the app's `httpx<1.0` pin.
3. Core tests, which need nothing: `pipenv run pytest tests/agent --noconftest -q --ignore=tests/agent/adapters`
   Expected: 138 passed. (`--noconftest` because `tests/conftest.py` connects to MySQL at import.)
4. Your full suite: `pipenv run pytest`. Expected: no new failures against `master`.
5. Create a **separate dev Slack app** from `manifest.agent.yml` (replace `<YOUR_DOMAIN>`). Do not
   change the production app: switching a manifest to `agent_view` cannot be reversed and, for a
   distributed app, triggers Marketplace review; `assistant:write` forces workspaces to reinstall.
   If Slack rejects the manifest, check the field names `agent_view` and `agent_description` first.
6. In `.env`: `AGENT_ENABLED=false` for now, `ANTHROPIC_API_KEY=<key>`.

## Checklist

| # | Do this | Expected | Result |
|---|---|---|---|
| 1 | `AGENT_ENABLED=false`. DM the bot "where's my job?" | Watson answers exactly as today. No agent log lines. | |
| 2 | `AGENT_ENABLED=true`, restart. Same DM. | A reply in the first person listing your jobs from RAY. "Working" then "Ready" status in the agent panel. No task card. | |
| 3 | DM "status of TJ<a real job>" | Correct status, languages, target date. | |
| 4 | DM in Japanese or German: "what can you do?" | Reply in that language. | |
| 5 | DM "Put this in Japanese: The launch moves to 14 October." | A task card "Translate the text", then the translation arrives through the existing `slack:direct:mt:result` path. Usage billed once as `direct_machine_translation`. | |
| 6 | DM a .docx with "translate this into French" | A button "Open document form". Clicking it opens your existing Document MT modal with the file selected. | |
| 7 | DM an .mp4 with "add Japanese subtitles" | A button "Open media form" that opens the existing media modal. | |
| 8 | In a channel thread: "@bot post this in Japanese" | The translation appears in the thread with no confirming click, through the existing MT result path. Billed once. | |
| 9 | From a DM: "post a Japanese version of <message link> into its thread in #channel" as user A | A reply with **Post it** and **Decline**. Status "Waiting on you". Nothing posted yet. User B clicking gets an ephemeral refusal. | |
| 10 | User A clicks **Post it**, then clicks it again | One translation in the thread. The second click gets "already used". One billing record. | |
| 11 | Start a long request, press Slack's Stop button | "Stopped." and status back to Ready. | |
| 12 | Send the same Slack event twice (replay the request body) | One reply, one model call. | |
| 13 | Unset `ANTHROPIC_API_KEY` or block egress, DM anything | With no key the agent is off and Watson answers. With egress blocked: fixed copy plus three buttons; "My jobs" shows your existing job list. | |
| 14 | In an IBM-flagged workspace, DM "how do I connect my account and top up?" | No mention of connecting, balances or top-ups. | |
| 15 | As a non-admin with `QUOTE_ADMIN_ONLY=true`, DM "how much will this cost?" | No amount in the reply. | |
| 16 | Trigger any `/ray/events` event with the flag on and off | Your existing handling is unchanged in both cases. | |
| 17 | (After reviewing `adapters/straker_tools.py`) `AGENT_NATIVE_DOCUMENT_QUOTES=true`. As an Admin, DM a file with "translate into German, it's in English" | Your existing quote message with Accept Quote appears. The agent states no price. | |
| 18 | Same flag. As a non-admin with `QUOTE_ADMIN_ONLY=true`, DM a file with "translate into German, it's in English" | "This will be charged to your organization. Translate the attached file into German?" with **Translate now**. No price anywhere. Nothing submitted yet. | |
| 19 | Type "yes, go ahead" instead of clicking | The agent points to the button. No second button. Nothing submitted. | |
| 20 | Click **Translate now**, then click it again | One `process_document_mt_submission` task enqueued, identical to a form submission. Second click: "already used". Status returns to Ready; the file arrives through the existing delivery path. | |

## Please confirm or correct

These are judgement calls made without being able to ask you.

1. **`usage_type`.** The agent bills inline translation as `direct_machine_translation`. A distinct
   `agent_translate` value would make usage reports clearer, if LanguageCloud accepts new values.
2. **IBM detection.** `facts.py` uses `is_ibm_customer_enterprise`, so Straker's own IBM-like sandbox
   is not treated as IBM for the no-connect rule. Should it be `is_ibm_enterprise`?
3. **Job filters.** `list_jobs` maps "open" to `IN_PROGRESS`, "waiting on me" to `PENDING_QUOTES`,
   "delivered" to `COMPLETED`. Is `PENDING_QUOTES` the right status for quotes awaiting acceptance?
4. **Threads.** Does `get_mt_translation(..., thread_ts=...)` with no `response_url` post the result
   publicly in that thread? The public-post tool assumes it does.
5. **Event correlation.** `verify:slack:document:translated` does not appear to carry a `quote_id`. The
   bridge falls back to "most recent open agent session for this person in this channel". A `quote_id`
   on that event would make it exact.
6. **Slack payload details.** The streaming calls use `ts` for the message timestamp and
   `{"type": "task_update", "task": {...}}` chunks with status `complete`, following the published
   examples. Slack's prose says `completed` in places. The locked slack-sdk predates these methods.
7. **Process load.** The agent holds an in-process task for the length of a model call (seconds), in the
   web process that also runs the SAQ worker. `app/routers/slack.py` already warns above 5 s.
8. **Conversion rule** between the dollars shown in quotes and Credits, if Credits are to be shown.
9. **Does anything downstream match on** `"source": "Straker Translate for Slack"`? (For the rebrand.)
10. **Notifications.** Does a reply in a day-old agent session thread notify the person, or only show in
    their Threads view? If the latter, the agent should also post a top-level pointer.
11. **Per-slide review and reviewer notes.** Can the service review one slide or page by number, and can a
    reviewer's change note come back with the file? The POC currently offers "send that slide as its own file".
12. **Scope approval.** `app/slack/app.py` notes that new scopes are held until approved for
    production. `assistant:write` is the only new one.

## If something breaks

Set `AGENT_ENABLED=false` and restart. Every edit to existing files is behind that flag, and an
exception inside the agent already falls through to Watson.

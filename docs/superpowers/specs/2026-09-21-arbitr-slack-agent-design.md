# Arbitr for Slack: agent rebuild

Design spec. Agreed with Nake on 2026-09-21 through a question-by-question interview.
Status: awaiting Nake's review. No code has been written.

## 1. Purpose

Straker Translate is a Slack app used by about 10,000 IBM employees a month (Straker's
published case study). Today it is a bot: two message shortcuts, one slash command, and a set
of forms. The user has to know which tool to pick.

This project rebuilds the same feature set as a Slack agent, under the Arbitr name. A person
states a goal in plain language, watches a plan run, and approves anything that costs money.

Nake's three goals, in order of this spec's coverage:

1. Rebuild the app, with its current features, as an agentic experience. **This spec.**
2. Rebrand the app from Straker to Arbitr. **Partly this spec** (everything new is Arbitr from
   day one); the rebrand of the live listing and existing screens is a second spec.
3. Increase visibility inside IBM. **Not this spec.** The proactive behaviours here are its
   foundation; the adoption campaign is a third piece of work.

## 2. Hard constraints

- **Production is off-limits.** No push to `strakergroup/slack-straker-translate`, no change to
  the live Slack app (ID `A040KQM9HEK`) or its Marketplace listing, no live credentials on
  Nake's machine. The local clone's push address is disabled on purpose.
- The repo belongs to Wade Norman's team and changes almost daily. The work must be something
  they can adopt, not something that competes with them.
- Every IBM-specific behaviour in the existing app is preserved exactly (section 7).
- Claims: never mention trust scoring, agent consensus voting, or Glass Box. Describe only what
  this app does in production: AI translation, quality evaluation, human review.
- Naming: the product is **Arbitr**, capitalised (Nake's ruling, 2026-09-21, following Design
  System v2). No emoji anywhere. No em dashes in published copy. The retired positioning lines
  stay retired.

## 3. Deliverables

Two deliverables, built in this order, each with its own build plan.

### Deliverable 1: front-end POC

A clickable simulation of the agent experience that runs in a browser. No Slack workspace, no
API key, nothing needed from Wade's team. Everything is scripted.

- The conversation area imitates Slack, because it depicts Slack. The Arbitr lens device is the
  app icon. All agent copy follows the v2 voice.
- The frame around it (title, scenario picker, presenter notes) is built with Design System v2
  tokens, fonts and components from `design-system-v2/`.
- Six scenarios:
  1. Document request: live task cards, quote, Approve, delivered file.
  2. Jobs: "Where's my job?" and "What's waiting on me?"
  3. Private language-gap suggestion in a channel, with "Not now" and "Don't suggest this again".
  4. Hand-off: a video request opens the existing form.
  5. IBM workspace view: no connect prompt, no top-up prompt, admin-only channel switch.
  6. App Home: digest on/off switch and the muted-suggestions list.
- Published as a private link Nake can share. Verified with screenshots at desktop and mobile
  widths.
- It is also the visual specification for Deliverable 2. What the POC shows, the working app
  must do.

### Deliverable 2: the working app, handed to Wade's team

The real agent: real Slack, real Claude. It runs first against a simulated Straker backend,
then against Straker's UAT environment once Wade's team grants access. It is a **new version of
the existing app**: a self-contained agent module inside a private copy of their codebase,
handed over as pull requests plus a hand-over pack (section 11).

## 4. What users experience

### The conversation

- People open Arbitr in Slack's agent panel or a DM and type a goal in their own language. The
  agent replies in that language.
- Each request becomes a named session the person can find again.
- The agent shows its plan as task cards that update live.
- Anything paid shows an itemised quote, in the unit the existing quote system returns
  (section 9 explains why the unit is not relabelled here).
- The session status reads "Waiting on you" until someone clicks. Buttons offer real
  alternatives, for example approve everything, take a cheaper option, or decline.
- Delivered files arrive in the same thread. A Stop button is available while the agent works.
- The agent is always labelled as an AI agent. Agent responses carry the line Slack's
  Marketplace rules require: AI output can be inaccurate.
- Where the existing app restricts quotes to admins, a non-admin sees "Request approval from an
  admin", as the forms do today.

### What is conversational in version 1

- Translate text and messages, including "translate the thread above".
- Translate documents, with quote and approval.
- Jobs: status of one job, what is waiting on me, a summary.
- Help and onboarding. This replaces IBM Watson Assistant, the app's current intent matcher.

### Everything else

Media and subtitles, quality evaluation, human translation, and channel auto-translate
settings are recognised by the agent and handed to the existing form, pre-filled where
possible. Nobody reaches a dead end.

### What does not change

Both message shortcuts, every existing form, and the App Home job list. The slash command
gains `/arbitr`; `/straker` keeps working as an alias so nobody at IBM is broken on rename day.
App Home gains one block: the digest switch and a list of muted suggestions.

### Workspaces on free Slack plans

The agent panel needs a paid Slack plan. On free workspaces the same agent answers in the
ordinary Messages tab and in threads, without the panel features. Slack requires this of
Marketplace apps. IBM is unaffected.

## 5. When the agent speaks first

Three proactive behaviours ship in version 1:

- **Job follow-ups by DM.** A quote that has been waiting, a job that is ready, an obvious next
  step. Goes only to people already using the app.
- **Language-gap offers in channels.** Someone posts in one language in a channel where many
  members work in another. Only the poster sees an offer to post a translation in the thread.
- **Opt-in digest.** A morning or weekly DM with the person's jobs and a team-usage line.

Deferred to phase 2: file-upload offers in channels. Dropped: feature tips.

Five safety rules, all hard requirements:

1. Channel behaviour is off until someone turns it on for that channel.
2. Suggestions are private. Only the person who triggered one sees it.
3. Every suggestion carries "Not now" and "Don't suggest this again".
4. There is a hard cap on how often the agent can speak first.
5. No message goes to the LLM to decide whether to suggest. That check uses Straker's existing
   language detection and members' Slack locale settings. The LLM is involved only after a
   person clicks yes.

Who can turn on channel suggestions: admins only in IBM workspaces; anyone in the channel
elsewhere.

## 6. How it works

Everything below is a new, self-contained module inside the existing app. Nothing existing is
rewritten.

1. **Front door.** Messages in the agent panel, DMs and @-mentions go to the agent. Shortcuts,
   the slash command and forms bypass it.
2. **Context check.** Before the model sees anything, the app's existing logic establishes who
   the person is, whether they are connected, whether they are an admin, whether the workspace
   is IBM, and their language. The agent is told these facts; it does not work them out.
3. **Reasoning layer.** Claude receives the conversation, those facts, and a menu of about ten
   actions. It sits behind a vendor-neutral interface so another model can be swapped in
   without touching agent code. Default model: Claude Opus 5; revisit once real cost per
   conversation is measured.
4. **Two kinds of action.**
   - Look-up actions run immediately: job status, job list, balance, pricing a document,
     opening a form.
   - Spend or post actions never run on the model's say-so. They create a pending approval and
     show buttons.
5. **Approval gate.** A click is verified in code: right person, right amount, admin rules
   respected, quote not expired. Only then does the existing function run, the same one the
   forms call today. A confused or manipulated model cannot spend money or post publicly.
6. **Event bridge.** When Straker's services report progress or completion, the matching
   session's task cards update and the agent resumes. Jobs not started by the agent behave as
   they do today.
7. **Session memory.** Each thread's state is saved so a session can wait hours for an approval
   or a human reviewer. Memory is per session. The agent keeps no long-term profile of people.

**Privacy rule: content goes to the translation engine, instructions go to the LLM.**
Documents, thread contents and files flow to Straker's existing engine as they do today.
Claude sees the person's request and metadata (file name, size, languages, price), not the
document. This keeps the sub-processor disclosure narrow and means channel text cannot be used
to manipulate the agent.

Other pieces:

- **Suggestion engine.** Rule-based, no LLM: the channel switch, language detection, the
  frequency cap, the mute list.
- **Audit log.** Per agent turn: who asked, actions taken, model, tokens and cost, outcome,
  error type. These are the fields Slack's governance guide tells admins to expect.
- **Failure behaviour.** If the model is slow or down, the person gets a plain message and the
  quick-action buttons. Never an invented answer, never raw error text. Refusals follow the v2
  voice: what happened, why, what happens next.
- **Simulator.** A stand-in for Straker's services and database. Used for the first demo, kept
  as the permanent test harness.

Session storage needs a small database addition. Wade's team controls database changes, so the
prototype uses the app's existing Redis and the hand-over pack calls this out.

## 7. IBM behaviour that must be preserved

The existing code treats IBM workspaces differently in about fifteen places. The agent reuses
that logic and never re-implements it:

- IBM workspaces skip account connect; the Slack email is the identity.
- Billing goes to an org-level wallet. IBM users never see top-up prompts.
- Human translation jobs route through a fixed IBM service account so IBM's usage reports work.
- IBM-specific copy variants stay.
- Quality evaluation as a standalone option stays hidden where it is hidden today.

## 8. Branding in this build

Slack controls fonts, colours and button styles inside conversations, so the design system
governs these things:

- App icon: the lens device from `design-system-v2/assets/logo`, used as supplied, never
  redrawn. Its minimum size is 24px and Slack shows icons near 20px in places, so rendering is
  tested and shown to Nake.
- Slack app background colour: a v2 token (proposed: Midnight `#0D092A`).
- Every word the agent says follows the v2 voice: Exact, Candid, Understated, Convinced,
  Senior. Arbitr is named as the actor. Never "users". A waiting approval is the product
  working, never an error. Retired and avoided words stay out.
- No invented icons. Inside Slack, only Slack's own task-card icons and text labels.
- The POC frame, demo materials and screenshots use v2 tokens, fonts and components.
- The governance state words (Cleared, Held, Blocked, In review) are not used for translation
  jobs. A "Held" job would imply a governance check that is not happening.
- The sandbox Slack app is named "Arbitr (dev)"; the bot shows as "Arbitr".

## 9. Deferred to the rebrand spec

- The roughly 65 Straker, RAY, LanguageCloud and Verify mentions in existing screens. Each
  renamed string needs its translations re-seeded in Straker's database.
- The internal `"source": "Straker Translate for Slack"` label on outbound requests. Untouched
  until Wade confirms nothing depends on it.
- The Marketplace listing: name, short and long descriptions (including "formerly Straker
  Translate" for the transition), icon, help links, setup guide.
- The security tab: disclose Claude, update LLM retention answers, fix the "no sub-processors"
  contradiction, add the AI disclaimer, flag the pen test dated 2023-09-21.
- "tokens" versus "Credits". The unit the quote system returns is not yet known, and IBM's
  wallet is described in AI tokens. Until that is known the agent shows amounts exactly as the
  existing system returns them.
- A review of which Slack permissions each feature needs, so unused ones can be dropped.
- Japanese-market listing images and help pages wait on the unresolved CJK typeface question in
  the design system.

## 10. Testing and evidence

- The existing test suite (about 1,440 tests) is run before any change to get a baseline, then
  after each milestone. It must not get worse.
- New tests in three layers:
  1. Rule tests with no LLM: the approval gate, the suggestion engine's five safety rules, IBM
     behaviour.
  2. Scripted conversations: about 30 realistic requests in several languages, each with the
     expected action, including attempts to make the agent spend or post without approval. Run
     against real Claude; produces a pass rate and a cost per conversation. The same script is
     the basis of any later watsonx comparison.
  3. Automated copy checks: no emoji, no em dashes, no retired words, no off-limits claims,
     Arbitr capitalised, AI disclaimer present.
- Evidence at every milestone: screenshots at desktop and mobile widths, test output, and at
  the end a short recording of the five-minute demo.

### Definition of done for Deliverable 2

1. In the sandbox workspace, a plain-language request with a document produces a live task
   plan, a quote with Approve and Decline, and the delivered file in the thread.
2. "Where's my job?" and "What's waiting on me?" return correct answers from the simulated
   data.
3. One language-gap offer fires in an enabled channel, is visible only to the poster, and
   honours "Don't suggest this again".
4. A request for something not yet conversational opens the existing form.
5. An IBM-flagged workspace shows no connect prompt and no top-up prompt, and only admins can
   enable channel suggestions.
6. The existing tests still pass, the agent has its own tests, the copy checks pass, and Nake
   has the screenshots.

## 11. Build order and hand-over

Deliverable 1 (POC) is built first, in one pass, from the six scenarios.

Deliverable 2, each step ending in something visible:

1. The sandbox app answers in the agent panel with sessions, status and the Stop button.
2. Jobs and help are conversational. Watson is replaced.
3. Text translation, then document translation with quote and approval gate.
4. Hand-offs to existing forms.
5. Proactive behaviours with all five safety rules.
6. Hand-over pack for Wade's team: the proposal, the pull requests, the test harness, the
   release plan, and the list of things only they can do.

The release plan covers the one-way door: switching the live app to the agent panel cannot be
reversed and triggers Marketplace review, and the name change is likely to trigger IBM admin
re-approval. Both happen in one release that Wade's team runs, with IBM told in advance. Neither
happens in this project.

Demo audiences, in order: internal, then Wade's team, then IBM. IBM sees it only after it runs
on real services.

## 12. What is needed from people

From Nake:

- Before Deliverable 2 step 1: a Slack Developer Program sandbox workspace (free, includes the
  agent features). Click-by-click steps will be provided.
- Before step 2: an Anthropic API key, placed in a local `.env` file by Nake, never pasted into
  a chat.
- Around step 3: send Wade the heads-up message (drafted for him). It says what is being
  prototyped, promises the repo is untouched, asks for UAT access, and asks three questions:
  which progress events can drive task cards, what unit quotes use, and whether anything
  depends on the "source" label.
- Update three files that still say the name is lowercase: `canon/naming-and-copy.md` and
  `canon/design-rules.md` in arbitr-align, and the global `CLAUDE.md`.

From Wade's team, later: UAT access, answers to the three questions, the session-storage
database addition, review of the pull requests, and ownership of the production release.

## 13. Known risks

- The app cannot start without reaching a Straker database. Making it start against the
  simulator may need untangling inside the private copy. If that proves hard, it is reported
  first, before further work.
- Slack's agent APIs are new. The docs have been read; nothing has been built against them yet.
  Step 1 is deliberately small for that reason.
- Wade's team moves fast. Their changes are pulled regularly; a large refactor on their side
  could cost rework.
- Live streaming adds load to a web process that already shares space with background file
  jobs. This is flagged to Wade's team, not fixed here.
- The Marketplace security tab currently names gpt-3.5-turbo as the app's LLM, but no LLM
  client exists in this repo. Either a downstream service uses it or the tab is wrong. Wade's
  team should confirm before the listing is rewritten.

## 14. Out of scope

- Any change to production, the live listing, or Straker's databases.
- New translation capabilities. The agent can do only what the buttons already do.
- Governance features such as checking whether content is safe to publish.
- The IBM adoption campaign.
- A second LLM vendor. The interface allows one; none is built.

## 15. Amendments after code mapping and two mock-up reviews (2026-09-21)

These supersede the sections above where they differ.

**Hand-off (replaces the sandbox demo in sections 3, 10, 11, 12).** The front-end POC is the demo. The working app is code inside a copy of the existing app, relying on Straker's database and services, run by Wade's team. It cannot start outside Straker's network (five private packages, import-time database queries), so it is handed over as "tested core, reviewed wiring": an agent core with no dependency on the app, tested locally, and a thin adapter that has been read against the source but never run. No Slack sandbox, no simulated backend. Nake lifted the no-push rule for one branch, `arbitr-agent`, at hand-over only, after Wade has had the heads-up and with a fresh confirmation at that moment.

**What the code showed.**
- One seam: every free-text DM and @-mention reaches the single Watson call in `respond_to_message`.
- Quotes display in dollars (`USD 40.00`); balance prompts say "AI tokens". Document quotes last 12 hours; evaluation and human-translation quotes 30 days.
- With `QUOTE_ADMIN_ONLY=true`, only Verify Admins and Owners see quotes. Everyone else's document form submits and bills the organization without one.
- Inline translation is metered against the balance and never quoted.
- A form can only be opened from a click, so hand-offs are buttons.
- The app has no scheduler, no per-slide review, and no record linking a job to a Slack thread.

**The promise (replaces "approve anything that costs money").** Quoted work waits for your click. Arbitr never posts for others unless you asked or clicked. Inline translation is metered as it is today.

**Decisions.**
- Units: show dollars and Credits together in the POC; Credits figures are illustrative until the conversion rule is known. In the app, prices are printed by the existing quote message.
- People who cannot see quotes: one confirming click, no price, then the same submission their form makes today. Behind `AGENT_NATIVE_DOCUMENT_QUOTES`, off by default; with it off everyone is handed the existing form. A no-click version was deliberately not built.
- Quoted work: the agent only requests the quote; the existing quote message and Accept button do the rest. A typed "yes" is never an approval, and there is never a second live button.
- An explicit request in a channel thread posts without a click (it is the attributable record, and the action is metered and unquoted). A click is still required when the request comes from elsewhere and whenever Arbitr speaks first.
- Suggestions are delivered as a direct message, not an ephemeral message. Privacy wording: the app already receives messages in channels it is a member of; a suggestion adds a language-detection call to Straker's own service; nothing goes to the model provider until a click.
- Voice: "I" in conversation; "Arbitr" in notices, buttons and attributions; never "we". Lead with the way forward, limit second. AI disclaimer on deliverables only.
- Plans: buttons ship with the quote. No task card for simple look-ups, none that arrives already finished, none left on "Working"; waiting is the session status. Long jobs are handed to the service and the session returns to Ready. Stop applies to the agent's own work; cancelling a paid job stays its own explicit action.
- Auto-approve under a threshold: shown in the POC's Home tab as a labelled concept only. It changes who authorises spending and needs a billing-policy decision by Straker and the customer.
- Follow-ups and digest: rules built and tested; never remind about an expired quote; scheduler hook defined but not wired.

**Not built, shown in the POC as planned:** suggested prompts, feedback buttons, attribution and Remove on in-thread posts, automatic re-quote of expired quotes, resume after account connect, using what the person has open beside the agent, the channel trigger for suggestions, a "Waiting on you" Home block, default target languages, localisation of fixed copy.

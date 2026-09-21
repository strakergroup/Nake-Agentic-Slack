# Arbitr in Slack: Front-End POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A clickable, scripted browser simulation of the Arbitr agent experience in Slack, published as a private shareable link, that doubles as the visual specification for the working app.

**Architecture:** A static page with no build step. A small pure-JavaScript "player" walks a scenario (a graph of scripted steps with branches) and produces a list of visible items; a renderer draws those items as an imitation Slack surface; a Design System v2 frame around it holds the scenario picker and presenter notes. All words live in one data file so they can be checked automatically against the Arbitr voice rules.

**Tech Stack:** HTML, CSS, vanilla JavaScript ES modules. Node 22 built-in test runner (`node --test`) for the player and the copy checks. Python 3 `http.server` for local preview. Design System v2 tokens, self-hosted woff2 fonts and lens artwork copied from `design-system-v2/`. Published with the Artifact tool.

**Spec:** `docs/superpowers/specs/2026-09-21-arbitr-slack-agent-design.md`, sections 3 (Deliverable 1), 4, 5, 7, 8.

---

## Plain-language summary for Nake

What you will get: one private link. It opens a page with the Arbitr wordmark, six scenario buttons, and an imitation Slack window. You click "Next" (or the buttons inside the fake Slack) and the conversation plays out exactly as scripted below. A notes panel on the right tells the presenter what to say at each step and which safety rule is on show. A switch flips the whole thing into "IBM workspace" mode.

What it is not: it is not connected to Slack, Claude, or Straker. Every name, job number and amount is made up and the page says so.

How you will know it is done: automated checks pass (the player logic, and a scan of every word for emoji, em dashes, retired words, lowercase "arbitr", and the word "users"); you get screenshots of all six scenarios at desktop and phone widths, taken from the published link, not just from my machine.

One decision inside this plan to be aware of: the project folder gets local version control (Task 0) so work is saved step by step. Nothing is pushed anywhere. The cloned Straker repo and the design system copy are excluded.

Unit wording: the POC shows amounts in "Credits" (your canon's billing unit) with a presenter note that the real unit is to be confirmed with Wade's team.

---

## File structure

All new files live under `/Users/nake/Projects/Arbitr Slack/poc/`. Nothing inside `slack-straker-translate/` or `design-system-v2/` is modified.

| File | Responsibility |
|---|---|
| `poc/index.html` | Page shell: v2 frame, Slack stage container, notes panel. Loads `js/main.js`. |
| `poc/css/frame.css` | The Design System v2 frame. Imports the vendored tokens. No invented colours, radii or fonts. |
| `poc/css/slack.css` | The imitation Slack surface. Deliberately not v2: it depicts Slack. System font stack, Slack-like neutrals, scoped under `.slack`. |
| `poc/vendor/arbitr-ds/` | Copied, unmodified: `tokens/*.css`, the 7 `assets/fonts/*.woff2`, and the lens and wordmark SVGs. `fonts.woff2.css` is the one new file here (same families as `tokens/fonts.css`, pointing at the woff2 files). |
| `poc/js/engine.js` | Pure logic. `createPlayer(scenario)`. No DOM. |
| `poc/js/scenarios.js` | All six scenarios as data. Every user-visible word of the simulation lives here. |
| `poc/js/copy-rules.js` | Pure logic. `checkCopy(text)` returns a list of voice-rule violations. |
| `poc/js/render.js` | Turns player state into DOM inside the Slack stage. One function per item kind. |
| `poc/js/main.js` | Wires picker, IBM switch, Next and Restart buttons, notes panel, keyboard. |
| `poc/tests/engine.test.mjs` | Player behaviour and scenario graph integrity. |
| `poc/tests/copy.test.mjs` | Runs `checkCopy` over every string in `scenarios.js` and over `index.html`. |
| `.claude/launch.json` | Local preview server definition (port 4173). |
| `.gitignore` | Excludes `slack-straker-translate/`, `design-system-v2/`, `.env`, `.DS_Store`. |

### Data shapes (used by every task below)

```js
// A scenario
{
  id: 'document',                 // unique, kebab-case
  title: 'Document request',      // picker label
  surface: 'dm',                  // 'dm' | 'channel' | 'home'
  start: 's1',                    // id of the first step
  steps: { s1: Step, s2: Step }   // keyed by step id
}

// A step. `kind` decides which other fields apply.
{
  kind: 'user' | 'agent' | 'plan' | 'plan_update' | 'quote' | 'choices'
      | 'files' | 'ephemeral' | 'thread_reply' | 'modal' | 'home' | 'status',
  next: 's2',                     // omitted on the last step and on 'choices'
  note: 'Presenter note text',    // optional, shown in the notes panel
  ibm: { ...fieldOverrides },     // optional, merged over the step when IBM mode is on
  // kind-specific fields:
  who: 'Mika Kato', text: '...', file: 'Q3-launch.pptx',          // user
  text: '...', disclaimer: true,                                  // agent
  planId: 'p1', tasks: [{ id, title, detail, state }],            // plan; state: 'pending'|'in_progress'|'complete'|'waiting'
  planId: 'p1', taskId: 't4', state: 'complete', detail: '...',   // plan_update
  lines: [{ label, amount }], total: 165, unit: 'Credits', footnote: '...', // quote
  choices: [{ id, label, style: 'primary'|'default', next }],     // choices
  files: ['Q3-launch_ja.pptx'],                                   // files
  text: '...', choices: [...],                                    // ephemeral (has its own choices)
  text: '...', attribution: '...',                                // thread_reply
  title: '...', fields: [{ label, value }], actions: ['Submit','Cancel'], // modal
  blocks: [...],                                                  // home
  status: 'working' | 'waiting' | 'ready'                         // status
}
```

Player state returned by `player.state`:

```js
{ items: [VisibleItem], status: 'ready', awaiting: null | { stepId, choices }, done: false, note: '' }
```

`plan_update` does not add an item; it mutates the matching task inside the earlier `plan` item.

---

## Scenario scripts (the words)

These are the source of truth for `scenarios.js`. Voice rules: Arbitr is named as the actor; never "users"; no exclamation marks; no emoji; no em dashes; refusals are what happened, why, what happens next; a waiting approval is never styled as an error. People and amounts are fictional.

### 1. Document request (`document`, surface `dm`)

| # | Kind | Content | Presenter note |
|---|---|---|---|
| s1 | user | Mika Kato: "Translate this deck into Japanese and German, and have a person check the legal slide." File: `Q3-launch.pptx` | "A goal in plain language. No shortcut, no form, no command to remember." |
| s2 | status | working | |
| s3 | plan | p1: t1 "Read the deck" in_progress; t2 "Price AI translation" pending; t3 "Price human review" pending; t4 "Your approval" pending; t5 "Translate and deliver here" pending | "The plan is visible before anything happens. These are Slack's own task cards." |
| s4 | plan_update | t1 complete, detail "24 slides, English" | |
| s5 | plan_update | t2 complete, detail "2 languages" | |
| s6 | plan_update | t3 complete, detail "Slide 19 only" | |
| s7 | plan_update | t4 waiting, detail "Waiting on you" | |
| s8 | agent | "Here is the plan. Nothing is charged until you approve." | |
| s9 | quote | "AI translation, Japanese and German" 120; "Human review, slide 19" 45; total 165 Credits. IBM override footnote: "Charged to your organization's wallet." | "Unit shown as Credits. The real unit is to be confirmed with Wade's team." |
| s10 | status | waiting | "Session status reads Waiting on you. This is the product working, not an error." |
| s11 | choices | "Approve 165 Credits" (primary) to a1; "AI only, 120 Credits" to b1; "Decline" to c1 | "The click is the only way money moves. The model can propose; it cannot spend. Enforced in code." |
| a1 | plan_update | t4 complete, detail "Approved by Mika Kato" | |
| a2 | status | working | |
| a3 | plan_update | t5 in_progress, detail "Japanese, German" | "Progress comes from Straker's existing services reporting back." |
| a4 | plan_update | t5 complete, detail "2 files" | |
| a5 | files | `Q3-launch_ja.pptx`, `Q3-launch_de.pptx` | |
| a6 | agent (disclaimer) | "The Japanese and German decks are ready. Slide 19 is with a reviewer, and Arbitr will post the reviewed slide in this thread when it comes back." | "Every agent response carries the AI disclaimer Slack requires." |
| a7 | status | ready (end) | |
| b1 | plan_update | t3 complete, detail "Skipped at your request" | |
| b2 | plan_update | t4 complete, detail "Approved by Mika Kato, AI only" | |
| b3 | plan_update | t5 complete, detail "2 files" | |
| b4 | files | same two files | |
| b5 | agent (disclaimer) | "The Japanese and German decks are ready. No human review was ordered. You can ask for one on any slide later." | |
| b6 | status | ready (end) | |
| c1 | plan_update | t4 complete, detail "Declined" | |
| c2 | agent | "Declined. Nothing was charged and nothing was translated. Ask again whenever you need it." | "A refusal in three parts: what happened, why it matters, what happens next." |
| c3 | status | ready (end) | |

### 2. Jobs (`jobs`, surface `dm`)

| # | Kind | Content | Presenter note |
|---|---|---|---|
| s1 | user | Mika Kato: "Where's my job?" | |
| s2 | plan | p1: t1 "Look up your open jobs" complete, detail "2 found" | "Look-ups run straight away. Only spending and posting need a click." |
| s3 | agent | "You have two open jobs.\nTJ-48213, Q3-launch.pptx, Japanese and German: slide 19 is with a reviewer, due tomorrow.\nTJ-48190, Returns-policy.docx, French: the quote is waiting on you." | |
| s4 | user | "What's waiting on me?" | |
| s5 | agent | "One thing. The quote for Returns-policy.docx, 60 Credits, has been waiting since Thursday." | |
| s6 | choices | "Approve 60 Credits" (primary) to a1; "View quote" to b1; "Not now" to c1 | |
| a1 | agent (disclaimer) | "Approved. Arbitr has started the French translation and will deliver it in this thread." then status ready (end) | |
| b1 | quote | "AI translation, French" 60; total 60 Credits; then returns to s6 | |
| c1 | agent | "Understood. The quote stays open. Arbitr will remind you once, on Monday." then status ready (end) | "Follow-ups are capped. One reminder, then silence." |

### 3. Private suggestion (`suggestion`, surface `channel`, channel `#launch-global`)

| # | Kind | Content | Presenter note |
|---|---|---|---|
| s1 | user | Dana Whitfield (in channel): "Launch moves to 14 October. Regional leads, please confirm your readiness by Friday." | "Suggestions are on in this channel because someone turned them on. Off is the default." |
| s2 | ephemeral | Label "Only visible to you". Text: "Most people in this channel work in Japanese. Want this posted in Japanese as well?" Choices: "Post in Japanese" (primary) to a1; "Not now" to b1; "Don't suggest this again" to c1 | "Only Dana sees this. The check used language detection and members' Slack language settings. Dana's message has not gone to the LLM." |
| a1 | thread_reply | Japanese translation of the announcement. Attribution: "Translated from English by Arbitr (AI). Posted at Dana Whitfield's request." (end) | "The LLM is involved only after the click. The post says who asked for it and that it is AI." |
| b1 | ephemeral | "No problem. Nothing was posted." (end) | |
| c1 | ephemeral | "Done. Arbitr will not suggest translations to you in this channel. You can change this in the Arbitr Home tab." (end) | "Both opt-outs are on every suggestion. There is also a hard cap on how often Arbitr speaks first." |

The Japanese text in a1 is sample user content, not Arbitr product copy. If any Arbitr UI string is ever rendered in Japanese in this POC, invoke the `ja-localise` skill first.

### 4. Hand-off (`handoff`, surface `dm`)

| # | Kind | Content | Presenter note |
|---|---|---|---|
| s1 | user | Mika Kato: "Add Japanese subtitles to this video." File: `town-hall.mp4` | |
| s2 | agent | "Subtitles run through the media form. Arbitr has opened it with your file attached." | "Not yet conversational, but never a dead end. The agent understands the request and opens the form that exists today." |
| s3 | modal | Title "Transcribe and translate". Fields: File `town-hall.mp4`; Spoken language English; Subtitle language Japanese; Output "Subtitle file (SRT)". Actions Submit, Cancel (end) | "This is the existing form, unchanged. IBM's current habits keep working." |

### 5. IBM workspace (`ibm`, surface `dm`)

Shown as two columns on desktop, stacked on mobile: "Other workspaces" and "IBM workspace".

| # | Kind | Content | Presenter note |
|---|---|---|---|
| s1 | user | Both columns. "Translate this deck into Japanese." File `Q3-launch.pptx` | |
| s2 | agent | Other: "To price this, Arbitr needs your account. Connect it once and you will not be asked again." with button "Connect account". IBM: no connect message; goes straight to the plan. | "IBM workspaces skip account connect. The Slack identity is the identity. This is existing behaviour, reused, not rebuilt." |
| s3 | quote | Other: 60 Credits, footnote "Balance after this job: 140 Credits. Top up". IBM: 60 Credits, footnote "Charged to your organization's wallet." No top-up link. | "IBM employees never see a top-up prompt." |
| s4 | user | Both: "Turn on translation suggestions in #launch-global." | |
| s5 | agent | Other: "Suggestions are now on in #launch-global. Anyone here can turn them off." IBM: "In this workspace only an admin can turn on channel suggestions. Arbitr can send the request to your admins." with button "Ask an admin" (end) | "Admins stay in control inside IBM. A refusal in three parts." |

### 6. App Home (`home`, surface `home`)

| Block | Content | Presenter note |
|---|---|---|
| Header | "Arbitr" with the lens device; subtitle "Your translation jobs and settings" | |
| Jobs list (existing) | TJ-48213 Q3-launch.pptx, In review; TJ-48190 Returns-policy.docx, Quote waiting; TJ-48102 Onboarding-guide.docx, Delivered | "The job list is today's Home tab, unchanged." |
| New: Digest | Label "Digest"; options Off (selected), Daily, Weekly; helper "A summary of your jobs, sent by direct message." | "Opt-in only. Off by default." |
| New: Muted suggestions | "#launch-global, translation offers" with button "Unmute" | "Every mute is visible and reversible." |
| New, admin only: Channel suggestions | "#launch-global, on, turned on by Dana Whitfield" with button "Turn off" | |

---

## Task 0: Project setup

**Files:**
- Create: `/Users/nake/Projects/Arbitr Slack/.gitignore`
- Create: `/Users/nake/Projects/Arbitr Slack/.claude/launch.json`
- Create: `poc/` tree and `poc/vendor/arbitr-ds/` (copied files)
- Create: `poc/vendor/arbitr-ds/fonts.woff2.css`

- [ ] **Step 1: Initialise local version control (nothing is pushed)**

```bash
cd "/Users/nake/Projects/Arbitr Slack"
git init -b main
printf 'slack-straker-translate/\ndesign-system-v2/\n.env\n.DS_Store\n' > .gitignore
git add .gitignore docs && git commit -m "docs: add Arbitr Slack agent spec and POC plan"
```

Expected: one commit; `git status` shows a clean tree; `git remote -v` prints nothing.

- [ ] **Step 2: Vendor the design system files the POC needs**

```bash
cd "/Users/nake/Projects/Arbitr Slack"
mkdir -p poc/css poc/js poc/tests poc/vendor/arbitr-ds/tokens poc/vendor/arbitr-ds/assets/fonts poc/vendor/arbitr-ds/assets/logo
cp design-system-v2/tokens/{colors,typography,spacing,radius,shadow,layout}.css poc/vendor/arbitr-ds/tokens/
cp design-system-v2/assets/fonts/*.woff2 poc/vendor/arbitr-ds/assets/fonts/
cp design-system-v2/assets/logo/{lens-device-primary,lens-device-reversed,wordmark-primary,wordmark-reversed}.svg poc/vendor/arbitr-ds/assets/logo/
```

- [ ] **Step 3: Write `poc/vendor/arbitr-ds/fonts.woff2.css`**

Same families and weights as the design system's `tokens/fonts.css`, pointing at the woff2 binaries (112 KB total instead of the ttf set). No font CDN, per the design system's compliance rule.

```css
@font-face{font-family:'Plus Jakarta Sans';font-style:normal;font-weight:400;font-display:swap;src:url('./assets/fonts/PlusJakartaSans-400.woff2') format('woff2')}
@font-face{font-family:'Plus Jakarta Sans';font-style:normal;font-weight:600;font-display:swap;src:url('./assets/fonts/PlusJakartaSans-600.woff2') format('woff2')}
@font-face{font-family:'Plus Jakarta Sans';font-style:normal;font-weight:800;font-display:swap;src:url('./assets/fonts/PlusJakartaSans-800.woff2') format('woff2')}
@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:400;font-display:swap;src:url('./assets/fonts/IBMPlexSans-400.woff2') format('woff2')}
@font-face{font-family:'IBM Plex Sans';font-style:normal;font-weight:600;font-display:swap;src:url('./assets/fonts/IBMPlexSans-600.woff2') format('woff2')}
@font-face{font-family:'IBM Plex Mono';font-style:normal;font-weight:400;font-display:swap;src:url('./assets/fonts/IBMPlexMono-400.woff2') format('woff2')}
@font-face{font-family:'IBM Plex Mono';font-style:normal;font-weight:500;font-display:swap;src:url('./assets/fonts/IBMPlexMono-500.woff2') format('woff2')}
```

(The design system ships no IBM Plex Mono 600 woff2; the POC does not use that weight.)

- [ ] **Step 4: Write `.claude/launch.json`**

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "arbitr-slack-poc",
      "runtimeExecutable": "python3",
      "runtimeArgs": ["-m", "http.server", "4173", "--directory", "poc"],
      "port": 4173
    }
  ]
}
```

- [ ] **Step 5: Commit**

```bash
git add .claude/launch.json poc && git commit -m "chore: scaffold POC and vendor Design System v2 assets"
```

---

## Task 1: The player (pure logic, test first)

**Files:**
- Create: `poc/js/engine.js`
- Test: `poc/tests/engine.test.mjs`

- [ ] **Step 1: Write the failing tests**

```js
// poc/tests/engine.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createPlayer } from '../js/engine.js';

const demo = {
  id: 'demo', title: 'Demo', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Hello', next: 's2', note: 'first' },
    s2: { kind: 'plan', planId: 'p1', tasks: [{ id: 't1', title: 'Read', detail: '', state: 'in_progress' }], next: 's3' },
    s3: { kind: 'plan_update', planId: 'p1', taskId: 't1', state: 'complete', detail: '24 slides', next: 's4' },
    s4: { kind: 'status', status: 'waiting', next: 's5' },
    s5: { kind: 'choices', choices: [{ id: 'yes', label: 'Approve', style: 'primary', next: 'a1' }, { id: 'no', label: 'Decline', style: 'default', next: 'c1' }] },
    a1: { kind: 'agent', text: 'Approved.', disclaimer: true, ibm: { text: 'Approved for IBM.' } },
    c1: { kind: 'agent', text: 'Declined.' },
  },
};

test('starts empty, ready, not done', () => {
  const p = createPlayer(demo);
  assert.deepEqual(p.state.items, []);
  assert.equal(p.state.status, 'ready');
  assert.equal(p.state.done, false);
});

test('next() reveals one step and exposes its note', () => {
  const p = createPlayer(demo);
  p.next();
  assert.equal(p.state.items.length, 1);
  assert.equal(p.state.items[0].text, 'Hello');
  assert.equal(p.state.note, 'first');
});

test('plan_update mutates the earlier plan instead of adding an item', () => {
  const p = createPlayer(demo);
  p.next(); p.next(); p.next();
  assert.equal(p.state.items.length, 2);
  assert.equal(p.state.items[1].tasks[0].state, 'complete');
  assert.equal(p.state.items[1].tasks[0].detail, '24 slides');
});

test('status steps change status without adding an item', () => {
  const p = createPlayer(demo);
  p.next(); p.next(); p.next(); p.next();
  assert.equal(p.state.status, 'waiting');
  assert.equal(p.state.items.length, 2);
});

test('choices block next() until choose() is called', () => {
  const p = createPlayer(demo);
  for (let i = 0; i < 5; i++) p.next();
  assert.equal(p.state.awaiting.choices.length, 2);
  p.next();
  assert.equal(p.state.items.at(-1).kind, 'choices');
  p.choose('no');
  assert.equal(p.state.awaiting, null);
  assert.equal(p.state.items.at(-1).text, 'Declined.');
  assert.equal(p.state.items.at(-2).chosen, 'no');
  assert.equal(p.state.done, true);
});

test('choose() with an unknown id throws', () => {
  const p = createPlayer(demo);
  for (let i = 0; i < 5; i++) p.next();
  assert.throws(() => p.choose('maybe'), /unknown choice/);
});

test('ibm mode merges overrides', () => {
  const p = createPlayer(demo, { ibm: true });
  for (let i = 0; i < 5; i++) p.next();
  p.choose('yes');
  assert.equal(p.state.items.at(-1).text, 'Approved for IBM.');
});

test('reset() returns to the start', () => {
  const p = createPlayer(demo);
  p.next(); p.next();
  p.reset();
  assert.deepEqual(p.state.items, []);
  assert.equal(p.state.done, false);
});

test('the scenario object is never mutated', () => {
  const before = JSON.stringify(demo);
  const p = createPlayer(demo);
  p.next(); p.next(); p.next();
  assert.equal(JSON.stringify(demo), before);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd "/Users/nake/Projects/Arbitr Slack/poc" && node --test tests/engine.test.mjs`
Expected: FAIL, cannot find module `../js/engine.js`.

- [ ] **Step 3: Implement**

```js
// poc/js/engine.js
export function createPlayer(scenario, { ibm = false } = {}) {
  let state;
  let cursor;

  const resolve = (id) => {
    const raw = scenario.steps[id];
    if (!raw) throw new Error(`unknown step: ${id}`);
    const step = structuredClone(raw);
    if (ibm && step.ibm) Object.assign(step, step.ibm);
    delete step.ibm;
    return { id, ...step };
  };

  const reset = () => {
    cursor = scenario.start;
    state = { items: [], status: 'ready', awaiting: null, done: false, note: '' };
  };

  const apply = (step) => {
    state.note = step.note ?? '';
    if (step.kind === 'plan_update') {
      const plan = state.items.find((i) => i.kind === 'plan' && i.planId === step.planId);
      if (!plan) throw new Error(`plan_update before plan: ${step.planId}`);
      const task = plan.tasks.find((t) => t.id === step.taskId);
      if (!task) throw new Error(`unknown task: ${step.taskId}`);
      task.state = step.state;
      if (step.detail !== undefined) task.detail = step.detail;
    } else if (step.kind === 'status') {
      state.status = step.status;
    } else {
      state.items.push(step);
    }
    const ownChoices = step.kind === 'choices' || (step.kind === 'ephemeral' && step.choices);
    if (ownChoices) {
      state.awaiting = { stepId: step.id, choices: step.choices };
      cursor = null;
    } else {
      cursor = step.next ?? null;
      if (cursor === null) state.done = true;
    }
  };

  const next = () => {
    if (state.done || state.awaiting || cursor === null) return;
    apply(resolve(cursor));
  };

  const choose = (choiceId) => {
    if (!state.awaiting) return;
    const choice = state.awaiting.choices.find((c) => c.id === choiceId);
    if (!choice) throw new Error(`unknown choice: ${choiceId}`);
    const item = state.items.find((i) => i.id === state.awaiting.stepId);
    item.chosen = choiceId;
    state.awaiting = null;
    cursor = choice.next ?? null;
    if (cursor === null) { state.done = true; return; }
    apply(resolve(cursor));
  };

  reset();
  return { get state() { return state; }, next, choose, reset };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/engine.test.mjs`
Expected: 9 pass, 0 fail.

- [ ] **Step 5: Commit**

```bash
git add poc/js/engine.js poc/tests/engine.test.mjs && git commit -m "feat(poc): scenario player with branching and IBM overrides"
```

---

## Task 2: Voice-rule checker (pure logic, test first)

**Files:**
- Create: `poc/js/copy-rules.js`
- Test: `poc/tests/copy.test.mjs` (first half)

- [ ] **Step 1: Write the failing tests**

```js
// poc/tests/copy.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkCopy } from '../js/copy-rules.js';

const rules = (text) => checkCopy(text).map((v) => v.rule);

test('clean copy passes', () => {
  assert.deepEqual(rules('Arbitr has started the French translation.'), []);
});
test('flags em and en dashes', () => {
  assert.deepEqual(rules('Ready — two files'), ['no-dash']);
  assert.deepEqual(rules('Ready – two files'), ['no-dash']);
});
test('flags emoji', () => {
  assert.deepEqual(rules('Done \u{1F44D}'), ['no-emoji']);
});
test('flags lowercase product name but not commands or paths', () => {
  assert.deepEqual(rules('ask arbitr to translate'), ['capitalise-arbitr']);
  assert.deepEqual(rules('Type /arbitr to start'), []);
});
test('flags exclamation marks', () => {
  assert.deepEqual(rules('Your file is ready!'), ['no-exclamation']);
});
test('flags the word users', () => {
  assert.deepEqual(rules('Users can mute this'), ['no-users']);
});
test('flags retired and off-limits words', () => {
  assert.deepEqual(rules('A seamless experience'), ['retired-word']);
  assert.deepEqual(rules('Your trust score is 92'), ['off-limits-claim']);
  assert.deepEqual(rules('Explained by Glass Box'), ['off-limits-claim']);
});
test('flags retired names', () => {
  assert.deepEqual(rules('Connect to Verify'), ['retired-name']);
  assert.deepEqual(rules('Open LanguageCloud'), ['retired-name']);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/copy.test.mjs`
Expected: FAIL, cannot find module `../js/copy-rules.js`.

- [ ] **Step 3: Implement**

```js
// poc/js/copy-rules.js
const RULES = [
  { rule: 'no-dash', re: /[–—]/ },
  { rule: 'no-emoji', re: /\p{Extended_Pictographic}/u },
  { rule: 'capitalise-arbitr', re: /(?<![\/\w.-])arbitr(?![\w-])/ },
  { rule: 'no-exclamation', re: /!/ },
  { rule: 'no-users', re: /\busers?\b/i },
  { rule: 'retired-word', re: /\b(seamless(ly)?|empower(s|ed|ing)?|unlock(s|ed|ing)?|effortless(ly)?|bottleneck|raw potential|revolutionary|next-gen)\b/i },
  { rule: 'off-limits-claim', re: /\b(trust scor(e|ing)|consensus voting|glass box)\b/i },
  { rule: 'retired-name', re: /\b(Straker\.AI|NotVerify|LanguageCloud|RAY Translate|Connect to Verify)\b/ },
];

export function checkCopy(text) {
  return RULES.filter(({ re }) => re.test(text)).map(({ rule }) => ({ rule, text }));
}
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/copy.test.mjs`
Expected: 8 pass, 0 fail.

- [ ] **Step 5: Commit**

```bash
git add poc/js/copy-rules.js poc/tests/copy.test.mjs && git commit -m "feat(poc): automated Arbitr voice-rule checker"
```

---

## Task 3: Scenario data and integrity tests

**Files:**
- Create: `poc/js/scenarios.js`
- Modify: `poc/tests/engine.test.mjs` (append), `poc/tests/copy.test.mjs` (append)

- [ ] **Step 1: Append the integrity tests (they fail until the data exists)**

```js
// append to poc/tests/engine.test.mjs
import { scenarios } from '../js/scenarios.js';

test('six scenarios with unique ids in the agreed order', () => {
  assert.deepEqual(scenarios.map((s) => s.id), ['document', 'jobs', 'suggestion', 'handoff', 'ibm', 'home']);
});

for (const s of scenarios) {
  test(`${s.id}: every next and choice target exists`, () => {
    assert.ok(s.steps[s.start], 'start exists');
    for (const [id, step] of Object.entries(s.steps)) {
      if (step.next) assert.ok(s.steps[step.next], `${id}.next -> ${step.next}`);
      for (const c of step.choices ?? []) if (c.next) assert.ok(s.steps[c.next], `${id}.${c.id} -> ${c.next}`);
    }
  });

  test(`${s.id}: every branch reaches an end within 60 steps, in both modes`, () => {
    for (const ibm of [false, true]) {
      const walk = (path) => {
        const p = createPlayer(s, { ibm });
        let picks = [...path];
        for (let i = 0; i < 60 && !p.state.done; i++) {
          if (p.state.awaiting) {
            if (picks.length === 0) return p.state.awaiting.choices.map((c) => c.id);
            p.choose(picks.shift());
          } else p.next();
        }
        assert.equal(p.state.done, true, `${s.id} ${JSON.stringify(path)} ibm=${ibm}`);
        return [];
      };
      const queue = [[]];
      const seen = new Set();
      while (queue.length) {
        const path = queue.shift();
        const key = path.join('>');
        if (seen.has(key) || path.length > 4) continue;
        seen.add(key);
        for (const id of walk(path)) queue.push([...path, id]);
      }
    }
  });
}
```

```js
// append to poc/tests/copy.test.mjs
import { scenarios } from '../js/scenarios.js';
import { readFileSync } from 'node:fs';

const strings = (value, out = []) => {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') Object.entries(value).forEach(([k, v]) => { if (!['id', 'kind', 'next', 'start', 'surface', 'planId', 'taskId', 'state', 'status', 'style'].includes(k)) strings(v, out); });
  return out;
};

test('every scenario string passes the voice rules', () => {
  const bad = scenarios.flatMap((s) => strings(s).flatMap((t) => checkCopy(t).map((v) => `${s.id}: ${v.rule}: ${t}`)));
  assert.deepEqual(bad, []);
});

test('the view-back loop in the jobs scenario cannot run forever', () => {
  const jobs = scenarios.find((s) => s.id === 'jobs');
  assert.ok(Object.keys(jobs.steps).length < 20);
});

test('index.html text passes the voice rules', () => {
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8').replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<[^>]+>/g, ' ');
  assert.deepEqual(checkCopy(html).map((v) => v.rule), []);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `node --test tests/*.test.mjs`
Expected: FAIL, cannot find module `../js/scenarios.js`.

- [ ] **Step 3: Write `poc/js/scenarios.js`**

Export `export const scenarios = [document, jobs, suggestion, handoff, ibm, home];` in that order. Transcribe the six tables in "Scenario scripts" above into the data shapes in "Data shapes", word for word. Rules for the transcription:

- Step ids and branch targets are exactly the `#` column values.
- A table row that says "then status ready (end)" becomes two steps: the agent step with `next` pointing at a `status` step that has no `next`.
- Jobs `b1` (the quote) has `next: 's6b'`, where `s6b` is a second `choices` step offering only "Approve 60 Credits" to `a1` and "Not now" to `c1`. This removes the loop while keeping the script's meaning.
- The IBM scenario uses one step list; each "Other / IBM" difference is an `ibm: {...}` override on that step. `main.js` renders this scenario twice, side by side, once per mode.
- The `home` scenario is a single `home` step whose `blocks` array mirrors the App Home table; `note` holds the presenter notes joined by blank lines.
- The Japanese thread reply in `suggestion.a1` is: "ローンチは10月14日に変更になりました。各地域のリードは、金曜日までに準備状況をご確認ください。"
- Worked example, the first four steps of `document`:

```js
const document = {
  id: 'document', title: 'Document request', surface: 'dm', start: 's1',
  steps: {
    s1: { kind: 'user', who: 'Mika Kato', text: 'Translate this deck into Japanese and German, and have a person check the legal slide.', file: 'Q3-launch.pptx', next: 's2', note: 'A goal in plain language. No shortcut, no form, no command to remember.' },
    s2: { kind: 'status', status: 'working', next: 's3' },
    s3: { kind: 'plan', planId: 'p1', next: 's4', note: 'The plan is visible before anything happens. These are Slack’s own task cards.', tasks: [
      { id: 't1', title: 'Read the deck', detail: '', state: 'in_progress' },
      { id: 't2', title: 'Price AI translation', detail: '', state: 'pending' },
      { id: 't3', title: 'Price human review', detail: '', state: 'pending' },
      { id: 't4', title: 'Your approval', detail: '', state: 'pending' },
      { id: 't5', title: 'Translate and deliver here', detail: '', state: 'pending' },
    ] },
    s4: { kind: 'plan_update', planId: 'p1', taskId: 't1', state: 'complete', detail: '24 slides, English', next: 's5' },
    // ... continue from the table
  },
};
```

- [ ] **Step 4: Run to verify pass**

Run: `node --test tests/*.test.mjs`
Expected: all pass except `index.html text passes the voice rules`, which fails because `index.html` does not exist yet. That test goes green in Task 4.

- [ ] **Step 5: Commit**

```bash
git add poc/js/scenarios.js poc/tests && git commit -m "feat(poc): six scripted scenarios with graph and voice checks"
```

---

## Task 4: The Design System v2 frame

**Files:**
- Create: `poc/index.html`, `poc/css/frame.css`

Read `design-system-v2/readme.md` sections "Visual foundations" and "Logo & brand assets" before starting. Constraints that are release gates here: Ice `#F4F5FF` page canvas with white cards on top; indigo is scarce, so exactly one solid-indigo element in the frame (the Next button) and every other control is outline; radii are `var(--radius-container)` and `var(--radius-chip)` only; IBM Plex Sans for UI, IBM Plex Mono for micro-labels (11px, uppercase, 0.16em tracking), Plus Jakarta Sans only in the page title; the wordmark is the supplied SVG at 96px wide or more, never live text; no icons are drawn (controls are text labels); dividers are hairlines.

- [ ] **Step 1: Write `poc/index.html`**

Structure: `<header>` with `wordmark-primary.svg` (alt "Arbitr"), the title "Arbitr in Slack: the agent experience", and a mono eyebrow "Simulation. Scripted data. Names and amounts are illustrative."; a `<nav aria-label="Scenarios">` with six buttons generated by `main.js`; a labelled switch "IBM workspace"; `<main>` holding `<section id="stage" class="slack" aria-live="polite">` and `<aside id="notes" aria-label="Presenter notes">`; a control row with "Next" (solid indigo, `id="next"`), "Restart" (outline, `id="restart"`), and a mono step counter. `<title>` is "Arbitr in Slack". Links `vendor/arbitr-ds/fonts.woff2.css`, the six vendored token files, `css/frame.css`, `css/slack.css`, and `<script type="module" src="js/main.js">`.

- [ ] **Step 2: Write `poc/css/frame.css`** using only `var(--*)` tokens from the vendored files. Desktop: stage and notes side by side (notes 320px). At widths below 900px: notes drop below the stage, scenario buttons wrap, 16px side gutters, no horizontal scroll. Honour `prefers-reduced-motion` by disabling the step-in transition.

- [ ] **Step 3: Run the tests**

Run: `node --test tests/*.test.mjs`
Expected: all pass, including `index.html text passes the voice rules`.

- [ ] **Step 4: Preview and screenshot the empty frame**

Start `arbitr-slack-poc` with the Browser pane's `preview_start`, then screenshot at desktop width and with `resize_window` preset `mobile`. Check: wordmark crisp, exactly one solid indigo element, no horizontal scroll on mobile. Reset the viewport to `desktop` afterwards.

- [ ] **Step 5: Commit**

```bash
git add poc/index.html poc/css/frame.css && git commit -m "feat(poc): Design System v2 frame"
```

---

## Task 5: Slack surface renderer for conversations

**Files:**
- Create: `poc/css/slack.css`, `poc/js/render.js`, `poc/js/main.js`

- [ ] **Step 1: Write `render.js`** exporting `render(stageEl, scenario, state, { onChoose })`. It clears and redraws the stage from `state.items`. One small function per kind: `user` (avatar initials, name, text, optional file chip), `agent` (lens device as the avatar using `lens-device-primary.svg`, name "Arbitr", a text tag "AI agent", text with `\n` as line breaks, and when `disclaimer` is true the line "AI output can be inaccurate. Human review is available on any job."), `plan` (rows of state label plus title plus detail; states are shown as text labels "Done", "Working", "Waiting", "Not started", never colour alone), `quote` (lines, total, unit, footnote), `choices` (buttons; after a choice the chosen one is marked and all are disabled), `files` (file chips). The session header shows the scenario title and a status pill with text "Working", "Waiting on you" or "Ready". All text is set with `textContent`, never `innerHTML`.

- [ ] **Step 2: Write `slack.css`** scoped under `.slack`: system font stack, white message pane, 36px avatars, hover-free (it is a simulation), buttons styled like Slack's default and primary buttons. The status pill for "Waiting on you" uses a neutral or amber-tinted treatment, never red.

- [ ] **Step 3: Write `main.js`**: build the scenario buttons from `scenarios`; keep `{ scenarioId, ibm }`; create a player; wire Next, Restart, the IBM switch (recreates the player with `{ ibm }`), choice clicks to `player.choose`, and the notes panel to `state.note`. Keyboard: Right Arrow and Space trigger Next when focus is not on a button; `R` restarts. Remember the last scenario in `localStorage` inside `try/catch`.

- [ ] **Step 4: Play scenarios 1, 2 and 4 in the preview** and click through all branches of scenario 1. Check the browser console for errors with `read_console_messages` (expected: none).

- [ ] **Step 5: Run tests and commit**

```bash
node --test tests/*.test.mjs && git add poc && git commit -m "feat(poc): conversation renderer, plan cards, quotes and approvals"
```

---

## Task 6: Channel, thread, form and Home surfaces

**Files:**
- Modify: `poc/js/render.js`, `poc/css/slack.css`, `poc/js/main.js`

- [ ] **Step 1: Add renderers**: `ephemeral` (a tinted block headed "Only visible to you" with its own choice buttons), `thread_reply` (indented under the channel message, with the attribution line in smaller text), `modal` (an in-flow dimmed panel inside the stage, not `position: fixed`, with read-only fields and the two action buttons), `home` (header with the lens device, then each block from the table: job rows, the Digest radio group, the muted list with "Unmute", and the admin-only list with "Turn off"). Channel surface shows a `#launch-global` header in place of the session header.

- [ ] **Step 2: Add the side-by-side IBM view**: when the scenario id is `ibm`, `main.js` creates two players (`ibm: false`, `ibm: true`), renders them into two labelled columns ("Other workspaces", "IBM workspace"), and advances both on Next. The IBM switch is hidden for this scenario. Columns stack below 900px.

- [ ] **Step 3: Play scenarios 3, 5 and 6 in the preview**, all branches of scenario 3. Console must be clean.

- [ ] **Step 4: Run tests and commit**

```bash
node --test tests/*.test.mjs && git add poc && git commit -m "feat(poc): channel suggestion, form hand-off, IBM comparison and Home tab"
```

---

## Task 7: Verification before publishing

- [ ] **Step 1: Full test run.** `node --test tests/*.test.mjs`. Expected: 0 failures. Paste the summary line into the report to Nake.
- [ ] **Step 2: Screenshots, local.** For each of the six scenarios at its most informative step (scenario 1 at the quote, and again after Approve): one desktop screenshot and one at the `mobile` preset. Twelve or more images. Reset the viewport to `desktop` at the end.
- [ ] **Step 3: Lens device at small size.** Zoom on an agent avatar in a screenshot. If the inner ring has closed up at 36px, raise the avatar's rendered size or report it to Nake as the design system predicts for sizes under 24px.
- [ ] **Step 4: Accessibility pass.** Tab through the page: every control reachable, visible focus ring in indigo, status never carried by colour alone, `aria-live` announces new items. Fix what fails.
- [ ] **Step 5: Commit** any fixes: `git commit -am "fix(poc): verification fixes"`.

---

## Task 8: Publish as a private link and verify it live

- [ ] **Step 1: Load the `artifact-design` skill** and follow its page contract (title, theming, storage, size limits). If it conflicts with Design System v2 on colours or fonts, v2 wins inside the page and the conflict is reported to Nake.
- [ ] **Step 2: Publish** `poc/index.html` with the Artifact tool, passing every file under `poc/css`, `poc/js` and `poc/vendor` through `files` with `root` set to `poc`. Icon word: `chat`. Description: "Scripted simulation of the Arbitr agent experience in Slack." The link is private by default; Nake decides who it is shared with.
- [ ] **Step 3: Fallback if module or font loading fails on the published page.** Write `poc/build-single.mjs` that inlines the CSS, the JS modules (concatenated in dependency order with imports and exports stripped), the SVGs, and the woff2 files as base64 data URIs into `poc/dist/arbitr-slack-poc.html` (about 200 KB), then publish that one file instead. Do not attempt a third approach without telling Nake why the first two failed.
- [ ] **Step 4: Verify live.** Read the published page back, open it in the Browser pane, play scenario 1 through Approve, and take desktop and mobile screenshots from the live link.
- [ ] **Step 5: Report to Nake**: the link, the test summary line, the live screenshots, and anything that differs from the spec.
- [ ] **Step 6: Commit and offer next steps**: commit, then offer to write the Deliverable 2 plan and to draft the heads-up message to Wade with the POC link in it.

---

## Self-review against the spec

- Spec section 3, Deliverable 1, six scenarios: Tasks 3, 5, 6. Private link and desktop plus mobile screenshots: Tasks 7 and 8.
- Spec section 4 (labelled AI agent, disclaimer, Waiting on you, alternatives on buttons, files in thread, admin-only wording): scenario scripts 1, 2, 5 and the `agent` renderer.
- Spec section 5 (five safety rules, admin-only in IBM): scenario 3 notes, scenario 5 step s5, scenario 6 blocks.
- Spec section 7 (IBM behaviour): scenario 5 and the IBM switch.
- Spec section 8 (branding): Task 4 constraints, lens avatar, no invented icons, governance state words unused, copy checker in Task 2.
- Not in the POC by design: real Slack, Claude, Straker services, the Stop button behaviour (shown only as a status), the audit log, free-plan fallback. These belong to Deliverable 2.
- Names are consistent across tasks: `createPlayer`, `state.items`, `state.awaiting`, `choose`, `checkCopy`, `scenarios`, `render`.

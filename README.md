# Arbitr agent for the Slack app

Work in progress by Nake Sekander. Private to Straker Group.

The Slack app (`strakergroup/slack-straker-translate`) is a bot today: shortcuts, a slash
command and forms. This project turns the same feature set into a Slack agent under the
Arbitr name: a person states a goal in plain words, changes their mind in plain words, and
confirms with a click where a click matters.

**Status: a scripted demo, a tested agent core, and reviewed wiring that has never run.**
The app cannot start outside Straker's network, so the integration is unproven until someone
runs the checklist in a dev environment. Nothing here has touched production, the live Slack
app, or its Marketplace listing.

## Start here

1. **See it (5 minutes):** https://claude.ai/artifact/Sxx8yDUnwVW6E8bbGVFerL
   A clickable, scripted simulation. Nine scenes with presenter notes. Not connected to Slack.
2. **Read the hand-over note:** [`handover/message-to-wade.md`](handover/message-to-wade.md)
3. **Read how it works:** [`handover/docs/arbitr-agent.md`](handover/docs/arbitr-agent.md)
4. **Try it in dev:** [`handover/docs/arbitr-agent-runbook.md`](handover/docs/arbitr-agent-runbook.md), a 20-step checklist with expected results.

## Two branches, two unrelated histories

| Branch | What it holds |
|---|---|
| `main` (this one) | The project: spec, build plans, the POC source, hand-over documents, patches. |
| `arbitr-agent` | A full copy of `slack-straker-translate` with the agent on top: `app/agent/`, tests, evals, docs, and five small flag-guarded edits. |
| `upstream-master` | Their `master` at the commit `arbitr-agent` was branched from, for comparison. |

See exactly what the agent adds:
https://github.com/strakergroup/Nake-Agentic-Slack/compare/upstream-master...arbitr-agent

Bring the branch into the real repo when you are ready (nothing here does that for you):

```bash
git remote add nake-agent https://github.com/strakergroup/Nake-Agentic-Slack.git
git fetch nake-agent arbitr-agent
git checkout -b arbitr-agent nake-agent/arbitr-agent
```

## What is in `main`

| Path | What it is |
|---|---|
| `docs/superpowers/specs/` | The design spec, including the amendments made after mapping the code and two mock-up reviews. |
| `docs/superpowers/plans/` | The two build plans (POC, working app). |
| `poc/` | The POC source: static HTML, CSS and JavaScript, no build step. `node --test poc/tests/*.test.mjs` runs its 41 tests. |
| `handover/docs/` | Architecture, run-book, POC-to-code map, spec. |
| `handover/patches/` | The `arbitr-agent` commits as patch files, for anyone who prefers them to the branch. |

## The safety properties worth knowing before you read the code

- One switch, `AGENT_ENABLED`, default off. With it off the app behaves exactly as before. If the agent throws, Watson answers as today.
- The agent cannot accept, pay for or cancel anything. Quoted work goes through the existing quote message and its Accept button.
- It can start paid work in exactly one way: for people who cannot see quotes, after one confirming click, with no price shown, making the same call the document form's Submit makes today. That sits behind a second flag, off by default, and a test fails if the submission function appears anywhere else.
- A typed "yes" is never an approval. A click is verified in code: right person, in time, once, not stopped.
- The language model sees the request and file names, never file contents or channel threads.

## Not in this repository

The Arbitr Design System (only the few tokens, fonts and logo files the POC needs are vendored
under `poc/vendor/`), any credentials, and any `.env` file.

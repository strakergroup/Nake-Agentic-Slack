# Message to Wade (draft for Nake to send)

Subject: Arbitr agent for the Slack app: a branch for you to try in dev, nothing touched

Hi Wade,

I've been working on where the Slack app goes next, and I have something for your team to look at. Nothing of yours has been changed: no pushes to your repo, no change to the live Slack app or its listing, no credentials used. It was done on a local copy and lives in its own repo.

**What it is.** An agent layer for the Slack app, under the Arbitr name. Someone says what they need in plain words in a DM, the agent panel or an @-mention, and it uses the functions the app already has. It replaces the Watson intent matcher for free text. Shortcuts, `/straker`, every form and the Home tab stay exactly as they are.

**See the experience first (5 minutes).** A clickable, scripted demo: https://claude.ai/artifact/Sxx8yDUnwVW6E8bbGVFerL
Nine scenes, with presenter notes that explain the Slack platform constraints behind each choice.

**What the code is.** One new folder, `app/agent/`, and five small edits to existing files (35 lines in the Python files), all behind `AGENT_ENABLED`, which defaults to off. With it off the app behaves exactly as today. If the agent throws, Watson answers as before.

- `app/agent/core/` imports nothing from the rest of the app. It has 138 tests that run anywhere, and passes your ruff and pyright settings.
- `app/agent/adapters/` is the only part that touches your code. It was written by reading the source.

**What has and has not been proven.** The core is tested. The adapter has never run: the app needs your database and private packages, so it cannot start outside your network. I'd describe this as "tested core, reviewed wiring, ready for your dev environment", not as working. `docs/arbitr-agent-runbook.md` is a 20-step checklist with the expected result at each step; that checklist is the real test, and I expect some of my readings of the code to be wrong.

**On money.** The agent cannot accept, pay for or cancel anything. Quoted work still goes through your quote message and your Accept button. By default everyone is handed your existing document form. Behind a second flag (`AGENT_NATIVE_DOCUMENT_QUOTES`, off), people who can't see quotes get the same submission your form makes for them today, but in conversation and only after one confirming click with no price shown. A test fails if `enqueue_document_mt_submission` appears anywhere except inside that one click-gated function, or if the adapter references your acceptance or cancellation functions at all. Please review `app/agent/adapters/straker_tools.py` before turning that flag on.

**What I'd like from you.**
1. A look at the demo, and your honest reaction.
2. Someone to run the checklist on a dev Slack app (there is a separate `manifest.agent.yml`; please don't apply it to production, since switching to the agent view is one-way and the new scope forces a reinstall).
3. Answers to the ten questions at the end of the run-book. They are the places I had to guess.

**Where it lives.** A separate private repo in the org, so your repository is untouched: https://github.com/strakergroup/Nake-Agentic-Slack

- `main` has the spec, the POC source and the documents. The README is the map.
- `arbitr-agent` is a full copy of `slack-straker-translate` with the agent on top of your `master` at `594bff3d`.
- See exactly what it adds (13 commits, 51 files): https://github.com/strakergroup/Nake-Agentic-Slack/compare/upstream-master...arbitr-agent
- To try it in your repo: `git remote add nake-agent https://github.com/strakergroup/Nake-Agentic-Slack.git && git fetch nake-agent arbitr-agent && git checkout -b arbitr-agent nake-agent/arbitr-agent`. The same commits are in `handover/patches/` if you prefer patches.

**How we'll know it landed.** Your filled-in checklist. If steps 1, 2, 6, 8 and 16 pass, the approach holds and the rest is tuning. Steps 17 to 20 are the ones that involve money; they wait for your review.

Start with `docs/arbitr-agent.md`.

Thanks,
Nake

# Media Workflow V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Ticket:** [RAY-81819](https://app.clickup.com/t/86d4a891k)

**Goal:** Replace the three Slack media buttons with one Configure modal, two quotes, an optional SRT review/replace gate, and embed only after SRT approval — without double-charging.

**Architecture:** A pure `advance_media_workflow` reducer in slack-ray-translator owns legal transitions and returns commands. Slack/Ray adapters map clicks and callbacks to events and execute commands. `sup-subtitle-ai-cons` no longer auto-embeds after Quote 2; embed is a later `embed` job using the approved/replaced SRT file id.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, Slack Bolt, Redis media-quote session, pytest (`pipenv run pytest`). Consumer: existing `sup-subtitle-ai:media:asr` + `translate_only` / `embed` pipeline types.

## Global Constraints

- Feature branches: `RAY-81819_media-workflow-v2` from `origin/master` in **slack-ray-translator** and **sup-subtitle-ai-cons**. Never branch from or merge `uat`.
- Out of scope: zip of assets, 72h timeout (keep 12h `MEDIA_QUOTE_TTL_SECONDS`), Word/docx transcript layouts, speaker diarization, `sup-mt-service`.
- Thread “existing SRT → embed-only quote” stays as a leftover path, not the Configure flow.
- Review gate default **on**. Slack `file_input` lives in a **modal** opened by Replace (not on the thread message).
- Quote 1: transcription + source embed only if checked. Quote 2: AI translation + translated embed only if checked. Never `translate_embed` auto-chain after Quote 2.
- After source SRT approved: if `embed_source`, emit `start_source_embed`; if translation selected, also `post_quote2` (embed may run while Quote 2 is shown). Transcription-only ends after source embed (or immediately if no embed).
- Review gate off: `transcription_completed` / `translation_completed` auto-approve inside the reducer (no second handler path).
- Named error `MediaWorkflowTransitionError` for illegal events. No secrets in logs.
- Tests: reducer table tests with no Slack/Redis mocks. Listener tests stay thin.
- This repo uses Pipfile: `pipenv run pytest path -x`. Do not migrate to uv.

## Seams

1. `advance_media_workflow(session, event) -> MediaWorkflowDecision` — primary, pure.
2. Slack Configure modal + review/replace actions — map to events, execute commands.
3. Ray media callbacks — map result events to reducer events.
4. Consumer: `translate_only` then later `embed` with replaced `result_file_id`.

---

## File map

**Create (SRT):**
- `app/media/media_workflow.py` — stages, events, commands, session, reducer, `MediaWorkflowTransitionError`
- `tests/media/test_media_workflow.py` — table tests for the five walks plus illegal transitions

**Modify (SRT):**
- `app/slack/templates/messages.py` — `VideoOptionsMessage` → one Configure button
- `app/slack/templates/views.py` — unified Configure modal (workflow type, languages, embed checkboxes, review gate)
- `app/slack/media_quotes.py` — persist config flags; Quote 1/2 line items from flags not `pipeline_kind` embed enum
- `app/slack/media_quote_actions.py` — Quote 1 accept → `start_transcribe`; Quote 2 accept → `translate_only` only
- `app/slack/handlers/media.py` / listeners — Configure submit, review Approve/Replace
- `app/ray/events/media_pipeline_events.py` — after ASR/MT, reducer decides review vs auto-approve vs embed; stop posting “reupload is required”; stop auto-embed-ready copy before review
- `app/slack/listener_actions.py` — thread SRT during review is replace-by-filename, not a new embed quote

**Modify (consumer):**
- `app/pipeline/handler.py` — Quote 2 resume is `translate_only` only; embed waits for a later `embed` job with current `result_file_id`

---

### Task 1: Media workflow reducer

**Files:**
- Create: `app/media/media_workflow.py`
- Test: `tests/media/test_media_workflow.py`

**Interfaces:**
- Consumes: nothing (pure)
- Produces: `MediaWorkflowStage`, `MediaWorkflowType`, `MediaWorkflowEvent`, `MediaWorkflowCommand`, `MediaWorkflowConfig`, `MediaWorkflowSession`, `MediaWorkflowDecision`, `MediaWorkflowTransitionError`, `advance_media_workflow`

- [ ] **Step 1: Write the failing test** for Quote 1 accept

```python
from app.media.media_workflow import (
    MediaWorkflowCommand,
    MediaWorkflowEvent,
    MediaWorkflowStage,
    MediaWorkflowType,
    advance_media_workflow,
    make_media_workflow_session,
)


def test_quote1_accepted_starts_transcribe():
    session = make_media_workflow_session(
        workflow_type=MediaWorkflowType.TRANSCRIBE_ONLY,
        embed_source=False,
        embed_translated=False,
        review_gate=True,
    )
    decision = advance_media_workflow(session, MediaWorkflowEvent.QUOTE1_ACCEPTED)
    assert decision.session.stage == MediaWorkflowStage.TRANSCRIBING
    assert decision.commands == (MediaWorkflowCommand.START_TRANSCRIBE,)
```

- [ ] **Step 2:** `pipenv run pytest tests/media/test_media_workflow.py::test_quote1_accepted_starts_transcribe -x` — expect import/collection fail
- [ ] **Step 3:** Minimal types + reducer arm for `QUOTE1_ACCEPTED`
- [ ] **Step 4:** Re-run; expect PASS
- [ ] **Step 5:** Add table rows (one red-green cycle each) for:
  1. Transcribe only, no embed, gate off → transcription completed → `done` + `mark_done`
  2. Transcribe only, embed source, gate on → transcription completed → `awaiting_source_review` + `post_source_review`; approve → `start_source_embed`; embed completed → `done`
  3. Translate, no embed, gate off → transcription completed → `post_quote2`; quote2 accepted → `start_translate`; translation completed → `done`
  4. Translate, both embeds, gate on → replace stays in review; approve → `start_source_embed` + `post_quote2`; quote2 + translation review + `start_translated_embed`
  5. Cancel on Quote 1 → `cancelled`, no start commands
  6. Illegal event (approve during `transcribing`) raises `MediaWorkflowTransitionError`
  7. Gate off does not emit `post_source_review`

---

### Task 2: Quote line items from config flags

**Files:**
- Modify: `app/slack/media_quotes.py`
- Test: `tests/slack/test_media_quotes.py`

Quote 1 includes source embedding tokens only when `embed_source`. Quote 2 includes translated embedding tokens only when `embed_translated`. Stop using `PIPELINE_TRANSCRIBE_TRANSLATE_EMBED` as “embedding already paid on Quote 1” for the Configure flow.

---

### Task 3: Configure entry (one button + modal)

**Files:**
- Modify: `app/slack/templates/messages.py`, `app/slack/templates/views.py`, listeners/handlers
- Test: `tests/slack/test_message_templates.py`, `tests/slack/test_views.py`

Replace Transcribe / Transcribe & AI Translate / Embed Subtitles with Configure. Modal: workflow type, languages (shown if translation; `views.update` on type change), embed source, embed translated (translation + video only), review gate default on. Submit creates media-quote session with config flags and posts Quote 1.

Audio-only: hide embed checkboxes.

---

### Task 4: Review + Replace SRT

**Files:**
- Modify: `app/ray/events/media_pipeline_events.py`, Slack action handlers, `app/slack/listener_actions.py`
- Test: `tests/ray/events/test_media_pipeline_events.py`, listener tests

After ASR: if gate on, upload SRT + Approve & Continue + Replace. Replace opens modal with `file_input` (`filetypes=["srt"]`, `max_files=1`). Thread SRT during review: filename match → `SOURCE_SRT_REPLACED` / `TRANSLATED_SRT_REPLACED`, **no new embed quote**. Gate off: auto-approve via reducer. Do not post copy that makes edit mandatory. Do not post “video with embedded subtitles is ready” before embed actually completes after approval.

---

### Task 5: Stop auto-embed after Quote 2 (SRT + consumer)

**Files:**
- Modify: `app/slack/media_quote_actions.py` (`_resume_translate_phase` always `translate_only` for Configure flow)
- Modify consumer `app/pipeline/handler.py`: do not treat Configure Quote 2 as `translate_embed`
- After translation review (or auto-approve), SRT sets `result_file_id` (and per-language ids) then posts `embed` job
- Test: existing media pipeline event tests + consumer pipeline tests

Leftover thread embed-only quote unchanged.

---

### Task 6: Verification matrix

Run:

```bash
pipenv run pytest tests/media/test_media_workflow.py tests/slack/test_media_quotes.py tests/slack/test_message_templates.py tests/ray/events/test_media_pipeline_events.py -x
```

In consumer:

```bash
# repo-local pytest for pipeline handler embed/translate_only
```

Confirm the five walks from Task 1 still pass through adapters (Quote 1 line items, no second embed quote on thread replace).

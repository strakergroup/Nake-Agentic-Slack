# Optional Word Transcript Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Ticket:** [RAY-81850](https://app.clickup.com/t/86d4ahb9t) (subtask of [RAY-81819](https://app.clickup.com/t/86d4a891k))

**Goal:** Optional Word (`.docx`) transcript download on Configure, for transcribe-only and transcribe+translate, without extra fees and without changing the default SRT path.

**Architecture:** Configure stores `word_transcript_format` on the media-quote session and ASR `extra_data`. `sup-subtitle-ai-cons` does **one** ASR call: Whisper `whisper-1` (`verbose_json`) unless the format needs speakers, in which case it calls Azure OpenAI `gpt-4o-transcribe-diarize` (`diarized_json`, `chunking_strategy=auto`) instead. SRT is always speaker-free. Word is built from the same cue list and uploaded to the file API. slack-ray-translator downloads those file ids into the Slack thread after the SRTs. The media workflow reducer is unchanged.

**Tech Stack:** Python 3, Pydantic v2, Slack Block Kit, pytest via `pipenv run pytest`. Consumer: Azure OpenAI client, `python-docx`, existing `srt` package. Repos: `slack-straker-translate` and `sup-subtitle-ai-cons`.

## Global Constraints

- Branches: commit RAY-81850 work on the parent feature branch `RAY-81819_media-workflow-v2` in **both** repos (already checked out). Do not create `RAY-81850_word-transcript`. Never merge `uat` into these branches. Base for a later master PR is `origin/master` after RAY-81819 lands, or stack the PR on the V2 branch.
- This repo and the consumer use Pipfile. Run `pipenv run pytest path -x`. Do not migrate to uv.
- Default path stays SRT. Word is opt-in. No extra fee / pricing / quote line-item work.
- Word formats (all `.docx`): `text`, `speakers`, `timestamps`, `speakers_and_timestamps`. No extra SRT variants.
- Speakers are `Speaker 1` / `Speaker 2` / … (first-seen API label order). Never real names. Do not send `known_speaker_names` / `known_speaker_references`.
- Word for **source and each target language** when translating. Filenames match SRT: `clip.docx`, `clip_Spanish.docx`.
- SRT stays speaker-free so translate/embed/review are unchanged. Speakers exist only in Word. Translated Word copies `cue_speakers[i]` onto translated cue `i`.
- Not an extra ASR pass. Whisper-1 for SRT-only and Word `text` / `timestamps`. Speakers (`speakers` / `speakers_and_timestamps`) replace Whisper with **one** `gpt-4o-transcribe-diarize` call (plus existing size chunking when the WAV is over `openai_api_limit_mb`).
- Prerequisite (ops, not this code): Foundry deployment of `gpt-4o-transcribe-diarize`. Config `DIARIZE_MODEL`. If speakers are requested and the model is unset/empty, fail the task with named error `DiarizeModelNotConfigured` — do not silently drop speakers.
- Do not extend `advance_media_workflow`. Word is extra_data + delivery only.
- v1: do **not** rebuild Word after SRT replace. Replacement updates SRT/embed only. Document that in the ClickUp comment when shipping.
- Named errors, not bare `Exception` / `ValueError` / stringly `detail=`. `SecretStr` for credentials. Never log tokens.
- Tests: no implementation-coupled Slack/Azure mocks beyond existing seams. Consumer tests fake the OpenAI response object.

## File map

**Create (consumer `sup-subtitle-ai-cons`):**
- `app/services/transcript_cues.py` — `TranscriptCue`, `WordTranscriptFormat`, parse Whisper/diarize → cues, cues → SRT, cues → docx bytes, speaker label mapping
- `tests/test_transcript_cues.py`

**Modify (consumer):**
- `Pipfile` — add `python-docx`
- `app/config.py` + `.env.example` — `diarize_model` from `DIARIZE_MODEL`
- `app/models/schemas.py` — `word_transcript_format`, `word_source_file_id`, `word_translated_file_ids`, `cue_speakers` on `TranscriptionTaskExtraData`
- `app/services/transcription_service.py` — return cues; branch Whisper vs diarize
- `app/pipeline/handler.py` — upload source docx; merge extra_data; after MT upload translated docx
- `tests/test_transcription_service.py`, `tests/test_pipeline_handler.py`

**Modify (SRT `slack-straker-translate`):**
- `app/media/word_transcript.py` — `WordTranscriptFormat` enum only (shared values; no docx lib in this repo)
- `app/slack/templates/views.py` — optional Word checkbox + format radio
- `app/slack/media_configure.py` — parse + persist format
- `app/slack/handlers/media.py` + `app/slack/listeners.py` — dispatch_action to show/hide format radio
- `app/slack/media_quote_actions.py` — copy format into ASR extra_data
- `app/saq_jobs/tasks.py` + `app/saq_jobs/dispatch.py` — upload source `.docx` after source SRT
- `app/ray/events/media_pipeline_events.py` — upload translated `.docx` after each translated SRT
- Tests: `tests/slack/test_views.py`, `tests/slack/test_media_configure.py`, `tests/saq_jobs/test_tasks.py`, `tests/ray/events/test_media_pipeline_events.py`

---

### Task 1: Cue model, Word formats, docx renderer (consumer)

**Files:**
- Create: `app/services/transcript_cues.py`
- Test: `tests/test_transcript_cues.py`
- Modify: `Pipfile` (add `python-docx`)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class WordTranscriptFormat(StrEnum)` with `TEXT = "text"`, `SPEAKERS = "speakers"`, `TIMESTAMPS = "timestamps"`, `SPEAKERS_AND_TIMESTAMPS = "speakers_and_timestamps"`
  - `def needs_diarize(fmt: WordTranscriptFormat | None) -> bool`
  - `@dataclass(frozen=True) class TranscriptCue` with `start: timedelta`, `end: timedelta`, `text: str`, `speaker_index: int | None`
  - `def cues_from_whisper_verbose(response: Any) -> list[TranscriptCue]`
  - `def cues_from_diarized_json(response: Any) -> list[TranscriptCue]`
  - `def cues_to_srt(cues: Sequence[TranscriptCue]) -> str` — ignores speakers
  - `def cues_to_docx_bytes(cues: Sequence[TranscriptCue], fmt: WordTranscriptFormat) -> bytes`
  - `def speaker_label(index: int) -> str` → `"Speaker 1"`
  - `def overlay_speakers(cues: Sequence[TranscriptCue], speaker_indexes: Sequence[int | None]) -> list[TranscriptCue]`
  - `class DiarizeModelNotConfigured(Exception)`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import timedelta

from app.services.transcript_cues import (
    TranscriptCue,
    WordTranscriptFormat,
    cues_to_docx_bytes,
    cues_to_srt,
    needs_diarize,
    overlay_speakers,
    speaker_label,
)


def test_needs_diarize_only_for_speaker_formats():
    assert needs_diarize(None) is False
    assert needs_diarize(WordTranscriptFormat.TEXT) is False
    assert needs_diarize(WordTranscriptFormat.TIMESTAMPS) is False
    assert needs_diarize(WordTranscriptFormat.SPEAKERS) is True
    assert needs_diarize(WordTranscriptFormat.SPEAKERS_AND_TIMESTAMPS) is True


def test_cues_to_srt_omits_speakers():
    cues = [
        TranscriptCue(timedelta(seconds=1), timedelta(seconds=2), "Hello", 1),
        TranscriptCue(timedelta(seconds=2), timedelta(seconds=3), "Hi", 2),
    ]
    body = cues_to_srt(cues)
    assert "Speaker" not in body
    assert "Hello" in body
    assert "Hi" in body


def test_overlay_speakers_by_index():
    cues = [
        TranscriptCue(timedelta(0), timedelta(seconds=1), "Hola", None),
        TranscriptCue(timedelta(seconds=1), timedelta(seconds=2), "Adiós", None),
    ]
    out = overlay_speakers(cues, [1, 2])
    assert [c.speaker_index for c in out] == [1, 2]
    assert [c.text for c in out] == ["Hola", "Adiós"]


def test_docx_text_has_paragraphs_no_speakers_or_times():
    from docx import Document
    import io

    cues = [
        TranscriptCue(timedelta(seconds=1), timedelta(seconds=2), "Hello", 1),
        TranscriptCue(timedelta(seconds=2), timedelta(seconds=3), "Hi", 2),
    ]
    doc = Document(io.BytesIO(cues_to_docx_bytes(cues, WordTranscriptFormat.TEXT)))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert texts == ["Hello", "Hi"]


def test_docx_speakers_and_timestamps():
    from docx import Document
    import io

    cues = [
        TranscriptCue(timedelta(seconds=1), timedelta(seconds=2), "Hello", 1),
        TranscriptCue(timedelta(seconds=2), timedelta(seconds=3), "Hi", 2),
    ]
    doc = Document(
        io.BytesIO(
            cues_to_docx_bytes(cues, WordTranscriptFormat.SPEAKERS_AND_TIMESTAMPS)
        )
    )
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    assert texts[0] == "Speaker 1"
    assert "00:00:01" in texts[1]
    assert texts[2] == "Hello"
    assert texts[3] == "Speaker 2"
```

Also cover `cues_from_whisper_verbose` / `cues_from_diarized_json` with SimpleNamespace segments (`id`, `start`, `end`, `text`) and diarize segments (`start`, `end`, `text`, `speaker` as `"A"` / `"B"` mapped to 1 / 2 in first-seen order). Skip empty text.

- [ ] **Step 2: Run tests to verify they fail**

Run (consumer): `pipenv run pytest tests/test_transcript_cues.py -x`

Expected: FAIL with import error or missing module.

- [ ] **Step 3: Implement**

`needs_diarize`: True iff format is `SPEAKERS` or `SPEAKERS_AND_TIMESTAMPS`.

`cues_from_whisper_verbose`: same filter as `_verbose_json_to_srt` today (`segment.text.strip()`), `speaker_index=None`.

`cues_from_diarized_json`: iterate `response.segments` (or `response.diarized_segments` if that is what the SDK returns — pin to whatever the Azure SDK object actually has in a unit test with a stub). Map speaker ids with a `dict[str, int]` first-seen → 1-based index. `speaker_label(1) == "Speaker 1"`.

`cues_to_srt`: `srt.Subtitle(index=i, start, end, content=text)` then `srt.compose`. No speaker in content.

Docx layout (one cue at a time, no merge):

| format | paragraphs per cue |
|---|---|
| `text` | `text` |
| `speakers` | `Speaker N` (omit line if `speaker_index` is None), then `text` |
| `timestamps` | `00:00:01,000 → 00:00:02,000` using `srt.timedelta_to_srt_timestamp`, then `text` |
| `speakers_and_timestamps` | speaker line, timestamp line, text |

Write via `Document()`, `document.save(BytesIO())`.

`overlay_speakers`: zip cues with indexes; extra indexes ignored; missing indexes stay `None`.

Add `python-docx` to `[packages]` in `Pipfile`, then `pipenv lock && pipenv sync --dev`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pipenv run pytest tests/test_transcript_cues.py -x`

Expected: PASS

- [ ] **Step 5: Commit** (consumer)

```bash
git add Pipfile Pipfile.lock app/services/transcript_cues.py tests/test_transcript_cues.py
git commit -m "$(cat <<'EOF'
feat(RAY-81850): add cue model and Word transcript renderer.

EOF
)"
```

---

### Task 2: Whisper vs diarize ASR (consumer)

**Files:**
- Modify: `app/config.py` — `diarize_model: str = ""`
- Modify: `.env.example` — `DIARIZE_MODEL=`
- Modify: `app/services/transcription_service.py`
- Modify: `tests/test_transcription_service.py`
- Modify: `tests/conftest.py` if a new env var is required at import

**Interfaces:**
- Consumes: `TranscriptCue`, `cues_from_*`, `cues_to_srt`, `needs_diarize`, `DiarizeModelNotConfigured`, `WordTranscriptFormat`
- Produces: `TranscriptionService.transcribe(audio_path: Path, *, word_format: WordTranscriptFormat | None = None) -> tuple[list[TranscriptCue], str, str | None]` returning `(cues, srt_content, detected_language)`

- [ ] **Step 1: Write the failing test** that speakers format calls diarize, not whisper

Stub `client.audio.transcriptions.create` and assert:

- `word_format=None` → `model=config.model` (`whisper-1`), `response_format="verbose_json"`, no `chunking_strategy`
- `word_format=WordTranscriptFormat.TEXT` → same Whisper call
- `word_format=WordTranscriptFormat.SPEAKERS` → `model=config.diarize_model`, `response_format="diarized_json"`, `chunking_strategy="auto"`
- `word_format=SPEAKERS` and `config.diarize_model == ""` → raises `DiarizeModelNotConfigured`

Keep existing chunked Whisper tests working: after the signature change they must pass `word_format=None` by default.

- [ ] **Step 2: Run to verify fail**

Run: `pipenv run pytest tests/test_transcription_service.py -x`

Expected: FAIL on new kwargs / missing diarize branch.

- [ ] **Step 3: Implement**

`config.diarize_model: str = ""` (env `DIARIZE_MODEL`). Document in `.env.example` as the Azure **deployment name**.

Change `transcribe` / `_transcribe_simple` / `_transcribe_chunked` / `_transcribe_chunk_from_file` to take `word_format`.

If `needs_diarize(word_format)`:
- if not `config.diarize_model.strip()`: raise `DiarizeModelNotConfigured`
- create call uses `model=config.diarize_model`, `response_format="diarized_json"`, `chunking_strategy="auto"`
- parse with `cues_from_diarized_json`
- language: `whisper_language_to_straker_code(getattr(response, "language", None))` — if the diarize payload has no language, return `None` (existing MT fallback)

Else:
- existing Whisper `verbose_json` path, parse with `cues_from_whisper_verbose`
- keep `_verbose_json_to_srt` as a thin wrapper around cues or delete it once callers use `cues_to_srt`

Chunked diarize: reuse `_prepare_chunks_to_disk` + offset combination, but combine **cues** (adjust `start`/`end` by chunk offset) rather than re-parsing SRT. Speaker indexes are **per chunk** (first-seen in that chunk). Do not try to match speakers across 20MB chunks.

Return `(cues, cues_to_srt(cues), detected_language)`. Empty cues → `TranscriptionError("Transcription resulted in empty content")` as today.

Do not log audio, API keys, or bot tokens.

- [ ] **Step 4: Tests pass**

Run: `pipenv run pytest tests/test_transcription_service.py tests/test_transcript_cues.py -x`

- [ ] **Step 5: Commit** (consumer)

```bash
git commit -m "$(cat <<'EOF'
feat(RAY-81850): switch ASR to diarize only when Word speakers are requested.

EOF
)"
```

---

### Task 3: Persist Word file ids on the ASR task (consumer)

**Files:**
- Modify: `app/models/schemas.py`
- Modify: `app/pipeline/handler.py` (`_update_task_status`, `_handle_transcribe`)
- Test: `tests/test_pipeline_handler.py`

**Interfaces:**
- Consumes: `word_format` from `task.extra_data.word_transcript_format`
- Produces: source `.docx` uploaded via `file_service.upload`; `extra_data.word_source_file_id`, `extra_data.cue_speakers` merged onto the task JSON. `result_file_id` remains the SRT.

- [ ] **Step 1: Failing test**

When extra_data has `word_transcript_format="text"`, after a successful transcribe the handler must:

1. still upload `clip.srt` and set `result_file_id` to that id
2. upload `clip.docx` with content type `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
3. merge `word_source_file_id` and `cue_speakers` (list of `None` for Whisper) into `extra_data`

When format is absent/None: **no** docx upload.

Stub `transcription_service.transcribe` to return one cue; stub `file_service.upload` to return `"srt-1"` then `"docx-1"`.

- [ ] **Step 2: Run to verify fail**

Run: `pipenv run pytest tests/test_pipeline_handler.py -k word -x`

- [ ] **Step 3: Implement**

Add to `TranscriptionTaskExtraData`:

```python
word_transcript_format: Optional[Literal[
    "text", "speakers", "timestamps", "speakers_and_timestamps"
]] = None
word_source_file_id: Optional[str] = None
word_translated_file_ids: Optional[dict[str, str]] = None
cue_speakers: Optional[list[int | None]] = None
```

Extend `_update_task_status` with `extra_data: dict[str, Any] | None = None`. If provided, merge onto `task.extra_data` (dict copy, update keys, write back). Never replace the whole blob.

In transcribe handler, after SRT upload:

```python
fmt_raw = extra.word_transcript_format if extra else None
fmt = WordTranscriptFormat(fmt_raw) if fmt_raw else None
# transcribe(..., word_format=fmt)
if fmt is not None:
    docx_name = f"{Path(file_name).stem}.docx"
    docx_path = temp_path / docx_name
    docx_path.write_bytes(cues_to_docx_bytes(cues, fmt))
    word_id = await self.file_service.upload(
        docx_path,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=docx_name,
    )
    extra_update = {
        "word_source_file_id": word_id,
        "cue_speakers": [c.speaker_index for c in cues],
        "word_transcript_format": fmt.value,
    }
```

Pass `extra_update` into the completed `_update_task_status` merge.

Parse `word_transcript_format` from `task_info.extra_data` **before** transcribe so the ASR branch is correct.

- [ ] **Step 4: Tests pass**

Run: `pipenv run pytest tests/test_pipeline_handler.py tests/test_transcription_service.py -x`

- [ ] **Step 5: Commit** (consumer)

```bash
git commit -m "$(cat <<'EOF'
feat(RAY-81850): upload source Word transcript next to the SRT.

EOF
)"
```

---

### Task 4: Translated Word from MT SRTs (consumer)

**Files:**
- Modify: `app/pipeline/handler.py` (`_handle_translate_only` after `delivered` is known)
- Test: `tests/test_pipeline_handler.py`

**Interfaces:**
- Consumes: `extra_data.word_transcript_format`, `extra_data.cue_speakers`, delivered translated SRT file ids
- Produces: `extra_data.word_translated_file_ids: dict[str, str]`

- [ ] **Step 1: Failing test**

Translate-only with `word_transcript_format="speakers"`, `cue_speakers=[1, 2]`, two delivered SRT ids. After success, `file_service.upload` was called once per target with `.docx` names, and extra_data contains `word_translated_file_ids={"es": "docx-es", "fr": "docx-fr"}`.

If format is None, no extra uploads.

If translated SRT cue count ≠ `len(cue_speakers)`, overlay what fits; do not fail the translation.

- [ ] **Step 2: Run to verify fail**

- [ ] **Step 3: Implement**

After `delivered` is set, if format is set:

For each `lang, srt_id` in `delivered`:
1. download SRT
2. parse to cues (`speaker_index=None`)
3. `overlay_speakers(cues, extra_data.cue_speakers or [])`
4. `cues_to_docx_bytes`
5. upload `{stem}_{lang}.docx` (stem from `task.file_name`; lang code is fine here — Slack will rename with the display language)

Merge `word_translated_file_ids` via `_update_task_status`.

Word generation failure for one language: log ERROR and skip that language; do not fail the whole translation (SRT already delivered).

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit** (consumer)

```bash
git commit -m "$(cat <<'EOF'
feat(RAY-81850): build translated Word transcripts from MT SRTs and cue speakers.

EOF
)"
```

---

### Task 5: Configure modal Word checkbox + format radio (SRT)

**Files:**
- Create: `app/media/word_transcript.py` — `WordTranscriptFormat` StrEnum with the same four values (do not import from the consumer)
- Modify: `app/slack/templates/views.py` — `video_configure_media_modal`
- Modify: `app/slack/handlers/media.py` — preserve Word state on workflow-type rebuild; new handler for Word checkbox dispatch
- Modify: `app/slack/listeners.py` — `@app.action("word_transcript_options")`
- Test: `tests/slack/test_views.py`, `tests/slack/test_media_configure.py`

**Interfaces:**
- Consumes: existing `video_configure_media_modal(...)` kwargs
- Produces: extra kwargs `word_transcript: bool = False`, `word_transcript_format: str | None = None`. Checkbox `block_id=word_transcript`, `action_id=word_transcript_options`, value `word_transcript`. When checked, InputBlock `block_id=word_format`, radio `action_id=word_format_options`, values matching the enum, default `text`.

- [ ] **Step 1: Failing tests**

```python
def test_configure_modal_hides_word_format_until_checked():
    modal = video_configure_media_modal(channel_id="C1", files=self._files())
    ids = _modal_block_ids(modal)
    assert "word_transcript" in ids
    assert "word_format" not in ids


def test_configure_modal_shows_format_radio_when_word_checked():
    modal = video_configure_media_modal(
        channel_id="C1",
        files=self._files(),
        word_transcript=True,
    )
    fmt = _modal_block(modal, "word_format")
    values = [opt["value"] for opt in fmt["element"]["options"]]
    assert values == [
        "text",
        "speakers",
        "timestamps",
        "speakers_and_timestamps",
    ]
    assert fmt["element"]["initial_option"]["value"] == "text"
```

Labels (i18n via `_()`):

- Checkbox: `Word transcript`
- Radio heading: `Word format`
- Options: `Text`, `Speakers`, `Timestamps`, `Speakers and timestamps`

Word checkbox InputBlock: `optional=True`, `dispatch_action=True` so checking it rebuilds the modal (same pattern as workflow type).

Rebuild handlers must pass through current Word checkbox + selected format (read from `view.state`) so switching Transcription only ↔ translation does not drop the Word choice.

- [ ] **Step 2: Run**

Run: `pipenv run pytest tests/slack/test_views.py -k configure -x`

- [ ] **Step 3: Implement** the modal + dispatch handlers. Unchecked Word → omit format block; parse treats format as `None`.

- [ ] **Step 4: Tests pass** including existing configure modal tests.

- [ ] **Step 5: Commit** (SRT)

```bash
git commit -m "$(cat <<'EOF'
feat(RAY-81850): add optional Word transcript controls to Configure.

EOF
)"
```

---

### Task 6: Parse submit and pass extra_data through Quote 1 (SRT)

**Files:**
- Modify: `app/slack/media_configure.py` — `VideoConfigureMediaSelection.word_transcript_format: str | None`
- Modify: `configure_media_quote_fields` extra dict
- Modify: `app/slack/media_quote_actions.py` — copy `session["word_transcript_format"]` into ASR `extra_data` on Quote 1 accept
- Test: `tests/slack/test_media_configure.py`, `tests/slack/test_media_quote_actions.py`

**Interfaces:**
- Produces: quote session key `word_transcript_format` (`None` or one of the four strings). ASR extra_data same key. Unchecked Word → key omitted or `None` so consumer skips docx.

- [ ] **Step 1: Failing tests**

Parse view with Word checkbox selected and format `speakers` → `selection.word_transcript_format == "speakers"`.

Unchecked → `None`.

`configure_media_quote_fields` includes `"word_transcript_format": "speakers"` inside `extra` only when set.

Quote 1 accept extra_data includes the same key (patch `create_asr_task` and assert).

If format radio is missing but checkbox is on (Slack race), default to `"text"`.

Invalid format value → treat as `None` (do not 500 the modal).

- [ ] **Step 2–4: Implement and pass** `pipenv run pytest tests/slack/test_media_configure.py tests/slack/test_media_quote_actions.py -x`

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(RAY-81850): persist Word transcript format on the media quote and ASR task.

EOF
)"
```

---

### Task 7: Slack delivery of source and translated docx (SRT)

**Files:**
- Modify: `app/saq_jobs/dispatch.py` / `enqueue_transcription_upload` — optional `word_file_id: str | None`, `word_file_name: str | None`
- Modify: `app/saq_jobs/tasks.py` — `slack_upload_transcription` uploads Word after SRT when those args are set
- Modify: `app/ray/events/media_pipeline_events.py` — pass `task_info.extra_data["word_source_file_id"]` on transcription complete; on translation complete upload each `word_translated_file_ids[lang]` as `{stem}_{lang_name}.docx` immediately after that language’s SRT
- Test: `tests/saq_jobs/test_tasks.py`, `tests/ray/events/test_media_pipeline_events.py`

**Interfaces:**
- Word upload is best-effort relative to SRT: if Word download/upload fails, log ERROR and still deliver SRT + review buttons. Do not fail the media quote.

- [ ] **Step 1: Failing tests**

`slack_upload_transcription` with `word_file_id="docx-1"` and `word_file_name="clip.docx"` calls `upload_file_to_slack_memory_efficient` twice (SRT then docx).

`handle_transcription_complete` reads `word_source_file_id` from extra_data and passes it to enqueue.

`handle_translation_complete` with `word_translated_file_ids={"es": "docx-es"}` uploads `clip_Spanish.docx` (use existing `get_auto_translate_language_name`).

When ids are missing, behaviour matches today (SRT only).

- [ ] **Step 2–4: Implement and pass** the targeted pytest files.

Rename downloaded Word files before Slack upload the same way SRT is renamed, so the extension survives.

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(RAY-81850): deliver Word transcripts in the Slack thread after SRTs.

EOF
)"
```

---

### Task 8: Cross-repo verification

- [ ] **Consumer:** `pipenv run pytest tests/test_transcript_cues.py tests/test_transcription_service.py tests/test_pipeline_handler.py -x`
- [ ] **SRT:** `pipenv run pytest tests/slack/test_views.py tests/slack/test_media_configure.py tests/slack/test_media_quote_actions.py tests/saq_jobs/test_tasks.py tests/ray/events/test_media_pipeline_events.py tests/media/test_media_workflow.py -x`
- [ ] Confirm `tests/media/test_media_workflow.py` is unchanged in behaviour (reducer still has no Word fields).
- [ ] Manual UAT only after Foundry `DIARIZE_MODEL` is set on the consumer secret: Configure with Word off (SRT-only regression); Word text; Word speakers (need diarize deploy); transcribe+translate Word for one target.

Do not merge to `uat` until the diarize deployment exists if you want to demo speakers. Text/timestamps Word can ship on Whisper alone.

---

## Ops prerequisite (not a code task)

Azure Foundry: deploy `gpt-4o-transcribe-diarize` on the same resource the consumer already uses. Set `DIARIZE_MODEL` to that **deployment name**. If `diarized_json` 400s, bump `AZURE_OPENAI_API_VERSION` on the consumer to a preview that supports the diarize transcription API (try after the first UAT failure; do not guess a version in code now). Use skill `envvar-tool` only when asked to put the secret on UAT k8s.

Speaker identity is not stable across 20MB WAV chunks. That is acceptable for v1.

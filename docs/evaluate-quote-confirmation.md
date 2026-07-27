# Evaluate Quote Confirmation (RAY-79115)

Slack Human Translation submissions use a **sequential quote confirmation** flow instead of immediate processing. Slack no longer exposes a standalone post-MT Quality Evaluation quote; after AI Translation, the staged quote combines Quality Evaluation with the Human Translation quote.

## Behaviour

Human Translation (`evaluate_job_human`) submissions:

- Submit without a fixed workflow by default so CVC can build the MT → QE → publish workflow
- Set `confirmation_required=true` on `/evaluate/create`
- Handle staged Slack events and quote accept actions
- Treat `ready_for_qe_quote` as the combined Quality Evaluation + Human Translation quote, not as a standalone QE view

## Admin-only quote UX (`QUOTE_ADMIN_ONLY`)

When `QUOTE_ADMIN_ONLY=true` (default), only Verify group **Admin/Owner** members see Accept/Adjust quote UI. Role comes from `get_client_type` / `user_may_receive_quotes`.

| Role | AI | QE | Human Translation |
|------|----|----|-------------------|
| Admin / Owner | Quote → accept | Combined **QE + HT** quote at worst-case QE tier → accept | Included in that combined accept |
| Non-admin | Auto-proceed (no quote) | Auto-purchase QE (no quote) | Separate HT quote (`HumanJobQuoteMessage`) after QE |

Non-admin evaluate jobs still set `confirmation_required=true` so AI/QE staging works, and set `extra_info.slack_ht_quote_after_qe=true`. CVC’s synthetic workflow then includes QE but **omits** the `human-verification` node so HT is not created before the Slack HT quote. PDF pre-quotes are skipped for non-admins (convert/create runs immediately with the same flag).

Evaluate submit resolves the member via `get_ray_client` (workspace super-group link is not required). Definite Verify `401`/`402`/`403` on auto AI/QE releases the Redis event claim so a later redelivery can retry. Media non-admin auto-start falls back to posting the Accept quote when balance/login blocks start.

Document MT and Media use the same Admin/Owner gate: admins see quotes; non-admins skip quote UX and auto-start processing.

## Resubmission prevention

QE and HT modal submits use a 24h dedupe gate in `process_evaluation_submission` (`check_and_record_evaluate_submission_async`). Unlike Document MT (per target language), evaluate treats the **full target set** as one unit for simpler grouping:

- Duplicate only when the same user/team/file content/name is submitted again with the **same source and exact same target-language set** (order-independent)
- Overlapping-but-different sets are allowed (e.g. prior `fr+de`, new `fr+es` proceeds as a full new job)
- Hash: `sha256("evaluate:{content}:{source}:{sorted_targets}")` so evaluate rows in `slack_file_translation_submissions` do **not** cross-block Document MT
- QE and HT share that evaluate namespace
- `failed` unlocks retry; `created` / `completed` continue to block within 24h
- Recording runs only on the path that creates work (non-PDF submit, or PDF **after** quote accept). The PDF pre-quote display does not insert rows, so Accept can re-enqueue safely
- All files duplicate: DM the user and skip `/evaluate/create` / PDF convert. Multi-file partial: DM for blocked files and submit only remaining files

```mermaid
flowchart TD
    modal[QE_or_HT_modal_submit]
    saq[process_evaluation_submission]
    dl[Download_and_validate]
    pdfGate{PDF_and_not_preaccepted}
    quote[Show_PDF_AI_quote]
    dedupe[check_and_record_evaluate_submission_per_file_target_set]
    block[DM_duplicate_message]
    submit[submit_evaluation_job_or_PDF_convert]
    modal --> saq --> dl --> pdfGate
    pdfGate -->|yes| quote
    pdfGate -->|no| dedupe
    dedupe -->|all_files_dup| block
    dedupe -->|some_files_new| submit
```

## Flow

```mermaid
sequenceDiagram
    participant User as SlackUser
    participant SRT as slack-ray-translator
    participant CVA as cloud-verify-api
    participant CVC as cloud-verify-consumer

    User->>SRT: Submit Human Translation or Quality Evaluation modal
    SRT->>User: Analyzing content...
    CVC->>CVA: ES_COMPLETED (pause)
    CVA->>SRT: verify:slack:evaluate:ready_for_ai_quote
    SRT->>User: AI Translation quote + Adjust Request / Accept
    User->>SRT: Remove target languages
    SRT->>User: Recalculate selected PDF + AI cost
    User->>SRT: Accept selected scope
    SRT->>CVA: POST /evaluate/proceed (AI pair scope)
    CVC->>CVA: MT complete
    CVA->>SRT: verify:slack:evaluate:ready_for_qe_quote
    SRT->>User: Update AI quote to complete + post new QE + Human Translation quote
    User->>SRT: Deselect file/language pairs or Accept
    SRT->>CVA: POST /evaluate/{uuid}/proceed-quality-evaluation (same QE + HT scope)
    CVC->>SRT: verify:slack:evaluate:complete
    SRT->>CVA: Submit Human Translation job automatically
    SRT->>User: Human Translation submitted
```

## Slack events

| Event | When |
|-------|------|
| `verify:slack:evaluate:ready_for_ai_quote` | Extract complete, awaiting AI quote accept |
| `verify:slack:evaluate:ready_for_qe_quote` | MT complete, awaiting combined QE + Human Translation quote accept |
| `verify:slack:evaluate:complete` | Full path complete, or `ai_only` when QE skipped |

Published by cloud-verify-api (`src/evaluate/slack_events.py`) via stream proxy.
redis-slack-consumer must subscribe to the new event names.

## cloud-verify-consumer coordination

Separate PR required in **cloud-verify-consumer** — full contract documented in `cloud-verify-api/docs/cvc-evaluate-quote-coordination.md`:

1. Do not auto-proceed Slack jobs with `confirmation_required=true` at `ES_COMPLETED`
2. Call `POST /evaluate/{uuid}/slack/ready-for-ai-quote` when extract completes
3. On proceed with `skip_quality_evaluation` / `mt_only`: run MT, pause before QE, then call `POST /evaluate/{uuid}/slack/ready-for-qe-quote`
4. On `proceed-quality-evaluation` / `qe_only`: resume the deferred workflow into QE scoring
5. Emit `verify:slack:evaluate:complete` with `ai_only=true` when user skips QE quote

## PDF conversion fee

PDF submissions use a pre-job quote so Adobe PDF-to-DOCX conversion is not paid before the user accepts. SRT records each file's PDF page count and derives the AI Translation token cost from Slack file metadata, then shows PDF conversion cost first (25 tokens/page), followed by AI Translation prices grouped by source filename and target language. **Adjust Request** lets the user choose independent file/language pairs; AI cost is recalculated for the selected pairs while PDF conversion cost covers only files that remain in scope. Cost refreshes rewrite checkbox `initial_options` from the live modal state so Slack `views.update` cannot re-select deselected pairs. On accept, SRT forwards `ai_translation_filename_and_languages` (and preaccepted quote metadata) through PDF convert or direct evaluate create — including when the remaining selection is non-PDF only. CVA maps those upload filenames onto `extra_info.ai_translation_file_and_languages` after the files are saved, so MT/QE only process the chosen pairs instead of the full file×language cross-product. int-slack also remaps `.pdf` selections onto the converted `.docx` upload names.

Direct AI Translate (Document MT) quotes use the same Service Quote layout: optional PDF conversion cost first, then AI Translation cost, and total cost. Document MT intro copy states that running AI translation will incur the displayed cost. Staged evaluate (HT) quotes use “AI pre-translation before human review will incur the following cost:” and always show an **AI Translation:** section header beneath PDF conversion so file/language rows are clearly AI costs. Evaluate staged quotes and Document MT quotes no longer use “Estimated” labels or aggregate-only totals when PDF conversion applies.

## Pricing display

Evaluate quote messages display formatted USD costs for all clients. Token counts are still used internally for debit/proceed calls, but Slack no longer renders token-based quote labels such as "Token cost" or "Total tokens".

Only staged AI Translation quotes expose **Adjust Request**; direct AI Translate remains unchanged. The quote groups pricing by source filename and then shows each target language and its pair-specific price, matching the Human Translation quote's layout without showing a completion date. Guidance above the Accept/Adjust actions tells users to review the cost and use Adjust Request to remove languages and/or source files. Language labels are resolved from CVA's Verify language catalogue when the evaluation response contains only UUIDs, including quotes generated by the standalone SAQ worker. The staged modal uses the same file → language-price layout, with file headings read-only and each language rendered as an independent checkbox row beneath its file. Selections are per file/language pair (`file_uuid:language_uuid`); deselecting a language on one file does not change that language on other files. At least one pair is required. The modal shows PDF conversion (when applicable) and **Total cost** only — it does not repeat a separate AI Translation cost line. Opening Adjust Request stores the original quote `channel_id` and `message_ts` in modal private metadata so Accept Quote can update the original Slack message even when the view submission body has no message context. The modal's **Accept Quote** submit action persists that scope and immediately starts the same acceptance flow as the original message button; the original quote message is updated in place to remove its actions, keep **Total cost**, show deselected languages as **Cancelled**, and omit any estimated completion date. Extracted jobs use CVA's per-pair quote details for the displayed costs. Acceptance sends the same selected pairs to CVA, preventing the quote display, debit, and processing scope from diverging. Adjustment is rejected once quote acceptance starts.

## Combined QE + Human Translation quote

After the AI Translation quote is accepted and MT completes, SRT updates the original AI quote message to an AI-complete state (status only — no download action; copy: “AI translation is complete. Review the human translation quote below.”) and posts a new Human Translation quote that includes the Quality Evaluation fee, an “Adjust Request” button, and a “Download AI Translations” button for the MT outputs. Subsequent HT accept / post-QE updates target the new message (`message_ts`), while `ai_message_ts` keeps the AI quote reference. The quote is restricted to pairs that completed AI Translation. Adjust Request only offers checkboxes for pairs that have an active cost row, so file-specific language splits do not show the other language at USD 0.00. Pre-QE estimates and the Adjust Request modal label the total as **Maximum Total Cost** and show a short Accept Quote helper line above the action buttons (“Click **Accept Quote** to send your translation for human review. A **discount** will be applied to the quote above based on the quality of the AI translation.”). Quote amounts use a single USD display shape (`USD 40.00`, with a space and no `$` / `US$` / `USD$` variants). Slack uses CVA's per-pair QE quote details and embeds each exact QE fee in its matching Human Translation row instead of evenly distributing an aggregate fee. Deselecting a row therefore removes that pair's exact QE fee and HT price. Because QE has not run yet, SRT requests `/automation/service/pricing` with `assumed_quality_tier=bad` for backend pricing, but the Slack quote **hides** the per-target quality line and aggregate `(saved USD X)` until QE scoring completes. After accept, the quote status reads: “Quote accepted! Submitting for human translation and calculating your final discount based on AI quality...”

When the user accepts this combined quote, SRT purchases QE via `POST /evaluate/{uuid}/proceed-quality-evaluation` and sends the same remaining pair list as both `quality_evaluation_file_and_languages` and `human_translation_file_and_languages`. CVA validates both lists against the AI-translated scope and stores them on the job. CVC evaluates only the QE scope and later creates Human Translation only for the HT scope. SRT does not submit HT after QE; it only updates the Slack quote when the backend workflow reports completion. There is no second user accept step after QE. Accept / Adjust Request submit refreshes keep the full language grid with **Cancelled** rows for deselected pairs (including asymmetric per-file selections) so intermediary “Accepting quote…” panels never show phantom `USD 0.00` lines.

When `verify:slack:evaluate:complete` arrives, SRT validates that refreshed totals include the exact QE charges for the **selected** file/language pairs only, then **updates the original HT/QE quote message in place** with post-QE line amounts, a single Estimated Completion date, and the final-cost status (`Final cost after AI quality evaluation: USD X` plus submission confirmation). No separate follow-up message is posted. The HT quote `message_ts` is taken from the Slack SDK post response when the combined quote is first posted (not only from plain dict responses), and later Redis saves preserve that distinct HT timestamp so complete can `chat.update` instead of reposting. The AI Translation quote message stays in place above with the same per-file/per-language layout (deselected Adjust Request pairs shown as **Cancelled**) and an AI-complete status line.

## Human verification quality discount

When CVA returns `quality_discount` metadata from `/automation/service/pricing`, legacy standalone Human Translation quotes (fixed `HUMAN_EVALUATION` workflow) display a compact quality line beneath each price, for example: `USD 45.75` and `Quality: good`. Their quote total uses CVA's final `estimated_cost` sum and shows aggregate savings for the currently selected file/language pairs, recalculating when the user deselects items. Combined QE + Human Translation quotes and non-admin post-QE HT-only quotes (`slack_ht_quote_after_qe`) do not display quality tiers — the discount is already reflected in the line price (QE has already run).

# Document MT — Deferred Billing (RAY-80417)

Slack document machine translation (Document MT) accepts PDF uploads. Adobe PDF Services converts each PDF to DOCX before the standard extract → translate → merge pipeline runs. That conversion has a separate **PDF conversion fee** (`pdf_conversion_fee`, 25 tokens per page) on top of the normal document-MT character debit.

On the **Slack** and **Teams** paths, **no billing happens in int-slack-verify-consumer**: the document-MT debit and (on Slack, when the source was a PDF) the combined PDF conversion fee are deferred until the respective Ray app confirms delivery, then charged in a **single** `/mt/transaction` call.

## Policy

| Event | PDF conversion fee | Document MT fee |
|---|---|---|
| PDF invalid / encrypted / conversion error | Not charged | Not charged |
| Extract, merge, or translation fails after conversion | **Not charged** (cost absorbed) | **Not charged** (cost absorbed) |
| Insufficient balance before MT starts | Not charged | Not charged |
| Translated file **successfully delivered** to the user | Charged once | Charged per target |

Both fees are **deferred until successful end-to-end delivery**. Failed jobs no longer leave orphan document-MT or PDF-fee rows on the usage report.

## Flow (Slack)

```mermaid
sequenceDiagram
    participant User as Slack user
    participant SRT as slack-ray-translator
    participant Consumer as int-slack-verify-consumer
    participant Adobe as Adobe PDF Services
    participant LC as pt-languagecloud-api
    participant SAQ as SAQ worker (SRT)

    User->>SRT: Submit Document MT (PDF)
    SRT->>SRT: Upload to GridFS, create slack_job
    SRT->>Consumer: slack:job:machine:translate:v2
    Consumer->>Adobe: PDF → DOCX
    Note over Consumer: Track pdf_conversion_page_count<br/>on the task state (no charge yet)
    Consumer->>Consumer: Extract → MT → merge
    Note over Consumer: Build DocumentTransaction (+ PDF fields);<br/>NO charge — payload rides on the event
    Consumer->>SRT: verify:slack:document:translated<br/>(carries mt_charge payload)
    SRT->>SAQ: slack_upload_mt_result
    SAQ->>User: Upload translated file to Slack
    SAQ->>SAQ: charge_document_mt (background)
    SAQ->>LC: POST /mt/transaction<br/>document MT + combined PDF fee
```

The prepared charge payload (`mt_charge`) rides on the `verify:slack:document:translated` event itself — there is no `slack_job.extra_data` hand-off. The consumer assembles it (it owns language resolution and the idempotency keys) but never charges; slack-ray relays it after delivery. Double-charge safety comes from the gateway idempotency keys: the document-MT key is per target, the PDF key is per `task_uuid` (so it dedupes to one across multi-target charges).

## Flow (Teams)

Teams delivery is handled by **ms-teams-ray-translator** on `verify:teams:document:translated`. Document MT is charged **after** the translated-file notification is delivered — the same deferred pattern as Slack. The consumer sends `mt_charge` on the event but never calls the gateway. The PDF conversion fee is **waived for Teams** (RAY-80417).

```mermaid
sequenceDiagram
    participant User as Teams user
    participant Teams as ms-teams-ray-translator
    participant Consumer as int-slack-verify-consumer
    participant LC as pt-languagecloud-api

    Consumer->>Consumer: Extract → MT → merge
    Note over Consumer: Build mt_charge; no gateway call
    Consumer->>Teams: verify:teams:document:translated
    Teams->>User: Delivery notification + download
    Teams->>LC: POST /mt/transaction (document MT only)
```

## Linkage for reporting

The combined `/mt/transaction` call writes a document-MT debit and (when present) a `pdf_conversion_fee` debit, both carrying:

| Field | Purpose |
|---|---|
| `submission_group_uuid` | The `slack_job.task_uuid` — shared by the document-MT debit(s) and the PDF fee so the IBM usage report groups them (see below) |
| `idempotency_key` (MT) | Stable per `task_uuid` + target — replays dedupe |
| `pdf_idempotency_key` | Stable per `task_uuid` — PDF fee charged once across targets |
| `pdf_source_file_name` / `pdf_gridfs_file_id` | Original PDF basename + upload GridFS id on the PDF debit |

The resulting transactions are traceable on the usage report via their `submission_group_uuid`.

## Submission groups (RAY-80417)

`submission_group_uuid` is a producer-set id, written into `credit_transaction_usage.metadata`, that links **every debit of one logical submission** so the IBM usage report groups them at charge time instead of guessing with time-window/slack-job heuristics. It reuses the natural per-pipeline id rather than minting a new uuid:

| Submission | Shared id | Debits grouped |
|---|---|---|
| Slack document MT | `slack_job.task_uuid` | document MT + its PDF conversion fee (Teams: document MT only — PDF fee waived) |
| Media (subtitles) | `transcription_tasks.task_uuid` | transcription + SRT document translate + embedding |
| Verify AI / QE / HT | `job_uuid` | charged by the Verify API (not the spend gateway); already grouped on `job_uuid` |

The report (`cloud-verify-api`) prefers `metadata.submission_group_uuid` over all other grouping; rows charged before adoption (no metadata) fall back to the legacy heuristics. The producer field is defined in the MT spend contract (`pt-languagecloud-api/docs/mt-spend-contract-ray-80000.md`).

## Code map (slack-ray-translator)

| Area | Role |
|---|---|
| `app/ray/events/models.py` | `MtSuccessResponseSchema.mt_charge` carries the prepared `/mt/transaction` payload |
| `app/auth/connector.py` | `log_document_mt_by_client_id` relays the payload → `/mt/transaction` |
| `app/saq_jobs/tasks.py` | `slack_upload_mt_result` enqueues the charge after delivery; `charge_document_mt` SAQ task |
| `app/saq_jobs/dispatch.py` | `enqueue_document_mt_charge` |

## Related services

- **int-slack-verify-consumer** — PDF preprocess, balance gate; assembles `mt_charge` on the success event for both Slack and Teams (no gateway charge); stamps `submission_group_uuid` on document MT (slack_job task) and SRT subtitle translate (transcription task)
- **ms-teams-ray-translator** — `chargeTeamsDocumentMt` on `verify:teams:document:translated` → `logDocumentMtRequest` → `/mt/transaction` after delivery (PDF fee waived)
- **pt-languagecloud-api** — `POST /mt/transaction` charges document MT and, when PDF fields are present, the combined `pdf_conversion_fee` debit in the same call
- **cloud-verify-api** — IBM usage report Transaction Group prefers `usage.metadata.submission_group_uuid` (legacy rows fall back to the slack-job heuristic)

## Related tickets

- RAY-80000 — usage metadata / idempotency contract
- RAY-80261 — Adobe conversion error UX
- RAY-80324 — IBM usage report PDF-fee triage

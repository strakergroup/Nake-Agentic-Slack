# Document MT UAT file submit (RAY-81323)

Uploads a local file into the UAT IBM Straker DM and posts the **AI Translation**
button so a human can finish Document MT (quote → Accept).

## Why a new tool

`tools/bot-translation-uat-test` posts **text** as a *non-Straker* bot for channel
auto-translate. That path cannot submit Document MT:

| Approach | Result |
|----------|--------|
| Bot `@straker translate uat` | Ignored (`handle_app_mention` skips `is_bot`) |
| `/straker translate` | Opens **channel auto-translate settings**, not Document MT |
| Bot file share in a DM | Ignored (`is_bot` + IM returns before `respond_to_message`) |
| Human file share in the Straker DM | Posts `NewJobMessage` with **AI Translation** |

Bots also cannot click Slack buttons (no `trigger_id`). This tool therefore:

1. Uploads the file with the **Straker UAT bot** (`files:write`) into the same DM used for the RAY-81323 IBM tests.
2. Posts the Straker `document_mt_job` button against that Slack file id.
3. Stops. Click **AI Translation** → source **English** → target **Spanish (Spain)** → **Accept Quote**.

## Defaults (from UAT `slack_file_translation_submissions`)

| Field | Value |
|-------|-------|
| Channel | IBM Straker DM `D04CQDKNLR2` |
| Team | `T03PE1PGBV5` (Straker Slack App UAT) |
| Straker bot user | `U03U9RB1F0B` |
| File | `~/Documents/triage/RAY-81323_ibm_tm_exact_v4_en-us_es-es.txt` |

## Commands

```bash
cd tools/document-mt-uat-submit

make dry-run
make submit
make submit FILE=/path/to/other.txt
```

Token resolution: `STRAKER_BOT_TOKEN`, else local `ray_integration.slack_bots`, else `mysql-op uat-portal`.

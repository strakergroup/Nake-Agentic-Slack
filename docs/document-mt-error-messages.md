# Document MT error message mapping (RAY-81020)

Maps `verify:slack:document:translated`, `verify:slack:document:quote`, and
evaluate-complete typed errors onto Slack ephemerals.

## Helper

`app/slack/document_mt_error_messages.py` → `slack_message_for_document_mt_error`

| `error_type` | Template |
| --- | --- |
| `conversion_error` | `DocParseErrorMessage` (verbatim `error_data.message`) |
| `sample_text_not_found` | `DocParseErrorMessage` (message or shared no-content fallback) |
| `invalid_pdf` | `DocInvalidPdfErrorMessage` |
| `file_complexity_error` | `DocComplexityErrorMessage` |
| `insufficient_balance` | caller-built `RequiresMtToken*` |
| other | `DocMtMessage` / `EvaluateErrorMessage` |

Producer contract and stage semantics live in
`int-slack-verify-consumer/docs/document-mt-error-handling.md`.

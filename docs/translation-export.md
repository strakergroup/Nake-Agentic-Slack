# Translation Missing-String Export

The translation export tool reports Slack app UI messages that are missing from
the Straker translation database.

## Overview

The Slack app localizes UI copy by reading each user's Slack locale with
`users_info(..., include_locale=True)`, setting `app.translate.Translator`, and
looking up tagged source strings in `obj_stringtranslator`.

`tools/translation-export/export_missing_strings.py` follows the same lookup
shape for coverage checks:

```mermaid
flowchart LR
    Source[app Python source] --> Extract[_() string extraction]
    Extract --> Tag[Tag placeholders and emoji]
    Locale[Slack locale codes] --> Map[obj_m_langs bcp_47 to shortname]
    Map --> Compare[Compare labels in obj_stringtranslator]
    Tag --> Compare
    Compare --> Export[CSV / JSON / XLSX missing strings]
```

## Usage

```bash
cd tools/translation-export

# Default Slack locale coverage set, CSV output
make missing

# Explicit Slack locales from the same code family Slack returns in users.info
make missing LANGUAGES=fr-FR,fr-CA,de-DE,es-ES,ja-JP

# XLSX output
make missing LANGUAGES=fr-FR,fr-CA OUTPUT=output/missing_strings.xlsx FORMAT=xlsx

# XLSX output split into one file per resolved DB language
make missing-per-language OUTPUT=output/missing_strings.xlsx FORMAT=xlsx

# Fill blank translation cells with LanguageCloud MT
make mt-fill INPUT=output/missing_strings.xlsx

# Fill all per-language workbooks
make mt-fill INPUT='output/missing_strings_*.xlsx'

# Generate SQL insert statements for obj_stringtranslator
make import-sql INPUT=output/missing_strings.xlsx SQL_OUTPUT=output/import.sql

# Generate one combined SQL file from all per-language workbooks
make import-sql INPUT='output/missing_strings_*.xlsx' SQL_OUTPUT=output/import.sql
```

The equivalent direct command is:

```bash
pipenv run python tools/translation-export/export_missing_strings.py \
  --languages fr-FR,fr-CA,de-DE,es-ES,ja-JP \
  --output tools/translation-export/output/missing_strings.csv
```

`TRANSLATION_EXPORT_LANGUAGES` can also be used instead of `--languages`.

## Translation Workflow

The maintained tool mirrors the older `dev/` workflow as three explicit steps:

1. `make missing-per-language OUTPUT=output/missing_strings.xlsx FORMAT=xlsx`
   exports one workbook per resolved DB language, e.g.
   `missing_strings_fr.xlsx` and `missing_strings_fr-ca.xlsx`.
2. `make mt-fill INPUT='output/missing_strings_*.xlsx' CLIENT_ID=...` fills
   blank `target_text` cells using LanguageCloud MT. The tool generates a
   LanguageCloud JWT for the supplied client id, using the same
   `create_languagecloud_id_token` pattern as the app. Use
   `LANGUAGECLOUD_API_CLIENT_ID`, or set `LANGUAGECLOUD_API_TOKEN` to use a
   pre-generated bearer token.
   `LANGUAGECLOUD_API_URL` can override the default configured API base URL.
   The workbook `target_language` remains the DB language used for import, but
   MT requests are resolved through `obj_m_langs` to an MT-compatible code such
   as `google_code` when one is available. This prevents DB shortnames such as
   `kr` or `jp` from falling back to English in LanguageCloud.
3. `make import-sql INPUT='output/missing_strings_*.xlsx' SQL_OUTPUT=output/import.sql`
   creates SQL insert statements for `obj_stringtranslator` from all filled
   workbooks.

Review MT output before importing. The generated SQL is an import artifact; it
does not execute against the database.

Use `make missing OUTPUT=output/missing_strings.xlsx FORMAT=xlsx` when a single
combined workbook is preferred.

### MT Auth

The MT fill step follows the app's existing LanguageCloud auth pattern. It reads
the `languagecloud_api` integration key through `app.config`, fetches the
configured member from `obj_m_member` by `obj_uuid`, and creates a bearer JWT for the
`/mt/translate` request. No generated token is written to `.env`.

```bash
make mt-fill INPUT='output/missing_strings_*.xlsx'
make mt-fill INPUT='output/missing_strings_*.xlsx' CLIENT_ID=<client-uuid>
LANGUAGECLOUD_API_CLIENT_ID=<client-uuid> make mt-fill INPUT='output/missing_strings_*.xlsx'
```

## Import Validation

`make import-sql` validates placeholder tags before writing SQL. Every `<x id=N>`
tag present in `source_text` must also be present in `target_text`, and
translations must not introduce unexpected or malformed `<x ...>` tags. This
protects the runtime replacement logic used by `app.translate.Translator`.

The MT fill step also rejects LanguageCloud English fallback responses and
unchanged non-English output. SQL generation performs a batch-level guard for
non-English workbooks where a suspicious share of rows still matches the source
text, which catches full-language fallback outputs while allowing occasional
proper nouns to be reviewed normally.

If validation fails, no SQL file is written. A CSV report is written next to the
SQL output by default, e.g. `output/import_validation_errors.csv`. Override the
path when needed:

```bash
make import-sql INPUT='output/missing_strings_*.xlsx' \
  SQL_OUTPUT=output/import.sql \
  VALIDATION_REPORT=output/import_errors.csv
```

## Authoring Translatable Strings

User-facing app copy must be written as literal `_()` templates so the exporter
can discover it automatically. Payload values from callbacks, Slack, or upstream
services should be inserted as placeholders, for example
`_("Translation failed: {error_detail}")`, rather than translated directly with
`_(error_detail)`.

## Locale Handling

Slack returns IETF/BCP-47 locale values such as `fr-FR`, `de-DE`, `es-ES`, and
`ja-JP` when `include_locale=true` is requested. The exporter accepts those
Slack locale values directly, then resolves them through `obj_m_langs.bcp_47`
to the DB `shortname` used by `obj_stringtranslator.lang`.

English-like locales are skipped by default because runtime translation returns
the source string for languages starting with `en`, `gb`, or `us`. Use
`--include-english` only when auditing English catalog rows intentionally.

The app has runtime logic that can remap `fr-FR` users in North American
timezones to `fr-CA`. The exporter does not infer that timezone-specific
behaviour; include both `fr-FR` and `fr-CA` when both need coverage.

## Output Columns

- `source_language` - The source language code, currently `en`.
- `target_language` - The resolved `obj_stringtranslator.lang` value.
- `source_text` - The placeholder-tagged source string expected in the DB.
- `target_text` - The translated `langstring` value to import.
- `max_length` - Optional `_()` max length metadata.

Placeholders such as `{client_name}` and Slack emoji shortcodes such as
`:white_check_mark:` are converted to `<x id=N>` tags before lookup, matching
the app's current `Translator` behaviour.

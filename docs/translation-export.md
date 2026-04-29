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
```

The equivalent direct command is:

```bash
pipenv run python tools/translation-export/export_missing_strings.py \
  --languages fr-FR,fr-CA,de-DE,es-ES,ja-JP \
  --output tools/translation-export/output/missing_strings.csv
```

`TRANSLATION_EXPORT_LANGUAGES` can also be used instead of `--languages`.

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

- `slack_locale` - The locale value expected from Slack.
- `db_lang` - The resolved `obj_stringtranslator.lang` value.
- `source_text` - The original English app string.
- `db_label` - The placeholder-tagged lookup key expected in the DB.
- `max_length` - Optional `_()` max length metadata.
- `locations` - Source file and line references where the string appears.
- `notes` - Warnings such as missing `obj_m_langs.bcp_47` mapping.

Placeholders such as `{client_name}` and Slack emoji shortcodes such as
`:white_check_mark:` are converted to `<x id=N>` tags before lookup, matching
the app's current `Translator` behaviour.

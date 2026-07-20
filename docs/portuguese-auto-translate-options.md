# Portuguese auto-translate options (RAY-80734)

## Problem

Slack Document / channel language selects offered both:

- `Portuguese` (`pt`)
- `Portuguese (Brazil)` (`pt-BR`)

LanguageCloud and the document-MT consumer alias bare `pt` → `pt-br` (Brazilian).
Selecting both collapsed to one catalog UUID and duplicated billing/report rows.

European Portuguese in `obj_m_langs` is `pt-pt`, not bare `pt`.

## Fix

`get_auto_translate_languages()` now lists:

| Code | Label |
|------|--------|
| `pt-pt` | Portuguese (Portugal) |
| `pt-BR` | Portuguese (Brazil) |

Bare `pt` remains a legacy alias in LanguageCloud for older clients, but is no
longer offered in the Slack UI.

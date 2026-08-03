# Slack UI Export Tool
# Generates a single HTML file rendering all Slack Block Kit templates.
#
# Usage:
#   make            # Generate JSON + render HTML, then open in browser
#   make generate   # Python step only (creates output/blocks.json)
#   make render     # Node.js step only (creates output/ui-catalog.html)
#   make subset MATCH='MediaQuote|HumanJobQuote'  # Filtered catalog for active work
#   make supported-languages # Generate + render all supported Slack locales
#   make clean      # Remove generated output

SHELL := /bin/bash

REPO_ROOT := $(shell git rev-parse --show-toplevel)
PYTHON_VENV := $(REPO_ROOT)/.venv/bin/python
SUPPORTED_LANGUAGES ?= en,de,es,fr,fr-ca,jp
SUPPORTED_TRANSLATION_SOURCE ?= app

ifeq ($(wildcard $(PYTHON_VENV)),)
PYTHON := pipenv run python
else
PYTHON := $(PYTHON_VENV)
endif

# Prefer pipenv when the local .venv is incomplete (missing app deps).
ifeq ($(shell $(PYTHON) -c "import httpx" 2>/dev/null || echo missing),missing)
PYTHON := pipenv run python
endif

# nvm lazy-loads in interactive shells, so we source it explicitly for Make.
NVM_INIT  := export NVM_DIR="$$HOME/.nvm" && [ -s "$$NVM_DIR/nvm.sh" ] && . "$$NVM_DIR/nvm.sh"
NODE_BIN  := $(NVM_INIT) && node
NPM_BIN   := $(NVM_INIT) && npm

UI_EXPORT_ARGS :=
ifdef LANGUAGE
UI_EXPORT_ARGS += --language $(LANGUAGE)
endif
ifdef LANGUAGES
UI_EXPORT_ARGS += --languages $(LANGUAGES)
endif
ifdef TRANSLATIONS_FILE
UI_EXPORT_ARGS += --translations-file $(TRANSLATIONS_FILE)
endif
ifdef TRANSLATION_SOURCE
UI_EXPORT_ARGS += --translation-source $(TRANSLATION_SOURCE)
endif
ifdef MATCH
UI_EXPORT_ARGS += --match '$(MATCH)'
endif
ifdef NAMES
UI_EXPORT_ARGS += --names '$(NAMES)'
endif
ifdef CATEGORY
UI_EXPORT_ARGS += --category '$(CATEGORY)'
endif

SUPPORTED_UI_EXPORT_ARGS := --languages $(SUPPORTED_LANGUAGES) --translation-source $(SUPPORTED_TRANSLATION_SOURCE)
ifdef TRANSLATIONS_FILE
SUPPORTED_UI_EXPORT_ARGS += --translations-file $(TRANSLATIONS_FILE)
endif

SUBSET_JSON ?= output/blocks-subset.json
SUBSET_HTML ?= output/ui-catalog-subset.html

.PHONY: all generate render subset list-names supported-languages all-languages typecheck open open-subset clean install

all: generate render open

install: node_modules

node_modules: package.json
	$(NPM_BIN) install

generate:
	$(PYTHON) generate_blocks.py $(UI_EXPORT_ARGS)

output/blocks.json: $(REPO_ROOT)/app/slack/templates/messages.py \
                    $(REPO_ROOT)/app/slack/templates/views.py \
                    $(REPO_ROOT)/app/slack/templates/blocks.py \
                    generate_blocks.py
	$(PYTHON) generate_blocks.py $(UI_EXPORT_ARGS)

render: output/ui-catalog.html

output/ui-catalog.html: output/blocks.json render.mjs node_modules
	$(NODE_BIN) render.mjs

# Filtered catalog for the templates under active work (agents / ticket review).
# Examples:
#   make subset MATCH='MediaQuote|EvaluationCredits|HumanJobQuote|DocumentMtQuote|evaluation_ai_quote_adjust'
#   make subset CATEGORY=Quotes
#   make subset NAMES='HumanJobQuoteMessage,DocumentMtQuoteMessage (IBM AI Translate direct quote)'
subset: node_modules
	@if [ -z "$(MATCH)$(NAMES)$(CATEGORY)" ]; then \
		echo "Usage: make subset MATCH='regex' [CATEGORY=Quotes] [NAMES='Exact Name,...']"; \
		exit 1; \
	fi
	$(PYTHON) generate_blocks.py $(UI_EXPORT_ARGS) --output $(SUBSET_JSON)
	$(NODE_BIN) render.mjs --input $(SUBSET_JSON) --output $(SUBSET_HTML)
	@echo "Subset catalog: $(SUBSET_HTML)"

list-names:
	$(PYTHON) generate_blocks.py $(UI_EXPORT_ARGS) --list-names

supported-languages: node_modules
	$(PYTHON) generate_blocks.py $(SUPPORTED_UI_EXPORT_ARGS)
	$(NODE_BIN) render.mjs

all-languages: supported-languages

typecheck: node_modules render.mjs tsconfig.json
	$(NPM_BIN) run typecheck

open: output/ui-catalog.html
	@echo "Opening ui-catalog.html in default browser..."
	@open output/ui-catalog.html 2>/dev/null || xdg-open output/ui-catalog.html 2>/dev/null || echo "Open output/ui-catalog.html manually"

open-subset: $(SUBSET_HTML)
	@echo "Opening ui-catalog-subset.html in default browser..."
	@open $(SUBSET_HTML) 2>/dev/null || xdg-open $(SUBSET_HTML) 2>/dev/null || echo "Open $(SUBSET_HTML) manually"

clean:
	rm -rf output/blocks.json output/blocks-subset.json output/ui-catalog.html output/ui-catalog-subset.html \
		output/ibm-ht-staged-quote-flow.json output/ibm-ht-staged-quote-flow.html \
		output/ai-translate-quote-flow.json output/ai-translate-quote-flow.html \
		output/ai-translation-quote-flow.html

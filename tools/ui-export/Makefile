# Slack UI Export Tool
# Generates a single HTML file rendering all Slack Block Kit templates.
#
# Usage:
#   make            # Generate JSON + render HTML, then open in browser
#   make generate   # Python step only (creates output/blocks.json)
#   make render     # Node.js step only (creates output/ui-catalog.html)
#   make supported-languages # Generate + render all supported Slack locales
#   make clean      # Remove generated output

SHELL := /bin/bash

REPO_ROOT := $(shell git rev-parse --show-toplevel)
PYTHON_VENV := $(REPO_ROOT)/.venv/bin/python
SUPPORTED_LANGUAGES ?= de-DE,es-ES,es-LA,fr-FR,fr-CA,it-IT,ja-JP,ko-KR,pt-BR,zh-CN,zh-TW
SUPPORTED_TRANSLATION_SOURCE ?= app

ifeq ($(wildcard $(PYTHON_VENV)),)
PYTHON := pipenv run python
else
PYTHON := $(PYTHON_VENV)
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

SUPPORTED_UI_EXPORT_ARGS := --languages $(SUPPORTED_LANGUAGES) --translation-source $(SUPPORTED_TRANSLATION_SOURCE)
ifdef TRANSLATIONS_FILE
SUPPORTED_UI_EXPORT_ARGS += --translations-file $(TRANSLATIONS_FILE)
endif

.PHONY: all generate render supported-languages all-languages typecheck open clean install

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

supported-languages: node_modules
	$(PYTHON) generate_blocks.py $(SUPPORTED_UI_EXPORT_ARGS)
	$(NODE_BIN) render.mjs

all-languages: supported-languages

typecheck: node_modules render.mjs tsconfig.json
	$(NPM_BIN) run typecheck

open: output/ui-catalog.html
	@echo "Opening ui-catalog.html in default browser..."
	@open output/ui-catalog.html 2>/dev/null || xdg-open output/ui-catalog.html 2>/dev/null || echo "Open output/ui-catalog.html manually"

clean:
	rm -rf output/blocks.json output/ui-catalog.html

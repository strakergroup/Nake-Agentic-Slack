# Slack translation missing-string export tool.

SHELL := /bin/bash
REPO_ROOT := $(shell git rev-parse --show-toplevel)
PYTHON_VENV := $(REPO_ROOT)/.venv/bin/python

ifeq ($(wildcard $(PYTHON_VENV)),)
PYTHON := pipenv run python
else
PYTHON := $(PYTHON_VENV)
endif

EXPORT_ARGS :=
ifdef LANGUAGES
EXPORT_ARGS += --languages $(LANGUAGES)
endif
ifdef OUTPUT
EXPORT_ARGS += --output $(OUTPUT)
endif
ifdef FORMAT
EXPORT_ARGS += --format $(FORMAT)
endif
ifdef SOURCE_DIR
EXPORT_ARGS += --source-dir $(SOURCE_DIR)
endif
ifdef INCLUDE_ENGLISH
EXPORT_ARGS += --include-english
endif

.PHONY: missing clean

missing:
	$(PYTHON) export_missing_strings.py $(EXPORT_ARGS)

clean:
	rm -rf output

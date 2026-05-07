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
FILL_ARGS :=
IMPORT_ARGS :=
ifdef LANGUAGES
EXPORT_ARGS += --languages $(LANGUAGES)
endif
ifdef OUTPUT
EXPORT_ARGS += --output $(OUTPUT)
endif
ifdef FORMAT
EXPORT_ARGS += --format $(FORMAT)
endif
ifdef AUDIENCE
EXPORT_ARGS += --audience $(AUDIENCE)
endif
ifdef SOURCE_DIR
EXPORT_ARGS += --source-dir $(SOURCE_DIR)
endif
ifdef INCLUDE_ENGLISH
EXPORT_ARGS += --include-english
endif
ifdef PER_LANGUAGE
EXPORT_ARGS += --split-by-language
endif
ifdef INPUT
FILL_ARGS += --input '$(INPUT)'
IMPORT_ARGS += --input '$(INPUT)'
endif
ifdef BATCH_SIZE
FILL_ARGS += --batch-size $(BATCH_SIZE)
endif
ifdef SQL_OUTPUT
IMPORT_ARGS += --output $(SQL_OUTPUT)
endif
ifdef VALIDATION_REPORT
IMPORT_ARGS += --validation-report $(VALIDATION_REPORT)
endif

.PHONY: missing missing-per-language translator-missing mt-fill import-sql clean

missing:
	$(PYTHON) export_missing_strings.py $(EXPORT_ARGS)

missing-per-language:
	$(PYTHON) export_missing_strings.py $(EXPORT_ARGS) --split-by-language

translator-missing:
	$(PYTHON) export_missing_strings.py $(EXPORT_ARGS) --audience translator --format xlsx --split-by-language

mt-fill:
	$(PYTHON) mt_fill_translations.py $(FILL_ARGS)

import-sql:
	$(PYTHON) import_xlsx_translation.py $(IMPORT_ARGS)

clean:
	rm -rf output

# RAY-80512 UAT manual test runner for bot channel auto-translation.

SHELL := /bin/bash
REPO_ROOT := $(shell git rev-parse --show-toplevel)
PYTHON_VENV := $(REPO_ROOT)/.venv/bin/python

ifeq ($(wildcard $(PYTHON_VENV)),)
PYTHON := pipenv run python
else
PYTHON := $(PYTHON_VENV)
endif

CHANNEL_NAME ?= test2332
SCENARIO ?= all
WAIT_SECONDS ?=
DEBOUNCE_SECONDS ?=

RUN_ARGS := --channel-name $(CHANNEL_NAME) --scenario $(SCENARIO)
ifdef WAIT_SECONDS
RUN_ARGS += --wait-seconds $(WAIT_SECONDS)
endif
ifdef DEBOUNCE_SECONDS
RUN_ARGS += --debounce-seconds $(DEBOUNCE_SECONDS)
endif

.PHONY: lookup dry-run placeholders burst edit threaded-stream all help

help:
	@echo "Targets:"
	@echo "  make lookup                         # show mysql config for #test2332"
	@echo "  make dry-run SCENARIO=all           # print planned steps"
	@echo "  make placeholders                   # post :3dotsloading:/... only"
	@echo "  make burst                          # streaming burst -> one translation"
	@echo "  make edit                           # post + chat.update"
	@echo "  make threaded-stream                # IBM thread-root bug repro"
	@echo "  make all                            # run all scenarios"
	@echo ""
	@echo "Required env: STREAM_BOT_TOKEN (optional — auto-loaded from local MySQL)"
	@echo "Optional env: SLACK_READER_TOKEN, BOT_TRANSLATION_TEST_STREAM_BOT_USER_ID"

lookup:
	$(PYTHON) run_test.py --lookup-only --channel-name $(CHANNEL_NAME)

dry-run:
	$(PYTHON) run_test.py $(RUN_ARGS) --dry-run

placeholders:
	$(PYTHON) run_test.py $(RUN_ARGS) --scenario placeholders

burst:
	$(PYTHON) run_test.py $(RUN_ARGS) --scenario burst

edit:
	$(PYTHON) run_test.py $(RUN_ARGS) --scenario edit

threaded-stream:
	$(PYTHON) run_test.py $(RUN_ARGS) --scenario threaded-stream

all:
	$(PYTHON) run_test.py $(RUN_ARGS) --scenario all

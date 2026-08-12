# UAT helper: upload a local file into the IBM Straker DM and post AI Translation.

SHELL := /bin/bash
REPO_ROOT := $(shell git rev-parse --show-toplevel)
PYTHON_VENV := $(REPO_ROOT)/.venv/bin/python

ifeq ($(wildcard $(PYTHON_VENV)),)
PYTHON := pipenv run python
else
PYTHON := $(PYTHON_VENV)
endif

FILE ?= /home/wade/Documents/triage/RAY-81323_ibm_tm_exact_v4_en-us_es-es.txt
CHANNEL_ID ?= D04CQDKNLR2
TEAM_ID ?= T03PE1PGBV5

.PHONY: help dry-run submit

help:
	@echo "Targets:"
	@echo "  make dry-run FILE=...   # print planned upload"
	@echo "  make submit FILE=...    # upload to IBM Straker DM and post AI Translation"
	@echo ""
	@echo "Optional env: STRAKER_BOT_TOKEN (otherwise local/UAT MySQL slack_bots)"

dry-run:
	$(PYTHON) submit_file.py --file "$(FILE)" --channel-id $(CHANNEL_ID) --team-id $(TEAM_ID) --dry-run

submit:
	$(PYTHON) submit_file.py --file "$(FILE)" --channel-id $(CHANNEL_ID) --team-id $(TEAM_ID)

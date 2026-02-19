# Slack UI Export Tool
# Generates a single HTML file rendering all Slack Block Kit templates.
#
# Usage:
#   make            # Generate JSON + render HTML, then open in browser
#   make generate   # Python step only (creates output/blocks.json)
#   make render     # Node.js step only (creates output/ui-catalog.html)
#   make clean      # Remove generated output

REPO_ROOT := $(shell git rev-parse --show-toplevel)
PYTHON    := $(REPO_ROOT)/.venv/bin/python
NODE_BIN  := $(shell command -v node 2>/dev/null || echo /usr/local/n/versions/node/18.12.1/bin/node)
NPM_BIN   := $(shell command -v npm 2>/dev/null || echo /usr/local/n/versions/node/18.12.1/bin/npm)

.PHONY: all generate render open clean install

all: generate render open

install: node_modules

node_modules: package.json
	$(NPM_BIN) install

generate: output/blocks.json

output/blocks.json: $(REPO_ROOT)/app/slack/templates/messages.py \
                    $(REPO_ROOT)/app/slack/templates/views.py \
                    $(REPO_ROOT)/app/slack/templates/blocks.py \
                    generate_blocks.py
	$(PYTHON) generate_blocks.py

render: output/ui-catalog.html

output/ui-catalog.html: output/blocks.json render.mjs node_modules
	$(NODE_BIN) render.mjs

open: output/ui-catalog.html
	@echo "Opening ui-catalog.html in default browser..."
	@open output/ui-catalog.html 2>/dev/null || xdg-open output/ui-catalog.html 2>/dev/null || echo "Open output/ui-catalog.html manually"

clean:
	rm -rf output/blocks.json output/ui-catalog.html

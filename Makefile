# Each recipe line runs in its own shell, so nvm is loaded per line when it exists.
# In CI (no nvm) setup-node already provides Node 22.
FRONTEND := packages/ui/frontend
NVM := if [ -s "$$HOME/.nvm/nvm.sh" ]; then . "$$HOME/.nvm/nvm.sh" && nvm use --silent "$$(cat $(CURDIR)/$(FRONTEND)/.nvmrc)"; fi;

.PHONY: setup skills build test test-py test-js e2e demo demo-archive schemas brand docker

setup:
	uv sync
	cd $(FRONTEND) && $(NVM) npm ci
	$(MAKE) skills

skills:
	uvx library-skills install --claude --yes --skill shinyreact-build-app --skill shinyreact-convert-app
	$(NVM) npx skills add shadcn/ui --skill shadcn --agent claude-code --yes

build:
	cd $(FRONTEND) && $(NVM) npm run build

test: test-py test-js

test-py:
	uv run pytest

test-js:
	cd $(FRONTEND) && $(NVM) npm run typecheck && npm test

e2e: build
	uv run playwright install chromium
	uv run pytest -m e2e

demo: build
	uv run recordings-ui --demo

demo-archive:
	uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force

schemas:
	uv run recordings schemas --write

brand:
	uv run scripts/brand_css.py

docker:
	docker compose -f docker/compose.demo.yml up --build

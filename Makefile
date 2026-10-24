# Each recipe line runs in its own shell, so nvm is loaded per line when it exists.
# In CI (no nvm) setup-node already provides Node 22.
FRONTEND := packages/ui/frontend
NVM := if [ -s "$$HOME/.nvm/nvm.sh" ]; then . "$$HOME/.nvm/nvm.sh" && nvm use --silent "$$(cat $(CURDIR)/$(FRONTEND)/.nvmrc)"; fi;
# Images are tagged by the commit they run (spec §20, 2a). Uncommitted or untracked changes add
# -dirty, and `make deploy` refuses those: a rollback needs a commit to go back to.
IMAGE_TAG = $(shell git rev-parse --short=12 HEAD)$(shell test -z "$$(git status --porcelain)" || echo -dirty)
COMPOSE_ARGS = --env-file docker/deploy.env -f docker/compose.yml
COMPOSE = RECORDINGS_IMAGE_TAG=$(IMAGE_TAG) docker compose $(COMPOSE_ARGS)

.PHONY: help check setup skills build lint test test-py test-js test-utc leaks e2e demo
.PHONY: demo-archive pages pages-serve pages-test schemas brand docker deploy deploy-smoke
.PHONY: rollback prune-images restic

# The first target, so a bare `make` lists the targets rather than installing anything. GNU make
# 3.81 (macOS) is enough: the list is a grep of each target's `## ` comment.
help: ## list every target, with what it does
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN { FS = ":.*## " } { printf "  %-14s %s\n", $$1, $$2 }'

# What every commit runs first: lint, both suites, the Python suite again under UTC (dates are this
# app's core, and the server's clock is UTC), and the secret scan CI's leaks job runs.
check: lint test test-utc leaks ## lint, pytest and Vitest, pytest under TZ=UTC, then gitleaks

setup: ## install everything: uv sync, npm ci and the agent skills
	uv sync
	cd $(FRONTEND) && $(NVM) npm ci
	$(MAKE) skills

skills: ## install the shinyreact and shadcn agent skills
	uvx library-skills install --claude --yes --skill shinyreact-build-app --skill shinyreact-convert-app
	$(NVM) npx skills add shadcn/ui --skill shadcn --agent claude-code --yes

build: ## build the browser bundle, www/ui.js
	cd $(FRONTEND) && $(NVM) npm run build

lint: ## ruff check and ruff format --check
	uv run ruff check .
	uv run ruff format --check .

test: test-py test-js ## pytest and Vitest

test-py: ## pytest
	uv run pytest

test-js: ## the TypeScript check and Vitest
	cd $(FRONTEND) && $(NVM) npm run typecheck && npm test

test-utc: ## pytest again, with TZ=UTC
	TZ=UTC uv run pytest

# The whole history, as CI's leaks job scans it (.gitleaks.toml holds the allowlist).
leaks: ## gitleaks over the whole git history
	gitleaks git --redact --no-banner .

e2e: build ## Playwright against demo mode (builds first)
	uv run playwright install chromium
	uv run pytest -m e2e

demo: build ## run the app in demo mode
	uv run recordings-ui --demo

demo-archive: ## rebuild demo/archive from the cached media
	uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force

# The static GitHub Pages demo (spec §17.1): only demo/archive/ is ever exported.
pages: build ## build the static Pages demo into _site/
	uv run --group pages python scripts/build_pages.py

# Like Pages: under /recordings/, with Range requests, so seeking works.
pages-serve: ## serve _site/ as GitHub Pages does, with Range requests
	uv run python scripts/rangeserver.py _site --prefix /recordings/ --port 8008

pages-test: ## the Pages smoke test against _site/
	uv run playwright install chromium
	uv run pytest -m pages -rs

schemas: ## write the archive's JSON Schemas
	uv run recordings schemas --write

brand: ## the brand CSS, from _brand.yml
	uv run scripts/brand_css.py

docker: ## the demo in Docker
	docker compose -f docker/compose.demo.yml up --build

# Not while a job runs (docker/deploy-guard.sh; FORCE=1 deploys anyway). --wait: fail, rather than
# succeed silently, when web doesn't pass its health check within 3 minutes; then roll back.
deploy: ## build and start the deployment; refused while a job runs (FORCE=1)
	$(if $(findstring -dirty,$(IMAGE_TAG)),$(error make deploy: commit first: a -dirty image has no commit to roll back to))
	FORCE=$(FORCE) sh docker/deploy-guard.sh
	$(COMPOSE) up -d --build --wait --wait-timeout 180

# Back to an image built before, never building one: make rollback TAG=<tag>, with a tag from
# `docker image ls recordings`.
rollback: ## start an image built before: make rollback TAG=<tag>
	$(if $(TAG),,$(error usage: make rollback TAG=<a tag from docker image ls recordings>))
	RECORDINGS_IMAGE_TAG=$(TAG) docker compose $(COMPOSE_ARGS) up -d --no-build

# Each deploy leaves an image on the system disk: keep the newest KEEP (default 5) commit tags as
# rollback targets, and any a container was made from.
prune-images: ## keep the newest KEEP (default 5) commit-tagged images
	sh docker/prune-images.sh $(or $(KEEP),5)

deploy-smoke: ## run the real compose file once, as CI's compose-smoke does
	uv run python scripts/compose_smoke.py

restic: ## fetch the pinned restic into .cache/bin
	uv run python scripts/fetch_restic.py

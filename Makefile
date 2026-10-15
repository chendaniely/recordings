# Each recipe line runs in its own shell, so nvm is loaded per line when it exists.
# In CI (no nvm) setup-node already provides Node 22.
FRONTEND := packages/ui/frontend
NVM := if [ -s "$$HOME/.nvm/nvm.sh" ]; then . "$$HOME/.nvm/nvm.sh" && nvm use --silent "$$(cat $(CURDIR)/$(FRONTEND)/.nvmrc)"; fi;
# Images are tagged by the commit they run (spec §20, 2a). Uncommitted or untracked changes add
# -dirty, and `make deploy` refuses those: a rollback needs a commit to go back to.
IMAGE_TAG = $(shell git rev-parse --short=12 HEAD)$(shell test -z "$$(git status --porcelain)" || echo -dirty)
COMPOSE_ARGS = --env-file docker/deploy.env -f docker/compose.yml
COMPOSE = RECORDINGS_IMAGE_TAG=$(IMAGE_TAG) docker compose $(COMPOSE_ARGS)

.PHONY: restic setup skills build test test-py test-js e2e demo demo-archive pages pages-serve pages-test schemas brand docker deploy deploy-smoke rollback prune-images

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

# The static GitHub Pages demo (spec §17.1): only demo/archive/ is ever exported.
pages: build
	uv run --group pages python scripts/build_pages.py

# Like Pages: under /recordings/, with Range requests, so seeking works.
pages-serve:
	uv run python scripts/rangeserver.py _site --prefix /recordings/ --port 8008

pages-test:
	uv run playwright install chromium
	uv run pytest -m pages -rs

schemas:
	uv run recordings schemas --write

brand:
	uv run scripts/brand_css.py

docker:
	docker compose -f docker/compose.demo.yml up --build

# Not while a job runs (docker/deploy-guard.sh; FORCE=1 deploys anyway). --wait: fail, rather than
# succeed silently, when web doesn't pass its health check within 3 minutes; then roll back.
deploy:
	$(if $(findstring -dirty,$(IMAGE_TAG)),$(error make deploy: commit first: a -dirty image has no commit to roll back to))
	FORCE=$(FORCE) sh docker/deploy-guard.sh
	$(COMPOSE) up -d --build --wait --wait-timeout 180

# Back to an image built before, never building one: make rollback TAG=<tag>, with a tag from
# `docker image ls recordings`.
rollback:
	$(if $(TAG),,$(error usage: make rollback TAG=<a tag from docker image ls recordings>))
	RECORDINGS_IMAGE_TAG=$(TAG) docker compose $(COMPOSE_ARGS) up -d --no-build

# Each deploy leaves an image on the system disk: keep the newest KEEP (default 5) commit tags as
# rollback targets, and any a container was made from.
prune-images:
	sh docker/prune-images.sh $(or $(KEEP),5)

deploy-smoke:
	uv run python scripts/compose_smoke.py

restic:
	uv run python scripts/fetch_restic.py

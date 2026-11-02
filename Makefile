# Each recipe line runs in its own shell, so nvm is loaded per line when it exists.
# In CI (no nvm) setup-node already provides Node 22.
FRONTEND := packages/ui/frontend
NVM := if [ -s "$$HOME/.nvm/nvm.sh" ]; then . "$$HOME/.nvm/nvm.sh" && nvm use --silent "$$(cat $(CURDIR)/$(FRONTEND)/.nvmrc)"; fi;
# Images are tagged by the commit they run (spec §20, 2a). Uncommitted or untracked changes add
# -dirty, and `make deploy` refuses those: a rollback needs a commit to go back to.
IMAGE_TAG = $(shell git rev-parse --short=12 HEAD)$(shell test -z "$$(git status --porcelain)" || echo -dirty)
COMPOSE_ARGS = --env-file docker/deploy.env -f docker/compose.yml
COMPOSE = RECORDINGS_IMAGE_TAG=$(IMAGE_TAG) docker compose $(COMPOSE_ARGS)
# The Mac's own settings for the targets that reach the server (git-ignored): SERVER, your ssh
# name for the homelab server, and REPO_DIR, this repo's checkout there. They are expanded here,
# on the Mac, so the server's shell never sees a variable it doesn't have.
-include local.mk
# make consent-add's ID is pasted text: it is taken as written, never expanding a $(...) in it, and
# reaches the recipe in the environment, never in the shell line.
ifneq ($(origin ID),undefined)
override ID := $(value ID)
export ID
endif

.PHONY: help check setup skills build lint test test-py test-js test-utc leaks e2e demo
.PHONY: demo-archive pages pages-serve pages-test schemas brand docker deploy deploy-smoke
.PHONY: rollback prune-images restic consent-add plaud-sign-in plaud-dry-run plaud-catch-up
.PHONY: demo-live e2e-live

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

# Stage 2b's live demo (Dan's answer, Phase 1's Q&A §8): the web app and the Plaud worker on this
# machine, against the fake Plaud server (packages/core/tests/fake_plaud.py), with a synthetic
# account and a scratch archive under $TMPDIR, never a real one: it refuses to start if any setting
# points elsewhere. It prints the URL and the commands to type; Ctrl-C stops everything. PORT
# (default 8000) moves the web app; ARGS passes more (--schedule, --before-catch-up, --keep).
demo-live: build ## web app and Plaud worker against a fake Plaud, scratch archive; Ctrl-C stops
	uv run python scripts/demo_live.py $(if $(PORT),--port $(PORT)) $(ARGS)

# The Sync screen and Status against the live demo's web app and worker (stage 2b spec §21's "e2e,
# live"): packages/ui/tests/e2e_live/, which starts scripts/demo_live.py as make demo-live does.
e2e-live: build ## Playwright against make demo-live (the web app and worker on the fake Plaud)
	uv run playwright install chromium
	uv run pytest -m e2e_live

demo-archive: ## rebuild demo/archive from the cached media
	uv run python demo/build.py --media-dir demo/.cache/media --out demo/archive --force

# The static GitHub Pages demo (spec §17.1): only demo/archive/ and demo/plaud-sync.json are
# ever exported.
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
# succeed silently, when web or the worker doesn't pass its health check within 3 minutes; then roll
# back (to a release before stage 2b, as the runbook's "Rolling back across 2b" says).
deploy: ## build and start the deployment; refused while a job runs (FORCE=1)
	$(if $(findstring -dirty,$(IMAGE_TAG)),$(error make deploy: commit first: a -dirty image has no commit to roll back to))
	FORCE=$(FORCE) sh docker/deploy-guard.sh
	$(COMPOSE) up -d --build --wait --wait-timeout 180

# Back to an image built before, never building one: make rollback TAG=<tag>, with a tag from
# `docker image ls recordings`. To a release before stage 2b, the runbook's "Rolling back across
# 2b" comes first: this compose.yml would start a worker the older image can't run.
rollback: ## start an image built before: make rollback TAG=<tag>
	$(if $(TAG),,$(error usage: make rollback TAG=<a tag from docker image ls recordings>))
	RECORDINGS_IMAGE_TAG=$(TAG) docker compose $(COMPOSE_ARGS) up -d --no-build

# Each deploy leaves an image on the system disk: keep the newest KEEP (default 5) commit tags as
# rollback targets, and any a container was made from.
prune-images: ## keep the newest KEEP (default 5) commit-tagged images
	sh docker/prune-images.sh $(or $(KEEP),5)

deploy-smoke: ## run the real compose file once, as CI's compose-smoke does
	uv run python scripts/compose_smoke.py

# Run on the Mac (stage 2b spec §12.1): checks the ID (a bare ID, of_..., or what the Sync screen's
# Copy ID copied, in single quotes), appends it to the server's consent list by writing a new file
# and renaming it (never making a list, or extending an empty one), then prints what the worker
# last read: its count may still be the old one for a few seconds.
consent-add: ## add a Plaud ID to the server's consent list, from the Mac: make consent-add ID='<id>'
	$(if $(ID),,$(error usage: make consent-add ID='<a Plaud ID, or what Copy ID copied>'))
	$(if $(SERVER),,$(error make consent-add: set SERVER, your ssh name for the homelab server, in local.mk))
	$(if $(REPO_DIR),,$(error make consent-add: set REPO_DIR, the repo's checkout on the server, in local.mk))
	@id=$$(uv run python scripts/consent_id.py "$$ID") \
	  || { echo "make consent-add: step 1 failed, checking the ID (above)" >&2; exit 1; }; \
	ssh "$(SERVER)" "cd '$(REPO_DIR)' && sh docker/consent-add.sh '$$id' || exit 3; sh docker/up-deployed.sh exec -T worker recordings plaud consent || { [ \$$? = 78 ] && exit 5; exit 4; }"; \
	rc=$$?; \
	case $$rc in \
	  0) ;; \
	  255) echo "make consent-add: step 2 failed: the server could not be reached, or the connection dropped (ssh exit 255): run: rc exec worker recordings plaud consent, on the server, to see whether the ID is on the list" >&2 ;; \
	  3) echo "make consent-add: step 2 failed, on the server: adding the ID (above); the list is unchanged" >&2 ;; \
	  5) echo "make consent-add: the ID was added, but the list still stops imports (above): fix the lines it names" >&2 ;; \
	  4) echo "make consent-add: step 3 failed, on the server: reading the list back (above); the ID is on the list" >&2 ;; \
	  *) echo "make consent-add: step 2 failed: ssh exited $$rc; check the list on the server" >&2 ;; \
	esac; \
	[ $$rc = 0 ] || exit 1; \
	echo "make consent-add: the worker reads the new list within a few seconds. If the line (by the worker: N IDs) above still shows the old count, run: rc exec worker recordings plaud consent"

# Run on the Mac (stage 2b spec §10.5): signs in to Plaud with the vendor CLI under a temporary
# HOME (made under $TMPDIR, which Time Machine skips, at mode 0700, and removed by a trap whatever
# happens), so the Mac's own Plaud sign-ins are untouched; then pipes the CLI's token file over ssh
# into `recordings plaud token-import -` in the server's worker, which checks it, makes one cheap
# call and installs it. The token never passes as an argument or an environment variable, and is
# never printed. PLAUD_CLI names the vendor CLI (audio-router's `plaud`). A Claude session never
# runs this target (CLAUDE.md).
PLAUD_CLI ?= plaud
plaud-sign-in: ## sign in to Plaud for the server, from the Mac: make plaud-sign-in
	$(if $(SERVER),,$(error make plaud-sign-in: set SERVER, your ssh name for the homelab server, in local.mk))
	$(if $(REPO_DIR),,$(error make plaud-sign-in: set REPO_DIR, the repo's checkout on the server, in local.mk))
	@tmp=$${TMPDIR:-/tmp}; \
	tmphome=$$(mktemp -d "$${tmp%/}/plaud-sign-in.XXXXXX") \
	  || { echo "make plaud-sign-in: step 1 failed: no temporary HOME could be made under $$tmp" >&2; exit 1; }; \
	trap 'rm -rf "$$tmphome"' EXIT; trap 'exit 1' HUP INT TERM; \
	chmod 700 "$$tmphome" || { echo "make plaud-sign-in: step 1 failed: the temporary HOME's mode" >&2; exit 1; }; \
	echo "make plaud-sign-in: sign in to Plaud in the browser that opens"; \
	HOME="$$tmphome" $(PLAUD_CLI) login \
	  || { echo "make plaud-sign-in: step 2 failed: the Plaud sign-in (above); nothing was sent" >&2; exit 1; }; \
	[ -s "$$tmphome/.plaud/tokens.json" ] \
	  || { echo "make plaud-sign-in: step 2 failed: $(PLAUD_CLI) login left no .plaud/tokens.json in its HOME; nothing was sent" >&2; exit 1; }; \
	ssh "$(SERVER)" "cd '$(REPO_DIR)' && sh docker/up-deployed.sh exec -T worker recordings plaud token-import -" < "$$tmphome/.plaud/tokens.json"; \
	rc=$$?; \
	case $$rc in \
	  0) ;; \
	  255) echo "make plaud-sign-in: step 3 failed: the server could not be reached, or the connection dropped (ssh exit 255); nothing is known to be installed: run make plaud-sign-in again" >&2 ;; \
	  77) echo "make plaud-sign-in: step 4 failed, on the server: the token file or the sign-in was refused (above); nothing was installed: run make plaud-sign-in again" >&2 ;; \
	  69) echo "make plaud-sign-in: step 4 failed, on the server: Plaud could not be reached from it (above); nothing was installed: run make plaud-sign-in again later" >&2 ;; \
	  78) echo "make plaud-sign-in: step 4 failed, on the server: the sign-in's folder or the settings (above); nothing was installed" >&2 ;; \
	  *) echo "make plaud-sign-in: step 4 failed, on the server: token-import exited $$rc (above); check with: rc exec worker recordings plaud check" >&2 ;; \
	esac; \
	[ $$rc = 0 ] || exit 1; \
	echo "make plaud-sign-in: done. The worker's next sync uses it; to check now, on the server: rc exec worker recordings plaud check"

# Run on the server, in the checkout ($REPO_DIR in the runbook; stage 2b spec §11.6 and §17.5, and
# the runbook's sections 7 and 8):
# the first catch-up's dry run, then the catch-up itself, in the containers `make deploy` started
# (docker/up-deployed.sh, as `rc` in the runbook). Neither ever runs from the Mac or in CI.
UP_DEPLOYED = sh docker/up-deployed.sh
plaud-dry-run: ## on the server: the first catch-up's dry run (lists and classifies, imports nothing)
	@$(UP_DEPLOYED) exec -T worker recordings plaud sync --dry-run --list; rc=$$?; \
	case $$rc in \
	  0) ;; \
	  1) echo "make plaud-dry-run: the listing is held or couldn't be read (above): check it as the runbook's section 7 says" >&2 ;; \
	  69) echo "make plaud-dry-run: Plaud could not be reached (above): run it again later" >&2 ;; \
	  75) echo "make plaud-dry-run: another Plaud run holds the sync (above): run it again when it ends" >&2 ;; \
	  77) echo "make plaud-dry-run: the Plaud sign-in is needed: at the Mac, make plaud-sign-in" >&2 ;; \
	  78) echo "make plaud-dry-run: the consent list or the settings stop it (above): fix the lines it names" >&2 ;; \
	  *) echo "make plaud-dry-run: exited $$rc (above)" >&2 ;; \
	esac; \
	$(UP_DEPLOYED) exec -T worker recordings plaud consent || true; \
	[ $$rc = 0 ]

# The catch-up, in §11.6's order: the backup service stopped (no scheduled backup in the middle,
# and started again by a trap however it ends), a `pre-import` backup, the baseline, `sync
# --catch-up`, a deep validation, a `post-import` backup. Each step runs only if the one before it
# passed; the catch-up's exit 2 (some recordings failed, the catch-up done) goes on. Run it again
# after any failure: every step is safe to repeat.
plaud-catch-up: ## on the server: the first catch-up, supervised (backups, baseline, sync --catch-up, validate)
	@up="$(UP_DEPLOYED)"; \
	$$up stop backup || { echo "make plaud-catch-up: step 1 failed: stopping the backup service" >&2; exit 1; }; \
	trap '$$up start backup >/dev/null 2>&1 || echo "make plaud-catch-up: start the backup service again: rc start backup" >&2' EXIT; \
	trap 'exit 1' HUP INT TERM; \
	$$up run --rm backup recordings backup run --tag pre-import \
	  || { echo "make plaud-catch-up: step 2 failed: the pre-import backup (above); nothing was imported" >&2; exit 1; }; \
	$$up exec -T worker recordings plaud baseline \
	  || { echo "make plaud-catch-up: step 3 failed: the baseline (above); nothing was imported: run make plaud-catch-up again once it is fixed" >&2; exit 1; }; \
	$$up exec -T worker recordings plaud sync --catch-up; rc=$$?; \
	case $$rc in \
	  0) ;; \
	  2) echo "make plaud-catch-up: the catch-up is done, but some recordings failed (above): the scheduled sync retries them" >&2 ;; \
	  *) echo "make plaud-catch-up: step 4 failed: the catch-up exited $$rc (above): see the runbook's section 8, then run make plaud-catch-up again" >&2; exit 1 ;; \
	esac; \
	$$up run --rm web recordings validate /archive --deep \
	  || { echo "make plaud-catch-up: step 5 failed: validate (above): stop, and ask in a Claude session before tagging" >&2; exit 1; }; \
	$$up run --rm backup recordings backup run --tag post-import \
	  || { echo "make plaud-catch-up: step 6 failed: the post-import backup (above): run it again: rc run --rm backup recordings backup run --tag post-import" >&2; exit 1; }; \
	echo "make plaud-catch-up: done. You may start tagging now. Within 20 minutes the sync's Healthchecks check should show up."

restic: ## fetch the pinned restic into .cache/bin
	uv run python scripts/fetch_restic.py

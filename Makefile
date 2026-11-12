# microcloud-ui developer tasks. Override VENV to use a venv elsewhere.
VENV   ?= .venv
PY     ?= python3
BIN    := $(abspath $(VENV))/bin
FE     := frontend

.PHONY: help venv install install-backend install-frontend test test-backend test-frontend \
        lint lint-backend lint-frontend lint-shell format build bundle image image-save screenshots dev-backend dev-frontend serve hooks clean

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

venv:            ## create the Python virtualenv
	test -d $(VENV) || $(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

install: install-backend install-frontend ## install everything

install-backend: venv ## pip install backend (editable) + dev deps
	$(BIN)/pip install -e "backend[dev]"

install-frontend: ## npm ci the frontend
	cd $(FE) && npm ci

test: test-backend test-frontend ## run all tests with coverage gates (>=90%)

test-backend:
	cd backend && $(BIN)/pytest --cov

test-frontend:
	cd $(FE) && npm run coverage

lint: lint-backend lint-frontend lint-shell ## ruff, mypy, eslint, tsc, shellcheck

lint-backend:
	cd backend && $(BIN)/ruff check . ../scripts && $(BIN)/ruff format --check . ../scripts && $(BIN)/mypy

lint-frontend:
	cd $(FE) && npm run lint && npm run typecheck

lint-shell:
	$(BIN)/shellcheck deploy/install.sh deploy/mcui-cli deploy/container/entrypoint.sh scripts/bundle.sh scripts/pre-commit scripts/commit-msg

format:          ## auto-format backend
	cd backend && $(BIN)/ruff format . && $(BIN)/ruff check --fix .

build:           ## build the frontend into frontend/dist
	cd $(FE) && npm run build

bundle:          ## offline install bundle for Ubuntu in dist/ (PYVER=3.12 ARCH=x86_64; docs/DEPLOY.md)
	BIN=$(BIN) PYVER=$(or $(PYVER),3.12) ARCH=$(or $(ARCH),x86_64) scripts/bundle.sh

ENGINE  ?= $(shell command -v podman || command -v docker)
VERSION := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' backend/mcui/__init__.py)

# --network=host: build steps use the host's network, so a bridge MTU larger than the
# host's (1500 inside an OVN instance at 1442) can't stall downloads (D64);
# podman's default OCI format would drop the HEALTHCHECK
image:           ## container image mcui:<version> + :latest with podman or docker (ENGINE=...)
	@test -n "$(ENGINE)" || { echo "make image: needs podman or docker"; exit 1; }
	$(ENGINE) build --network=host $(if $(findstring podman,$(ENGINE)),--format docker) \
		-f deploy/container/Containerfile -t mcui:$(VERSION) -t mcui:latest .

image-save: image ## the image as dist/mcui-image-<version>.tar.gz for hosts without a registry
	mkdir -p dist && $(ENGINE) save mcui:$(VERSION) | gzip > dist/mcui-image-$(VERSION).tar.gz

screenshots: build ## README screenshots from the invented demo cluster (needs Google Chrome or CHROME_PATH)
	cd $(FE) && node screenshots/shoot.ts ../docs/screenshots && node screenshots/shoot.ts ../docs/screenshots --light --only=overview

dev-backend:     ## backend with auto-reload on :8000
	$(BIN)/mcui serve --reload

dev-frontend:    ## Vite dev server on :5173 (proxies /api to :8000)
	cd $(FE) && npm run dev

serve: build     ## build SPA and serve everything from the backend on :8000
	$(BIN)/mcui serve

hooks:           ## install the git pre-commit and commit-msg hooks
	ln -sf ../../scripts/pre-commit .git/hooks/pre-commit
	ln -sf ../../scripts/commit-msg .git/hooks/commit-msg

clean:           ## remove build/test artefacts
	rm -rf $(FE)/dist $(FE)/coverage backend/.pytest_cache backend/.coverage backend/htmlcov

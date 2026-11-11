# microcloud-ui developer tasks. Override VENV to use a venv elsewhere.
VENV   ?= .venv
PY     ?= python3
BIN    := $(abspath $(VENV))/bin
FE     := frontend

.PHONY: help venv install install-backend install-frontend test test-backend test-frontend \
        lint lint-backend lint-frontend format build screenshots dev-backend dev-frontend serve hooks clean

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

lint: lint-backend lint-frontend ## ruff, mypy, eslint, tsc

lint-backend:
	cd backend && $(BIN)/ruff check . && $(BIN)/ruff format --check . && $(BIN)/mypy

lint-frontend:
	cd $(FE) && npm run lint && npm run typecheck

format:          ## auto-format backend
	cd backend && $(BIN)/ruff format . && $(BIN)/ruff check --fix .

build:           ## build the frontend into frontend/dist
	cd $(FE) && npm run build

screenshots: build ## README screenshots from the invented demo cluster (needs Google Chrome or CHROME_PATH)
	cd $(FE) && node screenshots/shoot.ts ../docs/screenshots && node screenshots/shoot.ts ../docs/screenshots --light --only=overview

dev-backend:     ## backend with auto-reload on :8000
	$(BIN)/mcui serve --reload

dev-frontend:    ## Vite dev server on :5173 (proxies /api to :8000)
	cd $(FE) && npm run dev

serve: build     ## build SPA and serve everything from the backend on :8000
	$(BIN)/mcui serve

hooks:           ## install the git pre-commit hook
	ln -sf ../../scripts/pre-commit .git/hooks/pre-commit

clean:           ## remove build/test artefacts
	rm -rf $(FE)/dist $(FE)/coverage backend/.pytest_cache backend/.coverage backend/htmlcov

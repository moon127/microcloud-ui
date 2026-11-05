# microcloud-ui

A web UI for Canonical MicroCloud (LXD, MicroCeph, MicroOVN) with built-in
metrics: per-instance CPU/memory/disk/network history, Ceph and local storage
usage, and OVN network visibility.

**Status:** Phase 0 (scaffold) complete. See [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md),
[`docs/PLAN.md`](docs/PLAN.md) and the design log in [`agents.md`](agents.md).

## Development setup

Requires **Python 3.10+**, **Node 20+** (22 recommended) and `make`.
On macOS: `brew install python@3.12 node` (the bundled `python3` may be too old).

```bash
git clone <repo> microcloud-ui && cd microcloud-ui

# Python backend in a virtual environment (created at ./.venv)
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e "backend[dev]"          # editable install + test/lint tools

# Frontend
cd frontend && npm ci && cd ..

# or do both in one go:  make install

make hooks                             # optional: lint + tests run before every commit
```

`make help` lists every target. The common ones:

| Command | What it does |
|---|---|
| `make test` | backend `pytest --cov` + frontend `vitest --coverage`; fails below 90% |
| `make lint` | ruff, ruff format check, mypy (strict), eslint, tsc |
| `make dev-backend` | API with auto-reload on http://127.0.0.1:8000 |
| `make dev-frontend` | Vite dev server on http://localhost:5173, proxying `/api` to :8000 |
| `make serve` | build the SPA and serve everything from the backend on :8000 |

Use a different venv location with `make test VENV=~/venvs/mcui`.

Leave the venv with `deactivate`.

## Manual run for development (macOS)

For prototyping only; production uses the systemd unit or container image (Phase 7).

```bash
source .venv/bin/activate

# Terminal 1: backend with auto-reload
mcui serve --reload                    # or: make dev-backend
curl http://127.0.0.1:8000/api/health  # {"status":"ok","version":"..."}

# Terminal 2: frontend with hot reload
cd frontend && npm run dev             # open http://localhost:5173

# Or a single process serving the built SPA:
make serve                             # open http://127.0.0.1:8000
```

From Phase 1 the backend also takes a config file with LXD endpoints and
certificates (`mcui serve --config config.toml`, see `docs/CONFIGURATION.md`),
and `mcui check` tests the connection to the cluster.

> If you copy `frontend/node_modules` between machines (e.g. from a Linux VM),
> delete it and run `npm ci` again: it contains platform-specific binaries.

## LXD certificates

The backend talks to LXD with mutual TLS; browsers never need a certificate.
Step-by-step guide covering key generation on macOS, trusting the certs in
LXD, fetching the shared `cluster.crt` for verification, and testing with
curl: [`docs/LXD-CREDENTIALS.md`](docs/LXD-CREDENTIALS.md).
Configuration reference, including how to switch metrics to a dedicated port:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).
Never commit certs or keys (they are git-ignored).

## Licence

MIT, see [`LICENSE`](LICENSE).

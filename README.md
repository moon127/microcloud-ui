# microcloud-ui

A web UI for Canonical MicroCloud (LXD, MicroCeph, MicroOVN) with built-in
metrics: per-instance CPU/memory/disk/network history, Ceph and local storage
usage, and OVN network visibility.

**Status:** planning. See [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md),
[`docs/PLAN.md`](docs/PLAN.md) and the design log in [`agents.md`](agents.md).

## Development setup (Python backend)

Requires Python 3.10+ and Node 20+ (for the frontend).

```bash
# create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# install the backend with dev/test dependencies (editable)
pip install --upgrade pip
pip install -e "backend[dev]"

# run the tests with coverage (must stay >= 90%)
pytest --cov --cov-branch --cov-fail-under=90

# leave the venv when done
deactivate
```

> The `backend/` package lands in Phase 0; these commands become live then.

## Manual run for development (macOS)

For prototyping only; production uses the systemd unit or container image.

```bash
# one-off prerequisites (Homebrew); macOS's bundled python3 may be too old
brew install python@3.12 node

# backend: venv as above, then
cp config.example.toml config.toml        # edit LXD endpoints + cert paths
mkdir -p ~/.config/mcui/certs && chmod 700 ~/.config/mcui/certs
#   client/metrics certs + lxd-cluster.crt, see docs/LXD-CREDENTIALS.md
source .venv/bin/activate
mcui check --config config.toml           # verifies TLS, trust type, cluster members
uvicorn mcui.main:app --reload --port 8000  # API on http://127.0.0.1:8000 (MCUI_CONFIG=config.toml)

# frontend (second terminal): hot reload, proxies /api and websockets to :8000
cd frontend
npm install
npm run dev                               # open http://localhost:5173

# or a single process serving the built SPA
(cd frontend && npm run build) && mcui serve --config config.toml   # http://127.0.0.1:8000
```

The first run creates a local `admin` user and prints a one-time password.
Development data (SQLite metrics, users) lives in `./data/`, which git ignores.

## LXD certificates

The backend talks to LXD with mutual TLS; browsers never need a certificate.
Step-by-step guide covering key generation on macOS, trusting the certs in
LXD, fetching the shared `cluster.crt` for verification, and testing with
curl: [`docs/LXD-CREDENTIALS.md`](docs/LXD-CREDENTIALS.md).
Configuration reference, including how to switch metrics to a dedicated port:
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).
Never commit certs or keys (they are git-ignored).


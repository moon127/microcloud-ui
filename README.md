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
#   put client.crt/.key, metrics.crt/.key, server.crt in there; keys chmod 600
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

```bash
# management identity
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -sha384 \
  -keyout client.key -nodes -out client.crt -days 3650 -subj "/CN=microcloud-ui"
lxc config trust add client.crt

# metrics-only identity (can only read /1.0/metrics)
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -sha384 \
  -keyout metrics.key -nodes -out metrics.crt -days 3650 -subj "/CN=microcloud-ui-metrics"
lxc config trust add metrics.crt --type=metrics

# optional: dedicated metrics listener on every member
lxc config set core.metrics_address ":8444"
```

Copy the cluster's `/var/snap/lxd/common/lxd/server.crt` too so the backend can
verify LXD. Never commit certs or keys (they are git-ignored).

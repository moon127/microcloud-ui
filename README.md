# microcloud-ui

A web UI for Canonical MicroCloud (LXD, MicroCeph, MicroOVN) with built-in
metrics: per-instance CPU/memory/disk/network history, Ceph and local storage
usage, and OVN network visibility.

**Status:** planning. See [`docs/PLAN.md`](docs/PLAN.md) and the design log in
[`agents.md`](agents.md).

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

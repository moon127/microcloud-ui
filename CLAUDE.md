# CLAUDE.md

microcloud-ui: a web UI with built-in metrics for Canonical MicroCloud (LXD, MicroCeph,
MicroOVN). Backend FastAPI (Python ≥3.10), frontend React 19 + TS + Vite + uPlot.

## Read first

1. [`agents.md`](agents.md): the ground rules, the **design-decision log (D1–D64)** and
   **Status** (what's built and what's next), plus working conventions and test-cloud facts.
   Read Status and the latest decisions before changing anything.
2. `docs/REQUIREMENTS.md` (requirement IDs), `docs/PLAN.md` (phases).
3. Before touching an area, read its doc:

   | Area | Doc |
   |---|---|
   | Config | `docs/CONFIGURATION.md` |
   | LXD certificates | `docs/LXD-CREDENTIALS.md` |
   | Host metrics | `docs/HOST-METRICS.md` |
   | Ceph metrics | `docs/CEPH-METRICS.md` |
   | Production (bundle, systemd, container) | `docs/DEPLOY.md` |
   | Checks only the owner can run | `docs/LIVE-TESTS.md` |

## Rules that must hold

- **Commits:** author *and* committer `Gareth Woolridge <gareth@woolridge.org.uk>`:
  `git -c user.email=gareth@woolridge.org.uk -c user.name="Gareth Woolridge" commit …`.
  Commit on `main` when a piece of work is done and green; don't push unless asked.
  Messages end with the `Co-Authored-By` line only: **no `Claude-Session:` trailer or
  claude.ai session URL** (D66; `make hooks` installs a commit-msg hook that rejects them).
- **Tests in the same commit** as every change. Coverage must stay **≥90%** for backend and
  frontend (currently about 99.7% and 98%).
- **`make lint test` before every commit:** ruff, mypy strict, eslint, tsc, shellcheck,
  pytest and vitest.
- **The design log:** add or amend a `D<n>` row in `agents.md` for any design decision, and
  update its Status section, in the same commit. Keep the docs in sync with the code.
- **No real IPs, hostnames or screenshots of the real cluster** in new docs, fixtures or
  screenshots. Use 192.0.2.x, 198.51.100.x and 2001:db8::. Screenshots come only from the
  invented demo cluster (`make screenshots`, D59). Recorded fixtures are scrubbed (D26).
- **Live cluster limits (D41):**
  - Anything that changes the real cluster happens only in LXD project **`mcui-test`**.
  - Outside it, the agreed exceptions are `mcui-test-*` ACLs, network forwards and a bridge
    network.
  - Never touch OVN uplinks or Ceph pools.
  - Cluster-wide actions (evacuate, restore, warnings, project create or delete) are
    fixture-tested only; the owner runs them (`docs/LIVE-TESTS.md`).
- **Throwaway VMs are allowed in `mcui-test`** for our work (owner, 2026-11-12):
  - Its limits are normally 0 VMs, 1 GiB, 6 GiB disk, 2 CPU. Raise only what the run needs,
    record the originals, and restore them afterwards.
  - Delete the VM **and** any image it pulled into the project.
  - Never copy LXD keys into a VM unless the owner asks.
  - The snap store works inside such VMs, but the owner said **no rockcraft/chisel for now**.
- **Phase 5** (metric-store evaluation, alerts, export, quota history) waits for the
  owner's go-ahead. Don't start it unprompted.

## Environment (owner's Mac)

- Python venv at `.venv` (Python 3.14; there's no `uv`). Node 22. `make install` sets
  everything up. Run backend tools as `.venv/bin/…`, or through `make`. `npx vitest` and
  `npx tsc` run from `frontend/`.
- **There's no `lxc`, Docker, Podman or Linux VM on the Mac.** Live work goes through the
  LXD API with the dev certificates:
  - `.venv/bin/python scripts/lxd_api.py GET "/1.0/instances?project=mcui-test"`
  - in Python: `req`, `wait`, `exec_`, `push`
  
  For Linux-only checks (installers, systemd, containers), use a throwaway VM as above.
- Dev config and certs: `~/.config/mcui/config.toml` and `~/.config/mcui/certs/`, outside
  the repo. The owner puts them there; they're never pasted into the chat. On a new
  machine, follow README "Moving development to a new machine" (copy, `chmod`,
  `mcui check`). If they're missing, ask the owner rather than making new certificates. Run against the cluster with `MCUI_CONFIG=~/.config/mcui/config.toml make serve`
  (http://127.0.0.1:8000; the dev config has `secure_cookies = false`). node_exporter runs
  over plain http on the three hosts (`[node_exporter] scheme = "http"` in the dev config).
- The shell is zsh: an unquoted `$VAR` holding several words is **not** split into
  arguments. Use arrays or write the arguments out.

## Gotchas learned the hard way

- **Check before creating a test file:** `backend/tests/test_<name>.py` may already exist.
  Overwriting `test_files.py` once silently removed the files-API tests. A sudden drop in
  coverage or test count is the warning sign.
- **Don't run Prettier:** the frontend isn't Prettier-formatted, so it rewrites unrelated
  lines. Format the backend with `make format`, which runs ruff.
- **Ruff line length is 100.** `make lint` also checks `scripts/*.py` using the backend's
  ruff settings.
- **Vitest runs with `frontend/` as the working directory.** Tests that read files use
  paths relative to it.
- **One frontend test was timing-dependent** (YAML editor, then Save). The fix pattern is
  `await waitFor(() => expect(button).toBeEnabled())`.
- **Inside LXD instances on OVN (MTU 1442), container bridges default to 1500 and HTTPS
  stalls.** Build with `--network=host` (D64).
- **Images from the `default` project can't be used to create instances in `mcui-test`.**
  Pull from `https://cloud-images.ubuntu.com/releases` (simplestreams, alias `24.04`). Give
  the VM a NIC on OVN network `default`; apt works through it.
- **Tarballs made on macOS** need `COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata
  --no-fflags`. Otherwise GNU tar prints a warning for every file.
- **After a backend change, the owner's running server needs a restart** before they see it.

## Owner preferences

- Plain, concise reports: what changed, what was verified (live or fixtures), and what's
  left. Say plainly when something wasn't tested.
- Ask before anything outward-facing or outside the agreed scope. Within the agreed scope,
  just proceed.
- Wants a Canonical/Ubuntu feel:
  - Ubuntu orange; README screenshots in **dark**, with one light example.
  - Ubuntu base images.
  - Podman (Quadlet) on cluster members; **never Docker on a member**.
- Wants production paths documented and tested for real: the bundle and the container,
  both verified in VMs (D63, D64).

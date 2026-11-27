# Code review by two agents

Every piece of work is reviewed by two independent review agents before it's committed
(owner, 2026-11-27; D90). They read the same change from different starting points, so
their findings overlap where it matters and cover more between them. The session that
wrote the code then checks every finding against the code, live where it's safe, and
fixes or records each one. A finding isn't accepted or dismissed on an agent's word.

The first review (2026-11-27) found, among others, a path injection that let a project
admin send DELETEs to any LXD endpoint as the app, and terminal secrets reaching read-only
users (D88, D89, advisory GHSA-f59q-5gr8-959p).

## How to run it

1. Finish the change, with tests, and get `make lint test` green.
2. Start both reviewers **in parallel**, each with the brief below plus:
   - the change to review: the diff (`git diff`, or `git diff <from>..HEAD` for a range),
     the commits' subjects, and the design-log rows they touch;
   - "review the change, but follow it into the code it calls and the code that calls it".
3. When both are back, check each finding yourself: read the code; reproduce it in a
   test; for anything that depends on LXD's behaviour, check live in the sandbox (CLAUDE.md
   limits) or in LXD's source. Mark each: confirmed, not a problem (say why), or needs the
   owner.
4. Fix what's confirmed, with a test that reproduces the problem, then `make lint test`.
5. Record it: the commit message says the change was reviewed and what the review
   changed; anything left open goes in `agents.md` Status. Ask the owner about anything
   outward-facing (advisories, releases) or out of scope.

Small changes (typos, docs only, version bumps) don't need a review. Anything touching
the API, authorisation, LXD requests, files, uploads, streams, deployment or CI does.

## The briefs

Both reviewers are **read-only**: no edits, no commits, no `make`, no server, nothing that
talks to LXD or the network, nothing under `~/.config/mcui`. They may read files, run
read-only shell commands and run existing tests (`.venv/bin/pytest -q -p no:warnings
backend/tests/<file>`, `cd frontend && npx vitest run <file>`).

They read `CLAUDE.md`, `agents.md` (the design log and Status), `docs/USERS.md` ("Read this
before deploying") and the docs of the areas the change touches first. The security model:
the app holds one admin certificate to LXD and enforces users, roles and project grants
itself (`mcui/auth/policy.py`, `mcui/api/deps.py`); every route declares one policy; a
project the user can't see answers 404.

### Reviewer A: the server side

Authentication, sessions, CSRF; authorisation of every route the change touches (read vs
operate vs manage vs cluster admin; a second project in the body or query; objects
addressed in another project); what's forwarded to LXD verbatim (config, devices,
profiles, YAML, image sources, network settings) and whether a project user can reach the
host or another project with it; request construction (names in URLs, query parameters;
`LXDClient` refuses unsafe paths, D88); the host-devices right and hidden folders
(D86–D88); operations and events (no websocket secrets, D88); correctness bugs and races.

### Reviewer B: what reaches users and hosts

Streams and files (websocket Origin and session checks, re-checks on attach, paths,
download headers); uploads and imports (size caps, what a tarball can carry); the web shell
(static serving, security headers, cookies, error messages, reverse-proxy trust); the
frontend (rendering LXD-provided strings, no `dangerouslySetInnerHTML`, what's offered to
whom, CSRF token handling); secrets and config (key permissions, logging, the audit log,
the fixture recorder's scrubbing, D26/D89); deployment and supply chain (`deploy/`, the
Containerfile and entrypoint, the Quadlet, `.github/workflows`, pinning, D76/D89);
metrics scraping (TLS, credentials, size caps).

### What each returns

A numbered list of findings, most severe first. For each: title; severity (Critical,
High, Medium, Low, Info) and confidence (confirmed by reading code, likely, speculative);
`file:line`; what's wrong, with a concrete scenario (which user or attacker, which request,
what happens); a proposed fix and a test that would catch it. Then "Checked and found OK":
what was reviewed without a finding, so the two reviews' coverage can be compared. No
generic advice: every finding points at real code.

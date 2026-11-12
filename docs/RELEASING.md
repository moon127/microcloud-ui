# Releasing

There are two channels, both built by `.github/workflows/release.yml`:

| Channel | Built from | Image tags | Bundles | Version |
|---|---|---|---|---|
| **release** | a tag `vX.Y.Z` (D67) | `X.Y.Z`, `X.Y`, `latest` | GitHub Release `vX.Y.Z` | `X.Y.Z` |
| **edge** | every push to `main` once CI passes (D68) | `edge`, `edge-<sha>` | pre-release `edge` | `X.Y.Z.devN` |

`latest` is only ever a release. Images are `ghcr.io/moon127/microcloud-ui`, for amd64
and arm64.

## A release

Pushing a tag `vX.Y.Z` runs:

1. **CI** in full (`ci.yml`), and `scripts/build_version.py release` checks that the tag
   is exactly `v` + `mcui.__version__`. A version still ending `.devN` is refused.
2. **The image** is built from `deploy/container/Containerfile` on two native runners,
   `ubuntu-24.04` (amd64) and `ubuntu-24.04-arm` (arm64; free for public repositories),
   each pushed by digest. A merge job then tags both as one image,
   **`ghcr.io/moon127/microcloud-ui`**, with `X.Y.Z`, `X.Y` and `latest`.
3. **The offline bundles** are built for Ubuntu 26.04 (py3.14), 24.04 (py3.12) and 22.04
   (py3.10), x86_64 and aarch64: six in all.
4. **The GitHub Release** is created with the bundles attached. Its notes list the
   commits since the previous release (`scripts/build_version.py changes`), leaving out
   the version bumps ("Release X.Y.Z", "Back to development"), then a compare link.
   Commit subjects are the release notes, so write them for a reader.

The build runs on GitHub's runners, never on a cluster member.

## Steps

```bash
# 1. set the version (backend/mcui/__init__.py) and commit it
sed -i '' 's/^__version__ = .*/__version__ = "0.1.0"/' backend/mcui/__init__.py
make lint test
git commit -am "Release 0.1.0"

# 2. tag and push; anything after the tag message's first line heads the release notes
git tag -a v0.1.0 -m "microcloud-ui 0.1.0" -m "Why this release matters, if it isn't obvious."
git push origin main v0.1.0

# 3. afterwards, move main on to the next development version
sed -i '' 's/^__version__ = .*/__version__ = "0.1.1.dev0"/' backend/mcui/__init__.py
git commit -am "Back to development: 0.1.1.dev0"
```

Pushing the release commit to `main` also makes an edge build of it, as `X.Y.Z`.

Follow the run under **Actions → Release** (or `gh run watch`). If it fails before the release job, nothing
has been published: fix the problem, delete the tag (`git tag -d v0.1.0`,
`git push origin :v0.1.0`) and tag again. If the image was pushed but a later job failed,
re-run the failed jobs rather than re-tagging.

## Edge: the tip of main

After CI passes on a push to `main`, the same workflow builds that commit:

- **Version:** the source says e.g. `0.1.1.dev0`; the build is stamped
  `0.1.1.devN`, N = commits since the last release tag (`scripts/build_version.py dev`).
  `mcui --version`, `/api/health` and the bundle names show it. Only the build is
  stamped, never a commit.
- **Image:** `:edge` (moves with each build) and `:edge-<sha>` (kept, to go back to a
  build). The image's `org.opencontainers.image.revision` label is the commit.
- **Bundles:** the pre-release **edge** on the Releases page, its tag moved to the commit
  and its six bundles replaced. Being a pre-release it never shows as "Latest". Its notes
  list the commits since the latest release.

A newer push cancels an edge build still running. CI runs from pull requests, and from
forks, never make edge builds. Old `edge-<sha>` images stay in the registry; delete them
from the package settings when they pile up (GHCR storage is free for public packages).

To follow edge on a host, see docs/DEPLOY.md §9.1.

## Dry run

**Actions → Release → Run workflow** on `main` runs CI and builds the image (both
architectures, natively) and the bundles, stamped as a dev version, but logs in to
nothing, pushes nothing and creates no release. The bundles are kept as a downloadable
artifact of the run.

## Package visibility

The package `ghcr.io/moon127/microcloud-ui` was created by the first release (0.1.0) and is
**public**, like the repository it is linked to through the image's
`org.opencontainers.image.source` annotation: hosts pull it without logging in. Its
settings are under GitHub → your profile → Packages → `microcloud-ui`.

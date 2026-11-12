# Releasing

A release is a git tag `vX.Y.Z`. Pushing it runs `.github/workflows/release.yml` (D67):

1. **CI** runs in full (`ci.yml`), and `scripts/release_check.py` checks that the tag is
   exactly `v` + `mcui.__version__`. A version still ending `.devN` is refused.
2. **The image** is built from `deploy/container/Containerfile` for `linux/amd64` and
   `linux/arm64` and pushed to **`ghcr.io/moon127/microcloud-ui`** as `X.Y.Z`, `X.Y`
   and `latest`.
3. **The offline bundles** are built for Ubuntu 24.04 (py3.12) and 22.04 (py3.10), x86_64
   and aarch64.
4. **The GitHub Release** is created with the bundles attached and notes generated from
   the commits since the previous tag.

The build runs on GitHub's runners, never on a cluster member.

## Steps

```bash
# 1. set the version (backend/mcui/__init__.py) and commit it
sed -i '' 's/^__version__ = .*/__version__ = "0.1.0"/' backend/mcui/__init__.py
make lint test
git commit -am "Release 0.1.0"

# 2. tag and push
git tag -a v0.1.0 -m "microcloud-ui 0.1.0"
git push origin main v0.1.0

# 3. afterwards, move main on to the next development version
sed -i '' 's/^__version__ = .*/__version__ = "0.1.1.dev0"/' backend/mcui/__init__.py
git commit -am "Back to development: 0.1.1.dev0"
```

Follow the run under **Actions → Release**. If it fails before the release job, nothing
has been published: fix the problem, delete the tag (`git tag -d v0.1.0`,
`git push origin :v0.1.0`) and tag again. If the image was pushed but a later job failed,
re-run the failed jobs rather than re-tagging.

## Dry run

**Actions → Release → Run workflow** on `main` runs CI and builds the image (both
architectures) and the bundles, but logs in to nothing, pushes nothing and creates no
release. The bundles are kept as a downloadable artifact of the run.

## Once only: make the package public

The first push creates the package `microcloud-ui` under your GitHub account as
**private**. To let hosts pull it without logging in: GitHub → your profile → Packages →
`microcloud-ui` → Package settings → Change visibility → Public. The image's
`org.opencontainers.image.source` label links the package to this repository.

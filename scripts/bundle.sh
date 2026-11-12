#!/bin/bash
# Build the offline install bundle for Ubuntu (docs/DEPLOY.md, D63):
#   dist/mcui-<version>-py<PYVER>-<ARCH>.tar.gz
# holding the mcui wheel (with the built SPA inside), wheels for every dependency
# for the target Python and CPU, and deploy/ (install.sh, unit, example config).
# Usage: scripts/bundle.sh   (env: PYVER=3.12 for 24.04, 3.10 for 22.04; ARCH=x86_64|aarch64)
set -euo pipefail

PYVER=${PYVER:-3.12}
ARCH=${ARCH:-x86_64}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BIN=${BIN:-$ROOT/.venv/bin}
cd "$ROOT"

version=$("$BIN/python" -c 'import mcui; print(mcui.__version__)')
name="mcui-$version-py$PYVER-$ARCH"
out="dist/$name"

(cd frontend && npm run build)
rm -rf backend/mcui/static backend/build
cp -R frontend/dist backend/mcui/static
trap 'rm -rf backend/mcui/static backend/build' EXIT

rm -rf "$out" "$out.tar.gz"
mkdir -p "$out/wheels"
"$BIN/pip" wheel --quiet --no-deps --wheel-dir "$out/wheels" ./backend
whl=("$out"/wheels/mcui-*.whl)
"$BIN/python" -c "import sys, zipfile; n = zipfile.ZipFile(sys.argv[1]).namelist(); \
assert 'mcui/static/index.html' in n, 'SPA missing from the wheel'" "${whl[0]}"

# binary wheels for the target (Ubuntu 22.04+ glibc is manylinux_2_35 or newer)
platforms=()
for p in manylinux_2_28 manylinux_2_17 manylinux2014; do platforms+=(--platform "${p}_$ARCH"); done
# pip judges markers by the Python running it, not --python-version: name the
# backports the target needs (backend/pyproject.toml) explicitly
extra=()
case "$PYVER" in 3.10) extra+=("tomli>=2.0") ;; esac
"$BIN/pip" download --quiet --dest "$out/wheels" --only-binary=:all: \
    --implementation cp --python-version "$PYVER" "${platforms[@]}" "${whl[0]}" ${extra[@]+"${extra[@]}"}

cp deploy/install.sh deploy/mcui.service deploy/mcui-cli deploy/config.example.toml "$out/"
cp docs/DEPLOY.md "$out/README.md"
cp LICENSE frontend/THIRD_PARTY_NOTICES.md "$out/"
mkdir -p "$out/container"
cp deploy/container/mcui.container deploy/container/compose.yaml "$out/container/"
echo "$PYVER" >"$out/PYTHON"
# no macOS metadata (GNU tar warns about every file) and root-owned entries
if tar --version | grep -q bsdtar; then
    COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata --no-fflags --uid 0 --gid 0 --uname root --gname root \
        -C dist -czf "$out.tar.gz" "$name"
else
    tar --no-xattrs --owner=0 --group=0 --numeric-owner -C dist -czf "$out.tar.gz" "$name"
fi
echo "built $out.tar.gz ($(du -h "$out.tar.gz" | cut -f1), $(find "$out/wheels" -name '*.whl' | wc -l | tr -d ' ') wheels)"

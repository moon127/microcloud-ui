#!/bin/bash
# Install, upgrade or remove microcloud-ui on Ubuntu from an unpacked bundle
# (`make bundle`; docs/DEPLOY.md, D63). Run as root from the bundle directory:
#
#   sudo ./install.sh               install or upgrade, then (re)start the service
#   sudo ./install.sh --uninstall   remove the service and program; keep config and data
#   sudo ./install.sh --purge       also remove /etc/mcui, /var/lib/mcui and the mcui user
#
# Needs only python3 and python3-venv from Ubuntu; every Python package comes
# from the bundle's wheels/ directory, so nothing is fetched from the internet.
set -euo pipefail

PREFIX=/opt/mcui
ETC=/etc/mcui
DATA=/var/lib/mcui
UNIT=/etc/systemd/system/mcui.service
CLI=/usr/local/bin/mcui
KEEP=3 # installs kept under $PREFIX for rollback

HERE=$(cd "$(dirname "$0")" && pwd)

say() { printf '==> %s\n' "$*"; }
die() {
    printf 'install.sh: %s\n' "$*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || die "run as root (sudo ./install.sh)"

uninstall() {
    say "stopping and removing the service"
    systemctl disable --now mcui.service 2>/dev/null || true
    rm -f "$UNIT" "$CLI"
    systemctl daemon-reload
    rm -rf "$PREFIX"
    if [ "${1:-}" = purge ]; then
        say "removing $ETC, $DATA and the mcui user"
        rm -rf "$ETC" "$DATA"
        userdel mcui 2>/dev/null || true
    else
        say "kept $ETC and $DATA (remove them with --purge)"
    fi
}

case "${1:-}" in
"") ;;
--uninstall)
    uninstall
    exit 0
    ;;
--purge)
    uninstall purge
    exit 0
    ;;
*) die "unknown option $1 (use --uninstall or --purge)" ;;
esac

# --- what's in the bundle -------------------------------------------------
shopt -s nullglob
wheels=("$HERE"/wheels/mcui-*.whl)
[ ${#wheels[@]} -eq 1 ] || die "expected one wheels/mcui-*.whl next to install.sh"
version=$(basename "${wheels[0]}" | cut -d- -f2)
want_py=$(cat "$HERE/PYTHON")

# --- prerequisites ----------------------------------------------------------
command -v python3 >/dev/null || die "python3 is missing: apt install python3 python3-venv"
have_py=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
[ "$have_py" = "$want_py" ] ||
    die "this bundle is for Python $want_py but python3 is $have_py; build one with: make bundle PYVER=$have_py"
python3 -c 'import ensurepip, venv' 2>/dev/null || die "python3-venv is missing: apt install python3-venv"
command -v openssl >/dev/null || die "openssl is missing: apt install openssl"

# --- user and directories ---------------------------------------------------
if ! getent passwd mcui >/dev/null; then
    say "creating system user mcui"
    useradd --system --user-group --home-dir "$DATA" --no-create-home --shell /usr/sbin/nologin mcui
fi
install -d -o root -g mcui -m 0750 "$ETC"
install -d -o mcui -g mcui -m 0700 "$ETC/certs" "$DATA"

# --- program: a fresh venv per version, "current" points at the live one ----
# (a venv can't be moved, so each install gets its own directory)
dest="$PREFIX/$version-$(date +%Y%m%d%H%M%S)"
say "installing microcloud-ui $version into $dest"
install -d -m 0755 "$PREFIX"
python3 -m venv "$dest"
if ! "$dest/bin/pip" install --quiet --no-index --find-links "$HERE/wheels" "${wheels[0]}"; then
    rm -rf "$dest"
    die "pip install failed; the running version is unchanged"
fi
previous=$(readlink "$PREFIX/current" 2>/dev/null || true)
ln -sfn "$dest" "$PREFIX/current.new"
mv -T "$PREFIX/current.new" "$PREFIX/current"

# keep the newest $KEEP versions (and whatever was live before this run)
mapfile -t old < <(find "$PREFIX" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2- | tail -n +$((KEEP + 1)))
for d in "${old[@]}"; do
    [ "$d" = "$previous" ] || rm -rf "$d"
done

# --- config and HTTPS certificate (only when missing) -----------------------
if [ ! -e "$ETC/config.toml" ]; then
    say "writing $ETC/config.toml (edit it: see docs/DEPLOY.md)"
    install -o root -g mcui -m 0640 "$HERE/config.example.toml" "$ETC/config.toml"
fi
if [ ! -e "$ETC/certs/ui.key" ]; then
    fqdn=$(hostname -f 2>/dev/null || hostname)
    san="DNS:$(hostname),DNS:$fqdn"
    for ip in $(hostname -I); do san="$san,IP:$ip"; done
    say "making a self-signed HTTPS certificate for $san"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
        -subj "/CN=$fqdn" -addext "subjectAltName=$san" \
        -keyout "$ETC/certs/ui.key" -out "$ETC/certs/ui.crt" 2>/dev/null
    chown mcui:mcui "$ETC/certs/ui.key" "$ETC/certs/ui.crt"
    chmod 600 "$ETC/certs/ui.key"
    chmod 644 "$ETC/certs/ui.crt"
fi

# --- service ----------------------------------------------------------------
install -m 0644 "$HERE/mcui.service" "$UNIT"
install -m 0755 "$HERE/mcui-cli" "$CLI"
systemctl daemon-reload
systemctl enable mcui.service >/dev/null 2>&1
say "restarting mcui.service"
systemctl restart mcui.service
sleep 3
if systemctl is-active --quiet mcui.service; then
    say "running: microcloud-ui $version"
else
    systemctl --no-pager --lines=20 status mcui.service || true
    die "the service did not start; see: journalctl -u mcui"
fi

if [ -n "$previous" ] && [ "$previous" != "$dest" ]; then
    say "previous version kept for rollback: $previous"
fi
cat <<EOF

Next steps (docs/DEPLOY.md):
  - first time: sudo mcui user add admin --role admin
  - LXD: put the certificates in $ETC/certs, fill in [lxd] in $ETC/config.toml,
    then: sudo mcui check && sudo systemctl restart mcui
  - open https://$(hostname -f 2>/dev/null || hostname):$(sed -n 's/^port *= *\([0-9]*\).*/\1/p' "$ETC/config.toml" | head -1)/
EOF

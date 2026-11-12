#!/bin/sh
# Container entrypoint (D64). `serve` (default): make a self-signed HTTPS certificate in
# /var/lib/mcui/tls if there is none, then run the server. `health`: the health check.
# Anything else runs the mcui CLI, e.g. `podman exec -it mcui mcui user add admin`.
set -eu

tls=/var/lib/mcui/tls

selfsigned() {
    [ -e "$tls/ui.key" ] && return 0
    # names the browser uses: MCUI_TLS_SAN="DNS:mcui.example.org,IP:192.0.2.10"
    san="DNS:localhost,IP:127.0.0.1,DNS:$(hostname)${MCUI_TLS_SAN:+,$MCUI_TLS_SAN}"
    echo "mcui-entrypoint: making a self-signed HTTPS certificate for $san" >&2
    mkdir -p "$tls"
    chmod 700 "$tls"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
        -subj "/CN=$(hostname)" -addext "subjectAltName=$san" \
        -keyout "$tls/ui.key" -out "$tls/ui.crt" 2>/dev/null
    chmod 600 "$tls/ui.key"
}

case "${1:-serve}" in
serve)
    [ $# -gt 0 ] && shift
    selfsigned
    exec mcui serve "$@"
    ;;
health)
    # https first (the default config), then plain http (behind a reverse proxy)
    exec python3 - "${MCUI_SERVER__PORT:-8843}" <<'PY'
import ssl, sys, urllib.request

port = sys.argv[1]
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE  # our own server on loopback, possibly self-signed
for scheme in ("https", "http"):
    try:
        urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/api/health", context=ctx, timeout=4)
        sys.exit(0)
    except Exception:
        pass
sys.exit(1)
PY
    ;;
mcui)
    shift
    exec mcui "$@"
    ;;
*)
    exec mcui "$@"
    ;;
esac

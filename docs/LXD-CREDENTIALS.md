# Creating LXD credentials for microcloud-ui

microcloud-ui talks to LXD over mutual TLS. Both sides prove who they are:

| File | What it is | Where it comes from | Secret? |
|---|---|---|---|
| `client.crt` / `client.key` | microcloud-ui's identity for the **management API** (instances, networks, storage…) | You generate it on your Mac | key: **yes** |
| `metrics.crt` / `metrics.key` | identity that can **only** read `/1.0/metrics` | You generate it on your Mac | key: **yes** |
| `lxd-cluster.crt` | the certificate LXD presents, used to **verify** the cluster (pinning) | Copied from the cluster | no (public) |

Only the `.crt` files ever leave your Mac. The `.key` files stay where
microcloud-ui runs.

All `lxc` commands below run **once, on any one cluster member**. The trust
store is shared across the whole cluster.

---

## 1. Generate the two client key pairs (on your Mac)

These commands work with both macOS's bundled LibreSSL and OpenSSL 1.1+/3.x.

```bash
mkdir -p ~/.config/mcui/certs && chmod 700 ~/.config/mcui/certs
cd ~/.config/mcui/certs

# management identity
openssl ecparam -name secp384r1 -genkey -noout -out client.key
openssl req -new -x509 -sha384 -days 3650 -key client.key \
  -subj "/CN=microcloud-ui" -out client.crt

# metrics-only identity
openssl ecparam -name secp384r1 -genkey -noout -out metrics.key
openssl req -new -x509 -sha384 -days 3650 -key metrics.key \
  -subj "/CN=microcloud-ui-metrics" -out metrics.crt

chmod 600 *.key
```

(The LXD docs' one-liner `openssl req -x509 -newkey ec -pkeyopt …` does the
same thing, but `-pkeyopt` can fail on macOS LibreSSL.)

## 2. Copy the public certificates to a cluster member

```bash
scp ~/.config/mcui/certs/client.crt ~/.config/mcui/certs/metrics.crt <user>@<member-ip>:/tmp/
```

## 3. Trust them in LXD (on that member)

```bash
# metrics-only certificate: can read /1.0/metrics and nothing else
lxc config trust add /tmp/metrics.crt --type=metrics --name microcloud-ui-metrics
```

Then choose **one** option for the management certificate.

**Option A: full access (recommended for the test cloud).**
A plain client certificate gets full admin rights, which microcloud-ui needs
to manage networks, storage pools and cluster members.

```bash
lxc config trust add /tmp/client.crt --name microcloud-ui
```

**Option B: fine-grained identity (LXD 5.21+).**
Same end result as A, but managed through LXD's group-based permissions.
Useful if you want to start read-only and upgrade later.

```bash
lxc auth group create mcui
lxc auth group permission add mcui server viewer     # read-only to start…
# lxc auth group permission add mcui server admin    # …full access later
lxc auth identity create tls/microcloud-ui /tmp/client.crt --group mcui
```

> Restricted certificates (`--restricted --projects …`) are **not** suitable.
> They are confined to projects and can't manage cluster-wide objects such as
> storage pools, networks or members.

Check the result and tidy up:

```bash
lxc config trust list        # option A + metrics appear here
lxc auth identity list       # all identities, including option B
rm /tmp/client.crt /tmp/metrics.crt
```

## 4. Fetch the cluster certificate (for verifying LXD)

In a cluster, every member answers on 8443 with the **same shared certificate**,
`cluster.crt`, not the member's own `server.crt`. That is the file to pin.

On the member:
```bash
sudo cat /var/snap/lxd/common/lxd/cluster.crt     # copy to your Mac as lxd-cluster.crt
sudo openssl x509 -noout -fingerprint -sha256 -in /var/snap/lxd/common/lxd/cluster.crt
```

Or from your Mac, then compare the fingerprint with the one above:
```bash
cd ~/.config/mcui/certs
openssl s_client -connect <member-ip>:8443 </dev/null 2>/dev/null | openssl x509 -out lxd-cluster.crt
openssl x509 -noout -fingerprint -sha256 -in lxd-cluster.crt
```

Check which names the certificate is valid for:
```bash
openssl x509 -noout -text -in lxd-cluster.crt | grep -A1 "Subject Alternative Name"
```
If your member IPs aren't listed (usual for the self-signed cluster cert),
set `server_name` in microcloud-ui's config to one of the listed DNS names.
The connection is still fully verified against the pinned cert.

## 5. Optional: dedicated metrics port

`core.metrics_address` is a **per-member** setting, so set it on each member:

```bash
for m in $(lxc cluster list -f csv -c n); do
  lxc config set core.metrics_address ":8444" --target "$m"
done
```

Without it, `/1.0/metrics` is still served on the normal 8443 port.

## 6. Quick test from your Mac (before microcloud-ui exists)

```bash
cd ~/.config/mcui/certs
IP=<member-ip>; SNI=<name-from-SAN>     # SNI only if step 4 said you need it

# management API: should return your identity, with "auth": "trusted"
curl -s --cacert lxd-cluster.crt --cert client.crt --key client.key \
  --resolve "$SNI:8443:$IP" "https://$SNI:8443/1.0" | python3 -m json.tool | head -20

# metrics: should return Prometheus text (lxd_cpu_seconds_total …)
curl -s --cacert lxd-cluster.crt --cert metrics.crt --key metrics.key \
  --resolve "$SNI:8444:$IP" "https://$SNI:8444/1.0/metrics" | head
```
(Use port 8443 for metrics if you skipped step 5. If the SAN already contains the IP, drop `--resolve` and use `https://$IP:8443`.)

## 7. What to send me

- The member IPs, plus the `server_name` from step 4 if you needed one.
- Whether you used option A or B, and whether you set the 8444 metrics port.
- The three files `client.crt`, `metrics.crt` and `lxd-cluster.crt`. For live testing from this session I also need `client.key` and `metrics.key`. Treat those as passwords: use a throwaway test-cloud pair and revoke it afterwards with `lxc config trust remove <fingerprint>` (or `lxc auth identity delete tls/microcloud-ui`).

## Revoking

```bash
lxc config trust list                       # find fingerprints
lxc config trust remove <fingerprint>
lxc auth identity delete tls/microcloud-ui  # if you used option B
```

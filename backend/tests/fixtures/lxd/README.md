# LXD fixtures

Hand-written in the LXD 5.21 response format (shapes taken from the LXD REST
API reference), using documentation IPs (192.0.2.0/24) and fake names.
Real responses recorded from the test cloud with `mcui record-fixtures` go in
`../recorded/` once the owner captures them (D26).

| File | Endpoint |
|---|---|
| `server_clustered.json` | `GET /1.0` metadata, clustered, trusted |
| `server_standalone.json` | `GET /1.0` metadata, standalone, trusted |
| `cluster_members.json` | `GET /1.0/cluster/members?recursion=1` metadata |
| `metrics_m1_t0.txt`, `metrics_m1_t15.txt` | `/1.0/metrics` on member m1, 15 s apart |
| `metrics_m2_t0.txt` | `/1.0/metrics` on member m2 |

# LXD REST API surface (reference)

Path templates from `canonical/lxd` `doc/rest-api.yaml` @ cc4facd (2026-11-04); `?recursion=`/`?public` variants omitted, parameters normalised to `{}`.

MicroCloud clusters may run an older LXD (5.21 LTS / 6.x), so the backend must read `api_extensions` from `GET /1.0` and hide features the server lacks. Most collection endpoints accept `?project=`, `?all-projects=true`, `?target=<member>` and `?recursion=1|2`.

126 paths in 24 groups.

## (root) (1)

- `/1.0`

## auth (19)

- `/1.0/auth/groups`
- `/1.0/auth/groups/{}`
- `/1.0/auth/identities`
- `/1.0/auth/identities/bearer`
- `/1.0/auth/identities/bearer/{}`
- `/1.0/auth/identities/bearer/{}/state`
- `/1.0/auth/identities/bearer/{}/token`
- `/1.0/auth/identities/current`
- `/1.0/auth/identities/oidc`
- `/1.0/auth/identities/oidc/{}`
- `/1.0/auth/identities/oidc/{}/state`
- `/1.0/auth/identities/tls`
- `/1.0/auth/identities/tls/{}`
- `/1.0/auth/identities/tls/{}/state`
- `/1.0/auth/identity-provider-groups`
- `/1.0/auth/identity-provider-groups/{}`
- `/1.0/auth/oidc-sessions`
- `/1.0/auth/oidc-sessions/{}`
- `/1.0/auth/permissions`

## certificates (2)

- `/1.0/certificates`
- `/1.0/certificates/{}`

## cluster (10)

- `/1.0/cluster`
- `/1.0/cluster/certificate`
- `/1.0/cluster/groups`
- `/1.0/cluster/groups/{}`
- `/1.0/cluster/links`
- `/1.0/cluster/links/{}`
- `/1.0/cluster/links/{}/state`
- `/1.0/cluster/members`
- `/1.0/cluster/members/{}`
- `/1.0/cluster/members/{}/state`

## events (1)

- `/1.0/events`

## image-registries (3)

- `/1.0/image-registries`
- `/1.0/image-registries/{}`
- `/1.0/image-registries/{}/images`

## images (7)

- `/1.0/images`
- `/1.0/images/aliases`
- `/1.0/images/aliases/{}`
- `/1.0/images/{}`
- `/1.0/images/{}/export`
- `/1.0/images/{}/refresh`
- `/1.0/images/{}/secret`

## instances (23)

- `/1.0/instances`
- `/1.0/instances/{}`
- `/1.0/instances/{}/backups`
- `/1.0/instances/{}/backups/{}`
- `/1.0/instances/{}/backups/{}/export`
- `/1.0/instances/{}/console`
- `/1.0/instances/{}/exec`
- `/1.0/instances/{}/files`
- `/1.0/instances/{}/logs`
- `/1.0/instances/{}/logs/exec-output`
- `/1.0/instances/{}/logs/exec-output/{}`
- `/1.0/instances/{}/logs/{}`
- `/1.0/instances/{}/metadata`
- `/1.0/instances/{}/metadata/templates`
- `/1.0/instances/{}/rebuild`
- `/1.0/instances/{}/sftp`
- `/1.0/instances/{}/snapshots`
- `/1.0/instances/{}/snapshots/{}`
- `/1.0/instances/{}/snapshots/{}/bitmaps`
- `/1.0/instances/{}/snapshots/{}/bitmaps/{}`
- `/1.0/instances/{}/snapshots/{}/nbd`
- `/1.0/instances/{}/state`
- `/1.0/instances/{}/uefi-vars`

## metadata (1)

- `/1.0/metadata/configuration`

## metrics (1)

- `/1.0/metrics`

## network-acls (3)

- `/1.0/network-acls`
- `/1.0/network-acls/{}`
- `/1.0/network-acls/{}/log`

## network-allocations (1)

- `/1.0/network-allocations`

## network-zones (4)

- `/1.0/network-zones`
- `/1.0/network-zones/{}`
- `/1.0/network-zones/{}/records`
- `/1.0/network-zones/{}/records/{}`

## networks (13)

- `/1.0/networks`
- `/1.0/networks/{}`
- `/1.0/networks/{}/forwards`
- `/1.0/networks/{}/forwards/{}`
- `/1.0/networks/{}/leases`
- `/1.0/networks/{}/load-balancer-pools`
- `/1.0/networks/{}/load-balancer-pools/{}`
- `/1.0/networks/{}/load-balancer-pools/{}/state`
- `/1.0/networks/{}/load-balancers`
- `/1.0/networks/{}/load-balancers/{}`
- `/1.0/networks/{}/peers`
- `/1.0/networks/{}/peers/{}`
- `/1.0/networks/{}/state`

## operations (4)

- `/1.0/operations`
- `/1.0/operations/{}`
- `/1.0/operations/{}/wait`
- `/1.0/operations/{}/websocket`

## placement-groups (2)

- `/1.0/placement-groups`
- `/1.0/placement-groups/{}`

## profiles (2)

- `/1.0/profiles`
- `/1.0/profiles/{}`

## projects (3)

- `/1.0/projects`
- `/1.0/projects/{}`
- `/1.0/projects/{}/state`

## replicators (3)

- `/1.0/replicators`
- `/1.0/replicators/{}`
- `/1.0/replicators/{}/state`

## resources (1)

- `/1.0/resources`

## state (1)

- `/1.0/state`

## storage-pools (17)

- `/1.0/storage-pools`
- `/1.0/storage-pools/{}`
- `/1.0/storage-pools/{}/buckets`
- `/1.0/storage-pools/{}/buckets/{}`
- `/1.0/storage-pools/{}/buckets/{}/keys`
- `/1.0/storage-pools/{}/buckets/{}/keys/{}`
- `/1.0/storage-pools/{}/resources`
- `/1.0/storage-pools/{}/volumes`
- `/1.0/storage-pools/{}/volumes/{}`
- `/1.0/storage-pools/{}/volumes/{}/{}`
- `/1.0/storage-pools/{}/volumes/{}/{}/backups`
- `/1.0/storage-pools/{}/volumes/{}/{}/backups/{}`
- `/1.0/storage-pools/{}/volumes/{}/{}/backups/{}/export`
- `/1.0/storage-pools/{}/volumes/{}/{}/nbd`
- `/1.0/storage-pools/{}/volumes/{}/{}/snapshots`
- `/1.0/storage-pools/{}/volumes/{}/{}/snapshots/{}`
- `/1.0/storage-pools/{}/volumes/{}/{}/state`

## storage-volumes (2)

- `/1.0/storage-volumes`
- `/1.0/storage-volumes/{}`

## warnings (2)

- `/1.0/warnings`
- `/1.0/warnings/{}`


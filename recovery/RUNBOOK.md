# Observability placement and recovery

## Storage and version boundary

Grafana 11.2.0, Prometheus 2.54.1 and Tempo 2.6.1 keep their deployed
multi-architecture image indexes, Service identities, configuration and credentials.
The three new static local PVs use `/var/lib/observability/<service>` on **mp**,
5 GiB each, explicit claim binding and **Retain**. Old h7 claims remain available.
Do not prune them. A minAvailable=1 PodDisruptionBudget per service prevents
voluntary descheduler evictions of these single-replica local stores. Existing
Prometheus/Tempo events explicitly identified descheduler LowNodeUtilization.
The budget is not high availability and does not block intentional Deployment scaling.
This procedure patches the captured live Deployment templates;
it does not apply unrelated hardening differences already present in the base.

Active databases stay local. [Prometheus does not support NFS](https://prometheus.io/docs/prometheus/latest/storage/#operational-aspects),
and [SQLite WAL requires processes on the same host](https://www.sqlite.org/wal.html).
Tempo retains its existing single-process local WAL/block backend. NAS is the
backup destination, using `nfs-synology-v4` (`nfsvers=4.1,hard`, Retain) on the
reviewed Synology export. Backup readback does not prove NAS power-loss durability.

## Maintenance migration

Use Python 3.12+ locally, configured `kubectl --context k3s-heaven`, and SSH aliases
`mp` and `synology` with `sudo -n`. Both nodes need the pinned image prefetched;
the helper waits up to 20 minutes before stopping the source. The live h7 capture
pod uses pinned Python 3.14, so it does not depend on h7's Python 3.8 archive support.
NAS standalone verification/preparation supports Python 3.8; it was checked on
DSM Python 3.8.15 with a synthetic archive before maintenance.

All evidence contains private application data/credentials. Keep it outside Git,
under a new 0700 directory. The NAS migration parent must already exist, belong
to root and be private; `/volume1/@observability-migration-20260916` was created
for this migration. No source path or existing destination is overwritten.
Confirm mp free capacity and the common NFS probe before the maintenance window.
Migration order is Grafana, Tempo, then Prometheus, so monitoring remains available
for the earlier moves. An operator must approve the actual cutover window.

For **each** service, capture acceptance immediately before its migration:

```bash
set -euo pipefail
SERVICE=grafana # then tempo, then prometheus
EVIDENCE=/absolute/private/observability-migration
python3 -m recovery.acceptance "$SERVICE" --directory "$EVIDENCE/$SERVICE-before"
python3 -m recovery.migrate "$SERVICE" \
  --evidence "$EVIDENCE/$SERVICE-cutover" \
  --nas-root /volume1/@observability-migration-20260916
python3 -m recovery.acceptance "$SERVICE" \
  --directory "$EVIDENCE/$SERVICE-after" \
  --baseline "$EVIDENCE/$SERVICE-before/acceptance.json"
```

The helper validates the actual running image digest, source/target identities and
node, refuses pre-existing target PVs/PVCs/data, pre-pulls the exact image, and
rechecks source storage before stopping. It captures only this Deployment's
historical terminal pods and removes them with UID/resourceVersion preconditions
when necessary for Recreate. Active pod waits ignore terminal history.
It changes the old PV to Retain, stops one source Deployment, waits for active
pods to disappear, and archives **all** of its data including history/WAL.
Grafana SQLite becomes a verified standalone database through SQLite's backup API.
The private namespace export contains the existing Secrets and configuration.
The snapshot is verified locally and on NAS before preparation on mp. Numeric
ownership, permissions and timestamps are restored before the new data directory
is installed; symlinks require individual review. Only then does the helper bind
new static storage and start the captured template on mp.

Grafana's four provisioned dashboard JSON files are frozen from the live pod in
`deploy/k3s/dashboards`, preserving UIDs and titles. Startup no longer downloads
new revisions. The frozen ConfigMap is staged with server-side apply before any
source stop, avoiding Kubernetes' 256 KiB client-side annotation limit. Acceptance checks authenticated dashboard/datasource/user identity,
zero alert rules, unchanged credentials and effective ini/default hashes.
Prometheus acceptance compares the **same pre-migration historical timestamp**
for `up`; Tempo checks readiness and unchanged configuration. Cold-archive
verification protects its local history, but no synthetic trace is injected.

### Failure and rollback

Read the private `migration.json` receipt before acting. Before a target start
attempt, the helper restores the old template/replica on h7 if its UID/template
still match. An uncertain scale API response is treated as potentially applied.

**After a target start attempt, automatic rollback is disabled.** Even a failed
readiness check can leave new writes on mp. Preserve both stores. Stop the affected
Deployment, capture and verify its new local data and namespace state, then
prepare that capture in a **new** h7 directory and new Retain PV/PVC. Review a
UID/resourceVersion-tested patch of the saved template to that replacement claim,
then perform the same acceptance checks. Never point back to the stale old PVC
after new writes without explicitly accepting their loss. Do not delete either
store or the NAS migration snapshot during recovery.

## Scheduled NAS backups

Stage separately; the CronJob is initially suspended:

```bash
python3 -m recovery.install --evidence /absolute/private/backup-staging
# After all three moves and their application acceptance pass:
python3 -m recovery.install --enable --evidence /absolute/private/backup-enabling
```

The installer validates each PVC/PV UID, binding, local mp path or exact NFS
server/export, hard NFSv4.1 options, and Retain. It creates a read-only ClusterRole
limited to `get` on these four PV names. A namespace Role permits read-only recovery
exports, including Secrets; these stay in 0600 archives, never Job logs.
The source PVCs are mounted read-only. The container drops all capabilities except
`DAC_READ_SEARCH`, needed for mixed application UIDs, and uses a read-only root.
It cannot execute in application pods or change Kubernetes resources.

The first manual Job must complete, and its three application components must pass
NAS-side offline metadata preparation in a new private workspace, before the installer unsuspends the daily
**10:50 UTC** schedule. Jobs are bounded to 40 minutes and never overlap. Failed
or incomplete sets are not retention candidates. Only the newest **14 completed,
owned** sets are retained; migration evidence and unrelated directories are not
removed. Each set has SHA-256/size/gzip/tar validation and is published only after
verification. Each Job checks storage identity and active source pod placement
both before and after capture.

Recurring recovery scope is deliberately narrower than the cold migration:

- **Grafana:** online SQLite backup plus stable application files, plugins, and
  namespace configuration/Secrets. Existing cryptographic settings are retained.
  The Job requires `grafana.db` and rejects physical/quiescent SQLite fallback
  methods; inability to obtain an online SQLite backup fails the set.
- **Prometheus:** only completed immutable ULID blocks with valid metadata/index/
  chunks. WAL, head chunks and transient compaction files are excluded. Overlapping
  block ranges or changes during capture fail the Job. **Roughly the newest three
  hours may be absent**, in addition to the daily schedule interval.
- **Tempo:** only finalized tenant/block directories with `meta.json`, plus the
  cluster seed. Active WAL, partial/compacted blocks and dynamic indexes are omitted.
  **Recent traces are not guaranteed**, in addition to the daily schedule interval.
  Block identification follows [Tempo 2.6.1's local backend](https://github.com/grafana/tempo/blob/v2.6.1/tempodb/backend/local/local.go).

No service is paused, no admin API is enabled, and no snapshots are presented as a
boot-tested whole-cluster restore. A failed backup requires investigation; it is
not permission to weaken source checks. Private incomplete directories may be
reviewed and deleted individually after identifying their failed Job.

## Offline restore and metadata preparation

Each completed set contains `restore.py`, `prepare.py`, provenance, and archives.
On NAS or another Linux recovery host, first verify without changing host services:

```bash
python3 /absolute/snapshot/restore.py verify /absolute/snapshot
```

Prepare selected components under a **new**, root-owned, private workspace whose
ancestors are root-owned and not writable by other users (root-owned sticky `/tmp`
is allowed). Do not put it under a world-writable NAS provisioner path. For example,
create a private parent directly under trusted `/volume1`, then:

```bash
sudo mkdir -m 700 /volume1/@observability-offline-recovery
sudo python3 /absolute/snapshot/prepare.py /absolute/snapshot \
  --workspace /volume1/@observability-offline-recovery/prepared \
  --component grafana --component prometheus --component tempo
```

Verify the CLI usage with `--help` on the bundled script. Preparation verifies all
archives first, restores selected regular-file/directory numeric ownership, mode
and mtime using no-follow descriptors, and writes a private preparation receipt.
It refuses an existing workspace and dangerous file privilege bits; safe directory
setgid/sticky bits are retained. Link metadata is skipped and listed for review.
There is **no live apply** operation.

Review `EXTRACTED/kubernetes` only if separately extracted with `restore.py
verify --extract NEW_DIRECTORY`; Kubernetes exports are private recovery evidence,
not manifests to apply unfiltered. Strip status/server-assigned metadata, stop
controllers initially, suspend restored CronJobs, preserve Secrets and datasource
configuration, and bind new local volumes on the chosen replacement node. Install
prepared data only while the corresponding service is stopped, retaining the old
store. Restore the same pinned application versions, then check readiness,
authentication/dashboard identities and historical queries before re-enabling
clients and the backup schedule.

"""Read-only app-aware mp backup; publish verified private NAS snapshots only."""

# Orchestration keeps fail-closed phases together; immutable source helpers are reviewed separately.
# pylint: disable=line-too-long,too-many-locals,too-many-branches,unidiomatic-typecheck,too-few-public-methods,duplicate-code
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import shutil
import ssl
import stat
import tarfile
import tempfile
import time
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from .archive import create_archive
from .restore import verify_snapshot
from .tls_policy import https_opener

OWNER = "observability-mp-v1"
PARTIAL_OWNER_FILE = ".backup-owner.json"
LEGACY_PARTIAL_MIN_AGE_SECONDS = 24 * 60 * 60
RETAINED_SNAPSHOTS = 2
SERVICES = ("grafana", "prometheus", "tempo")
COMPONENT_TIMEOUT_SECONDS = 1200
BACKUP_COMPRESSLEVEL = 1
# Only one ordinary file is staged in RAM at a time; 128 MiB leaves headroom
# within the backup pod's 512 MiB memory limit. SQLite still uses NAS scratch.
BACKUP_MEMORY_FILE_LIMIT = 128 * 1024 * 1024
LIMITS = {
    "grafana": "Online SQLite snapshot plus stable application files and namespace configuration.",
    "prometheus": "Completed immutable blocks only; no WAL/head. Roughly the most recent three hours may be absent.",
    "tempo": "Finalized local blocks only; no active WAL. Recent traces are not guaranteed.",
}


@contextmanager
def _progress(phase, service=None):
    """Report bounded operational metadata without source paths or exceptions."""
    started = time.monotonic()
    event = {"event": "observability_backup", "phase": phase}
    if service is not None:
        event["service"] = service
    print(json.dumps({**event, "status": "start"}), flush=True)
    details = {}
    try:
        yield details
    except Exception as error:
        print(
            json.dumps({
                **event,
                "status": "failed",
                "duration_seconds": round(time.monotonic() - started, 3),
                "error_type": type(error).__name__,
            }),
            flush=True,
        )
        raise
    else:
        print(
            json.dumps({
                **event,
                "status": "complete",
                "duration_seconds": round(time.monotonic() - started, 3),
                **{key: details[key] for key in ("bytes", "uncompressed_bytes", "attempts") if key in details},
            }),
            flush=True,
        )


def save(path, value):
    """Persist a new private JSON artifact and flush it."""
    with path.open("x", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())


class Kube:
    """Bounded client for the explicitly authorized recovery operations."""

    def __init__(self):
        """Initialize the bounded client and its private evidence location."""
        service_account = Path("/var/run/secrets/kubernetes.io/serviceaccount")
        self.token = (service_account / "token").read_text().strip()
        self.context = ssl.create_default_context(
            cafile=str(service_account / "ca.crt")
        )
        self.opener = https_opener(self.context)
        self.base = (
            "https://"
            + os.environ["KUBERNETES_SERVICE_HOST"]
            + ":"
            + os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        )

    def get(self, path):
        """Read one Kubernetes object."""
        request = urllib.request.Request(
            self.base + path, headers={"Authorization": "Bearer " + self.token}
        )
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)


def validate_binding(claim, volume, expected, output=False):
    """Reject replaced or incorrectly located source and backup storage."""
    spec = volume["spec"]
    ref = spec["claimRef"]
    if (
        claim["metadata"]["uid"],
        volume["metadata"]["uid"],
        claim["spec"]["volumeName"],
    ) != (expected["claim_uid"], expected["volume_uid"], expected["volume"]):
        raise ValueError("Storage identity changed")
    if (
        claim["status"]["phase"] != "Bound"
        or ref.get("uid") != expected["claim_uid"]
        or ref.get("namespace") != "observability"
        or ref.get("name") != expected["claim"]
    ):
        raise ValueError("Storage binding is invalid")
    if spec.get("persistentVolumeReclaimPolicy") != "Retain":
        raise ValueError("Backup/source volumes must retain data")
    if output:
        nfs = spec.get("nfs", {})
        path = nfs.get("path", "")
        options = set(spec.get("mountOptions", []))
        if (
            nfs != expected["nfs"]
            or nfs.get("server") != "192.168.167.25"
            or not path.startswith("/volume1/k3s-nfs/")
            or str(Path(path)) != path
            or ".." in Path(path).parts
        ):
            raise ValueError("Backup destination is outside the approved NFS export")
        if not {"hard", "nfsvers=4.1"} <= options or options & {
            "soft",
            "softerr",
            "softreval",
        }:
            raise ValueError("Backup destination requires hard NFSv4.1")
    else:
        affinity = {
            "required": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "kubernetes.io/hostname",
                                "operator": "In",
                                "values": ["mp"],
                            }
                        ]
                    }
                ]
            }
        }
        if (
            spec.get("local")
            != {"path": "/var/lib/observability/" + expected["service"]}
            or spec.get("nodeAffinity") != affinity
        ):
            raise ValueError("Source volume is not the approved mp local path")


def check_identities(kube, identities):
    """Check pinned PVC/PV identity and the running application claims."""
    for name, expected in identities.items():
        claim = kube.get(
            "/api/v1/namespaces/observability/persistentvolumeclaims/"
            + expected["claim"]
        )
        volume = kube.get("/api/v1/persistentvolumes/" + expected["volume"])
        validate_binding(claim, volume, expected, name == "backups")
    pods = kube.get("/api/v1/namespaces/observability/pods")["items"]
    for service in SERVICES:
        active = [
            p
            for p in pods
            if p["metadata"].get("labels", {}).get("app") == service
            and p.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
        ]
        if (
            len(active) != 1
            or active[0]["spec"].get("nodeName") != "mp"
            or active[0]["metadata"].get("deletionTimestamp")
        ):
            raise ValueError("Expected one active application pod on mp")
        data = next(v for v in active[0]["spec"]["volumes"] if v["name"] == "data")
        if (
            data.get("persistentVolumeClaim", {}).get("claimName")
            != identities[service]["claim"]
        ):
            raise ValueError("Running application does not use the expected claim")


def completed_paths(service, root):
    """Choose only format-recognized immutable blocks; callers validate tar membership."""
    if service == "prometheus":
        selected, ranges = [], []
        for path in root.iterdir():
            if (
                not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", path.name)
                or not path.is_dir()
                or path.is_symlink()
            ):
                continue
            meta = json.loads((path / "meta.json").read_text())
            if (
                meta.get("ulid") != path.name
                or not (path / "index").is_file()
                or not (path / "chunks").is_dir()
            ):
                raise ValueError("Incomplete Prometheus block")
            start, end = meta["minTime"], meta["maxTime"]
            if type(start) is not int or type(end) is not int or start >= end:
                raise ValueError("Invalid Prometheus block range")
            ranges.append((start, end))
            selected.append(path.name)
        if not selected:
            raise ValueError("No completed Prometheus blocks are available")
        ordered = sorted(ranges)
        if any(a[1] > b[0] for a, b in pairwise(ordered)):
            raise ValueError("Prometheus compaction is in progress; retry later")
        return sorted(selected)
    if service == "tempo":
        selected = []
        blocks = root / "blocks"
        if not blocks.is_dir() or blocks.is_symlink():
            raise ValueError("Tempo blocks directory is missing")
        for tenant in blocks.iterdir():
            if (
                tenant.name == "tempo_cluster_seed.json"
                and tenant.is_file()
                and not tenant.is_symlink()
            ):
                selected.append("blocks/" + tenant.name)
                continue
            if not tenant.is_dir() or tenant.is_symlink():
                continue
            for block in tenant.iterdir():
                try:
                    valid = str(uuid.UUID(block.name)) == block.name
                except ValueError:
                    valid = False
                if (
                    valid
                    and block.is_dir()
                    and not block.is_symlink()
                    and (block / "meta.json").is_file()
                    and not (block / "meta.compacted.json").exists()
                ):
                    meta = json.loads((block / "meta.json").read_text())
                    if meta.get("blockID") != block.name:
                        raise ValueError("Tempo finalized block identity differs")
                    selected.append("blocks/" + tenant.name + "/" + block.name)
        return sorted(selected)
    raise ValueError("Unsupported block service")


def validate_grafana_capture(result):
    """Require the promised online SQLite method for the running Grafana database."""
    if "grafana.db" not in result.get("sqlite_databases", []):
        raise ValueError("Grafana primary SQLite database is absent from this capture")
    if result.get("sqlite_stable_wal_copies") or result.get("sqlite_quiescent_copies"):
        raise ValueError("Recurring Grafana backups require the online SQLite API")


def block_archive(service, root, destination):
    """Archive only completed blocks and reject concurrent layout changes."""
    selected = completed_paths(service, root)

    def included(relative):
        """Check the current recovery precondition."""
        return relative == "." or any(
            relative == path
            or relative.startswith(path + "/")
            or path.startswith(relative + "/")
            for path in selected
        )

    excludes = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs) + files:
            relative = str((Path(parent) / name).relative_to(root))
            if not included(relative):
                excludes.append(relative)
                if name in dirs:
                    dirs.remove(name)
    result = create_archive(
        root, destination, excludes=excludes,
        timeout_seconds=COMPONENT_TIMEOUT_SECONDS,
        compresslevel=BACKUP_COMPRESSLEVEL,
        memory_file_limit=BACKUP_MEMORY_FILE_LIMIT,
    )
    # A newly created partial/active block must never slip through a stale exclusion list.
    with tarfile.open(destination, "r:gz") as archive:
        for member in archive:
            if not included(member.name) or member.issym() or member.islnk():
                raise ValueError(
                    "Unexpected or linked data appeared during block capture"
                )
    if completed_paths(service, root) != selected:
        raise ValueError("Finalized block set changed during capture; retry later")
    result["completed_block_paths"] = selected
    return result


def _snapshot_time(name):
    """Recognize our exact snapshot tag, including a valid UTC calendar date."""
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", name):
        return None
    try:
        return datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_metadata(path):
    """Read a bounded regular JSON object without following a metadata symlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as content:
            metadata = os.fstat(content.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4 * 1024 * 1024:
                return None
            value = json.load(content)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


@contextmanager
def backup_lock(root):
    """Serialize backup and maintenance, including manually created Jobs, on NAS."""
    if root.is_symlink() or root.parent.is_symlink():
        raise ValueError("Backup root and its parent must not be symbolic links")
    # Keep the inode permanently: unlinking a locked file permits a second lock.
    # The sibling location leaves snapshots containing only backup directories.
    descriptor = os.open(
        root.parent / ".observability-backup.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Backup lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another observability backup operation is active") from error
        os.fchmod(descriptor, 0o600)
        yield
    finally:
        os.close(descriptor)


def _partial_payload_is_safe(path):
    """Accept only the bounded files and scratch layout produced by this backup."""
    files = {
        *(service + ".tar.gz" for service in (*SERVICES, "kubernetes")),
        "manifest.json", "restore.py", "prepare.py", "PROVENANCE.json", PARTIAL_OWNER_FILE,
    }
    scratch_files = {"archive.tar.gz", "file", "file-wal", "file-shm", "file-journal"}
    try:
        for index, child in enumerate(path.iterdir()):
            if index >= len(files) + 4:
                return False
            mode = child.lstat().st_mode
            if child.name in files and stat.S_ISREG(mode):
                continue
            if (
                not re.fullmatch(r"\.ha-dr-archive-[a-z0-9_]{8}", child.name)
                or not stat.S_ISDIR(mode)
            ):
                return False
            for scratch in child.iterdir():
                if scratch.name not in scratch_files or not stat.S_ISREG(scratch.lstat().st_mode):
                    return False
        return True
    except OSError:
        return False


def cleanup_partials(root, *, legacy_min_age_seconds=LEGACY_PARTIAL_MIN_AGE_SECONDS, now=None):
    """Remove recognized abandoned drafts; callers must hold the backup lock."""
    if not math.isfinite(legacy_min_age_seconds) or legacy_min_age_seconds < 0:
        raise ValueError("Legacy partial minimum age must be finite and nonnegative")
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Partial cleanup requires a timezone-aware time")
    removed = 0
    for path in root.iterdir():
        if not path.name.startswith(".partial-"):
            continue
        created = _snapshot_time(path.name[len(".partial-"):])
        try:
            if created is None or not stat.S_ISDIR(path.lstat().st_mode):
                continue
            marker = path / PARTIAL_OWNER_FILE
            try:
                marker.lstat()
            except FileNotFoundError:
                # Pre-marker versions can coexist during rollout. Give their
                # 80-minute Jobs a full day unless an operator proves inactivity.
                if (now - created).total_seconds() < legacy_min_age_seconds:
                    continue
                # A manifest, when present, must corroborate legacy ownership.
                # Missing manifests are expected after failed early captures.
                manifest_path = path / "manifest.json"
                try:
                    manifest_path.lstat()
                except FileNotFoundError:
                    pass
                else:
                    manifest = _read_metadata(manifest_path)
                    metadata = manifest.get("metadata") if manifest is not None else None
                    if not isinstance(metadata, dict) or metadata.get("owner") != OWNER:
                        continue
            else:
                owner = _read_metadata(marker)
                if owner is None or owner.get("owner") != OWNER:
                    continue
            if not _partial_payload_is_safe(path):
                continue
        except OSError:
            continue
        shutil.rmtree(path)
        removed += 1
    return removed


def prune(root, keep=RETAINED_SNAPSHOTS):
    """Remove excess completed owned snapshots; callers must hold the backup lock."""
    if type(keep) is not int or keep < 1:
        raise ValueError("Retention must keep at least one completed snapshot")
    owned = []
    for path in root.iterdir():
        try:
            if _snapshot_time(path.name) is None or not stat.S_ISDIR(path.lstat().st_mode):
                continue
        except OSError:
            continue
        manifest = _read_metadata(path / "manifest.json")
        if manifest is None:
            continue
        metadata = manifest.get("metadata")
        if (
            manifest.get("completed") is True
            and isinstance(metadata, dict)
            and metadata.get("owner") == OWNER
        ):
            owned.append(path)
    expired = sorted(owned, reverse=True)[keep:]
    for path in expired:
        shutil.rmtree(path)
    return len(expired)


def main(maintenance_only=False, legacy_min_age_seconds=LEGACY_PARTIAL_MIN_AGE_SECONDS):
    """Run a verified backup or perform explicitly requested NAS maintenance."""
    if not math.isfinite(legacy_min_age_seconds) or legacy_min_age_seconds < 0:
        raise ValueError("Legacy partial minimum age must be finite and nonnegative")
    if legacy_min_age_seconds != LEGACY_PARTIAL_MIN_AGE_SECONDS and not maintenance_only:
        raise ValueError("Legacy partial age overrides require maintenance-only mode")
    os.umask(0o077)
    identities = json.loads(Path("/identity/storage.json").read_text(encoding="utf-8"))
    if set(identities) != {*SERVICES, "backups"}:
        raise ValueError("Incomplete storage identity receipt")
    kube = Kube()
    check_identities(kube, identities)
    root = Path("/backup/snapshots")
    if root.is_symlink() or root.parent.is_symlink():
        raise ValueError("Backup root and its parent must not be symbolic links")
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    with backup_lock(root):
        removed = {
            "removed_partials": cleanup_partials(root, legacy_min_age_seconds=legacy_min_age_seconds),
            "removed_completed": prune(root),
        }
        print(json.dumps({"event": "observability_backup", "phase": "maintenance", "status": "complete", **removed}), flush=True)
        if maintenance_only:
            return removed
        _create_snapshot(root, identities, kube)
        prune(root)
    print(
        "Verified observability backup published; retained two completed owned snapshots. Recent metrics/traces are excluded as documented.",
        flush=True,
    )
    return removed


def _create_snapshot(root, identities, kube):
    """Publish one verified set and discard its draft if any phase fails."""
    tag = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    draft = root / (".partial-" + tag)
    draft.mkdir(mode=0o700)
    try:
        save(draft / PARTIAL_OWNER_FILE, {"owner": OWNER})
        _capture_snapshot(draft, root / tag, identities, kube)
    finally:
        # Atomic publication removes the draft pathname. Exceptions are cleaned
        # immediately; process termination leaves a marked draft for the next run.
        if draft.exists():
            shutil.rmtree(draft)


def _capture_snapshot(draft, final_path, identities, kube):
    """Capture the unchanged app-aware components while holding the NAS lock."""
    components = []
    for service in SERVICES:
        source = Path("/sources") / service
        destination = draft / (service + ".tar.gz")
        with _progress("archive", service) as progress:
            result = (
                create_archive(
                    source, destination,
                    timeout_seconds=COMPONENT_TIMEOUT_SECONDS,
                    compresslevel=BACKUP_COMPRESSLEVEL,
                    memory_file_limit=BACKUP_MEMORY_FILE_LIMIT,
                )
                if service == "grafana"
                else block_archive(service, source, destination)
            )
            if service == "grafana":
                validate_grafana_capture(result)
            progress.update(result)
        result.update(
            name=service, source=identities[service], recovery_scope=LIMITS[service]
        )
        components.append(result)
    with _progress("archive", "kubernetes") as progress, tempfile.TemporaryDirectory(prefix="observability-config-") as directory:
        directory = Path(directory)
        groups = {
            "/api/v1": [
                "configmaps",
                "secrets",
                "services",
                "persistentvolumeclaims",
                "serviceaccounts",
            ],
            "/apis/apps/v1": ["deployments", "statefulsets"],
            "/apis/networking.k8s.io/v1": ["ingresses", "networkpolicies"],
            "/apis/rbac.authorization.k8s.io/v1": ["roles", "rolebindings"],
            "/apis/batch/v1": ["cronjobs"],
        }
        for prefix, kinds in groups.items():
            for kind in kinds:
                save(
                    directory / (kind + ".json"),
                    kube.get(prefix + "/namespaces/observability/" + kind),
                )
        save(directory / "storage-identities.json", identities)
        component = create_archive(
            directory, draft / "kubernetes.tar.gz", compresslevel=BACKUP_COMPRESSLEVEL,
            memory_file_limit=BACKUP_MEMORY_FILE_LIMIT,
        )
        progress.update(component)
        component.update(
            name="kubernetes", source="private read-only namespace recovery export"
        )
        components.append(component)
    check_identities(kube, identities)
    save(
        draft / "manifest.json",
        {
            "format_version": 1,
            "completed": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "components": components,
            "metadata": {"owner": OWNER, "scope": LIMITS},
        },
    )
    # The owner marker describes a draft only. Keep the published snapshot
    # format unchanged and let the manifest carry completed-set ownership.
    (draft / PARTIAL_OWNER_FILE).unlink()
    with _progress("verify"):
        verify_snapshot(draft)
    with _progress("publish"):
        for name in ("restore.py", "prepare.py", "PROVENANCE.json"):
            shutil.copyfile(Path(__file__).parent / name, draft / name)
            (draft / name).chmod(0o600)
        for path in draft.iterdir():
            with path.open("rb") as content:
                os.fsync(content.fileno())
        os.rename(draft, final_path)
        descriptor = os.open(final_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _cli():
    """Expose age overrides only for explicitly requested maintenance."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maintenance-only", action="store_true", help="Clean abandoned drafts and retain the newest two completed snapshots without creating a backup")
    parser.add_argument(
        "--legacy-min-age-seconds", type=float, default=LEGACY_PARTIAL_MIN_AGE_SECONDS,
        help="Minimum age of unmarked legacy drafts (default: 86400; overrides require maintenance-only and verified inactive legacy Jobs)",
    )
    arguments = parser.parse_args()
    if not math.isfinite(arguments.legacy_min_age_seconds) or arguments.legacy_min_age_seconds < 0:
        parser.error("--legacy-min-age-seconds must be finite and nonnegative")
    if arguments.legacy_min_age_seconds != LEGACY_PARTIAL_MIN_AGE_SECONDS and not arguments.maintenance_only:
        parser.error("overriding --legacy-min-age-seconds requires --maintenance-only")
    main(
        maintenance_only=arguments.maintenance_only,
        legacy_min_age_seconds=arguments.legacy_min_age_seconds,
    )


if __name__ == "__main__":
    _cli()

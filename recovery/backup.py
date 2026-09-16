"""Read-only app-aware mp backup; publish verified private NAS snapshots only."""

# Orchestration keeps fail-closed phases together; immutable source helpers are reviewed separately.
# pylint: disable=line-too-long,too-many-locals,too-many-branches,unidiomatic-typecheck,too-few-public-methods,duplicate-code
from __future__ import annotations

import json
import os
import re
import shutil
import ssl
import tarfile
import tempfile
import urllib.request
import uuid
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from .archive import create_archive
from .restore import verify_snapshot

OWNER = "observability-mp-v1"
SERVICES = ("grafana", "prometheus", "tempo")
LIMITS = {
    "grafana": "Online SQLite snapshot plus stable application files and namespace configuration.",
    "prometheus": "Completed immutable blocks only; no WAL/head. Roughly the most recent three hours may be absent.",
    "tempo": "Finalized local blocks only; no active WAL. Recent traces are not guaranteed.",
}


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
        with urllib.request.urlopen(
            request, context=self.context, timeout=30
        ) as response:
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
    result = create_archive(root, destination, excludes=excludes, timeout_seconds=600)
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


def prune(root, keep=14):
    """Remove only excess completed snapshots bearing this backup owner marker."""
    owned = []
    for path in root.iterdir():
        if (
            path.is_symlink()
            or not path.is_dir()
            or not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", path.name)
        ):
            continue
        try:
            manifest = json.loads((path / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        if (
            manifest.get("completed") is True
            and manifest.get("metadata", {}).get("owner") == OWNER
        ):
            owned.append(path)
    for path in sorted(owned, reverse=True)[keep:]:
        shutil.rmtree(path)


def main():
    """Run the bounded operator command."""
    os.umask(0o077)
    identities = json.loads(Path("/identity/storage.json").read_text(encoding="utf-8"))
    if set(identities) != {*SERVICES, "backups"}:
        raise ValueError("Incomplete storage identity receipt")
    kube = Kube()
    check_identities(kube, identities)
    root = Path("/backup/snapshots")
    if root.is_symlink():
        raise ValueError("Backup root must not be a symbolic link")
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    tag = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    draft = root / (".partial-" + tag)
    draft.mkdir(mode=0o700)
    components = []
    for service in SERVICES:
        source = Path("/sources") / service
        destination = draft / (service + ".tar.gz")
        result = (
            create_archive(source, destination, timeout_seconds=600)
            if service == "grafana"
            else block_archive(service, source, destination)
        )
        result.update(
            name=service, source=identities[service], recovery_scope=LIMITS[service]
        )
        components.append(result)
    with tempfile.TemporaryDirectory(prefix="observability-config-") as directory:
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
        component = create_archive(directory, draft / "kubernetes.tar.gz")
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
    verify_snapshot(draft)
    for name in ("restore.py", "prepare.py", "PROVENANCE.json"):
        shutil.copyfile(Path(__file__).parent / name, draft / name)
        (draft / name).chmod(0o600)
    for path in draft.iterdir():
        with path.open("rb") as content:
            os.fsync(content.fileno())
    os.rename(draft, root / tag)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    prune(root)
    print(
        "Verified observability backup published; retained 14 completed owned snapshots. Recent metrics/traces are excluded as documented."
    )


if __name__ == "__main__":
    main()

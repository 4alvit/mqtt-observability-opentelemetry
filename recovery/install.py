"""Stage the suspended backup; enable only after validating all four bound volumes."""

# Orchestration keeps fail-closed phases together; immutable source helpers are reviewed separately.
# pylint: disable=too-many-locals
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from .backup import SERVICES, validate_binding
from .migrate import Operator, write_json


def install(context, evidence, enable=False):
    """Stage suspended resources and gate scheduling on real recovery proof."""
    evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
    operator = Operator(evidence, context)
    existing = operator.run(
        operator.kube
        + [
            "-n",
            "observability",
            "get",
            "cronjob",
            "observability-backup",
            "--ignore-not-found",
            "-o",
            "json",
        ]
    )
    if existing.strip():
        current = json.loads(existing)
        operator.patch(
            "cronjob",
            "observability-backup",
            [
                {
                    "op": "test",
                    "path": "/metadata/uid",
                    "value": current["metadata"]["uid"],
                },
                {"op": "add", "path": "/spec/suspend", "value": True},
            ],
        )
    if any(
        p.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
        for p in operator.pods("observability-backup")
    ):
        raise ValueError("Wait for the current backup before updating its mounted code")
    operator.run(operator.kube + ["apply", "-k", str(Path(__file__).parent)])
    if not enable:
        print("Backup resources staged suspended; application storage unchanged.")
        return
    identities = {}
    for service in (*SERVICES, "backups"):
        name = (
            "observability-backups-nfs-v1"
            if service == "backups"
            else service + "-data-mp"
        )
        claim = operator.get("pvc", name)
        volume = operator.get("pv", claim["spec"]["volumeName"], False)
        expected = {
            "service": service,
            "claim": name,
            "claim_uid": claim["metadata"]["uid"],
            "volume": volume["metadata"]["name"],
            "volume_uid": volume["metadata"]["uid"],
        }
        if service == "backups":
            expected["nfs"] = volume["spec"].get("nfs", {})
        validate_binding(claim, volume, expected, service == "backups")
        identities[service] = expected
    operator.apply(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "observability-backup-identity",
                "namespace": "observability",
            },
            "data": {"storage.json": json.dumps(identities)},
        }
    )
    operator.apply(
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": "observability-backup-volumes"},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["persistentvolumes"],
                    "resourceNames": [v["volume"] for v in identities.values()],
                    "verbs": ["get"],
                }
            ],
        }
    )
    operator.apply(
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": "observability-backup-volumes"},
            "subjects": [
                {
                    "kind": "ServiceAccount",
                    "name": "observability-backup",
                    "namespace": "observability",
                }
            ],
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "observability-backup-volumes",
            },
        }
    )
    # First Job must pass before the schedule is allowed to start.
    name = "observability-backup-check-" + str(int(time.time()))
    operator.run(
        operator.kube
        + [
            "-n",
            "observability",
            "create",
            "job",
            "--from=cronjob/observability-backup",
            name,
        ]
    )
    operator.run(
        operator.kube
        + [
            "-n",
            "observability",
            "wait",
            "--for=condition=complete",
            "job/" + name,
            "--timeout=2400s",
        ],
        timeout=2430,
    )
    # A real NAS-side offline preparation is required before enabling recurrence.
    proof_code = r"""
import hashlib,json,sys
from datetime import datetime
from pathlib import Path
root=Path(NFS_ROOT)/'snapshots'
valid=[]
for path in root.iterdir():
    if path.is_symlink() or not path.is_dir() or path.name.startswith('.'):
        continue
    manifest=json.loads((path/'manifest.json').read_text())
    if manifest.get('completed') is True and manifest.get('metadata',{}).get('owner')=='observability-mp-v1' and datetime.fromisoformat(manifest['created_at']).timestamp() >= SINCE:
        valid.append(path)
if len(valid)!=1:
    raise ValueError('Expected exactly one newly completed backup')
snapshot=valid[0]
for name,expected in HASHES.items():
    if hashlib.sha256((snapshot/name).read_bytes()).hexdigest()!=expected:
        raise ValueError('Bundled offline tool differs from reviewed source')
sys.path.insert(0,str(snapshot))
import prepare
workspace=Path('/volume1')/('@observability-rehearsal-'+snapshot.name)
receipt=prepare.prepare_snapshot(snapshot,workspace,['grafana','prometheus','tempo'])
if receipt['links_not_prepared']:
    raise ValueError('Offline preparation contains links requiring individual review')
print(json.dumps({'completed':receipt['completed'],'snapshot':str(snapshot),'workspace':str(workspace),'components':receipt['components']}))
"""
    hashes = json.loads((Path(__file__).parent / "PROVENANCE.json").read_text())[
        "files_sha256"
    ]
    hashes = {name: hashes[name] for name in ("restore.py", "prepare.py")}
    proof_code = (
        "NFS_ROOT="
        + repr(identities["backups"]["nfs"]["path"])
        + "\nSINCE="
        + name.rsplit("-", 1)[-1]
        + "\nHASHES="
        + repr(hashes)
        + "\n"
        + proof_code
    )
    proof = json.loads(operator.ssh("synology", proof_code, timeout=900))
    if proof.get("completed") is not True:
        raise ValueError("Offline preparation did not complete")

    write_json(evidence / "offline-preparation.json", proof)
    operator.patch(
        "cronjob",
        "observability-backup",
        [
            {"op": "test", "path": "/spec/suspend", "value": True},
            {"op": "replace", "path": "/spec/suspend", "value": False},
        ],
    )
    print(
        "First app-aware backup verified and prepared offline on NAS; daily schedule enabled."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="k3s-heaven")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    try:
        install(args.context, args.evidence, args.enable)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, RuntimeError):
        raise SystemExit(
            "Backup installation incomplete; inspect private evidence and schedule state."
        ) from None

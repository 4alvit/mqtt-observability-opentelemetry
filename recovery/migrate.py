"""Move one stopped observability data store to a NEW mp volume, with rollback.

Run only in the approved maintenance window. This operator CLI requires kubectl,
SSH access to mp and synology, and a private local evidence directory. It never
deletes a PVC, a source volume, or a backup. Service and Ingress objects are not
changed. The live Deployment template is preserved except the named placement,
image, data claim and frozen Grafana dashboard changes.
"""

# Orchestration keeps fail-closed phases together; immutable source helpers are reviewed separately.
# pylint: disable=line-too-long,too-many-locals,too-many-branches,too-many-statements,broad-exception-caught,too-many-lines
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tarfile
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .archive import create_archive
from .restore import verify_snapshot

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = "observability"
IMAGES = {
    "grafana": "grafana/grafana:11.2.0@sha256:408afb9726de5122b00a2576763a8a57a3c86d5b0eff5305bc994ceb3eb96c3f",
    "tempo": "grafana/tempo:2.6.1@sha256:ef4384fce6e8ad22b95b243d8fc165628cda655376fd50e7850536ad89d71d50",
    "prometheus": "prom/prometheus:v2.54.1@sha256:f6639335d34a77d9d9db382b92eeb7fc00934be8eae81dbc03b31cfe90411a94",
}
PYTHON_IMAGE = "python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6"


def require(condition, message):
    """Reject an unmet migration precondition without exposing private data."""
    if not condition:
        raise ValueError(message)


def digest(value):
    """Fingerprint a captured Kubernetes object for drift detection."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def write_json(path, value):
    """Write private structured evidence."""
    with path.open("w") as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(value, output, indent=2)
        output.write("\n")


def validate_source(deployment, claim, volume, service):
    """Require the reviewed live version and exact h7 source binding."""
    require(deployment["metadata"]["name"] == service, "Unexpected Deployment")
    require(deployment["metadata"]["namespace"] == NAMESPACE, "Unexpected namespace")
    require(
        deployment["spec"]["replicas"] == 1, "Expected exactly one application replica"
    )
    pod = deployment["spec"]["template"]["spec"]
    container = next(c for c in pod["containers"] if c["name"] == service)
    require(
        container["image"].split("@")[0] == IMAGES[service].split("@")[0],
        "Application version changed",
    )
    data = next(v for v in pod["volumes"] if v["name"] == "data")
    require(
        data["persistentVolumeClaim"]["claimName"] == service + "-data",
        "Unexpected source claim",
    )
    require(claim["metadata"]["name"] == service + "-data", "Wrong source PVC")
    require(claim["status"]["phase"] == "Bound", "Source claim is not bound")
    require(
        claim["spec"]["volumeName"] == volume["metadata"]["name"],
        "Source PV name differs",
    )
    reference = volume["spec"]["claimRef"]
    require(
        reference.get("uid") == claim["metadata"]["uid"]
        and reference.get("namespace") == NAMESPACE
        and reference.get("name") == claim["metadata"]["name"],
        "Source PVC identity differs",
    )
    terms = volume["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"]
    require(
        terms
        == [
            {
                "matchExpressions": [
                    {
                        "key": "kubernetes.io/hostname",
                        "operator": "In",
                        "values": ["h7"],
                    }
                ]
            }
        ],
        "Source volume is not exclusively on h7",
    )
    path = Path(volume["spec"]["local"]["path"])
    require(
        path.is_absolute()
        and ".." not in path.parts
        and path.parent == Path("/var/lib/rancher/k3s/storage"),
        "Unexpected source local path",
    )


def target_template(deployment, service):
    """Change only reviewed placement, image and data-volume settings."""
    template = deepcopy(deployment["spec"]["template"])
    pod = template["spec"]
    require(not pod.get("affinity"), "Existing affinity needs individual review")
    require(not pod.get("nodeName"), "Existing nodeName needs individual review")
    selector = pod.setdefault("nodeSelector", {})
    require(
        not selector or selector == {"kubernetes.io/hostname": "h7"},
        "Existing selector needs individual review",
    )
    selector["kubernetes.io/hostname"] = "mp"
    next(v for v in pod["volumes"] if v["name"] == "data")["persistentVolumeClaim"][
        "claimName"
    ] = service + "-data-mp"
    next(c for c in pod["containers"] if c["name"] == service)["image"] = IMAGES[
        service
    ]
    if service == "grafana":
        require(
            [c["name"] for c in pod.get("initContainers", [])] == ["fetch-dashboards"],
            "Unexpected Grafana init container",
        )
        del pod["initContainers"]
        dashboards = next(v for v in pod["volumes"] if v["name"] == "dashboards")
        require("emptyDir" in dashboards, "Unexpected Grafana dashboard volume")
        dashboards.clear()
        dashboards.update(
            name="dashboards", configMap={"name": "grafana-host-dashboards"}
        )
    return template


def target_storage(service):
    """Describe new explicitly bound Retain storage on mp."""
    name = "observability-" + service + "-mp"
    return [
        {
            "apiVersion": "v1",
            "kind": "PersistentVolume",
            "metadata": {"name": name},
            "spec": {
                "capacity": {"storage": "5Gi"},
                "volumeMode": "Filesystem",
                "accessModes": ["ReadWriteOnce"],
                "persistentVolumeReclaimPolicy": "Retain",
                "storageClassName": "",
                "claimRef": {"namespace": NAMESPACE, "name": service + "-data-mp"},
                "local": {"path": "/var/lib/observability/" + service},
                "nodeAffinity": {
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
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": service + "-data-mp", "namespace": NAMESPACE},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "storageClassName": "",
                "volumeName": name,
                "resources": {"requests": {"storage": "5Gi"}},
            },
        },
    ]


class Operator:
    """Bounded client for the explicitly authorized recovery operations."""

    def __init__(self, evidence, context):
        """Initialize the bounded client and its private evidence location."""
        self.evidence = evidence
        self.kube = ["kubectl", "--context", context, "--request-timeout=30s"]
        self.counter = 0

    def run(self, args, data=None, timeout=180, output=None):
        """Execute a bounded command with private error evidence."""
        self.counter += 1
        result = subprocess.run(
            args,
            input=data,
            stdout=subprocess.PIPE if output is None else output,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            path = self.evidence / (f"command-{self.counter:03d}.stderr")
            path.write_bytes(result.stderr)
            path.chmod(0o600)
            raise RuntimeError(
                "Command failed; inspect private diagnostic " + path.name
            )
        return result.stdout

    def get(self, kind, name, namespaced=True):
        """Read one Kubernetes object."""
        args = (
            self.kube
            + (["-n", NAMESPACE] if namespaced else [])
            + ["get", kind, name, "-o", "json"]
        )
        return json.loads(self.run(args))

    def apply(self, value):
        """Apply the explicitly constructed resource."""
        self.run(self.kube + ["apply", "-f", "-"], json.dumps(value).encode())

    def patch(self, kind, name, operations, namespaced=True):
        """Apply conditional JSON patch operations."""
        args = self.kube + (["-n", NAMESPACE] if namespaced else [])
        self.run(
            args + ["patch", kind, name, "--type=json", "--patch-file=/dev/stdin"],
            json.dumps(operations).encode(),
        )

    def pods(self, service):
        """List pods carrying the exact application label."""
        return json.loads(
            self.run(
                self.kube
                + ["-n", NAMESPACE, "get", "pods", "-l", "app=" + service, "-o", "json"]
            )
        )["items"]

    def wait_stopped(self, service):
        """Wait until no active application pod can write its volume."""
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if not any(
                p.get("status", {}).get("phase") not in {"Succeeded", "Failed"}
                for p in self.pods(service)
            ):
                return
            time.sleep(2)
        raise TimeoutError("Application pods have not stopped; do not copy their data")

    def wait_ready(self, service, node):
        """Require one ready application on the intended node."""
        self.run(
            self.kube
            + [
                "-n",
                NAMESPACE,
                "rollout",
                "status",
                "deployment/" + service,
                "--timeout=600s",
            ],
            timeout=630,
        )
        running = [
            p
            for p in self.pods(service)
            if p.get("status", {}).get("phase") == "Running"
        ]
        require(
            len(running) == 1 and running[0]["spec"]["nodeName"] == node,
            "Application is not on the expected node",
        )
        require(
            all(c.get("ready") for c in running[0]["status"]["containerStatuses"]),
            "Application is not ready",
        )
        return running[0]

    def ssh(self, host, code, data=None, timeout=300):
        """Run bounded privileged recovery work through the configured host alias."""
        return self.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                host,
                "sudo -n python3 -c " + shlex.quote(code),
            ],
            data,
            timeout,
        )


def capture_pod(name, claim):
    """Build a temporary read-only source capture pod."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {"app": "observability-migration-capture"},
        },
        "spec": {
            "nodeSelector": {"kubernetes.io/hostname": "h7"},
            "restartPolicy": "Never",
            "activeDeadlineSeconds": 3600,
            "automountServiceAccountToken": False,
            "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [
                {
                    "name": "capture",
                    "image": PYTHON_IMAGE,
                    "command": ["python", "-c", "import time; time.sleep(3500)"],
                    "securityContext": {
                        "runAsUser": 0,
                        "runAsGroup": 0,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"], "add": ["DAC_READ_SEARCH"]},
                    },
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {
                            "cpu": "1",
                            "memory": "512Mi",
                            "ephemeral-storage": "2Gi",
                        },
                    },
                    "volumeMounts": [
                        {"name": "source", "mountPath": "/source", "readOnly": True},
                        {"name": "scratch", "mountPath": "/scratch"},
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "source",
                    "persistentVolumeClaim": {"claimName": claim, "readOnly": True},
                },
                {"name": "scratch", "emptyDir": {"sizeLimit": "2Gi"}},
            ],
        },
    }


def upload_snapshot(operator, snapshot, host, target, install_service=None):
    """Write a new private directory only; optionally install prepared data on mp."""
    files = {p.name: p for p in snapshot.iterdir() if p.is_file()}
    files.update(
        {name: ROOT / "recovery" / name for name in ("restore.py", "prepare.py")}
    )
    code = """
import json, os, sys, tarfile
from pathlib import Path
os.umask(0o077)
target = Path(TARGET)
if not target.is_absolute() or '..' in target.parts or target.exists() or target.is_symlink():
    raise ValueError('Remote staging destination must be new and absolute')
parent = target.parent
if not parent.is_dir() or parent.is_symlink():
    raise ValueError('Remote staging parent is missing or unsafe')
if not SERVICE and (parent.stat().st_uid != 0 or parent.stat().st_mode & 0o077):
    raise ValueError('NAS migration parent must be private and root-owned')
target.mkdir(mode=0o700)
descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    seen = set()
    with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as incoming:
        for member in incoming:
            name = member.name
            if name not in ALLOWED or name in seen or not member.isfile() or member.size > 4 * 1024**3:
                raise ValueError('Unsafe transfer member')
            seen.add(name)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
            with os.fdopen(fd, 'wb') as output, incoming.extractfile(member) as source:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
    if seen != set(ALLOWED) or os.stat(target).st_ino != os.fstat(descriptor).st_ino:
        raise ValueError('Incomplete transfer or replaced directory')
finally:
    os.close(descriptor)
sys.path.insert(0, str(target))
import restore
report = restore.verify_snapshot(target)
if SERVICE:
    import prepare
    workspace = target.parent / (target.name + '-prepared')
    receipt = prepare.prepare_snapshot(target, workspace, [SERVICE])
    if receipt['links_not_prepared']:
        raise ValueError('Data contains links requiring individual review')
    destination_parent = Path('/var/lib/observability')
    destination_parent.mkdir(mode=0o755, exist_ok=True)
    with prepare._trusted_parent(destination_parent) as parent_fd:
        try:
            os.stat(SERVICE, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('Destination data already exists; refusing overwrite')
        os.rename(workspace / 'data' / SERVICE, SERVICE, dst_dir_fd=parent_fd)
print(json.dumps({'verified': True, 'installed': bool(SERVICE), 'component_count': len(report['components'])}))
"""
    code = (
        "TARGET="
        + repr(str(target))
        + "\nSERVICE="
        + repr(install_service)
        + "\nALLOWED="
        + repr(sorted(files))
        + "\n"
        + code
    )
    transfer = operator.evidence / ("transfer-" + host + ".tar")
    with transfer.open("xb") as output:
        os.fchmod(output.fileno(), 0o600)
        with tarfile.open(fileobj=output, mode="w|") as stream:
            for name, path in sorted(files.items()):
                stream.add(path, arcname=name, recursive=False)
    with transfer.open("rb") as source:
        process = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                host,
                "sudo -n python3 -c " + shlex.quote(code),
            ],
            stdin=source,
            capture_output=True,
            timeout=900,
            check=False,
        )
    transfer.unlink()
    if process.returncode:
        private_error = operator.evidence / ("transfer-" + host + ".stderr")
        private_error.write_bytes(process.stderr)
        private_error.chmod(0o600)
        raise RuntimeError(
            "Remote transfer or verification failed; inspect private stderr"
        )
    result = json.loads(process.stdout)
    require(result["verified"], "Remote snapshot verification failed")
    return result


def stage_dashboards(operator):
    """Stage frozen files before outage without client-side annotation expansion."""
    files = (ROOT / "deploy/k3s/dashboards").glob("*.json")
    data = {
        p.name: p.read_text(encoding="utf-8")
        for p in files
        if p.name != "provenance.json"
    }
    require(len(data) == 4, "Exactly four frozen dashboards are required")
    value = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "grafana-host-dashboards", "namespace": NAMESPACE},
        "data": data,
    }
    operator.run(
        operator.kube
        + [
            "apply",
            "--server-side",
            "--field-manager=observability-migration",
            "-f",
            "-",
        ],
        json.dumps(value).encode(),
    )


def migrate(service, evidence, nas_root, context):
    """Move one stopped store after source, backup and destination gates."""
    os.umask(0o077)
    evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
    operator = Operator(evidence, context)
    receipt = {
        "format_version": 1,
        "service": service,
        "status": "preflight",
        "completed": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_node": "h7",
        "target_node": "mp",
    }
    write_json(evidence / "migration.json", receipt)
    require(
        nas_root.is_absolute()
        and ".." not in nas_root.parts
        and str(nas_root).startswith("/volume1/"),
        "Expected an approved NAS backup parent",
    )
    deployment = operator.get("deployment", service)
    claim = operator.get("pvc", service + "-data")
    volume = operator.get("pv", claim["spec"]["volumeName"], False)
    validate_source(deployment, claim, volume, service)
    expected_template = target_template(deployment, service)
    node = operator.get("node", "mp", False)
    require(
        node["metadata"]["labels"].get("kubernetes.io/arch") == "amd64",
        "mp is not amd64",
    )
    require(
        any(
            c["type"] == "Ready" and c["status"] == "True"
            for c in node["status"]["conditions"]
        ),
        "mp is not Ready",
    )
    source_pod = operator.wait_ready(service, "h7")
    source_image = next(
        c["imageID"]
        for c in source_pod["status"]["containerStatuses"]
        if c["name"] == service
    )
    require(
        source_image.split("@")[-1] == IMAGES[service].split("@")[-1],
        "Running source image digest differs from the reviewed index",
    )
    for kind, name, namespace in [
        ("pv", "observability-" + service + "-mp", []),
        ("pvc", service + "-data-mp", ["-n", NAMESPACE]),
    ]:
        existing = operator.run(
            operator.kube
            + namespace
            + ["get", kind, name, "--ignore-not-found", "-o", "name"]
        )
        require(
            not existing.strip(),
            "Destination PV/PVC already exists; refusing to adopt or overwrite it",
        )
    if service == "grafana":
        stage_dashboards(operator)
    preflight = """
import os, sys
from pathlib import Path
p = Path(PATH)
if p.exists() or p.is_symlink():
    raise ValueError('Migration destination already exists')
parent = p.parent
while not parent.exists():
    parent = parent.parent
for ancestor in [parent, *parent.parents]:
    m = ancestor.lstat()
    if ancestor.is_symlink() or m.st_uid != 0 or m.st_mode & 0o022:
        raise ValueError('Destination ancestors must be trusted root-owned directories')
v = os.statvfs(parent)
if v.f_bavail * v.f_frsize < 2 * 1024**3:
    raise ValueError('At least 2 GiB staging capacity is required')
p.parent.mkdir(mode=0o755, exist_ok=True)
print('ok')
"""
    operator.ssh(
        "mp", "PATH=" + repr("/var/lib/observability/" + service) + "\n" + preflight
    )
    operator.ssh(
        "synology",
        "from pathlib import Path; p=Path("
        + repr(str(nas_root))
        + "); assert p.is_dir() and not p.is_symlink(); print('ok')",
    )
    # Pull/start the exact application image on mp before any source stop.
    prepull = capture_pod(
        "observability-prepull-" + service + "-" + uuid.uuid4().hex[:8],
        claim["metadata"]["name"],
    )
    prepull["spec"]["nodeSelector"] = {"kubernetes.io/hostname": "mp"}
    prepull["spec"]["volumes"] = []
    prepull["spec"]["containers"][0].update(
        image=IMAGES[service], command=["/bin/sh", "-c", "sleep 900"], volumeMounts=[]
    )
    prepull["spec"]["containers"][0]["securityContext"]["capabilities"] = {
        "drop": ["ALL"]
    }
    operator.apply(prepull)
    try:
        operator.run(
            operator.kube
            + [
                "-n",
                NAMESPACE,
                "wait",
                "--for=condition=Ready",
                "pod/" + prepull["metadata"]["name"],
                "--timeout=1200s",
            ],
            timeout=1230,
        )
    finally:
        operator.run(
            operator.kube
            + [
                "-n",
                NAMESPACE,
                "delete",
                "pod",
                prepull["metadata"]["name"],
                "--wait=false",
            ]
        )
    for filename, value in (
        ("deployment-before.json", deployment),
        ("pvc-before.json", claim),
        ("pv-before.json", volume),
    ):
        write_json(evidence / filename, value)
    metadata = evidence / "metadata"
    metadata.mkdir(mode=0o700)
    resources = "deploy,sts,cm,secret,svc,ingress,pvc,networkpolicy,sa,role,rolebinding"
    exported = json.loads(
        operator.run(operator.kube + ["-n", NAMESPACE, "get", resources, "-o", "json"])
    )
    write_json(metadata / "namespace-before.json", exported)
    write_json(metadata / "source-volume.json", volume)
    write_json(metadata / "deployment-before.json", deployment)
    # Recreate may count historical terminal pods. Remove only captured terminal
    # descendants of this exact Deployment, guarded by UID/resourceVersion.
    for terminal in operator.pods(service):
        if terminal.get("status", {}).get("phase") not in {"Succeeded", "Failed"}:
            continue
        owners = [
            owner
            for owner in terminal["metadata"].get("ownerReferences", [])
            if owner.get("controller")
        ]
        require(
            len(owners) == 1 and owners[0]["kind"] == "ReplicaSet",
            "Terminal pod ownership requires review",
        )
        replica = operator.get("replicaset", owners[0]["name"])
        require(
            replica["metadata"]["uid"] == owners[0]["uid"]
            and any(
                owner.get("controller")
                and owner.get("kind") == "Deployment"
                and owner.get("uid") == deployment["metadata"]["uid"]
                for owner in replica["metadata"].get("ownerReferences", [])
            ),
            "Terminal pod belongs to another controller",
        )
        write_json(
            metadata / ("terminal-" + terminal["metadata"]["uid"] + ".json"), terminal
        )
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {
                "uid": terminal["metadata"]["uid"],
                "resourceVersion": terminal["metadata"]["resourceVersion"],
            },
        }
        operator.run(
            operator.kube
            + [
                "delete",
                "--raw",
                "/api/v1/namespaces/"
                + NAMESPACE
                + "/pods/"
                + terminal["metadata"]["name"],
                "-f",
                "/dev/stdin",
            ],
            json.dumps(options).encode(),
        )
    helper = "observability-capture-" + service + "-" + uuid.uuid4().hex[:8]
    helper_created = False
    stopped = False
    target_start_attempted = False
    try:
        operator.apply(capture_pod(helper, claim["metadata"]["name"]))
        helper_created = True
        operator.run(
            operator.kube
            + [
                "-n",
                NAMESPACE,
                "wait",
                "--for=condition=Ready",
                "pod/" + helper,
                "--timeout=1200s",
            ],
            timeout=1230,
        )
        fresh = operator.get("deployment", service)
        require(
            fresh["metadata"]["uid"] == deployment["metadata"]["uid"]
            and digest(fresh["spec"]) == digest(deployment["spec"]),
            "Deployment drifted before cutover",
        )
        operator.patch(
            "pv",
            volume["metadata"]["name"],
            [
                {
                    "op": "test",
                    "path": "/metadata/uid",
                    "value": volume["metadata"]["uid"],
                },
                {
                    "op": "test",
                    "path": "/spec/claimRef/uid",
                    "value": claim["metadata"]["uid"],
                },
                {
                    "op": "replace",
                    "path": "/spec/persistentVolumeReclaimPolicy",
                    "value": "Retain",
                },
            ],
            False,
        )
        fresh_claim = operator.get("pvc", claim["metadata"]["name"])
        fresh_volume = operator.get("pv", volume["metadata"]["name"], False)
        require(
            fresh_claim["metadata"]["uid"] == claim["metadata"]["uid"]
            and fresh_volume["metadata"]["uid"] == volume["metadata"]["uid"],
            "Source storage was replaced before stopping",
        )
        validate_source(fresh, fresh_claim, fresh_volume, service)
        stopped = (
            True  # Intent precedes API mutation: a lost response may have applied it.
        )
        operator.patch(
            "deployment",
            service,
            [
                {
                    "op": "test",
                    "path": "/metadata/uid",
                    "value": deployment["metadata"]["uid"],
                },
                {
                    "op": "test",
                    "path": "/spec/template",
                    "value": deployment["spec"]["template"],
                },
                {"op": "test", "path": "/spec/replicas", "value": 1},
                {"op": "replace", "path": "/spec/replicas", "value": 0},
            ],
        )
        stopped = True
        receipt["status"] = "source_stopped"
        write_json(evidence / "migration.json", receipt)
        operator.wait_stopped(service)
        snapshot = evidence / "snapshot"
        snapshot.mkdir(mode=0o700)
        command = "import json,sys,types; from pathlib import Path; m=types.ModuleType('archive'); exec(compile(sys.stdin.buffer.read(),'archive.py','exec'),m.__dict__); print(json.dumps(m.create_archive(Path('/source'),Path('/scratch/data.tar.gz'),timeout_seconds=240)))"
        capture = json.loads(
            operator.run(
                operator.kube
                + [
                    "-n",
                    NAMESPACE,
                    "exec",
                    "-i",
                    helper,
                    "--",
                    "python",
                    "-c",
                    command,
                ],
                (ROOT / "recovery/archive.py").read_bytes(),
                timeout=300,
            )
        )
        with (snapshot / "data.tar.gz").open("xb") as output:
            operator.run(
                operator.kube
                + [
                    "-n",
                    NAMESPACE,
                    "exec",
                    helper,
                    "--",
                    "cat",
                    "/scratch/data.tar.gz",
                ],
                timeout=300,
                output=output,
            )
        capture.update(
            name=service,
            source={
                "pvc_uid": claim["metadata"]["uid"],
                "pv_uid": volume["metadata"]["uid"],
                "node": "h7",
            },
        )
        config = create_archive(
            metadata, snapshot / "kubernetes.tar.gz", timeout_seconds=120
        )
        config.update(
            name="kubernetes", source="private namespace export before migration"
        )
        manifest = {
            "format_version": 1,
            "completed": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "components": [capture, config],
            "metadata": {
                "kind": "cold-migration",
                "service": service,
                "includes_active_history_wal": True,
                "original_deployment_uid": deployment["metadata"]["uid"],
            },
        }
        write_json(snapshot / "manifest.json", manifest)
        verify_snapshot(snapshot)
        tag = (
            "migration-"
            + service
            + "-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        receipt["nas_snapshot"] = str(nas_root / tag)
        upload_snapshot(operator, snapshot, "synology", nas_root / tag)
        write_json(evidence / "migration.json", receipt)
        upload_snapshot(
            operator,
            snapshot,
            "mp",
            Path("/var/lib/observability") / ("." + tag),
            service,
        )
        operator.apply(
            {"apiVersion": "v1", "kind": "List", "items": target_storage(service)}
        )
        operator.run(
            operator.kube
            + [
                "-n",
                NAMESPACE,
                "wait",
                "--for=jsonpath={.status.phase}=Bound",
                "pvc/" + service + "-data-mp",
                "--timeout=120s",
            ],
            timeout=150,
        )
        target_claim = operator.get("pvc", service + "-data-mp")
        target_volume = operator.get("pv", "observability-" + service + "-mp", False)
        require(
            target_claim["status"]["phase"] == "Bound"
            and target_volume["spec"]["claimRef"].get("uid")
            == target_claim["metadata"]["uid"],
            "Target PVC binding is not proven",
        )
        target_start_attempted = (
            True  # New data may exist even if the API response is lost.
        )
        operator.patch(
            "deployment",
            service,
            [
                {
                    "op": "test",
                    "path": "/metadata/uid",
                    "value": deployment["metadata"]["uid"],
                },
                {"op": "test", "path": "/spec/replicas", "value": 0},
                {
                    "op": "test",
                    "path": "/spec/template",
                    "value": deployment["spec"]["template"],
                },
                {"op": "replace", "path": "/spec/template", "value": expected_template},
                {"op": "replace", "path": "/spec/replicas", "value": 1},
            ],
        )
        pod = operator.wait_ready(service, "mp")
        receipt.update(
            status="ready_on_mp",
            completed=True,
            target_pod_uid=pod["metadata"]["uid"],
            target_pvc_uid=target_claim["metadata"]["uid"],
            target_pv_uid=target_volume["metadata"]["uid"],
            completed_at=datetime.now(timezone.utc).isoformat(),
            application_acceptance="Readiness passed; compare application identities and historical queries separately before declaring acceptance.",
        )
    except Exception as error:
        receipt.update(status="failed", error_type=type(error).__name__)
        if target_start_attempted:
            receipt["rollback"] = "manual_reverse_capture_required_new_writes_may_exist"
        elif stopped:
            try:
                current = operator.get("deployment", service)
                expected = deployment["spec"]["template"]
                require(
                    current["metadata"]["uid"] == deployment["metadata"]["uid"]
                    and current["spec"]["template"] == expected,
                    "Refusing rollback over another operator's changes",
                )
                operator.patch(
                    "deployment",
                    service,
                    [
                        {
                            "op": "test",
                            "path": "/metadata/uid",
                            "value": deployment["metadata"]["uid"],
                        },
                        {"op": "test", "path": "/spec/template", "value": expected},
                        {"op": "replace", "path": "/spec/replicas", "value": 0},
                    ],
                )
                operator.wait_stopped(service)
                operator.patch(
                    "deployment",
                    service,
                    [
                        {
                            "op": "test",
                            "path": "/metadata/uid",
                            "value": deployment["metadata"]["uid"],
                        },
                        {"op": "test", "path": "/spec/template", "value": expected},
                        {"op": "test", "path": "/spec/replicas", "value": 0},
                        {
                            "op": "replace",
                            "path": "/spec/template",
                            "value": deployment["spec"]["template"],
                        },
                        {"op": "replace", "path": "/spec/replicas", "value": 1},
                    ],
                )
                operator.wait_ready(service, "h7")
                receipt["rollback"] = "ready_on_h7"
            except Exception as rollback_error:  # noqa: BLE001 -- preserve original failure and report recovery status.
                receipt["rollback"] = "manual_action_required"
                receipt["rollback_error_type"] = type(rollback_error).__name__
        raise
    finally:
        if helper_created:
            try:
                operator.run(
                    operator.kube
                    + [
                        "-n",
                        NAMESPACE,
                        "delete",
                        "pod",
                        helper,
                        "--wait=true",
                        "--timeout=90s",
                    ],
                    timeout=120,
                )
            except Exception:  # noqa: BLE001 -- cleanup must not mask the migration failure.
                receipt["capture_pod_cleanup"] = "manual_action_required"
        write_json(evidence / "migration.json", receipt)
    return receipt


def main():
    """Run the bounded operator command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=list(IMAGES))
    parser.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help="New private local directory outside this Git checkout",
    )
    parser.add_argument(
        "--nas-root",
        type=Path,
        required=True,
        help="Existing approved NAS backup parent",
    )
    parser.add_argument("--context", default="k3s-heaven")
    args = parser.parse_args()
    require(
        ROOT not in args.evidence.resolve().parents,
        "Evidence must remain outside the Git checkout",
    )
    try:
        result = migrate(args.service, args.evidence, args.nas_root, args.context)
    except Exception:  # noqa: BLE001 -- private diagnostics must never leak through CLI tracebacks.
        print(
            "Migration did not complete; inspect the private migration.json receipt and rollback status.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{result['service']} is ready on mp; source PVC retained and NAS snapshot verified. Application acceptance is still required."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

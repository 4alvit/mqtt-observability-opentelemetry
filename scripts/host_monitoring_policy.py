"""Fail closed around the four operator-approved node-exporter exceptions."""

import hashlib
import json
from pathlib import Path

import yaml

MANIFEST_PATH = "deploy/k3s/node-exporter.yaml"
APPROVED_RULES = {"AVD-KSV-0009", "AVD-KSV-0010", "AVD-KSV-0024", "AVD-KSV-0121"}
# Canonical JSON for the two reviewed documents at 2b9ea23403d01b917c19fb9b2b52e34e661b0ee2.
# Also locks arguments, resources, selectors, annotations, port, Service and unknown fields.
APPROVED_MANIFEST_SHA256 = "c9f41676f86985fffc279db0ab56b0b2857b9302ed9d1d915ec27043efa68c73"


def require(condition: bool, message: str) -> None:
    """Stop the security gate before Trivy can use an overbroad exception."""
    if not condition:
        raise ValueError(message)


def check_yaml_keys(node: yaml.Node) -> None:
    """Reject duplicate YAML keys instead of accepting parser-dependent overrides."""
    if isinstance(node, yaml.MappingNode):
        keys = []
        for key, value in node.value:
            require(isinstance(key, yaml.ScalarNode), "Complex YAML keys are not approved")
            require(key.value not in keys, "Duplicate YAML key: " + key.value)
            keys.append(key.value)
            check_yaml_keys(value)
    elif isinstance(node, yaml.SequenceNode):
        for value in node.value:
            check_yaml_keys(value)


def yaml_documents(path: Path) -> list:
    """Load reviewed YAML with duplicate keys rejected before construction."""
    source = path.read_text(encoding="utf-8")
    for node in yaml.compose_all(source):
        check_yaml_keys(node)
    return list(yaml.safe_load_all(source))


def validate_exception_scope(root: Path) -> None:
    """Require exactly four misconfiguration exceptions for one literal file path."""
    require(not (root / ".trivyignore").exists(), "A global plain-text ignore file is not approved")
    config = yaml_documents(root / "trivy.yaml")
    require(config == [{"ignorefile": ".trivyignore.yaml"}], "Unexpected Trivy configuration")
    documents = yaml_documents(root / ".trivyignore.yaml")
    require(len(documents) == 1, "Only one ignore-policy document is approved")
    policy = documents[0]
    require(
        set(policy) == {"misconfigurations"},
        "Only the four misconfiguration exceptions are approved",
    )
    entries = policy["misconfigurations"]
    require(isinstance(entries, list) and len(entries) == 4, "Exactly four exceptions are approved")
    require({entry.get("id") for entry in entries} == APPROVED_RULES, "Unapproved exception IDs")
    for entry in entries:
        require(set(entry) == {"id", "paths", "statement"}, "Unexpected exception fields")
        require(
            entry["paths"] == [MANIFEST_PATH], "Exception must name the one literal approved path"
        )
        require(
            isinstance(entry["statement"], str) and bool(entry["statement"]), "Missing rationale"
        )


def validate_workload(documents: list) -> None:
    """Enforce named resources and explicit host-access protections, with no sidecars."""
    require(len(documents) == 2, "The approved file contains only one DaemonSet and one Service")
    for document, kind in zip(documents, ("DaemonSet", "Service"), strict=True):
        require(document.get("kind") == kind, "Unexpected resource kind")
        require(document["metadata"]["name"] == "node-exporter", "Unexpected resource name")
        require(document["metadata"]["namespace"] == "observability", "Unexpected namespace")
    pod = documents[0]["spec"]["template"]["spec"]
    require(pod["automountServiceAccountToken"] is False, "Service-account tokens are not approved")
    require(
        pod["securityContext"]
        == {
            "runAsNonRoot": True,
            "runAsUser": 65534,
            "runAsGroup": 65534,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "Non-root identity and RuntimeDefault seccomp are required",
    )
    require(
        "initContainers" not in pod and "ephemeralContainers" not in pod,
        "Extra containers are not approved",
    )
    require(len(pod["containers"]) == 1, "Sidecars are not approved")
    container = pod["containers"][0]
    require(container["name"] == "node-exporter", "Unexpected container name")
    require(
        container["image"] == "prom/node-exporter:v1.8.2", "Image change requires security review"
    )
    require(
        container["securityContext"]
        == {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "Read-only root, no escalation and dropping all capabilities are required",
    )
    require(
        pod["volumes"]
        == [
            {"name": "proc", "hostPath": {"path": "/proc"}},
            {"name": "sys", "hostPath": {"path": "/sys"}},
            {"name": "root", "hostPath": {"path": "/"}},
        ],
        "Only the three approved host paths may be mounted",
    )
    require(
        container["volumeMounts"]
        == [
            {"name": "proc", "mountPath": "/host/proc", "readOnly": True},
            {"name": "sys", "mountPath": "/host/sys", "readOnly": True},
            {
                "name": "root",
                "mountPath": "/host/root",
                "readOnly": True,
                "mountPropagation": "HostToContainer",
            },
        ],
        "Host mounts must stay read-only with the reviewed mount propagation",
    )
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":")).encode()
    require(
        hashlib.sha256(canonical).hexdigest() == APPROVED_MANIFEST_SHA256,
        "Manifest changed; review its exception before updating the policy fingerprint",
    )


def validate(root: Path) -> None:
    """Validate both the complete exception scope and the complete workload contract."""
    validate_exception_scope(root)
    validate_workload(yaml_documents(root / MANIFEST_PATH))


def main() -> None:
    """Run from the security gate without accepting an alternate policy root."""
    try:
        validate(Path(__file__).resolve().parents[1])
    except (KeyError, TypeError, ValueError, OSError, yaml.YAMLError) as error:
        raise SystemExit("Host-monitoring exception guard failed: " + str(error)) from error
    print("Approved node-exporter scope and workload invariants verified.")


if __name__ == "__main__":
    main()

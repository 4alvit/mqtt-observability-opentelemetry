"""Prove that the approved file-level exceptions cannot cover a changed workload."""

import copy
import tempfile
import unittest
from pathlib import Path

import yaml
from scripts import host_monitoring_policy as policy

ROOT = Path(__file__).resolve().parents[2]


class HostMonitoringPolicyTests(unittest.TestCase):
    """Exercise security-relevant mutations and scope expansion against the real guard."""

    def setUp(self):
        """Load the approved manifest independently for each test."""
        self.documents = policy.yaml_documents(ROOT / policy.MANIFEST_PATH)

    def test_approved_workload_and_scope_pass(self):
        """The checked-in, explicitly reviewed configuration is permitted."""
        policy.validate(ROOT)

    def test_semantic_changes_are_rejected(self):
        """Every dangerous control change fails even when its Trivy ID is ignored."""
        pod_path = (0, "spec", "template", "spec")
        container_path = (*pod_path, "containers", 0)
        mutations = [
            ("root identity", (*pod_path, "securityContext", "runAsUser"), 0),
            ("boolean spoof", (*pod_path, "securityContext", "runAsNonRoot"), 1),
            (
                "unconfined seccomp",
                (*pod_path, "securityContext", "seccompProfile", "type"),
                "Unconfined",
            ),
            ("service account token", (*pod_path, "automountServiceAccountToken"), True),
            ("host DNS fallback", (*pod_path, "dnsPolicy"), "ClusterFirst"),
            ("new host namespace", (*pod_path, "hostIPC"), True),
            ("image replacement", (*container_path, "image"), "prom/node-exporter:latest"),
            ("privileged", (*container_path, "securityContext", "privileged"), True),
            ("escalation", (*container_path, "securityContext", "allowPrivilegeEscalation"), True),
            (
                "writable image",
                (*container_path, "securityContext", "readOnlyRootFilesystem"),
                False,
            ),
            (
                "capability",
                (*container_path, "securityContext", "capabilities", "add"),
                ["SYS_ADMIN"],
            ),
            ("host path replacement", (*pod_path, "volumes", 0, "hostPath", "path"), "/etc"),
            ("writable mount", (*container_path, "volumeMounts", 0, "readOnly"), False),
            (
                "mount propagation",
                (*container_path, "volumeMounts", 2, "mountPropagation"),
                "Bidirectional",
            ),
            ("extra command", (*container_path, "command"), ["sh", "-c", "sleep 300"]),
            ("extra port", (*container_path, "ports", 0, "hostPort"), 9000),
            ("different service", (1, "spec", "type"), "NodePort"),
            ("different workload", (0, "metadata", "name"), "another-exporter"),
            (
                "pod annotation",
                (0, "spec", "template", "metadata", "annotations"),
                {"custom": "override"},
            ),
        ]
        for name, path, value in mutations:
            with self.subTest(name=name):
                changed = copy.deepcopy(self.documents)
                target = changed
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValueError):
                    policy.validate_workload(changed)

    def test_additional_workloads_and_mounts_are_rejected(self):
        """A second resource, sidecar, init container or hostPath never inherits the exception."""
        for addition in (
            "resource",
            "sidecar",
            "initContainers",
            "ephemeralContainers",
            "hostPath",
        ):
            with self.subTest(addition=addition):
                changed = copy.deepcopy(self.documents)
                pod = changed[0]["spec"]["template"]["spec"]
                if addition == "resource":
                    changed.append(copy.deepcopy(changed[0]))
                elif addition == "sidecar":
                    pod["containers"].append(copy.deepcopy(pod["containers"][0]))
                elif addition == "hostPath":
                    pod["volumes"].append({"name": "extra", "hostPath": {"path": "/etc"}})
                else:
                    pod[addition] = [copy.deepcopy(pod["containers"][0])]
                with self.assertRaises(ValueError):
                    policy.validate_workload(changed)

    def test_exception_expansion_is_rejected(self):
        """Missing paths, globs, other scanners and extra IDs cannot broaden the approval."""
        original = policy.yaml_documents(ROOT / ".trivyignore.yaml")[0]
        for mutation in ("missing path", "glob", "second path", "extra ID", "secrets", "config"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                changed = copy.deepcopy(original)
                entry = changed["misconfigurations"][0]
                if mutation == "missing path":
                    del entry["paths"]
                elif mutation == "glob":
                    entry["paths"] = ["deploy/k3s/*"]
                elif mutation == "second path":
                    entry["paths"].append("deploy/k3s/grafana.yaml")
                elif mutation == "extra ID":
                    changed["misconfigurations"].append({"id": "AVD-KSV-0118"})
                elif mutation == "secrets":
                    changed["secrets"] = [{"id": "generic-api-key"}]
                config = {"ignorefile": ".trivyignore.yaml"}
                if mutation == "config":
                    config["exit-code"] = 0
                (directory / "trivy.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
                (directory / ".trivyignore.yaml").write_text(
                    yaml.safe_dump(changed), encoding="utf-8"
                )
                with self.assertRaises(ValueError):
                    policy.validate_exception_scope(directory)

    def test_duplicate_yaml_keys_are_rejected(self):
        """A duplicate key cannot override an apparently compliant value."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.yaml"
            path.write_text(
                "securityContext:\n  runAsNonRoot: true\n  runAsNonRoot: false\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                policy.yaml_documents(path)


if __name__ == "__main__":
    unittest.main()

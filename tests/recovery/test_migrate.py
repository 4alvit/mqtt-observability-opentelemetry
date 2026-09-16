# ruff: noqa: SIM117
# Test doubles implement the real client interface; unittest owns temporary cleanup.
# pylint: disable=missing-function-docstring,missing-class-docstring,unused-argument,too-many-instance-attributes,consider-using-with,line-too-long,duplicate-code
"""Offline checks for migration ambiguity and the point where rollback loses data."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from recovery import migrate


def deployment(service="prometheus"):
    pod = {
        "nodeSelector": {"kubernetes.io/hostname": "h7"},
        "containers": [
            {
                "name": service,
                "image": migrate.IMAGES[service].split("@")[0],
                "env": [
                    {
                        "name": "CREDENTIAL",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": "existing-private",
                                "key": "password",
                            }
                        },
                    }
                ],
                "resources": {"requests": {"cpu": "100m"}, "limits": {"memory": "1Gi"}},
                "args": ["original-argument"],
            }
        ],
        "volumes": [
            {"name": "data", "persistentVolumeClaim": {"claimName": service + "-data"}}
        ],
    }
    if service == "grafana":
        pod["initContainers"] = [{"name": "fetch-dashboards", "args": ["fetch latest"]}]
        pod["volumes"].append({"name": "dashboards", "emptyDir": {}})
        pod["containers"][0]["volumeMounts"] = [
            {
                "name": "dashboards",
                "mountPath": "/var/lib/grafana/dashboards",
                "readOnly": True,
            }
        ]
    return {
        "metadata": {
            "name": service,
            "namespace": "observability",
            "uid": "deploy-original",
        },
        "spec": {
            "replicas": 1,
            "template": {"metadata": {"labels": {"app": service}}, "spec": pod},
        },
    }


class FakeOperator:
    """Apply JSON Patch tests in memory; fail after a potentially successful write."""

    def __init__(self, evidence, context, failure):
        self.evidence = evidence
        self.kube = ["kubectl-fixture", "--context", context]
        self.failure = failure
        self.current = deployment()
        self.patch_calls = []
        self.ready_calls = []
        self.applied = []
        self.failed_once = False

    def get(self, kind, name, namespaced=True):
        if kind == "deployment":
            return deepcopy(self.current)
        if kind == "node":
            return {
                "metadata": {"labels": {"kubernetes.io/arch": "amd64"}},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        target = name.endswith("-mp")
        if kind == "pvc":
            return {
                "metadata": {
                    "name": name,
                    "uid": "target-claim" if target else "source-claim",
                },
                "spec": {
                    "volumeName": "observability-prometheus-mp"
                    if target
                    else "source-volume"
                },
                "status": {"phase": "Bound"},
            }
        if kind == "pv":
            return {
                "metadata": {
                    "name": name,
                    "uid": "target-volume" if target else "source-volume-uid",
                },
                "spec": {
                    "claimRef": {
                        "uid": "target-claim" if target else "source-claim",
                        "namespace": "observability",
                        "name": "prometheus-data-mp" if target else "prometheus-data",
                    },
                    "persistentVolumeReclaimPolicy": "Retain",
                    "local": {"path": "/var/lib/rancher/k3s/storage/prometheus"},
                    "nodeAffinity": {
                        "required": {
                            "nodeSelectorTerms": [
                                {
                                    "matchExpressions": [
                                        {
                                            "key": "kubernetes.io/hostname",
                                            "operator": "In",
                                            "values": ["mp" if target else "h7"],
                                        }
                                    ]
                                }
                            ]
                        }
                    },
                },
            }
        raise AssertionError((kind, name, namespaced))

    def run(self, args, data=None, timeout=180, output=None):
        if output is not None:
            output.write(b"fixture archive")
            return None
        if "--ignore-not-found" in args:
            return b""
        if "get" in args:
            return b'{"items": []}'
        if "-i" in args:
            return b'{"file": "data.tar.gz", "files": 1}'
        return b""

    def apply(self, value):
        self.applied.append(deepcopy(value))

    def ssh(self, host, code, data=None, timeout=300):
        return b"ok"

    def pods(self, service):
        return []

    def wait_ready(self, service, node):
        self.ready_calls.append(node)
        return {
            "metadata": {"uid": "pod-fixture"},
            "status": {
                "containerStatuses": [
                    {"name": service, "imageID": migrate.IMAGES[service]}
                ]
            },
        }

    def wait_stopped(self, service):
        if self.current["spec"]["replicas"] != 0:
            raise AssertionError("Attempted to copy a running data store")

    def patch(self, kind, name, operations, namespaced=True):
        if kind != "deployment":
            return
        self.patch_calls.append(deepcopy(operations))
        before = deepcopy(self.current)
        for operation in operations:
            parts = operation["path"].strip("/").split("/")
            parent = self.current
            for key in parts[:-1]:
                parent = parent[key]
            if operation["op"] == "test":
                if parent[parts[-1]] != operation["value"]:
                    self.current = before
                    raise ValueError("Fixture JSON Patch precondition rejected")
            elif operation["op"] == "replace":
                parent[parts[-1]] = deepcopy(operation["value"])
            else:
                raise AssertionError(operation)
        starting_target = (
            self.current["spec"]["template"]["spec"]["nodeSelector"][
                "kubernetes.io/hostname"
            ]
            == "mp"
        )
        stopping_source = (
            before["spec"]["replicas"] == 1 and self.current["spec"]["replicas"] == 0
        )
        if self.failed_once:
            return
        if starting_target and self.failure == "target-response-lost":
            self.failed_once = True
            raise TimeoutError("Target-start response lost after application")
        if stopping_source and self.failure.startswith("stop-"):
            self.failed_once = True
            if self.failure == "stop-replaced-uid":
                self.current["metadata"]["uid"] = "different-operator-object"
            if self.failure == "stop-changed-template":
                self.current["spec"]["template"]["metadata"]["labels"][
                    "external-change"
                ] = "keep"
            raise TimeoutError("Source-stop response lost after application")


class MigrationFailureTests(unittest.TestCase):
    # Separate the exception assertion from fixture patches for readable failure scope.
    def run_failure(self, failure):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        evidence = Path(temporary.name) / "evidence"
        operator = FakeOperator(evidence, "fixture-only", failure)
        with (
            patch.object(migrate, "Operator", return_value=operator),
            patch.object(
                migrate, "create_archive", return_value={"file": "kubernetes.tar.gz"}
            ),
            patch.object(migrate, "verify_snapshot"),
            patch.object(migrate, "upload_snapshot"),
        ):
            with self.assertRaises(TimeoutError):
                migrate.migrate(
                    "prometheus", evidence, Path("/volume1/fixture"), "fixture-only"
                )
        return operator, json.loads((evidence / "migration.json").read_text())

    def test_lost_scale_zero_response_restores_original_template_and_replica(self):
        operator, receipt = self.run_failure("stop-response-lost")
        self.assertEqual(operator.current, deployment())
        self.assertEqual(operator.ready_calls, ["h7", "h7"])
        self.assertEqual(receipt["rollback"], "ready_on_h7")
        self.assertFalse(receipt["completed"])

    def test_target_start_uncertainty_never_switches_to_stale_h7_data(self):
        operator, receipt = self.run_failure("target-response-lost")
        self.assertEqual(operator.current["spec"]["replicas"], 1)
        self.assertEqual(
            operator.current["spec"]["template"]["spec"]["nodeSelector"],
            {"kubernetes.io/hostname": "mp"},
        )
        self.assertEqual(len(operator.patch_calls), 2)
        self.assertEqual(operator.ready_calls, ["h7"])
        self.assertEqual(
            receipt["rollback"], "manual_reverse_capture_required_new_writes_may_exist"
        )
        self.assertFalse(receipt["completed"])

    def test_replaced_deployment_refuses_rollback(self):
        operator, receipt = self.run_failure("stop-replaced-uid")
        self.assertEqual(len(operator.patch_calls), 1)
        self.assertEqual(
            operator.current["metadata"]["uid"], "different-operator-object"
        )
        self.assertEqual(receipt["rollback"], "manual_action_required")

    def test_concurrent_template_change_refuses_rollback(self):
        operator, receipt = self.run_failure("stop-changed-template")
        self.assertEqual(len(operator.patch_calls), 1)
        self.assertEqual(
            operator.current["spec"]["template"]["metadata"]["labels"][
                "external-change"
            ],
            "keep",
        )
        self.assertEqual(receipt["rollback"], "manual_action_required")

    def test_terminal_pod_owned_by_another_deployment_is_not_deleted(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        evidence = Path(temporary.name) / "evidence"
        operator = FakeOperator(evidence, "fixture-only", "none")
        terminal = {
            "metadata": {
                "name": "foreign-terminal",
                "uid": "pod-other",
                "resourceVersion": "42",
                "ownerReferences": [
                    {
                        "controller": True,
                        "kind": "ReplicaSet",
                        "name": "other-rs",
                        "uid": "rs-other",
                    }
                ],
            },
            "status": {"phase": "Failed"},
        }
        original_get = operator.get

        def get(kind, name, namespaced=True):
            if kind == "replicaset":
                return {
                    "metadata": {
                        "uid": "rs-other",
                        "ownerReferences": [
                            {
                                "controller": True,
                                "kind": "Deployment",
                                "uid": "foreign-deployment",
                            }
                        ],
                    }
                }
            return original_get(kind, name, namespaced)

        with (
            patch.object(migrate, "Operator", return_value=operator),
            patch.object(operator, "pods", return_value=[terminal]),
            patch.object(operator, "get", side_effect=get),
            patch.object(operator, "run", wraps=operator.run) as run,
        ):
            with self.assertRaises(ValueError):
                migrate.migrate(
                    "prometheus", evidence, Path("/volume1/fixture"), "fixture-only"
                )
        self.assertEqual(operator.patch_calls, [])
        self.assertEqual(operator.current, deployment())
        self.assertFalse(any("--raw" in call.args[0] for call in run.call_args_list))

    def test_grafana_template_preserves_credentials_resources_and_dashboard_mount(self):
        source = deployment("grafana")
        before = deepcopy(source)
        target = migrate.target_template(source, "grafana")
        self.assertEqual(source, before)
        original_container = source["spec"]["template"]["spec"]["containers"][0]
        container = target["spec"]["containers"][0]
        for key in ["env", "resources", "args", "volumeMounts"]:
            self.assertEqual(container[key], original_container[key])
        self.assertNotIn("initContainers", target["spec"])
        dashboard_volume = next(
            v for v in target["spec"]["volumes"] if v["name"] == "dashboards"
        )
        self.assertEqual(
            dashboard_volume,
            {"name": "dashboards", "configMap": {"name": "grafana-host-dashboards"}},
        )
        files = [
            p
            for p in (migrate.ROOT / "deploy/k3s/dashboards").glob("*.json")
            if p.name != "provenance.json"
        ]
        self.assertEqual(len(files), 4)
        self.assertTrue(all(json.loads(p.read_text()).get("uid") for p in files))


if __name__ == "__main__":
    unittest.main()

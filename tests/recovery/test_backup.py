"""Exercise immutable-block, retention and storage identity boundaries offline."""

# Test fixtures intentionally mirror the client protocol and use unittest cleanup.
# pylint: disable=missing-function-docstring,missing-class-docstring,consider-using-with
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from recovery.backup import (
    OWNER,
    block_archive,
    completed_paths,
    prune,
    validate_binding,
    validate_grafana_capture,
)
from recovery.migrate import stage_dashboards, target_template


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def block(self, name="01ARZ3NDEKTSV4RRFFQ69G5FAV", start=1, end=2):
        path = self.root / name
        path.mkdir()
        (path / "meta.json").write_text(
            json.dumps({"ulid": name, "minTime": start, "maxTime": end})
        )
        (path / "index").write_bytes(b"index")
        (path / "chunks").mkdir()
        (path / "chunks/000001").write_bytes(b"chunk")
        return path

    def test_only_complete_blocks_exclude_head_and_wal(self):
        block = self.block()
        (self.root / "wal").mkdir()
        (self.root / "wal/000001").write_text("must not be included")
        (self.root / "chunks_head").mkdir()
        with tempfile.TemporaryDirectory() as output:
            metadata = block_archive(
                "prometheus", self.root, Path(output) / "prometheus.tar.gz"
            )
        self.assertEqual(metadata["completed_block_paths"], [block.name])
        self.assertEqual(metadata["files"], 3)

    def test_incomplete_and_overlapping_blocks_fail(self):
        block = self.block(end=4)
        (block / "index").unlink()
        with self.assertRaises(ValueError):
            completed_paths("prometheus", self.root)
        (block / "index").write_text("index")
        self.block("01ARZ3NDEKTSV4RRFFQ69G5FAW", 2, 5)
        with self.assertRaises(ValueError):
            completed_paths("prometheus", self.root)

    def test_tempo_excludes_wal_partial_compacted_and_index(self):
        blocks = self.root / "blocks"
        tenant = blocks / "single-tenant"
        tenant.mkdir(parents=True)
        good = tenant / "cd052567-5c96-4351-92d4-f38e55d5cb03"
        good.mkdir()
        (good / "meta.json").write_text(json.dumps({"blockID": good.name}))
        (good / "data.parquet").write_text("data")
        partial = tenant / "cd052567-5c96-4351-92d4-f38e55d5cb04"
        partial.mkdir()
        (partial / "data.parquet").write_text("partial")
        (tenant / "index.json.gz").write_text("dynamic")
        (self.root / "wal").mkdir()
        self.assertEqual(
            completed_paths("tempo", self.root), ["blocks/single-tenant/" + good.name]
        )
        (good / "meta.compacted.json").write_text("{}")
        self.assertEqual(completed_paths("tempo", self.root), [])

    def test_retention_keeps_unowned_incomplete_and_latest_fourteen(self):
        for number in range(17):
            path = self.root / (f"20260916T{number:06d}Z-{number:08x}")
            path.mkdir()
            (path / "manifest.json").write_text(
                json.dumps({"completed": True, "metadata": {"owner": OWNER}})
            )
        unowned = self.root / "20200101T000000Z-11111111"
        unowned.mkdir()
        (unowned / "manifest.json").write_text(
            json.dumps({"completed": True, "metadata": {"owner": "someone-else"}})
        )
        incomplete = self.root / ".partial-important"
        incomplete.mkdir()
        prune(self.root)
        self.assertTrue(unowned.exists())
        self.assertTrue(incomplete.exists())
        self.assertEqual(len(list(self.root.iterdir())), 16)
        self.assertTrue((self.root / "20260916T000016Z-00000010").exists())

    def test_grafana_recurring_scope_rejects_non_online_sqlite(self):
        validate_grafana_capture({"sqlite_databases": ["grafana.db"]})
        for bad in (
            {"sqlite_databases": []},
            {
                "sqlite_databases": ["grafana.db"],
                "sqlite_stable_wal_copies": ["grafana.db"],
            },
            {
                "sqlite_databases": ["grafana.db"],
                "sqlite_quiescent_copies": ["grafana.db"],
            },
        ):
            with self.assertRaises(ValueError):
                validate_grafana_capture(bad)

    def test_large_dashboard_configmap_uses_server_side_apply(self):
        operator = Mock()
        operator.kube = ["kubectl", "--context", "fixture"]
        stage_dashboards(operator)
        args, content = operator.run.call_args.args
        self.assertIn("--server-side", args)
        self.assertGreater(len(content), 262144)
        document = json.loads(content)
        self.assertEqual(len(document["data"]), 4)
        self.assertNotIn("annotations", document["metadata"])

    def test_reviewed_helper_bytes_match_provenance(self):

        root = Path(__file__).resolve().parents[2] / "recovery"
        provenance = json.loads((root / "PROVENANCE.json").read_text())
        for name, expected in provenance["files_sha256"].items():
            self.assertEqual(
                hashlib.sha256((root / name).read_bytes()).hexdigest(), expected
            )

    def binding(self):
        expected = {
            "claim_uid": "c1",
            "volume_uid": "v1",
            "volume": "pv1",
            "claim": "backup",
            "nfs": {"server": "192.168.167.25", "path": "/volume1/k3s-nfs/approved"},
        }
        claim = {
            "metadata": {"uid": "c1"},
            "spec": {"volumeName": "pv1"},
            "status": {"phase": "Bound"},
        }
        volume = {
            "metadata": {"uid": "v1"},
            "spec": {
                "claimRef": {
                    "uid": "c1",
                    "namespace": "observability",
                    "name": "backup",
                },
                "persistentVolumeReclaimPolicy": "Retain",
                "nfs": deepcopy(expected["nfs"]),
                "mountOptions": ["hard", "nfsvers=4.1"],
            },
        }
        return claim, volume, expected

    def test_binding_rejects_uid_server_path_and_soft_mount(self):
        claim, volume, expected = self.binding()
        validate_binding(claim, volume, expected, True)
        mutations = [
            lambda v: v["metadata"].update(uid="replaced"),
            lambda v: v["spec"]["nfs"].update(server="wrong"),
            lambda v: v["spec"]["nfs"].update(path="/volume1/k3s-nfs/../elsewhere"),
            lambda v: v["spec"]["mountOptions"].append("soft"),
            lambda v: v["spec"].update(persistentVolumeReclaimPolicy="Delete"),
        ]
        for mutate in mutations:
            changed = deepcopy(volume)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_binding(claim, changed, expected, True)

    def test_narrow_template_preserves_runtime_config_and_source(self):
        source = {
            "spec": {
                "template": {
                    "metadata": {"labels": {"app": "prometheus"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "prometheus",
                                "image": "prom/prometheus:v2.54.1",
                                "env": [{"name": "PRIVATE", "value": "fixture"}],
                                "args": ["--web.enable-lifecycle"],
                                "resources": {"limits": {"memory": "1Gi"}},
                            }
                        ],
                        "volumes": [
                            {
                                "name": "data",
                                "persistentVolumeClaim": {
                                    "claimName": "prometheus-data"
                                },
                            }
                        ],
                    },
                }
            }
        }
        before = deepcopy(source)
        result = target_template(source, "prometheus")
        self.assertEqual(source, before)
        self.assertEqual(
            result["spec"]["containers"][0]["env"],
            source["spec"]["template"]["spec"]["containers"][0]["env"],
        )
        self.assertEqual(
            result["spec"]["containers"][0]["args"], ["--web.enable-lifecycle"]
        )
        self.assertEqual(
            result["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"],
            "prometheus-data-mp",
        )
        source["spec"]["template"]["spec"]["affinity"] = {"unexpected": True}
        with self.assertRaises(ValueError):
            target_template(source, "prometheus")


if __name__ == "__main__":
    unittest.main()

"""Exercise immutable-block, retention and storage identity boundaries offline."""

# Test fixtures intentionally mirror the client protocol and use unittest cleanup.
# pylint: disable=missing-function-docstring,missing-class-docstring,consider-using-with
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from recovery import backup
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

    def test_block_timeout_includes_initial_selection(self):
        self.block()
        clock = [100.0]
        original_iterdir = Path.iterdir

        def slow_iterdir(path):
            for entry in original_iterdir(path):
                clock[0] += backup.COMPONENT_TIMEOUT_SECONDS + 1
                yield entry

        with patch.object(backup.time, "monotonic", side_effect=lambda: clock[0]), patch.object(
            Path, "iterdir", slow_iterdir
        ), patch.object(backup, "create_archive") as capture:
            with self.assertRaises(TimeoutError):
                block_archive("prometheus", self.root, self.root / "unused.tar.gz")
        capture.assert_not_called()

    def test_block_timeout_includes_exclusion_walk(self):
        self.block()
        clock = [100.0]

        def slow_walk(*_args, **_kwargs):
            clock[0] += backup.COMPONENT_TIMEOUT_SECONDS + 1
            yield str(self.root), [], []

        with patch.object(backup.time, "monotonic", side_effect=lambda: clock[0]), patch.object(
            backup.os, "walk", side_effect=slow_walk
        ), patch.object(backup, "create_archive") as capture:
            with self.assertRaises(TimeoutError):
                block_archive("prometheus", self.root, self.root / "unused.tar.gz")
        capture.assert_not_called()

    def test_block_capture_receives_only_remaining_budget(self):
        self.block()
        clock = [100.0]

        def delayed_selection(*args, **kwargs):
            selected = completed_paths(*args, **kwargs)
            clock[0] += 11
            return selected

        with tempfile.TemporaryDirectory() as output, patch.object(
            backup.time, "monotonic", side_effect=lambda: clock[0]
        ), patch.object(backup, "completed_paths", side_effect=delayed_selection), patch.object(
            backup, "create_archive", wraps=backup.create_archive
        ) as capture:
            block_archive("prometheus", self.root, Path(output) / "archive.tar.gz")
        self.assertEqual(capture.call_args.kwargs["timeout_seconds"], 1189)

    def test_block_timeout_includes_tar_verification(self):
        self.assert_block_verification_timeout("tar")

    def test_block_timeout_includes_final_selection(self):
        self.assert_block_verification_timeout("final-selection")

    def assert_block_verification_timeout(self, phase):
        self.block()
        clock = [100.0]
        selections = []

        def selection(*args, **kwargs):
            selections.append(kwargs["deadline"])
            if phase == "final-selection" and len(selections) == 2:
                clock[0] += backup.COMPONENT_TIMEOUT_SECONDS + 1
            return completed_paths(*args, **kwargs)

        def members():
            if phase == "tar":
                clock[0] += backup.COMPONENT_TIMEOUT_SECONDS + 1
                yield backup.tarfile.TarInfo(".")

        archive = MagicMock()
        archive.__enter__.return_value.__iter__.side_effect = members
        with patch.object(
            backup.time, "monotonic", side_effect=lambda: clock[0]
        ), patch.object(
            backup, "completed_paths", side_effect=selection
        ), patch.object(backup, "create_archive", return_value={}), patch.object(
            backup.tarfile, "open", return_value=archive
        ):
            with self.assertRaises(TimeoutError):
                block_archive("prometheus", self.root, self.root / "unused.tar.gz")
        self.assertTrue(all(value == 1300 for value in selections))

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

    def test_retention_keeps_unowned_incomplete_and_latest_two(self):
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
        self.assertEqual(len(list(self.root.iterdir())), 4)
        self.assertTrue((self.root / "20260916T000015Z-0000000f").exists())
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

    def test_recurring_backup_uses_fast_bounded_capture_and_publishes_verified_set(self):
        sources = self.root / "sources"
        for service in backup.SERVICES:
            (sources / service).mkdir(parents=True)
        with closing(sqlite3.connect(sources / "grafana/grafana.db")) as connection, connection:
            connection.execute("CREATE TABLE dashboard (title TEXT)")
            connection.execute("INSERT INTO dashboard VALUES ('retained dashboard')")
        self.block().rename(sources / "prometheus/01ARZ3NDEKTSV4RRFFQ69G5FAV")
        tempo_block = sources / "tempo/blocks/single-tenant/cd052567-5c96-4351-92d4-f38e55d5cb03"
        tempo_block.mkdir(parents=True)
        (tempo_block / "meta.json").write_text(json.dumps({"blockID": tempo_block.name}))
        (tempo_block / "data.parquet").write_bytes(b"finalized trace block")
        identities = self.root / "storage.json"
        identities.write_text(json.dumps({name: {} for name in (*backup.SERVICES, "backups")}))
        snapshots = self.root / "snapshots"
        mapped = {
            "/sources": sources,
            "/identity/storage.json": identities,
            "/backup/snapshots": snapshots,
        }
        client = Mock()
        private_marker = "private export content must not enter logs"
        client.get.return_value = {"private": private_marker}
        output = io.StringIO()

        def fixture_path(value):
            return mapped.get(str(value), Path(value))

        previous_umask = os.umask(0o077)
        os.umask(previous_umask)
        try:
            with patch.object(backup, "Path", side_effect=fixture_path), patch.object(
                backup, "Kube", return_value=client
            ), patch.object(backup, "check_identities") as identities_check, patch.object(
                backup, "create_archive", wraps=backup.create_archive
            ) as capture, patch.object(
                backup, "verify_snapshot", wraps=backup.verify_snapshot
            ) as verify, redirect_stdout(output):
                backup.main()
        finally:
            os.umask(previous_umask)

        self.assertEqual(identities_check.call_count, 2)
        verify.assert_called_once()
        self.assertEqual(capture.call_count, 4)
        calls = {call.args[1].name: call.kwargs for call in capture.call_args_list}
        for service in backup.SERVICES:
            self.assertEqual(calls[service + ".tar.gz"]["compresslevel"], 1)
            self.assertGreater(calls[service + ".tar.gz"]["timeout_seconds"], 0)
            self.assertLessEqual(calls[service + ".tar.gz"]["timeout_seconds"], 1200)
            self.assertEqual(calls[service + ".tar.gz"]["memory_file_limit"], 128 * 1024 * 1024)
        self.assertEqual(calls["kubernetes.tar.gz"]["compresslevel"], 1)
        self.assertEqual(calls["kubernetes.tar.gz"]["memory_file_limit"], 128 * 1024 * 1024)
        completed = list(snapshots.iterdir())
        self.assertEqual(len(completed), 1)
        self.assertFalse(completed[0].name.startswith("."))
        self.assertFalse((completed[0] / ".backup-owner.json").exists())
        manifest = json.loads((completed[0] / "manifest.json").read_text())
        self.assertTrue(manifest["completed"])
        self.assertEqual(
            [item["name"] for item in manifest["components"]],
            [*backup.SERVICES, "kubernetes"],
        )
        self.assertNotIn(private_marker, output.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
        self.assertEqual(events[0], {
            "event": "observability_backup", "phase": "maintenance", "status": "complete",
            "removed_partials": 0, "removed_completed": 0,
        })
        events = events[1:]
        phases = [("archive", service) for service in (*backup.SERVICES, "kubernetes")]
        phases.extend([("verify", None), ("publish", None)])
        self.assertEqual(
            [(event["phase"], event.get("service"), event["status"]) for event in events],
            [(phase, service, status) for phase, service in phases for status in ("start", "complete")],
        )

    def test_progress_failure_is_flushed_without_private_exception_details(self):
        private_marker = "private source path and secret fixture"
        with patch("builtins.print") as output:
            with self.assertRaisesRegex(RuntimeError, private_marker):
                with backup._progress("archive", "prometheus"):
                    raise RuntimeError(private_marker)
        self.assertEqual(output.call_count, 2)
        events = []
        for call in output.call_args_list:
            self.assertIs(call.kwargs["flush"], True)
            self.assertNotIn(private_marker, call.args[0])
            events.append(json.loads(call.args[0]))
        self.assertEqual([event["status"] for event in events], ["start", "failed"])
        self.assertEqual(events[-1]["error_type"], "RuntimeError")

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

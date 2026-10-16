"""Test destructive retention boundaries and exclusive backup maintenance offline."""

# Test fixtures intentionally exercise invalid on-disk data and subprocess locks.
# pylint: disable=missing-function-docstring,missing-class-docstring,consider-using-with
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from recovery import backup


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "snapshots"
        self.root.mkdir()
        self.now = datetime(2026, 10, 14, 20, tzinfo=timezone.utc)

    def partial(self, when=None, suffix="12345678", owner=None):
        when = when or self.now - timedelta(days=2)
        path = self.root / (".partial-" + when.strftime("%Y%m%dT%H%M%SZ") + "-" + suffix)
        path.mkdir()
        (path / "grafana.tar.gz").write_bytes(b"unfinished archive")
        if owner is not None:
            (path / ".backup-owner.json").write_text(json.dumps({"owner": owner}))
        return path

    def snapshot(self, number, manifest=None):
        path = self.root / f"20261014T{number:06d}Z-{number:08x}"
        path.mkdir()
        (path / "manifest.json").write_text(json.dumps(
            manifest if manifest is not None
            else {"completed": True, "metadata": {"owner": backup.OWNER}}
        ))
        return path

    def clean(self, **kwargs):
        with backup.backup_lock(self.root):
            return backup.cleanup_partials(self.root, now=self.now, **kwargs)

    def test_prune_uses_snapshot_order_and_ignores_invalid_manifests(self):
        valid = [self.snapshot(number) for number in (5, 1, 4, 2, 3)]
        preserved = []
        for number, manifest in enumerate((
            [], None, {"completed": True, "metadata": []},
            {"completed": True, "metadata": {"owner": "another-owner"}},
            {"completed": False, "metadata": {"owner": backup.OWNER}},
        ), start=10):
            path = self.snapshot(number, manifest)
            if manifest is None:
                (path / "manifest.json").write_text("not json")
            preserved.append(path)
        linked = self.snapshot(20)
        (linked / "manifest.json").unlink()
        (linked / "manifest.json").symlink_to(valid[0] / "manifest.json")
        preserved.append(linked)
        directory_link = self.root / "20261014T000030Z-00000030"
        directory_link.symlink_to(valid[0], target_is_directory=True)
        preserved.append(directory_link)

        self.assertEqual(backup.prune(self.root), 3)
        self.assertTrue(valid[0].exists())
        self.assertTrue(valid[2].exists())
        self.assertTrue(all(not path.exists() for path in (valid[1], valid[3], valid[4])))
        self.assertTrue(all(path.exists() for path in preserved))

    def test_cleanup_removes_stale_legacy_and_abandoned_owned_drafts(self):
        stale = self.partial()
        fresh = self.partial(self.now - timedelta(hours=23), suffix="11111111")
        owned = self.partial(self.now, suffix="22222222", owner=backup.OWNER)
        self.assertEqual(self.clean(), 2)
        self.assertFalse(stale.exists())
        self.assertFalse(owned.exists())
        self.assertTrue(fresh.exists())

    def test_legacy_age_uses_name_not_mutable_filesystem_time(self):
        stale = self.partial()
        fresh = self.partial(self.now - timedelta(hours=1), suffix="11111111")
        future = self.partial(self.now + timedelta(hours=1), suffix="22222222")
        os.utime(stale, (self.now.timestamp(), self.now.timestamp()))
        old_mtime = (self.now - timedelta(days=30)).timestamp()
        os.utime(fresh, (old_mtime, old_mtime))
        os.utime(future, (old_mtime, old_mtime))
        self.assertEqual(self.clean(), 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(future.exists())

    def test_legacy_age_boundary_and_explicit_one_time_override(self):
        boundary = self.partial(self.now - timedelta(days=1))
        fresh = self.partial(self.now - timedelta(seconds=1), suffix="11111111")
        self.assertEqual(self.clean(), 1)
        self.assertFalse(boundary.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(self.clean(legacy_min_age_seconds=0), 1)
        self.assertFalse(fresh.exists())

    def test_cleanup_preserves_unknown_and_malformed_entries(self):
        kept = [
            self.partial(owner="another-owner"),
            self.partial(suffix="11111111", owner=backup.OWNER),
            self.partial(suffix="22222222"),
            self.partial(suffix="33333333"),
        ]
        (kept[1] / ".backup-owner.json").write_text("not json")
        (kept[2] / "private-unrelated-file").write_text("keep")
        (kept[3] / ".backup-owner.json").write_text("[]")
        for name in (".partial-important", ".partial-20260230T000000Z-12345678"):
            path = self.root / name
            path.mkdir()
            (path / "grafana.tar.gz").write_bytes(b"keep")
            kept.append(path)
        self.assertEqual(self.clean(legacy_min_age_seconds=0), 0)
        self.assertTrue(all(path.exists() for path in kept))

    def test_legacy_manifest_when_present_must_confirm_ownership(self):
        owned = self.partial()
        (owned / "manifest.json").write_text(json.dumps({
            "completed": False, "metadata": {"owner": backup.OWNER},
        }))
        kept = []
        for number, content in enumerate((
            '{"metadata":',
            "[]",
            json.dumps({"metadata": []}),
            json.dumps({"metadata": {"owner": "another-owner"}}),
            json.dumps({"completed": True}),
        ), start=1):
            path = self.partial(suffix=f"{number:08x}")
            (path / "manifest.json").write_text(content)
            kept.append(path)
        self.assertEqual(self.clean(), 1)
        self.assertFalse(owned.exists())
        self.assertTrue(all(path.exists() for path in kept))

    def test_owned_marker_allows_cleanup_after_partial_manifest_write(self):
        owned = self.partial(owner=backup.OWNER)
        (owned / "manifest.json").write_text('{"metadata":')
        self.assertEqual(self.clean(), 1)
        self.assertFalse(owned.exists())

    def test_cleanup_rejects_symlinked_draft_marker_and_payload(self):
        external = self.root.parent / "external"
        external.mkdir()
        sentinel = external / "sentinel"
        sentinel.write_text("keep")
        marker = external / "owner.json"
        marker.write_text(json.dumps({"owner": backup.OWNER}))
        linked_draft = self.root / ".partial-20261001T000000Z-12345678"
        linked_draft.symlink_to(external, target_is_directory=True)
        linked_marker = self.partial(suffix="11111111")
        (linked_marker / ".backup-owner.json").symlink_to(marker)
        linked_payload = self.partial(suffix="22222222")
        (linked_payload / "grafana.tar.gz").unlink()
        (linked_payload / "grafana.tar.gz").symlink_to(sentinel)
        self.assertEqual(self.clean(legacy_min_age_seconds=0), 0)
        self.assertTrue(linked_draft.is_symlink())
        self.assertTrue(linked_marker.exists())
        self.assertTrue(linked_payload.exists())
        self.assertEqual(sentinel.read_text(), "keep")

    def test_cleanup_allows_known_archive_scratch_directory(self):
        stale = self.partial()
        scratch = stale / ".ha-dr-archive-abcdefgh"
        scratch.mkdir()
        (scratch / "file").write_text("staged archive data")
        self.assertEqual(self.clean(), 1)
        self.assertFalse(stale.exists())

    def test_cleanup_preserves_unknown_or_linked_scratch_payload(self):
        unexpected = self.partial()
        scratch = unexpected / ".ha-dr-archive-abcdefgh"
        scratch.mkdir()
        (scratch / "unexpected-file").write_text("keep")
        linked = self.partial(suffix="11111111", owner=backup.OWNER)
        scratch = linked / ".ha-dr-archive-abcdefgh"
        scratch.mkdir()
        sentinel = self.root.parent / "sentinel"
        sentinel.write_text("keep")
        (scratch / "file").symlink_to(sentinel)
        self.assertEqual(self.clean(), 0)
        self.assertTrue(unexpected.exists())
        self.assertTrue(linked.exists())
        self.assertEqual(sentinel.read_text(), "keep")

    def lock_in_subprocess(self):
        command = (
            "import sys; from pathlib import Path; from recovery.backup import backup_lock; "
            "context = backup_lock(Path(sys.argv[1])); context.__enter__(); "
            "context.__exit__(None, None, None)"
        )
        return subprocess.run(
            [sys.executable, "-c", command, str(self.root)],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, check=False, timeout=10,
        )

    def test_lock_excludes_other_process_and_releases_after_failure(self):
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with backup.backup_lock(self.root):
                attempt = self.lock_in_subprocess()
                self.assertNotEqual(attempt.returncode, 0)
                self.assertIn("Another observability backup operation is active", attempt.stderr)
                raise RuntimeError("fixture failure")
        self.assertEqual(list(self.root.iterdir()), [])
        attempt = self.lock_in_subprocess()
        self.assertEqual(attempt.returncode, 0, attempt.stderr)

    def test_lock_rejects_symlinked_lock_and_parent(self):
        external = self.root.parent / "external"
        external.mkdir()
        sentinel = external / "sentinel"
        sentinel.write_text("keep")
        lock = self.root.parent / ".observability-backup.lock"
        lock.symlink_to(sentinel)
        with self.assertRaises((ValueError, OSError)):
            with backup.backup_lock(self.root):
                self.fail("must not lock through a symlink")
        lock.unlink()
        parent_link = self.root.parent / "linked-parent"
        parent_link.symlink_to(external, target_is_directory=True)
        (external / "snapshots").mkdir()
        with self.assertRaises((ValueError, OSError)):
            with backup.backup_lock(parent_link / "snapshots"):
                self.fail("must not operate through a symlinked parent")
        self.assertEqual(sentinel.read_text(), "keep")

    @contextmanager
    def main_fixture(self):
        identities = self.root.parent / "storage.json"
        identities.write_text(json.dumps({name: {} for name in (*backup.SERVICES, "backups")}))
        mapped = {"/identity/storage.json": identities, "/backup/snapshots": self.root}

        def fixture_path(value):
            return mapped.get(str(value), Path(value))

        previous_umask = os.umask(0o077)
        os.umask(previous_umask)
        try:
            with patch.object(backup, "Path", side_effect=fixture_path), patch.object(
                backup, "Kube", return_value=Mock()
            ), patch.object(backup, "check_identities"), redirect_stdout(io.StringIO()):
                yield
        finally:
            os.umask(previous_umask)

    def test_maintenance_cleans_and_prunes_without_capturing_or_creating_draft(self):
        stale = self.partial()
        completed = [self.snapshot(number) for number in range(1, 5)]
        with self.main_fixture(), patch.object(backup, "create_archive") as capture:
            result = backup.main(maintenance_only=True, legacy_min_age_seconds=0)
        self.assertEqual(result, {"removed_partials": 1, "removed_completed": 2})
        capture.assert_not_called()
        self.assertFalse(stale.exists())
        self.assertEqual(set(self.root.iterdir()), set(completed[-2:]))

    def test_capture_failure_removes_own_draft_but_preserves_unknown_draft(self):
        unknown = self.root / ".partial-important"
        unknown.mkdir()

        def failed_capture(*_args, **_kwargs):
            drafts = [path for path in self.root.iterdir() if path != unknown]
            self.assertEqual(len(drafts), 1)
            marker = json.loads((drafts[0] / ".backup-owner.json").read_text())
            self.assertEqual(marker["owner"], backup.OWNER)
            attempt = self.lock_in_subprocess()
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn("Another observability backup operation is active", attempt.stderr)
            raise RuntimeError("fixture capture failure")

        with self.main_fixture(), patch.object(
            backup, "create_archive", side_effect=failed_capture
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture capture failure"):
                backup.main()
        self.assertEqual(list(self.root.iterdir()), [unknown])
        self.assertEqual(self.lock_in_subprocess().returncode, 0)

    def test_main_rejects_nondefault_legacy_age_before_touching_storage(self):
        for age in (0, 1, 86401, 86400.5):
            with self.subTest(age=age):
                with self.assertRaisesRegex(ValueError, "maintenance-only"):
                    backup.main(legacy_min_age_seconds=age)

    def test_main_rejects_symlink_parent_before_creating_snapshot_directory(self):
        external = self.root.parent / "external"
        external.mkdir()
        linked_parent = self.root.parent / "linked-parent"
        linked_parent.symlink_to(external, target_is_directory=True)
        self.root = linked_parent / "snapshots"
        with self.main_fixture():
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                backup.main(maintenance_only=True)
        self.assertFalse((external / "snapshots").exists())
        self.assertFalse((external / ".observability-backup.lock").exists())

    def test_cli_rejects_nondefault_legacy_age_before_touching_storage(self):
        for age in ("0", "1", "86401", "86400.5"):
            with self.subTest(age=age):
                result = subprocess.run(
                    [sys.executable, "-m", "recovery.backup", "--legacy-min-age-seconds", age],
                    cwd=Path(__file__).resolve().parents[2],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("maintenance-only", result.stderr)
                self.assertNotIn("FileNotFoundError", result.stderr)

    def test_cronjob_uses_three_day_interval_without_resetting_schedule_anchor(self):
        manifest = (
            Path(__file__).resolve().parents[2] / "recovery/deploy/cronjob.yaml"
        ).read_text()
        self.assertRegex(manifest, r'(?m)^  schedule: "@every 72h"$')
        self.assertNotRegex(manifest, r'(?m)^  startingDeadlineSeconds:')
        self.assertRegex(manifest, r'(?m)^  concurrencyPolicy: Forbid$')


if __name__ == "__main__":
    unittest.main()

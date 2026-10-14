"""Offline regression coverage for fast, private, verified component archives."""

# Test fixtures intentionally inspect private capture boundaries.
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
from contextlib import closing
import gzip
import hashlib
import os
from pathlib import Path
import random
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from recovery import archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "backup"
        self.source.mkdir()
        self.output.mkdir()
        self.destination = self.output / "component.tar.gz"

    def test_fast_archive_round_trips_memory_and_disk_staged_payloads(self):
        payloads = {
            "settings.json": b'{"retained": true}\n',
            "block": random.Random(27).randbytes(archive.MEMORY_FILE_LIMIT + 1),
        }
        for name, payload in payloads.items():
            (self.source / name).write_bytes(payload)
        (self.source / "settings.json").chmod(0o640)

        result = archive.create_archive(self.source, self.destination, compresslevel=1)

        self.assertEqual(result["files"], len(payloads))
        self.assertEqual(result["uncompressed_bytes"], sum(map(len, payloads.values())))
        self.assertEqual(result["bytes"], self.destination.stat().st_size)
        self.assertEqual(result["sha256"], hashlib.sha256(self.destination.read_bytes()).hexdigest())
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.output.iterdir()), [self.destination])
        with tarfile.open(self.destination) as captured:
            self.assertEqual(captured.getnames(), [".", "block", "settings.json"])
            self.assertEqual(captured.getmember("settings.json").mode, 0o640)
            for name, payload in payloads.items():
                self.assertEqual(captured.extractfile(name).read(), payload)
                self.assertEqual((self.source / name).read_bytes(), payload)

    def test_default_and_fast_levels_are_effective_gzip_settings(self):
        (self.source / "payload").write_bytes(b"repeated fixture\n" * 100)
        default = self.output / "default.tar.gz"
        fast = self.output / "fast.tar.gz"
        archive.create_archive(self.source, default)
        archive.create_archive(self.source, fast, compresslevel=1)
        # RFC 1952's XFL flags distinguish maximum compression from fastest gzip.
        self.assertEqual(default.read_bytes()[8], 2)
        self.assertEqual(fast.read_bytes()[8], 4)
        self.assertEqual(gzip.decompress(default.read_bytes()), gzip.decompress(fast.read_bytes()))

    def test_invalid_compression_levels_are_rejected_before_staging(self):
        for level in (-1, 10, 1.0, True, False, "1", None):
            with self.subTest(level=level), self.assertRaises(ValueError):
                archive.create_archive(self.source, self.destination, compresslevel=level)
            self.assertEqual(list(self.output.iterdir()), [])

    def test_zero_compression_level_remains_supported(self):
        (self.source / "payload").write_bytes(b"uncompressed gzip fixture")
        archive.create_archive(self.source, self.destination, compresslevel=0)
        with tarfile.open(self.destination) as captured:
            self.assertEqual(captured.extractfile("payload").read(), b"uncompressed gzip fixture")

    def test_fast_live_sqlite_capture_includes_committed_wal_without_sidecars(self):
        database = self.source / "grafana.db"
        live = sqlite3.connect(database)
        self.addCleanup(live.close)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("CREATE TABLE dashboards (title TEXT)")
        live.execute("INSERT INTO dashboards VALUES ('latest committed dashboard')")
        live.commit()
        wal = Path(str(database) + "-wal")
        before = (database.read_bytes(), wal.read_bytes())

        result = archive.create_archive(self.source, self.destination, compresslevel=1)

        self.assertEqual(result["sqlite_databases"], ["grafana.db"])
        self.assertEqual(result["sqlite_quiescent_copies"], [])
        self.assertEqual(result["sqlite_stable_wal_copies"], [])
        self.assertEqual((database.read_bytes(), wal.read_bytes()), before)
        with tarfile.open(self.destination) as captured:
            self.assertEqual(captured.getnames(), [".", "grafana.db"])
            restored = self.root / "restored.db"
            restored.write_bytes(captured.extractfile("grafana.db").read())
        with closing(sqlite3.connect(restored)) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchall(), [("ok",)])
            self.assertEqual(
                connection.execute("SELECT title FROM dashboards").fetchall(),
                [("latest committed dashboard",)],
            )

    def test_timeout_after_staging_leaves_no_published_or_scratch_files(self):
        (self.source / "payload").write_bytes(b"fixture")
        now = [0.0]
        original = archive._copy_regular

        def expire_after_copy(*args):
            result = original(*args)
            now[0] = 2.0
            return result

        with patch.object(archive.time, "monotonic", side_effect=lambda: now[0]), patch.object(
            archive, "_copy_regular", side_effect=expire_after_copy
        ):
            with self.assertRaises(TimeoutError):
                archive.create_archive(
                    self.source, self.destination, timeout_seconds=1, compresslevel=1
                )
        self.assertEqual(list(self.output.iterdir()), [])

    def test_changed_source_retries_but_never_publishes_unstable_capture(self):
        source = self.source / "payload"
        source.write_bytes(b"fixture")
        original = archive._copy_stream

        def mutate_after_copy(*args):
            result = original(*args)
            with source.open("ab") as changed:
                changed.write(b" changed")
            return result

        with patch.object(archive, "_copy_stream", side_effect=mutate_after_copy) as copy:
            with self.assertRaises(archive.SourceChangedError):
                archive.create_archive(self.source, self.destination, compresslevel=1)
        self.assertGreater(copy.call_count, 1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_gzip_trailer_corruption_cannot_be_published(self):
        (self.source / "payload").write_bytes(b"fixture")
        original = archive._entries
        scans = 0

        def corrupt_after_capture(*args):
            nonlocal scans
            entries = original(*args)
            scans += 1
            if scans == 2:
                draft = next(self.output.glob(".ha-dr-archive-*/archive.tar.gz"))
                with draft.open("r+b") as stream:
                    stream.seek(-8, os.SEEK_END)
                    value = stream.read(1)
                    stream.seek(-8, os.SEEK_END)
                    stream.write(bytes([value[0] ^ 1]))
            return entries

        with patch.object(archive, "_entries", side_effect=corrupt_after_capture):
            with self.assertRaises(gzip.BadGzipFile):
                archive.create_archive(self.source, self.destination, compresslevel=1)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_destination_is_never_replaced(self):
        self.destination.write_bytes(b"previous complete archive")
        with self.assertRaises(FileExistsError):
            archive.create_archive(self.source, self.destination, compresslevel=1)
        self.assertEqual(self.destination.read_bytes(), b"previous complete archive")
        self.assertEqual(list(self.output.iterdir()), [self.destination])


if __name__ == "__main__":
    unittest.main()

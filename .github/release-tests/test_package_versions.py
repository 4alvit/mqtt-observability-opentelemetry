"""Reject a mixed-version MQTT candidate before snapshots or container builds."""

import importlib.util
import json
import shutil
import unittest
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

PACKAGER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "package_release.py"
SPEC = importlib.util.spec_from_file_location("package_release", PACKAGER_PATH)
assert SPEC is not None and SPEC.loader is not None
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


class PackageVersionTests(unittest.TestCase):
    """Exercise the actual packaging entry point with only build side effects replaced."""

    def setUp(self) -> None:
        """Create two small component manifests and an explicit companion policy."""
        directory = mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        self.root = Path(directory)
        self.output = self.root / "output"
        self.policy = {
            "mode": "release",
            "repository": "example/mqtt",
            "version_file": "interceptor.toml",
            "version_companions": ["exporter.toml"],
        }
        (self.root / ".release-policy.json").write_text(json.dumps(self.policy))
        (self.root / ".release-package.json").write_text("{}")
        for filename in ("interceptor.toml", "exporter.toml"):
            (self.root / filename).write_text('[project]\nversion = "0.2.1"\n')

    def test_matching_components_pass(self) -> None:
        """A synchronized candidate retains support for multiple component manifests."""
        PACKAGER.validate_versions(self.root, self.policy, "0.2.1")

    def test_mixed_companion_blocks_before_any_build(self) -> None:
        """An exporter still at the old version must never enter a new candidate archive."""
        (self.root / "exporter.toml").write_text('[project]\nversion = "0.1.0"\n')
        with patch.object(PACKAGER, "snapshot") as snapshot:
            with self.assertRaisesRegex(ValueError, "exporter.toml"):
                PACKAGER.build_candidate(self.root, "0.2.1", "beta", self.output)
            snapshot.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_missing_companion_is_not_silently_skipped(self) -> None:
        """Declared metadata must exist before a candidate can be built."""
        (self.root / "exporter.toml").unlink()
        with self.assertRaises(FileNotFoundError):
            PACKAGER.build_candidate(self.root, "0.2.1", "rc", self.output)
        self.assertFalse(self.output.exists())

    def test_invalid_companion_configuration_fails(self) -> None:
        """A scalar path must not be mistaken for a list of metadata files."""
        with self.assertRaisesRegex(ValueError, "version_companions"):
            PACKAGER.validate_versions(
                self.root, {**self.policy, "version_companions": "exporter.toml"}, "0.2.1"
            )

    def test_invalid_channel_is_rejected_before_adapter_execution(self) -> None:
        """Direct callers cannot forward an unchecked channel to the version adapter."""
        self.policy["versioning"] = {"schema": 1}
        (self.root / ".release-policy.json").write_text(
            json.dumps(self.policy), encoding="utf-8"
        )
        for channel in ("stable", "preview", "--root", "beta\nrc"):
            with self.subTest(channel=channel):
                with patch.object(PACKAGER.subprocess, "run") as run:
                    with patch.object(PACKAGER, "snapshot") as snapshot:
                        with self.assertRaisesRegex(ValueError, "promote an existing RC"):
                            PACKAGER.build_candidate(
                                self.root, "0.2.1", channel, self.output
                            )
                        run.assert_not_called()
                        snapshot.assert_not_called()
                self.assertFalse(self.output.exists())

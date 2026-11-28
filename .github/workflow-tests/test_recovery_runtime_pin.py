"""Keep the recovery runtime image, its interpreter and its probe in one pin.

The recovery runtime is deliberately CPython 3.14: its lock is compiled for that
interpreter, and the planner and smoke test deploy the same image reference. A
dependency update that moves only the Dockerfile breaks the image at build time
with a bare assertion from the probe, so the pins are checked here instead.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = "recovery/runtime/Dockerfile"
PLANNER = "recovery/migrate.py"
SMOKE = "scripts/kubernetes_smoke.py"
PROBE = "tests/tls/recovery_runtime_probe.py"
LOCK = "recovery/runtime/requirements.lock"
IMAGE = re.compile(r"python:(\d+)\.(\d+)-slim@sha256:[0-9a-f]{64}")


def read(path: str) -> str:
    return (ROOT / path).read_text()


def references(path: str) -> set[str]:
    """Pinned CPython slim image references in a file."""
    return {match.group(0) for match in IMAGE.finditer(read(path))}


class RecoveryRuntimePinTests(unittest.TestCase):
    def test_dockerfile_pins_exactly_one_runtime_image(self):
        self.assertEqual(len(references(DOCKERFILE)), 1, f"{DOCKERFILE} must pin one slim image")

    def test_planner_and_smoke_deploy_the_same_runtime_image(self):
        expected = references(DOCKERFILE)
        for path in (PLANNER, SMOKE):
            self.assertEqual(references(path), expected,
                             f"{path} must reference the image {DOCKERFILE} builds")

    def test_probe_expects_the_pinned_interpreter(self):
        image = IMAGE.search(read(DOCKERFILE))
        self.assertIsNotNone(image, f"{DOCKERFILE} must pin one slim image")
        probed = re.search(r"assert sys\.version_info\[:2\] == \((\d+), (\d+)\)", read(PROBE))
        self.assertIsNotNone(probed, f"{PROBE} must assert the interpreter version")
        self.assertEqual(probed.groups(), image.groups()[:2],
                         f"{PROBE} must expect the interpreter {DOCKERFILE} pins; "
                         "update it together with the image")

    def test_probe_expects_the_locked_cryptography(self):
        locked = re.search(r"(?m)^cryptography==(\S+)", read(LOCK))
        self.assertIsNotNone(locked, f"{LOCK} must pin cryptography")
        probed = re.search(r'assert cryptography\.__version__ == "([^"]+)"', read(PROBE))
        self.assertIsNotNone(probed, f"{PROBE} must assert the cryptography version")
        self.assertEqual(probed.group(1), locked.group(1),
                         f"{PROBE} must expect the cryptography build in {LOCK}")


if __name__ == "__main__":
    unittest.main()

"""Prevent an older generator from skipping main-branch release qualification."""

import unittest
from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def require_full_release(workflow: dict):
    steps = workflow["jobs"]["scope"]["steps"]
    classifiers = [step for step in steps if step.get("id") == "scope"]
    if len(classifiers) != 1:
        message = "Expected one release change classifier"
        raise ValueError(message)
    classifier = classifiers[0]
    value = classifier.get("env", {}).get("FORCE_FULL")
    if value is not True and value != "true":
        message = "Main-branch release qualification must always run in full"
        raise ValueError(message)
    command = classifier.get("run", "")
    if (
        "args+=(--force)" not in command
        or 'scripts/change_scope.py "${args[@]}"' not in command
    ):
        message = "Release classifier must forward the force-full argument"
        raise ValueError(message)


def require_full_quality(workflow: dict):
    checks = workflow["jobs"]["checks"]
    if (
        checks.get("uses") != "./.github/workflows/quality-gate.yml"
        or checks.get("with", {}).get("force-full") is not True
    ):
        message = "Release quality gate must explicitly force full validation"
        raise ValueError(message)


class ReleaseScopeTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-pipeline.yml").read_text()
        )

    def test_release_qualification_never_skips_documentation(self):
        require_full_release(self.workflow)

    def test_release_quality_gate_stays_full(self):
        require_full_quality(self.workflow)

    def test_release_quality_gate_rejects_missing_or_weak_force(self):
        workflow = deepcopy(self.workflow)
        for value in (False, None, 1, "true", "false", ""):
            with self.subTest(force_full=value):
                workflow["jobs"]["checks"]["with"]["force-full"] = value
                try:
                    require_full_quality(workflow)
                except ValueError as error:
                    if "explicitly force full validation" not in str(error):
                        raise
                else:
                    self.fail("Invalid quality force-full value was accepted")

    def test_old_generator_documentation_skip_is_rejected(self):
        workflow = deepcopy(self.workflow)
        (classifier,) = [
            step
            for step in workflow["jobs"]["scope"]["steps"]
            if step.get("id") == "scope"
        ]
        for value in ("false", False, None, 1, "TRUE", ""):
            with self.subTest(force_full=value):
                classifier["env"]["FORCE_FULL"] = value
                try:
                    require_full_release(workflow)
                except ValueError as error:
                    if "always run in full" not in str(error):
                        raise
                else:
                    self.fail("Invalid force-full value was accepted")


if __name__ == "__main__":
    unittest.main()

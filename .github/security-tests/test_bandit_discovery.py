"""Exercise real Bandit discovery with repository and worktree Git layouts."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = (".git", ".venv", ".venv-ci", "tests")
EXCLUDED_FILES = ("scripts/release.py", "scripts/release_control.py")
SOURCES = (
    "app.py",
    ".github/security_probe.py",
    "build_helpers.py",
    "tests_support.py",
    ".venv_helpers.py",
    "dist_helpers.py",
    "release-dist-helper.py",
    "scripts/release_helper.py",
)


class BanditDiscoveryTests(unittest.TestCase):
    """Exclude declared directories without hiding neighboring source files."""

    def configured_exclusions(self) -> str:
        commands = []
        for relative in ("scripts/ci.sh", ".github/workflows/release-security.yml"):
            text = (ROOT / relative).read_text().replace("\\\n", " ")
            commands.extend(line for line in text.splitlines() if "bandit -r " in line)
        self.assertEqual(len(commands), 1)
        values = []
        for command in commands:
            arguments = shlex.split(command)
            values.append(arguments[arguments.index("-x") + 1])
        self.assertEqual(len(set(values)), 1, "local and hosted discovery must agree")
        self.assertIn(
            "bash scripts/ci.sh bandit",
            (ROOT / ".github/workflows/release-security.yml").read_text(),
        )
        return values[0]

    def check_discovery(self, *, worktree: bool) -> None:
        exclusions = self.configured_exclusions()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in EXCLUDED:
                if directory == ".git" and worktree:
                    (root / directory).write_text(
                        "gitdir: /fixture/worktrees/example\n"
                    )
                    continue
                (root / directory).mkdir()
                (root / directory / "dependency.py").write_text("VALUE = 1\n")
            for name in (*SOURCES, *EXCLUDED_FILES):
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("VALUE = 1\n")
            report_file = root / "bandit-results.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bandit",
                    "-r",
                    ".",
                    "-x",
                    exclusions,
                    "-f",
                    "json",
                    "-o",
                    str(report_file),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_file.read_text())
            self.assertEqual(report["errors"], [])
            scanned = {
                name.removeprefix("./")
                for name in report["metrics"]
                if name != "_totals"
            }
            self.assertEqual(scanned, set(SOURCES))

    def test_regular_checkout(self) -> None:
        self.check_discovery(worktree=False)

    def test_git_worktree(self) -> None:
        self.check_discovery(worktree=True)

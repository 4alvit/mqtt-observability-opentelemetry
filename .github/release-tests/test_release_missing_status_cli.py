# Shared release-contract regression; keep consumers synchronized.
# ruff: noqa
# mypy: ignore-errors
# pylint: skip-file
# fmt: off
"""Exercise actual release entry points with a local Git checkout and fake gh."""

import base64
import hashlib
import json
import os
import shutil
# Subprocess calls use fixed argument vectors with shell=False.
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
REPO = "example/project"
LEDGER = "contents/release-version-state.json?ref=release-version-state"
STATE_REF = "git/ref/heads/release-version-state"

# This executable models the gh process boundary, including --include output.
# It never imports the release implementation or contacts GitHub.
FAKE_GH = r'''
import base64
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["FAKE_GH_ROOT"])
config = json.loads((root / "responses.json").read_text())
state_path = root / "remote.json"
state = json.loads(state_path.read_text())
args = sys.argv[1:]
if args[:2] == ["release", "upload"]:
    method, path = "UPLOAD", args[-2]
    asset = Path(args[-1])
    data = asset.read_bytes()
    state["assets"].append({"id": len(state["assets"]) + 1, "name": asset.name,
                            "size": len(data), "state": "uploaded",
                            "content": base64.b64encode(data).decode()})
    payload = b""
else:
    assert args[0] == "api" and args[args.index("--hostname") + 1] == "github.com"
    method = args[args.index("--method") + 1]
    endpoint = args[args.index("--") + 1]
    assert endpoint.startswith("repos/example/project")
    path = endpoint.removeprefix("repos/example/project").lstrip("/")
    body = json.load(sys.stdin) if "--input" in args else None
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps([method, path]) + "\n")

failure = config.get("failure", {})
if [method, path] == failure.get("request"):
    if "--include" in args:
        sys.stdout.write(failure.get("headers", "HTTP/2.0 %s Error\nContent-Type: application/json\r\n\r\n" % failure["status"]))
    sys.stdout.write(json.dumps(failure.get("body", {})))
    sys.stderr.write(failure["stderr"] + "\n")
    sys.exit(1)

status = 200
if method == "GET":
    if path in config["responses"]:
        payload = json.dumps(config["responses"][path]).encode()
    elif path == "releases/10/assets?per_page=100":
        payload = json.dumps([state["assets"]]).encode()
    elif path.startswith("releases/assets/"):
        asset = next(a for a in state["assets"] if a["id"] == int(path.split("/")[-1]))
        payload = base64.b64decode(asset["content"])
    elif path.startswith(("git/ref/tags/", "releases/tags/")) or path in (
        "contents/release-version-state.json?ref=release-version-state",
        "git/ref/heads/release-version-state",
    ):
        status = 404
        payload = b'{"message":"Not Found"}'
    else:
        raise AssertionError("Unexpected GET: " + path)
elif method == "POST" and path == "git/refs":
    state["refs"].append(body)
    payload = json.dumps(body).encode()
elif method == "POST" and path == "releases":
    state["releases"].append({**body, "id": 10, "html_url": "https://github.com/example/project/releases/tag/" + body["tag_name"]})
    payload = json.dumps(state["releases"][-1]).encode()
elif method == "PATCH" and path == "releases/10":
    state["releases"][-1].update(body)
    payload = json.dumps(state["releases"][-1]).encode()
elif method == "PUT" and path == "contents/release-version-state.json":
    state["ledger"] = json.loads(base64.b64decode(body["content"]))
    payload = b'{"content":{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}'
elif method != "UPLOAD":
    raise AssertionError("Unexpected mutation: " + method + " " + path)

state_path.write_text(json.dumps(state))
if "--paginate" in args and status == 200:
    assert "--include" in args and args[args.index("--jq") + 1] == "tojson"
    for page in json.loads(payload):
        sys.stdout.buffer.write(b"HTTP/2.0 200 Status\nContent-Type: application/json\r\n\r\n")
        sys.stdout.buffer.write(json.dumps(page).encode() + b"\n")
else:
    if "--include" in args:
        sys.stdout.buffer.write(("HTTP/2.0 %d Status\nContent-Type: application/json\r\n\r\n" % status).encode())
    sys.stdout.buffer.write(payload)
if status == 404:
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
    sys.exit(1)
'''


class MissingResourceStatusTests(unittest.TestCase):
    def checkout(self, versioned):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        fixture = Path(temporary.name)
        root = fixture / "checkout"
        root.mkdir()
        shutil.copytree(SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        policy = {"repository": REPO, "mode": "release", "version_file": "VERSION"}
        if versioned:
            policy["versioning"] = {
                "schema": 1, "promotion": "promote-bytes",
                "files": [{"path": "VERSION", "format": "text", "value": "package"}],
            }
        raw = json.dumps(policy).encode()
        (root / ".release-policy.json").write_bytes(raw)
        (root / "VERSION").write_text("1.2.3\n")
        for args in (
            ["init", "-q"], ["add", "."],
            ["-c", "user.name=Release fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture"],
        ):
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", *args], cwd=root, check=True, capture_output=True)  # nosec B603, B607
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()  # nosec B603, B607
        config = {"responses": {
            "": {"full_name": REPO, "default_branch": "main"},
            "actions/runs/17": {
                "id": 17, "run_attempt": 1, "head_sha": sha, "head_branch": "main",
                "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
                "path": ".github/workflows/release-pipeline.yml", "event": "workflow_dispatch",
                "status": "in_progress", "conclusion": None,
            },
            "actions/runs/17/attempts/1/jobs?per_page=100": [{"jobs": [{
                "name": "Release gate", "status": "completed", "conclusion": "success", "head_sha": sha,
            }]}],
            "contents/.release-policy.json?ref=" + sha: {
                "type": "file", "path": ".release-policy.json", "encoding": "base64",
                "size": len(raw), "content": base64.b64encode(raw).decode(),
                "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest(),
            },
            "compare/" + sha + "...main": {"status": "identical", "merge_base_commit": {"sha": sha}},
            "git/ref/heads/main": {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}},
            "tags?per_page=100": [[]], "releases?per_page=100": [[]],
        }}
        (fixture / "responses.json").write_text(json.dumps(config))
        (fixture / "remote.json").write_text(json.dumps({"refs": [], "releases": [], "assets": [], "ledger": None}))
        (fixture / "event.json").write_text('{"inputs":{"channel":"beta","version":"1.2.3"}}')
        (fixture / "gh").write_text("#!" + sys.executable + "\n" + FAKE_GH)
        (fixture / "gh").chmod(0o755)
        assets = root / "assets"
        assets.mkdir()
        (assets / "package.tar.gz").write_bytes(b"unchanged package bytes")
        env = {"HOME": str(fixture), "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull}
        env.update({
            "PATH": str(fixture) + os.pathsep + os.environ["PATH"], "FAKE_GH_ROOT": str(fixture),
            "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO,
            "GITHUB_RUN_ID": "17", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/release-pipeline.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(fixture / "event.json"),
            "GITHUB_OUTPUT": str(fixture / "output"), "PUBLICATION_ENABLED": "true",
        })
        command = (["release_versioned.py", "prepare", "--repo", REPO] if versioned else [
            "release_control.py", "candidate", "--repo", REPO, "--channel", "beta",
            "--version", "1.2.3", "--sha", sha, "--run-id", "17", "--run-attempt", "1", "--assets", "assets",
        ])
        command[0] = str(root / "scripts" / command[0])
        return fixture, root, config, env, [sys.executable, *command]

    def run_release(self, fixture, root, config, env, command):
        (fixture / "responses.json").write_text(json.dumps(config))
        return subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=30)  # nosec B603

    def test_genuine_404_allows_publication_or_plan_handoff(self):
        for versioned in (False, True):
            with self.subTest(versioned=versioned):
                fixture, root, config, env, command = self.checkout(versioned)
                result = self.run_release(fixture, root, config, env, command)
                self.assertEqual(result.returncode, 0, result.stderr)
                remote = json.loads((fixture / "remote.json").read_text())
                output = json.loads(result.stdout)
                if versioned:
                    plan = json.loads((root / ".release-plan.json").read_text())
                    self.assertEqual(remote["ledger"]["plans"]["17"]["plan"], plan)
                    self.assertEqual(remote["ledger"]["counter"], 1)
                    self.assertEqual(remote["ledger"]["publication_floor"], 0)
                    self.assertEqual(output["build"], "true")
                    self.assertEqual(output["plan_artifact"], "release-plan-17-1")
                    self.assertEqual(remote["releases"], [])
                else:
                    self.assertEqual(output["tag"], "v1.2.3-beta.1")
                    self.assertFalse(remote["releases"][0]["draft"])
                    self.assertEqual({a["name"] for a in remote["assets"]}, {"package.tar.gz", "release-manifest.json"})
                    self.assertTrue((root / ".release-evidence/release-manifest.json").is_file())
                self.assertTrue((fixture / "output").read_text())
                self.assertEqual((root / "VERSION").read_text(), "1.2.3\n")

    def assert_lookup_failure(self, versioned, path, failure):
        fixture, root, config, env, command = self.checkout(versioned)
        config["failure"] = {"request": ["GET", path], **failure}
        before = (fixture / "remote.json").read_bytes()
        result = self.run_release(fixture, root, config, env, command)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        if "headers" not in failure:
            self.assertIn("GET repos/" + REPO + "/" + path, result.stderr)
        else:
            self.assertIn("GitHub response headers", result.stderr)
        calls = [json.loads(line) for line in (fixture / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(calls[-1], ["GET", path])
        self.assertTrue(all(method == "GET" for method, _ in calls), calls)
        self.assertEqual((fixture / "remote.json").read_bytes(), before)
        self.assertEqual((root / "VERSION").read_text(), "1.2.3\n")
        for name in (".release-plan.json", ".release-inputs.json", ".release-evidence"):
            self.assertFalse((root / name).exists(), name)
        self.assertFalse((fixture / "output").exists())

    def test_status_shaped_error_strings_cannot_authorize_absence(self):
        # gh emits JSON `errors` strings verbatim, without the HTTP response code.
        for diagnostic in ("gh: HTTP 404", "gh: HTTP 404: upstream missing",
                           "gh: Not Found (HTTP 404)"):
            for versioned in (False, True):
                paths = ["git/ref/tags/v1.2.3", "releases/tags/v1.2.3"]
                if versioned:
                    paths += [LEDGER, STATE_REF]
                for path in paths:
                    with self.subTest(versioned=versioned, path=path, diagnostic=diagnostic):
                        self.assert_lookup_failure(versioned, path, {
                            "status": 403, "body": {"errors": diagnostic.removeprefix("gh: ")},
                            "stderr": diagnostic,
                        })

    def test_unavailable_or_non_404_status_fails_closed(self):
        failures = [
            {"status": status, "stderr": "gh: lookup failed"} for status in (401, 429, 500, 502)
        ] + [
            {"status": 403, "headers": headers, "stderr": "gh: HTTP 404"}
            for headers in ("", "HTTP/2.0 4040 Invalid\r\n\r\n", "HTTP 404\r\n\r\n")
        ]
        for versioned in (False, True):
            for failure in failures:
                with self.subTest(versioned=versioned, failure=failure):
                    self.assert_lookup_failure(versioned, "git/ref/tags/v1.2.3", failure)

    def test_failed_write_is_not_replayed(self):
        for versioned in (False, True):
            with self.subTest(versioned=versioned):
                fixture, root, config, env, command = self.checkout(versioned)
                config["failure"] = {
                    "request": ["POST", "git/refs"], "status": 403,
                    "body": {"errors": "HTTP 404: authorization failed"},
                    "stderr": "gh: HTTP 404: authorization failed",
                }
                before = (fixture / "remote.json").read_bytes()
                result = self.run_release(fixture, root, config, env, command)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("POST repos/" + REPO + "/git/refs", result.stderr)
                calls = [json.loads(line) for line in (fixture / "calls.jsonl").read_text().splitlines()]
                self.assertEqual([call for call in calls if call[0] != "GET"], [["POST", "git/refs"]])
                self.assertEqual((fixture / "remote.json").read_bytes(), before)
                self.assertFalse((root / ".release-plan.json").exists())
                self.assertFalse((fixture / "output").exists())


if __name__ == "__main__":
    unittest.main()

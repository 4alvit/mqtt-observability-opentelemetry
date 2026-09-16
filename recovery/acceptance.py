"""Capture/compare private read-only application acceptance across a placement move."""

# Orchestration keeps fail-closed phases together; immutable source helpers are reviewed separately.
# pylint: disable=too-many-locals
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .migrate import IMAGES, Operator, require, write_json


@contextmanager
def tunnel(operator, service, remote_port):
    """Expose one application Service on a temporary loopback-only port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (operator.evidence / (service + "-port-forward.log")).open("wb") as log:
        process = subprocess.Popen(
            operator.kube
            + [
                "-n",
                "observability",
                "port-forward",
                "service/" + service,
                str(port) + ":" + str(remote_port),
                "--address=127.0.0.1",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Port forward exited before acceptance")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.2)
            else:
                raise TimeoutError("Private acceptance tunnel unavailable")
            yield "http://127.0.0.1:" + str(port)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def request(base, path, credentials=None, json_result=True):
    """Read an application endpoint through the private local tunnel."""
    headers = {}
    if credentials:
        headers["Authorization"] = (
            "Basic " + base64.b64encode(credentials.encode()).decode()
        )
    with urllib.request.urlopen(
        urllib.request.Request(base + path, headers=headers), timeout=20
    ) as response:
        content = response.read()
    return json.loads(content) if json_result else content


def capture(service, directory, baseline=None, context="k3s-heaven"):
    """Record and optionally compare private application acceptance evidence."""
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    operator = Operator(directory, context)
    deployment = operator.get("deployment", service)
    pods = [
        p
        for p in operator.pods(service)
        if p.get("status", {}).get("phase") == "Running"
        and not p["metadata"].get("deletionTimestamp")
    ]
    require(
        len(pods) == 1
        and pods[0]["status"].get("containerStatuses")
        and all(c.get("ready") for c in pods[0]["status"]["containerStatuses"]),
        "Expected one ready application pod",
    )
    result = {
        "service": service,
        "pod_uid": pods[0]["metadata"]["uid"],
        "node": pods[0]["spec"]["nodeName"],
        "checks": {},
    }
    with tunnel(
        operator, service, {"grafana": 3000, "prometheus": 9090, "tempo": 3200}[service]
    ) as base:
        if service == "grafana":
            container = next(
                c
                for c in deployment["spec"]["template"]["spec"]["containers"]
                if c["name"] == service
            )
            credentials = []
            for variable in ("GF_SECURITY_ADMIN_USER", "GF_SECURITY_ADMIN_PASSWORD"):
                environment = next(e for e in container["env"] if e["name"] == variable)
                ref = environment["valueFrom"]["secretKeyRef"]
                secret = operator.get("secret", ref["name"])
                credentials.append(
                    base64.b64decode(secret["data"][ref["key"]]).decode()
                )
            auth = ":".join(credentials)
            require(
                request(base, "/api/health")["database"] == "ok",
                "Grafana database unhealthy",
            )
            dashboards = request(base, "/api/search?type=dash-db", auth)
            result["checks"]["dashboards"] = sorted(
                [{"uid": d["uid"], "title": d["title"]} for d in dashboards],
                key=lambda d: d["uid"],
            )
            datasources = request(base, "/api/datasources", auth)
            result["checks"]["datasources"] = sorted(
                datasources, key=lambda d: d["uid"]
            )
            result["checks"]["organization_users"] = sorted(
                [
                    {k: u.get(k) for k in ("userId", "login", "email", "role")}
                    for u in request(base, "/api/org/users", auth)
                ],
                key=lambda u: u["userId"],
            )
            result["checks"]["alert_rules"] = request(
                base, "/api/v1/provisioning/alert-rules", auth
            )
            config = operator.run(
                operator.kube
                + [
                    "-n",
                    "observability",
                    "exec",
                    pods[0]["metadata"]["name"],
                    "--",
                    "cat",
                    "/etc/grafana/grafana.ini",
                    "/usr/share/grafana/conf/defaults.ini",
                ]
            )
            result["checks"]["config_files_sha256"] = hashlib.sha256(config).hexdigest()
            result["checks"]["admin_credential_sha256"] = hashlib.sha256(
                auth.encode()
            ).hexdigest()
            require(
                len(dashboards) == 4 and not result["checks"]["alert_rules"],
                "Expected four dashboards and zero alert rules",
            )
        elif service == "prometheus":
            request(base, "/-/ready", json_result=False)
            timestamp = (
                baseline["checks"]["historical_timestamp"]
                if baseline
                else int(time.time()) - 600
            )
            value = request(
                base,
                "/api/v1/query?"
                + urllib.parse.urlencode({"query": "up", "time": timestamp}),
            )
            require(
                value["status"] == "success" and value["data"]["result"],
                "Historical up query returned no samples",
            )
            result["checks"] = {
                "historical_timestamp": timestamp,
                "historical_up": sorted(
                    value["data"]["result"],
                    key=lambda r: json.dumps(r["metric"], sort_keys=True),
                ),
            }
        else:
            request(base, "/ready", json_result=False)
            config = operator.get("configmap", "tempo-config")["data"]
            result["checks"]["config_sha256"] = hashlib.sha256(
                json.dumps(config, sort_keys=True).encode()
            ).hexdigest()
            result["checks"]["readiness"] = True
    write_json(directory / "acceptance.json", result)
    if baseline:
        require(
            result["checks"] == baseline["checks"],
            "Application acceptance differs; inspect private captures",
        )
        result["compared_equal"] = True
    write_json(directory / "acceptance.json", result)
    print(
        "Private application acceptance captured"
        + (" and baseline matched." if baseline else ".")
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=list(IMAGES))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--context", default="k3s-heaven")
    args = parser.parse_args()
    capture(
        args.service,
        args.directory,
        json.loads(args.baseline.read_text()) if args.baseline else None,
        args.context,
    )

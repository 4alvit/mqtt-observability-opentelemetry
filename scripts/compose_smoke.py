"""Exercise an isolated Compose stack and fail when health or traces are absent."""

import os
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml


def require_local_docker() -> None:
    """Prevent this local test helper from targeting a remote Docker host."""
    host = subprocess.check_output(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], text=True
    ).strip()
    host = os.environ.get("DOCKER_HOST", host)
    if not host.startswith(("unix://", "npipe://")):
        raise SystemExit("Compose smoke requires a local Docker endpoint")


def main() -> None:
    """Start disposable services without host ports or production container names."""
    require_local_docker()
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "docker/docker-compose.yml").read_text())
    for service in config["services"].values():
        service.pop("container_name", None)
        service.pop("ports", None)
    environment = config["services"]["mqtt-interceptor"]["environment"]
    environment[:] = [item for item in environment if not item.startswith("TRACE_SAMPLE_RATE=")]
    environment.append("TRACE_SAMPLE_RATE=1.0")
    with TemporaryDirectory(prefix="mqtt-compose-smoke-") as directory:
        path = Path(directory) / "compose.yml"
        path.write_text(yaml.safe_dump(config, sort_keys=False))
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(root / "docker"),
            "-f",
            str(path),
            "-p",
            "ci-mqtt-" + uuid.uuid4().hex[:10],
        ]
        services = [
            "mosquitto",
            "mqtt-interceptor",
            "mosquitto-exporter",
            "otelcol",
            "prometheus",
            "jaeger",
        ]
        try:
            subprocess.run([*command, "up", "-d", "--build", *services], check=True)
            readiness = (
                "import urllib.request; "
                "urls=['http://mosquitto-exporter:9494/metrics',"
                "'http://mqtt-interceptor:9464/metrics','http://jaeger:16686/',"
                "'http://prometheus:9090/-/healthy']; "
                "[urllib.request.urlopen(url,timeout=3).read(1) for url in urls]"
            )
            deadline = time.monotonic() + 120
            while True:
                result = subprocess.run(
                    [*command, "exec", "-T", "mqtt-interceptor", "python", "-c", readiness],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit("Stack readiness failed: " + result.stderr)
                time.sleep(2)
            subprocess.run([*command, "up", "-d", "--build", "mqtt-test-publisher"], check=True)
            trace_query = (
                "import json,urllib.request; "
                "response=urllib.request.urlopen('http://jaeger:16686/api/traces?"
                "service=mqtt-interceptor&lookback=1h',timeout=5); "
                "print(len(json.load(response).get('data',[])))"
            )
            deadline = time.monotonic() + 120
            while True:
                result = subprocess.run(
                    [*command, "exec", "-T", "mqtt-interceptor", "python", "-c", trace_query],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0 and int(result.stdout.strip()) > 0:
                    break
                if time.monotonic() >= deadline:
                    raise SystemExit("No MQTT interceptor trace reached Jaeger within 120 seconds")
                time.sleep(2)
        finally:
            subprocess.run([*command, "logs", "--tail", "80"], check=False)
            subprocess.run([*command, "down", "--volumes", "--remove-orphans"], check=True)


if __name__ == "__main__":
    main()

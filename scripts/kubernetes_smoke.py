"""Check hardened monitoring images using disposable local Docker services."""

import json
import subprocess
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from compose_smoke import require_local_docker

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "k3s"


def manifest(name: str, kind: str) -> dict:
    """Read one resource of the requested kind from a deployment file."""
    documents = yaml.safe_load_all((ROOT / f"{name}.yaml").read_text(encoding="utf-8"))
    return next(document for document in documents if document["kind"] == kind)


def write_config(name: str, directory: Path) -> Path:
    """Materialize the checked-in ConfigMap, using a disconnected Kubernetes API."""
    destination = directory / name
    destination.mkdir()
    for filename, content in manifest(name, "ConfigMap")["data"].items():
        if name == "prometheus-config":
            config = yaml.safe_load(content)
            # Exercise startup/storage without contacting any real Kubernetes cluster.
            for scrape in config["scrape_configs"]:
                for discovery in scrape.get("kubernetes_sd_configs", []):
                    discovery["api_server"] = "http://127.0.0.1:9"
            content = yaml.safe_dump(config)
        (destination / filename).write_text(content, encoding="utf-8")
    return destination


def runtime_service(pod: dict, container: dict, mounts: dict) -> dict:
    """Translate the manifest's image, identity and filesystem controls to Compose."""
    identity = pod["securityContext"]
    security = container["securityContext"]
    service = {
        "image": container["image"],
        "user": f"{identity['runAsUser']}:{identity['runAsGroup']}",
        "read_only": security["readOnlyRootFilesystem"],
        "cap_drop": security["capabilities"]["drop"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [],
        "tmpfs": ["/tmp:rw,noexec,nosuid,size=128m,mode=1777"],
    }
    for mount in container.get("volumeMounts", []):
        if mount["name"] == "temporary":
            continue
        source = mounts[mount["name"]]
        mode = "ro" if mount.get("readOnly") else "rw"
        service["volumes"].append(f"{source}:{mount['mountPath']}:{mode}")
    if "command" in container:
        service["entrypoint"] = container["command"][0]
        service["command"] = [argument.replace("$", "$$") for argument in container["command"][1:]]
    elif "args" in container:
        service["command"] = container["args"]
    service["environment"] = {
        item["name"]: item["value"] for item in container.get("env", []) if "value" in item
    }
    return service


def create_compose(directory: Path) -> dict:
    """Build test services from the actual Kubernetes workload settings."""
    dashboards = directory / "dashboards"
    dashboards.mkdir(mode=0o777)
    dashboards.chmod(0o777)  # Disposable bind mount emulates emptyDir's writable fsGroup.
    services = {}
    for name in ("grafana", "prometheus", "tempo"):
        pod = manifest(name, "Deployment")["spec"]["template"]["spec"]
        mounts = {"data": f"{name}-data", "dashboards": str(dashboards)}
        for volume in pod["volumes"]:
            if "configMap" in volume:
                mounts[volume["name"]] = str(write_config(volume["configMap"]["name"], directory))
        services[name] = runtime_service(pod, pod["containers"][0], mounts)
        if name == "grafana":
            services["bootstrap"] = runtime_service(pod, pod["initContainers"][0], mounts)
            services[name]["environment"]["GF_SECURITY_ADMIN_PASSWORD"] = uuid.uuid4().hex
            services[name]["depends_on"] = {
                "bootstrap": {"condition": "service_completed_successfully"}
            }
    return {
        "services": services,
        "volumes": {f"{name}-data": {} for name in ("grafana", "prometheus", "tempo")},
    }


def require_readiness(command: list[str]) -> None:
    """Require each configured monitoring image to reach its readiness endpoint."""
    probe = (
        "import urllib.request; "
        "urls=['http://grafana:3000/api/health','http://prometheus:9090/-/ready',"
        "'http://tempo:3200/ready']; "
        "[urllib.request.urlopen(url,timeout=3).read() for url in urls]"
    )
    deadline = time.monotonic() + 180
    while True:
        result = subprocess.run(
            [*command, "run", "--rm", "--no-deps", "-T", "bootstrap", "-c", probe],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise SystemExit("Hardened monitoring images did not become ready: " + result.stderr)
        time.sleep(3)


def main() -> None:
    """Check bootstrap output and readiness, then remove only this disposable stack."""
    require_local_docker()
    with TemporaryDirectory(prefix="kubernetes-smoke-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)
        compose = directory / "compose.yml"
        compose.write_text(yaml.safe_dump(create_compose(directory)), encoding="utf-8")
        command = ["docker", "compose", "-f", str(compose), "-p", "ci-k3s-" + uuid.uuid4().hex[:10]]
        try:
            subprocess.run([*command, "up", "-d", "grafana", "prometheus", "tempo"], check=True)
            require_readiness(command)
            dashboards = list((directory / "dashboards").glob("*.json"))
            if len(dashboards) != 4:
                raise SystemExit("Dashboard bootstrap did not provision all four dashboards")
            for dashboard in dashboards:
                content = dashboard.read_text(encoding="utf-8")
                if json.loads(content).get("id") is not None or "${DS_PROMETHEUS}" in content:
                    raise SystemExit("Dashboard normalization failed: " + dashboard.name)
            print("Hardened Grafana, Prometheus and Tempo are ready; all dashboards provisioned.")
        finally:
            subprocess.run([*command, "logs", "--tail", "60"], check=False)
            subprocess.run([*command, "down", "--volumes", "--remove-orphans"], check=True)


if __name__ == "__main__":
    main()

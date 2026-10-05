"""Exercise the deployed readiness commands and the isolated MP overlay."""

import io
import math
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from mosquitto_exporter.config import PrometheusConfig
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import OTELResourceDetector
from prometheus_client import CollectorRegistry, Gauge, generate_latest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "deploy" / "mqtt-k3s"
NOW = 2_000_000_000.0
CLIENTS = "flashmq_clients_connected"
HEARTBEAT = "mqtt_interceptor_last_message_timestamp_seconds"


class MQTTDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = list(
            yaml.safe_load_all((OVERLAY / "workloads.yaml").read_text())
        )
        cls.deployments = {
            doc["metadata"]["name"]: doc
            for doc in cls.documents
            if doc["kind"] == "Deployment"
        }

    def run_probe(self, component, body):
        """Run the exact manifest command; only HTTP I/O and wall time are replaced."""
        container = self.deployments[component]["spec"]["template"]["spec"][
            "containers"
        ][0]
        command = container["readinessProbe"]["exec"]["command"]
        self.assertEqual(command[:2], ["python", "-c"])
        self.assertEqual(len(command), 3)
        port = 9494 if component == "mosquitto-exporter" else 9464

        def response(url, timeout):
            self.assertEqual(url, f"http://127.0.0.1:{port}/metrics")
            self.assertEqual(timeout, 3)
            if isinstance(body, Exception):
                raise body
            return io.BytesIO(body)

        with (
            patch("urllib.request.urlopen", side_effect=response),
            patch("time.time", return_value=NOW),
            tempfile.TemporaryDirectory() as directory,
        ):
            script = Path(directory) / "readiness.py"
            script.write_text(command[2])
            runpy.run_path(str(script))

    def test_real_otel_prometheus_samples_with_and_without_scope_labels(self):
        """The default OTel scope labels must not make a healthy exporter unready."""
        for scope_labels in (True, False):
            with self.subTest(scope_labels=scope_labels):
                registry = CollectorRegistry()
                reader = PrometheusMetricReader(
                    registry=registry, scope_info_enabled=scope_labels
                )
                provider = MeterProvider(metric_readers=[reader])
                present = [True]
                provider.get_meter(
                    "mosquitto_exporter.app", "test"
                ).create_observable_gauge(
                    CLIENTS,
                    callbacks=[
                        lambda _, present=present: (
                            [Observation(25)] if present[0] else []
                        )
                    ],
                )
                try:
                    text = generate_latest(registry)
                    self.assertIn(
                        (CLIENTS + ("{" if scope_labels else " ")).encode(), text
                    )
                    self.run_probe("mosquitto-exporter", text)
                    present[0] = False
                    with self.assertRaises(AssertionError):
                        self.run_probe("mosquitto-exporter", generate_latest(registry))
                finally:
                    provider.shutdown()

    def test_real_heartbeat_export_accepts_optional_labels(self):
        for labels in ((), ("broker",)):
            with self.subTest(labels=labels):
                registry = CollectorRegistry()
                gauge = Gauge(
                    HEARTBEAT, "Latest receipt", labelnames=labels, registry=registry
                )
                sample = gauge.labels("cerbo") if labels else gauge
                sample.set(NOW - 10)
                self.run_probe("mqtt-interceptor", generate_latest(registry))

    def test_heartbeat_rejects_stale_future_and_nonfinite_samples(self):
        for value in (NOW - 45, NOW - 300, NOW + 1, 0, math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                registry = CollectorRegistry()
                Gauge(HEARTBEAT, "Latest receipt", registry=registry).set(value)
                with self.assertRaises(AssertionError):
                    self.run_probe("mqtt-interceptor", generate_latest(registry))

    def test_client_count_requires_finite_nonnegative_sample(self):
        self.run_probe("mosquitto-exporter", f"{CLIENTS} 0\n".encode())
        for value in ("NaN", "+Inf", "-Inf", "-1"):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                self.run_probe(
                    "mosquitto-exporter",
                    f'{CLIENTS}{{otel_scope_name="exporter"}} {value}\n'.encode(),
                )

    def test_missing_wrong_name_or_ambiguous_series_fail_closed(self):
        for component, metric, value in (
            ("mosquitto-exporter", CLIENTS, 25),
            ("mqtt-interceptor", HEARTBEAT, NOW - 1),
        ):
            bodies = (
                b"",
                f"# HELP {metric} Not a sample\n# TYPE {metric} gauge\n".encode(),
                f"{metric}_unrelated {value}\n".encode(),
                f'{metric}{{scope="one"}} {value}\n{metric}{{scope="two"}} {value}\n'.encode(),
            )
            for body in bodies:
                with (
                    self.subTest(component=component, body=body),
                    self.assertRaises(AssertionError),
                ):
                    self.run_probe(component, body)

    def test_probe_network_failure_is_not_a_success(self):
        for component in ("mqtt-interceptor", "mosquitto-exporter"):
            with self.subTest(component=component), self.assertRaises(TimeoutError):
                self.run_probe(component, TimeoutError())

    def test_exporter_port_uses_explicit_integer_despite_service_name_collision(self):
        container = self.deployments["mosquitto-exporter"]["spec"]["template"]["spec"][
            "containers"
        ][0]
        env = {item["name"]: item["value"] for item in container["env"]}
        with patch.dict(
            os.environ, {"PROMETHEUS_PORT": "tcp://10.43.0.80:9090"}, clear=True
        ):
            with self.assertRaises(ValidationError):
                PrometheusConfig()
            # Explicit container env also protects the numeric port if a caller
            # accidentally restores service links in a derived deployment.
            with patch.dict(os.environ, env):
                self.assertEqual(PrometheusConfig().port, 9494)
                self.assertTrue(PrometheusConfig().enabled)

    def test_reader_resource_env_is_accepted_by_the_standard_sdk_detector(self):
        for name, service in (
            ("mqtt-interceptor", "cerbo-mqtt-observer"),
            ("mosquitto-exporter", "cerbo-broker-exporter"),
        ):
            container = self.deployments[name]["spec"]["template"]["spec"][
                "containers"
            ][0]
            env = {item["name"]: item["value"] for item in container["env"]}
            with self.subTest(component=name):
                # JSON dictionaries are not valid in the standard OTel variable;
                # these readers need only their explicit unique service names.
                self.assertNotIn("OTEL_RESOURCE_ATTRIBUTES", env)
                with (
                    patch.dict(os.environ, env, clear=True),
                    self.assertNoLogs("opentelemetry.sdk.resources", level="WARNING"),
                ):
                    resource = OTELResourceDetector().detect()
                self.assertEqual(resource.attributes["service.name"], service)

    def test_workloads_are_singleton_restricted_mp_readers(self):
        self.assertEqual(
            set(self.deployments),
            {"mqtt-interceptor", "mosquitto-exporter", "mqtt-otel-collector"},
        )
        client_ids = []
        source_revisions = set()
        for name, deployment in self.deployments.items():
            with self.subTest(component=name):
                self.assertEqual(deployment["metadata"]["namespace"], "observability")
                self.assertEqual(deployment["spec"]["replicas"], 1)
                self.assertEqual(deployment["spec"]["strategy"]["type"], "Recreate")
                pod = deployment["spec"]["template"]["spec"]
                self.assertEqual(pod["nodeSelector"], {"kubernetes.io/hostname": "mp"})
                self.assertIs(pod["automountServiceAccountToken"], False)
                self.assertIs(pod["enableServiceLinks"], False)
                for key in ("hostNetwork", "hostPID", "hostIPC"):
                    self.assertFalse(pod.get(key, False))
                self.assertIs(pod["securityContext"]["runAsNonRoot"], True)
                self.assertGreater(pod["securityContext"]["runAsUser"], 0)
                self.assertEqual(
                    pod["securityContext"]["seccompProfile"]["type"], "RuntimeDefault"
                )
                self.assertEqual(len(pod["containers"]), 1)
                self.assertNotIn("initContainers", pod)
                for volume in pod.get("volumes", []):
                    self.assertEqual(set(volume), {"name", "configMap"})
                container = pod["containers"][0]
                security = container["securityContext"]
                self.assertIs(security["allowPrivilegeEscalation"], False)
                self.assertIs(security["readOnlyRootFilesystem"], True)
                self.assertEqual(security["capabilities"], {"drop": ["ALL"]})
                self.assertFalse(security.get("privileged", False))
                self.assertRegex(container["image"], r"@sha256:[a-f0-9]{64}$")
                for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
                    self.assertEqual(container[probe]["timeoutSeconds"], 5)
                self.assertEqual(container["startupProbe"]["periodSeconds"], 5)
                self.assertEqual(container["startupProbe"]["failureThreshold"], 36)
                for budget in ("requests", "limits"):
                    self.assertEqual(
                        set(container["resources"][budget]), {"cpu", "memory"}
                    )
                if name == "mqtt-otel-collector":
                    continue
                env = {item["name"]: item["value"] for item in container["env"]}
                self.assertEqual(env["MQTT_UPSTREAM_HOST"], "192.168.160.150")
                self.assertEqual(env["MQTT_UPSTREAM_PORT"], "1883")
                self.assertEqual(env["MQTT_VERSION"], "3")
                self.assertNotIn("envFrom", container)
                self.assertEqual(
                    env["OTEL_ENDPOINT"],
                    "http://mqtt-otel-collector.observability.svc.cluster.local:4317",
                )
                client_ids.append(env["MQTT_CLIENT_ID"])
                revision = deployment["spec"]["template"]["metadata"]["annotations"][
                    "victron.2560801.xyz/source-commit"
                ]
                self.assertRegex(revision, r"^[a-f0-9]{40}$")
                source_revisions.add(revision)
                if name == "mqtt-interceptor":
                    self.assertEqual(
                        env["TRACE_TOPIC_PATTERNS"],
                        "$SYS/broker/load/messages/received/total",
                    )
                else:
                    self.assertEqual(env["METRICS_BROKER_TYPE"], "flashmq")
                    self.assertEqual(env["METRICS_FLASHMQ_THREADS"], "1")
                    self.assertEqual(env["METRICS_STALE_THRESHOLD"], "45")
        self.assertEqual(len(set(client_ids)), 2)
        self.assertEqual(len(source_revisions), 1)
        for doc in self.documents:
            if doc["kind"] == "Service":
                self.assertIn(doc["spec"].get("type", "ClusterIP"), ["ClusterIP"])
                self.assertNotIn("externalIPs", doc["spec"])
                self.assertTrue(
                    all("nodePort" not in port for port in doc["spec"]["ports"])
                )

    def test_collector_payload_is_exact_and_expiration_is_bounded(self):
        runtime = (OVERLAY / "collector-runtime.yaml").read_text()
        configmap = yaml.safe_load((OVERLAY / "collector-config.yaml").read_text())
        self.assertEqual(configmap["data"]["config.yaml"], runtime)
        config = yaml.safe_load(runtime)
        self.assertEqual(set(config["receivers"]), {"otlp"})
        self.assertEqual(set(config["exporters"]), {"prometheus", "otlp/tempo"})
        self.assertEqual(config["exporters"]["prometheus"]["metric_expiration"], "60s")
        self.assertEqual(
            config["exporters"]["otlp/tempo"]["endpoint"],
            "tempo.observability.svc.cluster.local:4317",
        )
        self.assertLessEqual(
            config["exporters"]["otlp/tempo"]["sending_queue"]["queue_size"], 128
        )
        for pipeline in config["service"]["pipelines"].values():
            self.assertEqual(pipeline["processors"], ["memory_limiter", "batch"])

    def test_overlay_does_not_apply_legacy_stack_or_public_network_access(self):
        kustomization = yaml.safe_load((OVERLAY / "kustomization.yaml").read_text())
        self.assertEqual(
            set(kustomization["resources"]),
            {"collector-config.yaml", "workloads.yaml", "network-policy.yaml"},
        )
        network = yaml.safe_load((OVERLAY / "network-policy.yaml").read_text())["spec"]
        self.assertEqual(
            network["podSelector"]["matchLabels"],
            {"app.kubernetes.io/part-of": "mqtt-observability"},
        )
        self.assertEqual(set(network["policyTypes"]), {"Ingress", "Egress"})
        allowed_ports = {"ingress": {9464, 8889, 4317}, "egress": {53, 1883, 4317}}
        for direction in ("ingress", "egress"):
            peers_key = "from" if direction == "ingress" else "to"
            for rule in network[direction]:
                self.assertTrue(rule.get(peers_key))
                self.assertTrue(rule.get("ports"))
                self.assertTrue(
                    {port["port"] for port in rule["ports"]} <= allowed_ports[direction]
                )
                for peer in rule[peers_key]:
                    if "ipBlock" in peer:
                        self.assertEqual(
                            peer, {"ipBlock": {"cidr": "192.168.160.150/32"}}
                        )
                    else:
                        self.assertTrue(peer["podSelector"]["matchLabels"])
                        if "namespaceSelector" in peer:
                            self.assertEqual(
                                peer["namespaceSelector"],
                                {
                                    "matchLabels": {
                                        "kubernetes.io/metadata.name": "kube-system"
                                    }
                                },
                            )


if __name__ == "__main__":
    unittest.main()

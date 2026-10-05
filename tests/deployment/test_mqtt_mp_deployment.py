"""Exercise the deployed readiness commands and the isolated MP overlay."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from mosquitto_exporter.config import PrometheusConfig
from opentelemetry.sdk.resources import OTELResourceDetector
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "deploy" / "mqtt-k3s"


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
                self.assertEqual(container["resources"]["limits"]["cpu"], "500m")
                self.assertEqual(
                    container["resources"]["requests"]["cpu"],
                    "50m" if name == "mqtt-otel-collector" else "250m",
                )
                if name == "mqtt-otel-collector":
                    continue
                self.assertNotIn("exec", container["readinessProbe"])
                port = 9464 if name == "mqtt-interceptor" else 9494
                self.assertEqual(
                    container["readinessProbe"]["httpGet"],
                    {"path": "/ready", "port": port},
                )
                for probe in ("startupProbe", "livenessProbe"):
                    self.assertEqual(
                        container[probe]["httpGet"], {"path": "/metrics", "port": port}
                    )
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

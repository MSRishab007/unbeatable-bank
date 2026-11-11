"""Generate the Kubernetes manifests for the bank's services into deploy/bank/.

The rendered YAML is committed: ArgoCD deploys exactly what is in Git, and a
pull request shows the real change. To change a deployment, edit SERVICES
below (replicas, autoscaling, env ...) and run:

    pip install pyyaml
    python scripts/gen_manifests.py

CI fails if the committed manifests don't match this script.

What is NOT in Git (created by Terraform, infra/platform, because the values
are project-specific or secret):
    Secret bank-secrets        database URLs, JWT secret, staff logins
    ConfigMap bank-infra       GCP project, bucket names and Firestore database
    ServiceAccounts identity, fraud, deposits   (bound to a Google service account)
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deploy" / "bank"
NAMESPACE = "bank"
DYNA_REPLICAS = 3
DYNA_PEERS = ",".join(f"dynastore-{i}=dynastore-{i}.dynastore-headless:7000" for i in range(DYNA_REPLICAS))
DYNA_NODES = ",".join(f"dynastore-{i}.dynastore-headless:7000" for i in range(DYNA_REPLICAS))

# Wiring shared by every service (ConfigMap bank-env). Nothing project-specific.
COMMON_ENV = {
    "DOCSTORE": "firestore",
    "PUBSUB": "gcp",
    "OBJSTORE": "gcs",
    "KAFKA_BOOTSTRAP": "bank-kafka-kafka-bootstrap.kafka.svc:9092",
    "DYNASTORE_NODES": DYNA_NODES,
    "DYNASTORE_URL": "http://dynastore:8080",
    "LEDGER_URL": "http://ledger:8080",
    "ACCOUNT_URL": "http://account:8080",
    "INTAKE_URL": "http://intake:8080",
    "RATELIMIT_URL": "http://ratelimit:8080",
    "RISK_URL": "http://risk:8080",
    "IDENTITY_URL": "http://identity:8080",
    "FRAUD_URL": "http://fraud:8080",
    "DEPOSITS_URL": "http://deposits:8080",
    # gRPC goes to headless Services so clients can balance across pods (see ubcommon/grpcutil.py).
    "LEDGER_GRPC": "ledger-headless:50051",
    "RATELIMIT_GRPC": "ratelimit-headless:50052",
    "RISK_GRPC": "risk-headless:50053",
    "DB_POOL_MAX": "4",
    "LOG_LEVEL": "INFO",
}

# name -> settings. Keys:
#   grpc      gRPC port (adds a headless Service)
#   db        needs DATABASE_URL (from Secret bank-secrets, key DATABASE_URL_<NAME>) and a migration Job
#   outbox    runs the outbox relay as a sidecar (publishes committed events to Kafka)
#   secrets   extra keys from bank-secrets
#   hpa       (min, max) replicas, scaling on 60% CPU
#   config    folder under services/<name>/ mounted read-only at /srv/config
#   gcp       uses a Kubernetes ServiceAccount bound to a Google service account (created by Terraform)
#   infra     env name -> key in ConfigMap bank-infra (overrides the value envFrom would give)
#   public    exposed to the internet through a LoadBalancer
SERVICES: dict[str, dict] = {
    "gateway": {"secrets": ["JWT_SECRET"], "hpa": (1, 4), "public": True, "env": {"ALLOW_SIM_HEADERS": "true"}},
    "identity": {"db": True, "outbox": True, "secrets": ["JWT_SECRET", "STAFF_BOOTSTRAP"], "gcp": True,
                 "infra": {"GCS_BUCKET": "KYC_BUCKET"}},       # its bucket is the KYC one, not the reports one
    "account": {"db": True, "outbox": True},
    "ledger": {"db": True, "outbox": True, "grpc": 50051, "env": {"GRPC_PORT": "50051"}},
    "intake": {"db": True, "outbox": True, "hpa": (1, 6)},
    "ratelimit": {"grpc": 50052, "config": "config", "env": {"GRPC_PORT": "50052"}},
    "risk": {"grpc": 50053, "config": "config", "hpa": (1, 5), "env": {"GRPC_PORT": "50053"}},
    "fraud": {"db": True, "gcp": True},
    "deposits": {"db": True, "gcp": True},
}

LABELS = {"app.kubernetes.io/part-of": "unbeatable-bank"}
POD_SECURITY = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"}}
CONTAINER_SECURITY = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
SCRAPE = {"prometheus.io/scrape": "true", "prometheus.io/port": "8080", "prometheus.io/path": "/metrics"}


def labels(name: str) -> dict:
    return {"app": name, **LABELS}


def probes() -> dict:
    http = lambda path: {"httpGet": {"path": path, "port": "http"}}  # noqa: E731
    return {
        # /healthz never touches a dependency: a database outage must not restart every pod.
        "livenessProbe": {**http("/healthz"), "periodSeconds": 10, "failureThreshold": 3},
        # /readyz checks dependencies: an unready pod just stops receiving traffic.
        "readinessProbe": {**http("/readyz"), "periodSeconds": 5, "failureThreshold": 3},
        "startupProbe": {**http("/healthz"), "periodSeconds": 3, "failureThreshold": 40},
    }


def secret_env(name: str, key: str) -> dict:
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": "bank-secrets", "key": key}}}


def env_for(name: str, spec: dict) -> list[dict]:
    env = [{"name": k, "value": v} for k, v in spec.get("env", {}).items()]
    if spec.get("db"):
        env.append(secret_env("DATABASE_URL", f"DATABASE_URL_{name.upper()}"))
    env += [secret_env(key, key) for key in spec.get("secrets", [])]
    env += [{"name": k, "valueFrom": {"configMapKeyRef": {"name": "bank-infra", "key": key}}}
            for k, key in spec.get("infra", {}).items()]
    return env


ENV_FROM = [{"configMapRef": {"name": "bank-env"}}, {"configMapRef": {"name": "bank-infra"}}]
RESOURCES = {"requests": {"cpu": "100m", "memory": "160Mi"}, "limits": {"memory": "512Mi"}}
SIDECAR_RESOURCES = {"requests": {"cpu": "20m", "memory": "64Mi"}, "limits": {"memory": "192Mi"}}


def deployment(name: str, spec: dict) -> dict:
    ports = [{"name": "http", "containerPort": 8080}]
    if spec.get("grpc"):
        ports.append({"name": "grpc", "containerPort": spec["grpc"]})
    container = {
        "name": name, "image": f"unbeatable/{name}", "ports": ports, "envFrom": ENV_FROM,
        "env": env_for(name, spec), "resources": RESOURCES, "securityContext": CONTAINER_SECURITY, **probes(),
    }
    volumes = []
    if spec.get("config"):
        container["volumeMounts"] = [{"name": "config", "mountPath": "/srv/config", "readOnly": True}]
        volumes.append({"name": "config", "configMap": {"name": f"{name}-config"}})
    containers = [container]
    if spec.get("outbox"):
        containers.append({
            "name": "outbox-relay", "image": f"unbeatable/{name}",
            "command": ["python", "-m", "ubcommon.outbox", "--schema", name],
            "envFrom": ENV_FROM,
            "env": [secret_env("DATABASE_URL", f"DATABASE_URL_{name.upper()}"),
                    {"name": "OUTBOX_METRICS_PORT", "value": "9102"}],
            "ports": [{"name": "outbox-metrics", "containerPort": 9102}],
            "resources": SIDECAR_RESOURCES, "securityContext": CONTAINER_SECURITY,
        })
    pod = {"securityContext": POD_SECURITY, "terminationGracePeriodSeconds": 20, "containers": containers}
    if volumes:
        pod["volumes"] = volumes
    if spec.get("gcp"):
        pod["serviceAccountName"] = name
    body = {
        "selector": {"matchLabels": {"app": name}},
        "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1}},
        "template": {"metadata": {"labels": labels(name), "annotations": SCRAPE}, "spec": pod},
    }
    if not spec.get("hpa"):  # with an HPA, the autoscaler owns the replica count
        body = {"replicas": 1, **body}
    return {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "labels": labels(name)}, "spec": body}


def services(name: str, spec: dict) -> list[dict]:
    out = [{
        "apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "labels": labels(name)},
        "spec": {"selector": {"app": name}, "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]},
    }]
    if spec.get("grpc"):
        out.append({
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": f"{name}-headless", "labels": labels(name)},
            "spec": {"clusterIP": "None", "selector": {"app": name},
                     "ports": [{"name": "grpc", "port": spec["grpc"], "targetPort": "grpc"}]},
        })
    if spec.get("public"):
        out.append({
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": f"{name}-public", "labels": labels(name)},
            "spec": {"type": "LoadBalancer", "selector": {"app": name},
                     "ports": [{"name": "http", "port": 80, "targetPort": "http"}]},
        })
    return out


def hpa(name: str, low: int, high: int) -> dict:
    return {
        "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
        "metadata": {"name": name, "labels": labels(name)},
        "spec": {
            "scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": name},
            "minReplicas": low, "maxReplicas": high,
            "metrics": [{"type": "Resource", "resource": {
                "name": "cpu", "target": {"type": "Utilization", "averageUtilization": 60}}}],
            "behavior": {
                "scaleUp": {"stabilizationWindowSeconds": 0,
                            "policies": [{"type": "Pods", "value": 2, "periodSeconds": 30}]},
                "scaleDown": {"stabilizationWindowSeconds": 120},
            },
        },
    }


def migration_job(name: str) -> dict:
    """Runs `alembic upgrade head` before ArgoCD rolls out new pods (PreSync hook)."""
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": f"{name}-migrate", "labels": labels(name), "annotations": {
            "argocd.argoproj.io/hook": "PreSync",
            "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        }},
        "spec": {"backoffLimit": 4, "ttlSecondsAfterFinished": 3600, "template": {
            "metadata": {"labels": {"app": f"{name}-migrate", **LABELS}},
            "spec": {"restartPolicy": "Never", "securityContext": POD_SECURITY, "containers": [{
                "name": "migrate", "image": f"unbeatable/{name}", "command": ["alembic", "upgrade", "head"],
                "env": [secret_env("DATABASE_URL", f"DATABASE_URL_{name.upper()}")],
                "resources": SIDECAR_RESOURCES, "securityContext": CONTAINER_SECURITY,
            }]},
        }},
    }


def dynastore() -> list[dict]:
    name = "dynastore"
    container = {
        "name": name, "image": f"unbeatable/{name}",
        "ports": [{"name": "http", "containerPort": 8080}, {"name": "grpc", "containerPort": 7000}],
        "env": [
            {"name": "NODE_ID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}},
            {"name": "PEERS", "value": DYNA_PEERS},
            {"name": "GRPC_PORT", "value": "7000"},
            {"name": "DATA_DIR", "value": "/data"},
        ],
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        "resources": {"requests": {"cpu": "50m", "memory": "128Mi"}, "limits": {"memory": "384Mi"}},
        "securityContext": CONTAINER_SECURITY,
        "livenessProbe": {"httpGet": {"path": "/healthz", "port": "http"}, "periodSeconds": 10},
        # Deliberately no readiness gate on peers: a node must keep serving while others are down.
        "startupProbe": {"httpGet": {"path": "/healthz", "port": "http"}, "periodSeconds": 3, "failureThreshold": 40},
    }
    stateful = {
        "apiVersion": "apps/v1", "kind": "StatefulSet", "metadata": {"name": name, "labels": labels(name)},
        "spec": {
            "serviceName": "dynastore-headless", "replicas": DYNA_REPLICAS,
            "podManagementPolicy": "Parallel",  # leaderless: no node waits for another to start
            "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": labels(name), "annotations": SCRAPE},
                         "spec": {"securityContext": POD_SECURITY, "terminationGracePeriodSeconds": 15,
                                  "containers": [container]}},
            "volumeClaimTemplates": [{"metadata": {"name": "data"}, "spec": {
                "accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}}],
        },
    }
    headless = {
        "apiVersion": "v1", "kind": "Service", "metadata": {"name": "dynastore-headless", "labels": labels(name)},
        "spec": {"clusterIP": "None", "publishNotReadyAddresses": True, "selector": {"app": name},
                 "ports": [{"name": "grpc", "port": 7000, "targetPort": "grpc"}]},
    }
    http = {
        "apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "labels": labels(name)},
        "spec": {"selector": {"app": name}, "ports": [{"name": "http", "port": 8080, "targetPort": "http"}]},
    }
    return [stateful, headless, http]


def eod_cronjob() -> dict:
    """End-of-day at 00:05 India time: interest accrual, RD installments, FD maturities."""
    script = ("import urllib.request; r = urllib.request.urlopen(urllib.request.Request("
              "'http://deposits:8080/internal/v1/eod/run', method='POST'), timeout=600); print(r.read().decode())")
    return {
        "apiVersion": "batch/v1", "kind": "CronJob", "metadata": {"name": "end-of-day", "labels": labels("deposits")},
        "spec": {"schedule": "5 0 * * *", "timeZone": "Asia/Kolkata", "concurrencyPolicy": "Forbid",
                 "successfulJobsHistoryLimit": 3, "failedJobsHistoryLimit": 3,
                 "jobTemplate": {"spec": {"backoffLimit": 2, "template": {
                     "metadata": {"labels": {"app": "end-of-day", **LABELS}},
                     "spec": {"restartPolicy": "Never", "securityContext": POD_SECURITY, "containers": [{
                         "name": "eod", "image": "unbeatable/deposits", "command": ["python", "-c", script],
                         "resources": SIDECAR_RESOURCES, "securityContext": CONTAINER_SECURITY}]}}}}},
    }


def network_policies() -> list[dict]:
    """Internal services trust the X-Actor header set by the gateway, so nothing
    outside the namespace may reach them directly. Only the gateway is open."""
    return [
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": "internal-only"},
         "spec": {"podSelector": {}, "policyTypes": ["Ingress"], "ingress": [
             {"from": [{"podSelector": {}}]},  # same namespace
             {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}],
              "ports": [{"port": 8080, "protocol": "TCP"}, {"port": 9102, "protocol": "TCP"}]},
         ]}},
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
         "metadata": {"name": "gateway-public"},
         "spec": {"podSelector": {"matchLabels": {"app": "gateway"}}, "policyTypes": ["Ingress"],
                  "ingress": [{"ports": [{"port": 8080, "protocol": "TCP"}]}]}},
    ]


def dump(path: Path, docs: list[dict]) -> None:
    header = "# Generated by scripts/gen_manifests.py. Do not edit by hand; edit the script and regenerate.\n"
    path.write_text(header + "---\n".join(yaml.safe_dump(d, sort_keys=False, width=120) for d in docs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", help="image registry prefix, e.g. us-central1-docker.pkg.dev/<project>/unbeatable")
    parser.add_argument("--tag", help="image tag to deploy (the release workflow passes the commit SHA)")
    args = parser.parse_args()

    existing = {}
    if (OUT / "kustomization.yaml").exists():  # keep the registry/tag of the last release
        existing = {i["name"]: i
                    for i in (yaml.safe_load((OUT / "kustomization.yaml").read_text()) or {}).get("images", [])}
    for old in OUT.glob("*.yaml"):
        old.unlink()
    shutil.rmtree(OUT / "config", ignore_errors=True)
    (OUT / "config").mkdir(parents=True)

    resources, generators = [], []
    dump(OUT / "common.yaml", [
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "bank-env", "labels": LABELS},
         "data": COMMON_ENV},
        *network_policies(),
    ])
    resources.append("common.yaml")
    dump(OUT / "dynastore.yaml", dynastore())
    resources.append("dynastore.yaml")

    for name, spec in SERVICES.items():
        docs = [deployment(name, spec), *services(name, spec)]
        if spec.get("hpa"):
            docs.append(hpa(name, *spec["hpa"]))
        if spec.get("db"):
            docs.append(migration_job(name))
        if name == "deposits":
            docs.append(eod_cronjob())
        dump(OUT / f"{name}.yaml", docs)
        resources.append(f"{name}.yaml")
        if spec.get("config"):
            files = sorted((ROOT / "services" / name / spec["config"]).glob("*.yaml"))
            for f in files:
                shutil.copy(f, OUT / "config" / f"{name}-{f.name}")
            generators.append({"name": f"{name}-config",
                               "files": [f"{f.name}=config/{name}-{f.name}" for f in files]})

    images = []
    for n in ["dynastore", *SERVICES]:
        image = existing.get(f"unbeatable/{n}", {"name": f"unbeatable/{n}", "newName": f"REGISTRY/{n}", "newTag": "latest"})
        if args.registry:
            image["newName"] = f"{args.registry.rstrip('/')}/{n}"
        if args.tag:
            image["newTag"] = args.tag
        images.append(image)
    (OUT / "kustomization.yaml").write_text(
        "# Generated by scripts/gen_manifests.py, except `images`, which the release workflow\n"
        "# rewrites on every merge to main (that commit is what ArgoCD deploys).\n"
        + yaml.safe_dump({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "namespace": NAMESPACE, "resources": resources,
            # A changed policy or rule file gets a new ConfigMap name, so pods roll automatically.
            "configMapGenerator": generators,
            "images": images,
        }, sort_keys=False, width=120))
    print(f"wrote {len(resources)} manifest files to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

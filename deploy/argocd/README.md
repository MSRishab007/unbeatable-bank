# GitOps

ArgoCD watches this repository. Terraform (`infra/platform`) installs ArgoCD and
one root Application pointing at `deploy/argocd/apps`; everything else is
created by ArgoCD from Git. Nobody runs `kubectl apply`.

| Application | Source | What it deploys |
|---|---|---|
| `bank` | `deploy/bank` (Kustomize) | All microservices, DynaStore, HPAs, migration Jobs, the EOD CronJob |
| `kafka-operator` | Strimzi Helm chart | The operator that runs Kafka |
| `kafka` | `deploy/kafka` | Kafka cluster and topics |
| `monitoring` | kube-prometheus-stack Helm chart | Prometheus, Grafana, kube-state-metrics, node exporter |
| `dashboards` | `deploy/monitoring` | The Grafana dashboard |

A release is a merged pull request: the release workflow builds the images and
opens a pull request `deploy: <sha>` that writes the new tag into
`deploy/bank/kustomization.yaml`. When someone merges it, ArgoCD notices the change
on `main` and rolls the pods. To roll back, revert that merge.

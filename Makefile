# Day-to-day commands for the GCP deployment. Run from the repo root
# (Codespaces or any Linux/macOS shell with gcloud, terraform and kubectl).
#
#   make up        start everything          (~10 min, ~$0.20/hour while up)
#   make down      stop everything billable  (idle cost: a few cents a day)
#   make help      list all commands
#
# First-time setup is described in docs/DEPLOY.md.

PROJECT      ?= $(shell sed -n 's/^project_id *= *"\(.*\)"/\1/p' infra/main/terraform.tfvars 2>/dev/null)
STATE_BUCKET ?= $(PROJECT)-tfstate
ZONE         ?= us-central1-a
NODES        ?= 2
TF_MAIN      = terraform -chdir=infra/main
TF_PLATFORM  = terraform -chdir=infra/platform
GATEWAY_IP   = $(shell kubectl -n bank get service gateway-public -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)

.PHONY: help init up down flink-up flink-down kubeconfig url status argocd grafana smoke loadtest simulate logs destroy check-project

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-12s %s\n", $$1, $$2}'

check-project:
	@test -n "$(PROJECT)" || { echo "Set project_id in infra/main/terraform.tfvars first (see docs/DEPLOY.md)"; exit 1; }

init: check-project ## initialise Terraform (once per machine)
	$(TF_MAIN) init -input=false -backend-config="bucket=$(STATE_BUCKET)"
	$(TF_PLATFORM) init -input=false -backend-config="bucket=$(STATE_BUCKET)"

up: init ## start the cluster, database and all services
	$(TF_MAIN) apply -auto-approve -var node_count=$(NODES) -var sql_running=true
	$(MAKE) kubeconfig
	$(TF_PLATFORM) apply -auto-approve -var state_bucket=$(STATE_BUCKET)
	@echo "ArgoCD is now deploying from Git. Watch with: make status    Public URL: make url"

down: check-project ## stop everything that costs money (data is kept)
	@# The load balancer bills even with zero nodes, so remove it first. ArgoCD recreates it on `make up`.
	-kubectl -n bank delete service gateway-public --ignore-not-found --timeout=120s
	$(TF_MAIN) apply -auto-approve -var node_count=0 -var sql_running=false -var flink_enabled=false

flink-up: check-project ## create the Dataproc cluster and start the Flink job (~$0.20/hour extra)
	$(TF_MAIN) apply -auto-approve -var node_count=$(NODES) -var sql_running=true -var flink_enabled=true

flink-down: check-project ## delete the Dataproc cluster (the job resumes from its checkpoint next time)
	$(TF_MAIN) apply -auto-approve -var node_count=$(NODES) -var sql_running=true -var flink_enabled=false

kubeconfig: check-project ## point kubectl at the cluster
	gcloud container clusters get-credentials ub-cluster --zone $(ZONE) --project $(PROJECT)

url: ## print the public URL of the bank
	@test -n "$(GATEWAY_IP)" && echo "http://$(GATEWAY_IP)" || echo "No public IP yet. Check: kubectl -n bank get service gateway-public"

status: ## what ArgoCD has deployed, pods and autoscalers
	@kubectl -n argocd get applications 2>/dev/null || true
	@kubectl -n bank get pods,hpa

argocd: ## open the ArgoCD UI on http://localhost:8081 (user: admin)
	@echo "password: $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	kubectl -n argocd port-forward service/argocd-server 8081:80

grafana: ## open Grafana on http://localhost:3000 (admin / unbeatable)
	kubectl -n monitoring port-forward service/monitoring-grafana 3000:80

smoke: ## end-to-end check against the deployed system (set ADMIN_PASSWORD / ANALYST_PASSWORD)
	python scripts/smoke_test.py http://$(GATEWAY_IP) --skip-eod

loadtest: ## sustained load to make the autoscalers scale out (needs k6)
	k6 run -e BASE_URL=http://$(GATEWAY_IP) loadtest/payments.js

simulate: ## run the fraud personas against the deployed system
	python loadtest/simulator.py http://$(GATEWAY_IP)

logs: ## tail the logs of one service: make logs S=intake
	kubectl -n bank logs -f deployment/$(S) --all-containers --max-log-requests 10

destroy: check-project ## delete all cloud resources of infra/platform and infra/main (asks first)
	@read -p "Delete EVERYTHING in $(PROJECT) except the project itself? [y/N] " a; [ "$$a" = "y" ]
	-$(TF_PLATFORM) destroy -auto-approve -var state_bucket=$(STATE_BUCKET)
	$(TF_MAIN) destroy -auto-approve

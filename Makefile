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
# The one public load balancer is Traefik's Service (ArgoCD app "traefik", see deploy/argocd/apps).
GATEWAY_IP   = $(shell kubectl -n traefik get service traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
# The bank's public name (DuckDNS, kept pointing at the load balancer by the dns-updater
# CronJob once duckdns_token is in infra/main/terraform.tfvars). Without a token: the bare IP.
BANK_HOST    ?= unbeatable-bank.duckdns.org
HAS_DNS      = $(shell grep -q '^ *duckdns_token *= *"..*"' infra/main/terraform.tfvars 2>/dev/null && echo yes)
BASE_URL     ?= $(if $(HAS_DNS),https://$(BANK_HOST),http://$(GATEWAY_IP))

# Staff passwords for the test commands, read from staff_bootstrap in infra/main/terraform.tfvars
# (id:ROLE:password,...), so nobody has to type or paste them. Set a variable to override.
STAFF_LINE   = $(shell sed -n 's/^staff_bootstrap *= *"\(.*\)"/\1/p' infra/main/terraform.tfvars 2>/dev/null)
staff_pw     = $(shell echo '$(STAFF_LINE)' | tr ',' '\n' | sed -n 's/^$(1):[A-Z]*://p')
export ADMIN_PASSWORD    ?= $(call staff_pw,admin)
export ANALYST_PASSWORD  ?= $(call staff_pw,analyst1)
export ANALYST2_PASSWORD ?= $(call staff_pw,analyst2)
export TELLER_PASSWORD   ?= $(call staff_pw,teller1)
export KYC_PASSWORD      ?= $(call staff_pw,kyc1)

.PHONY: help init up down flink-up flink-down kubeconfig url dns status argocd grafana smoke ui-test loadtest simulate logs destroy check-project

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
	@# `make down` pauses ArgoCD (see there); start it again.
	-kubectl -n argocd scale statefulset argocd-application-controller --replicas=1
	@echo "ArgoCD is now deploying from Git. Watch with: make status    Public URL: make url"
	@echo "The DuckDNS name follows the new IP within ~2 minutes of the load balancer getting one."

down: check-project ## stop everything that costs money (data is kept)
	@# The load balancer bills even with zero nodes, so remove it first. ArgoCD would put it straight
	@# back (self-heal), so pause ArgoCD first; `make up` starts it again and it recreates the Service.
	@# The HTTPS certificate is a Secret in the cluster, so it is kept and not issued again.
	-kubectl -n argocd scale statefulset argocd-application-controller --replicas=0
	-kubectl -n traefik delete service traefik --ignore-not-found --timeout=180s
	-kubectl -n bank delete service gateway-public edge --ignore-not-found --timeout=120s
	$(TF_MAIN) apply -auto-approve -var node_count=0 -var sql_running=false -var flink_enabled=false

flink-up: check-project ## create the Dataproc cluster and start the Flink job (~$0.20/hour extra)
	$(TF_MAIN) apply -auto-approve -var node_count=$(NODES) -var sql_running=true -var flink_enabled=true

flink-down: check-project ## delete the Dataproc cluster (the job resumes from its checkpoint next time)
	$(TF_MAIN) apply -auto-approve -var node_count=$(NODES) -var sql_running=true -var flink_enabled=false

kubeconfig: check-project ## point kubectl at the cluster
	gcloud container clusters get-credentials ub-cluster --zone $(ZONE) --project $(PROJECT)

url: ## print the public URL of the bank, and whether its DNS name has caught up
	@test -n "$(GATEWAY_IP)" || { echo "No public IP yet. Check: kubectl -n traefik get service traefik"; exit 0; }; \
	echo "by IP (plain HTTP):  http://$(GATEWAY_IP)"; \
	for h in $(BANK_HOST) ubank.duckdns.org; do \
	  ip=$$(getent hosts $$h 2>/dev/null | awk '{print $$1}'); \
	  if [ "$$ip" = "$(GATEWAY_IP)" ]; then echo "by name (HTTPS):     https://$$h"; \
	  else echo "by name (HTTPS):     https://$$h   (still points at $${ip:-nothing}; try 'make dns' or wait 2 minutes)"; fi; \
	done

dns: ## point the DuckDNS names at the load balancer now, instead of waiting for the next 2-minute run
	@job=dns-now-$$(date +%s); kubectl -n bank create job $$job --from=cronjob/dns-updater && \
	kubectl -n bank wait --for=condition=complete job/$$job --timeout=60s; kubectl -n bank logs job/$$job

status: ## what ArgoCD has deployed, pods and autoscalers, the HTTPS certificate
	@kubectl -n argocd get applications 2>/dev/null || true
	@kubectl -n bank get pods,hpa
	@kubectl -n traefik get pods,hpa,service 2>/dev/null || true
	@kubectl -n bank get certificate bank-tls 2>/dev/null || true

argocd: ## open the ArgoCD UI on http://localhost:8081 (user: admin)
	@echo "password: $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	kubectl -n argocd port-forward service/argocd-server 8081:80

grafana: ## open Grafana on http://localhost:3000 (admin / unbeatable)
	kubectl -n monitoring port-forward service/monitoring-grafana 3000:80

smoke: ## end-to-end check against the deployed system (staff passwords come from infra/main/terraform.tfvars)
	python scripts/smoke_test.py $(BASE_URL) --skip-eod

ui-test: ## browser test of the web app against the deployed system (needs playwright)
	python scripts/ui_test.py --base-url $(BASE_URL)

loadtest: ## sustained load to make the autoscalers scale out (needs k6)
	k6 run -e BASE_URL=$(BASE_URL) loadtest/payments.js

simulate: ## run the fraud personas against the deployed system
	python loadtest/simulator.py $(BASE_URL)

logs: ## tail the logs of one service: make logs S=intake
	kubectl -n bank logs -f deployment/$(S) --all-containers --max-log-requests 10

destroy: check-project ## delete all cloud resources of infra/platform and infra/main (asks first)
	@read -p "Delete EVERYTHING in $(PROJECT) except the project itself? [y/N] " a; [ "$$a" = "y" ]
	-$(TF_PLATFORM) destroy -auto-approve -var state_bucket=$(STATE_BUCKET)
	$(TF_MAIN) destroy -auto-approve

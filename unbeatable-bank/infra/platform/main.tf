# In-cluster bootstrap. This is the ONLY thing Terraform puts inside Kubernetes:
#
#   1. what must not be in Git: secrets and project-specific settings
#   2. ArgoCD and its root Application
#
# From there ArgoCD deploys everything from the Git repository (GitOps).
# Apply this layer after infra/main, with at least one node running.

resource "kubernetes_namespace" "bank" {
  metadata {
    name = local.main.app_namespace
  }
}

# Database URLs, JWT signing secret, staff logins: copied from Terraform state
# (source of truth: Secret Manager, see infra/main/secrets.tf).
resource "kubernetes_secret" "bank" {
  metadata {
    name      = "bank-secrets"
    namespace = kubernetes_namespace.bank.metadata[0].name
  }
  data = local.main.app_secrets
}

# Project-specific, non-secret settings, so the manifests in Git stay generic.
resource "kubernetes_config_map" "infra" {
  metadata {
    name      = "bank-infra"
    namespace = kubernetes_namespace.bank.metadata[0].name
  }
  data = {
    GCP_PROJECT        = local.main.project_id
    GCS_BUCKET         = local.main.reports_bucket
    FIRESTORE_DATABASE = local.main.firestore_database
  }
}

# Workload Identity: these Kubernetes service accounts act as the Google
# service account ub-apps (Firestore, Pub/Sub, Cloud Storage). No key files.
resource "kubernetes_service_account" "workload_identity" {
  for_each = toset(local.main.workload_identity_users)
  metadata {
    name      = each.value
    namespace = kubernetes_namespace.bank.metadata[0].name
    annotations = {
      "iam.gke.io/gcp-service-account" = local.main.apps_service_account
    }
  }
}

# ---------------------------------------------------------------- GitOps controller

resource "helm_release" "argocd" {
  name             = "argocd"
  namespace        = "argocd"
  create_namespace = true
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "~7.7.0"
  timeout          = 600

  values = [yamlencode({
    dex           = { enabled = false }
    notifications = { enabled = false }
    configs = {
      params = { "server.insecure" = true } # UI is reached through `make argocd` (port-forward)
    }
    controller = { resources = { requests = { cpu = "100m", memory = "384Mi" } } }
    repoServer = { resources = { requests = { cpu = "50m", memory = "192Mi" } } }
    server     = { resources = { requests = { cpu = "50m", memory = "128Mi" } } }
  })]

  depends_on = [kubernetes_secret.bank, kubernetes_config_map.infra, kubernetes_service_account.workload_identity]
}

# The root "app of apps": ArgoCD reads deploy/argocd/apps and creates one
# Application per file found there.
resource "helm_release" "root_app" {
  name       = "root"
  namespace  = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argocd-apps"
  version    = "~2.0.0"

  values = [yamlencode({
    applications = {
      root = {
        namespace  = "argocd"
        project    = "default"
        finalizers = ["resources-finalizer.argocd.argoproj.io"]
        source = {
          repoURL        = var.git_repo_url
          targetRevision = var.git_revision
          path           = "deploy/argocd/apps"
        }
        destination = {
          server    = "https://kubernetes.default.svc"
          namespace = "argocd"
        }
        syncPolicy = {
          automated = { prune = true, selfHeal = true }
        }
      }
    }
  })]

  depends_on = [helm_release.argocd]
}

terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.33"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.16"
    }
  }

  # terraform init -backend-config="bucket=<project>-tfstate"
  backend "gcs" {
    prefix = "platform"
  }
}

# Everything this layer needs to know about the cloud resources comes from
# the outputs of infra/main.
data "terraform_remote_state" "main" {
  backend = "gcs"
  config = {
    bucket = var.state_bucket
    prefix = "main"
  }
}

locals {
  main = data.terraform_remote_state.main.outputs
}

provider "google" {
  project = local.main.project_id
  region  = local.main.region
}

data "google_client_config" "current" {}

provider "kubernetes" {
  host                   = "https://${local.main.cluster_endpoint}"
  token                  = data.google_client_config.current.access_token
  cluster_ca_certificate = base64decode(local.main.cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = "https://${local.main.cluster_endpoint}"
    token                  = data.google_client_config.current.access_token
    cluster_ca_certificate = base64decode(local.main.cluster_ca_certificate)
  }
}

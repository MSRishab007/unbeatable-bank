terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Step 2 (after the first apply): uncomment, then run
  #   terraform init -migrate-state
  # so the whole team shares this state from the GCS bucket.
  #
  # backend "gcs" {
  #   bucket = "unbeatable-bank-<group-no>-tfstate"
  #   prefix = "bootstrap"
  # }
}

provider "google" {
  region = var.region
}

# Budgets live on billing accounts, and the Budgets API needs a quota project.
# This alias bills those API calls to our own project.
provider "google" {
  alias                 = "billing"
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

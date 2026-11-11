variable "project_id" {
  description = "Globally unique GCP project ID, e.g. unbeatable-bank-12 (6-30 chars)."
  type        = string
}

variable "project_name" {
  description = "Display name of the project."
  type        = string
  default     = "Unbeatable Bank"
}

variable "org_id" {
  description = "Organization ID to create the project under. Leave null for a personal (no-org) project."
  type        = string
  default     = null
}

variable "region" {
  description = "Default region for everything."
  type        = string
  default     = "us-central1"
}

variable "billing_accounts" {
  description = <<-EOT
    Member key -> billing account ID (XXXXXX-XXXXXX-XXXXXX).
    Add a teammate's account only AFTER they have granted the team
    Billing Account User + Billing Account Costs Manager on it.
  EOT
  type        = map(string)
}

variable "active_billing" {
  description = "Key in billing_accounts that the project is currently billed to. Change this to switch accounts."
  type        = string
}

variable "team_members" {
  description = "Member key -> Google account email of the teammates (not the person running the bootstrap, who is already owner)."
  type        = map(string)
  default     = {}
}

variable "budget_amount" {
  description = "Credit per billing account, in budget_currency (e.g. 50 USD, or about 4300 INR)."
  type        = number
  default     = 50
}

variable "budget_alert_thresholds" {
  description = "Fractions of budget_amount that trigger an email. 0.3/0.6/0.8 of $50 = $15/$30/$40."
  type        = list(number)
  default     = [0.3, 0.6, 0.8]
}

variable "alert_emails" {
  description = "Emails that receive budget alerts (usually all four members)."
  type        = list(string)
}

variable "github_repository" {
  description = "owner/repo allowed to authenticate from GitHub Actions."
  type        = string
  default     = "MSRishab007/unbeatable-bank"
}

variable "labels" {
  description = "Labels applied to resources that support them."
  type        = map(string)
  default = {
    team = "unbeatable-bank"
  }
}

variable "budget_currency" {
  description = "Currency of the billing accounts (gcloud billing accounts describe ID --format='value(currencyCode)'). Google rejects a budget in any other currency."
  type        = string
  default     = "USD"
}

variable "budget_currencies" {
  description = "Member key -> currency, for billing accounts whose currency differs from budget_currency."
  type        = map(string)
  default     = {}
}

variable "budget_amounts" {
  description = "Member key -> credit in that account's currency, for accounts that differ from budget_amount."
  type        = map(number)
  default     = {}
}

variable "project_id" {
  description = "GCP project created by infra/bootstrap."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  description = "Single zone for the GKE cluster and Dataproc (a zonal cluster has no management fee)."
  type        = string
  default     = "us-central1-a"
}

# ---------------------------------------------------------------- cost switches (see Makefile)

variable "node_count" {
  description = "GKE nodes. 0 = everything in the cluster is off and costs nothing."
  type        = number
  default     = 2
}

variable "node_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "spot_nodes" {
  description = "Spot VMs are 60-70% cheaper; they can be reclaimed, which the system tolerates."
  type        = bool
  default     = true
}

variable "sql_running" {
  description = "false stops the Cloud SQL instance (only its disk is billed)."
  type        = bool
  default     = true
}

variable "sql_tier" {
  description = "db-g1-small allows enough connections for all services; db-f1-micro is cheaper but tight."
  type        = string
  default     = "db-g1-small"
}

variable "flink_enabled" {
  description = "Creates the Dataproc cluster and starts the Audit Analytics Flink job. Turn on only when needed."
  type        = bool
  default     = false
}

variable "flink_idle_delete_minutes" {
  description = "Dataproc deletes itself after this many idle minutes (safety net)."
  type        = number
  default     = 60
}

# ---------------------------------------------------------------- application settings

variable "firestore_location" {
  description = "Cannot be changed after the database is created."
  type        = string
  default     = "nam5"
}

variable "staff_bootstrap" {
  description = "Initial staff logins: 'id:ROLE:password,...'. Roles: ADMIN, ANALYST, TELLER, KYC."
  type        = string
  sensitive   = true
}

variable "email_from" {
  description = "Sender address: the Gmail account (SMTP), or a sender verified in Brevo. Empty = no emails."
  type        = string
  default     = ""
}

variable "smtp_password" {
  description = "App password of the email_from Gmail account (Google Account > Security > App passwords). Preferred over Brevo when set."
  type        = string
  default     = ""
  sensitive   = true
}

variable "brevo_api_key" {
  description = "Brevo API key: the other way to send email. Empty = not used."
  type        = string
  default     = ""
  sensitive   = true
}

variable "rbm_agent_id" {
  description = "Google RCS Business Messaging agent id. Empty = RCS messages go to a simulated outbox."
  type        = string
  default     = ""
}

variable "rbm_credentials" {
  description = "The RBM agent's service-account key (the JSON file's contents)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "duckdns_token" {
  description = "DuckDNS account token: the cluster keeps the bank's DuckDNS names pointing at its load balancer. Empty = names are not updated."
  type        = string
  default     = ""
  sensitive   = true
}

variable "kafka_bootstrap_nodeport" {
  description = "NodePort of the Kafka bootstrap listener (must match deploy/kafka/kafka.yaml)."
  type        = number
  default     = 32100
}

variable "labels" {
  type = map(string)
  default = {
    team = "unbeatable-bank"
  }
}

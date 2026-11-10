variable "state_bucket" {
  description = "Terraform state bucket created by infra/bootstrap (<project>-tfstate)."
  type        = string
}

variable "git_repo_url" {
  description = "Repository ArgoCD tracks. Must be readable without credentials (public)."
  type        = string
  default     = "https://github.com/MSRishab007/unbeatable-bank.git"
}

variable "git_revision" {
  type    = string
  default = "main"
}

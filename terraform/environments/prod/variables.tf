variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
  default     = "24832578-1a71-4345-9035-e3979ac8f9a3"
}

variable "tenant_id" {
  description = "Azure AD tenant ID"
  type        = string
  default     = "5581c9a8-168b-45f0-abd4-d375da99bf9f"
}

variable "location" {
  description = "Azure region for resources"
  type        = string
  default     = "swedencentral"
}

variable "frontend_location" {
  description = "Azure region for Static Web App (frontend)"
  type        = string
  default     = "westeurope"
}

variable "management_group_id" {
  description = "Management Group ID for RBAC assignments"
  type        = string
  default     = "5581c9a8-168b-45f0-abd4-d375da99bf9f"
}

variable "container_apps_subnet_cidr" {
  description = "CIDR range for Container Apps subnet"
  type        = string
  default     = "10.80.70.0/27"
}

variable "frontend_redirect_uris" {
  description = "Redirect URIs for frontend application"
  type        = list(string)
  default     = [
    "https://cloudex.flysas.tech/",
    "https://cloudex.flysas.tech/auth/callback"
  ]
}

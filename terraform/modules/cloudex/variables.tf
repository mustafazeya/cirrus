variable "environment" {
  description = "Environment name (test, prod)"
  type        = string
  
  validation {
    condition     = contains(["test", "prod"], var.environment)
    error_message = "Environment must be one of: test, prod."
  }
}

variable "location" {
  description = "Azure region for resources"
  type        = string
  default     = "westeurope"
}

variable "frontend_location" {
  description = "Azure region for Static Web App (frontend). Note: Static Web Apps are not available in Sweden Central."
  type        = string
  default     = "westeurope"
}

variable "tenant_id" {
  description = "Azure AD tenant ID"
  type        = string
}

variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "management_group_id" {
  description = "Management Group ID for RBAC assignments"
  type        = string
  default     = "5581c9a8-168b-45f0-abd4-d375da99bf9f"
}

variable "container_apps_subnet_cidr" {
  description = "CIDR range for Container Apps subnet"
  type        = string
}

variable "frontend_redirect_uris" {
  description = "Redirect URIs for frontend application"
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}

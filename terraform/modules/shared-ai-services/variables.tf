variable "resource_group_name" {
  description = "Name of the resource group for shared services"
  type        = string
}

variable "location" {
  description = "Azure region for shared resources"
  type        = string
  default     = "swedencentral"
}

variable "ai_services_name" {
  description = "Name of the Azure AI Services (OpenAI) account"
  type        = string
}

variable "ai_services_sku" {
  description = "SKU for Azure AI Services"
  type        = string
  default     = "S0"
}

variable "search_service_name" {
  description = "Name of the Azure AI Search service"
  type        = string
}

variable "search_service_sku" {
  description = "SKU for Azure AI Search"
  type        = string
  default     = "standard"
}

variable "search_replica_count" {
  description = "Number of search replicas"
  type        = number
  default     = 1
}

variable "search_partition_count" {
  description = "Number of search partitions"
  type        = number
  default     = 1
}

variable "log_analytics_name" {
  description = "Name of the Log Analytics workspace"
  type        = string
}

variable "log_retention_days" {
  description = "Log retention in days"
  type        = number
  default     = 30
}

variable "app_insights_name" {
  description = "Name of the Application Insights instance"
  type        = string
}

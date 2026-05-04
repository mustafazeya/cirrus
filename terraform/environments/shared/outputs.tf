# Shared AI Services Outputs
output "shared_resource_group_name" {
  description = "Name of the shared resource group"
  value       = module.shared_ai_services.resource_group_name
}

output "ai_services_endpoint" {
  description = "Endpoint for Azure AI Services (OpenAI)"
  value       = module.shared_ai_services.ai_services_endpoint
}

output "ai_services_primary_key" {
  description = "Primary key for Azure AI Services"
  value       = module.shared_ai_services.ai_services_primary_key
  sensitive   = true
}

output "search_service_endpoint" {
  description = "Endpoint for Azure AI Search"
  value       = module.shared_ai_services.search_service_endpoint
}

output "search_service_primary_key" {
  description = "Primary admin key for Azure AI Search"
  value       = module.shared_ai_services.search_service_primary_key
  sensitive   = true
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace ID"
  value       = module.shared_ai_services.log_analytics_workspace_resource_id
}

output "app_insights_connection_string" {
  description = "Application Insights connection string"
  value       = module.shared_ai_services.app_insights_connection_string
  sensitive   = true
}

output "app_insights_instrumentation_key" {
  description = "Application Insights instrumentation key"
  value       = module.shared_ai_services.app_insights_instrumentation_key
  sensitive   = true
}

output "resource_group_name" {
  description = "Name of the shared resource group"
  value       = azurerm_resource_group.shared.name
}

output "resource_group_id" {
  description = "ID of the shared resource group"
  value       = azurerm_resource_group.shared.id
}

output "ai_services_id" {
  description = "ID of the Azure AI Services account"
  value       = azurerm_ai_services.main.id
}

output "ai_services_endpoint" {
  description = "Endpoint URL for Azure AI Services"
  value       = azurerm_ai_services.main.endpoint
}

output "ai_services_primary_key" {
  description = "Primary access key for Azure AI Services"
  value       = azurerm_ai_services.main.primary_access_key
  sensitive   = true
}

output "search_service_id" {
  description = "ID of the Azure AI Search service"
  value       = azurerm_search_service.shared.id
}

output "search_service_endpoint" {
  description = "Endpoint URL for Azure AI Search"
  value       = "https://${azurerm_search_service.shared.name}.search.windows.net"
}

output "search_service_primary_key" {
  description = "Primary admin key for Azure AI Search"
  value       = azurerm_search_service.shared.primary_key
  sensitive   = true
}

output "search_service_query_keys" {
  description = "Query keys for Azure AI Search"
  value       = azurerm_search_service.shared.query_keys
  sensitive   = true
}

output "log_analytics_workspace_id" {
  description = "ID of the Log Analytics workspace"
  value       = azurerm_log_analytics_workspace.shared.id
}

output "log_analytics_workspace_resource_id" {
  description = "Resource ID of the Log Analytics workspace"
  value       = azurerm_log_analytics_workspace.shared.workspace_id
}

output "app_insights_id" {
  description = "ID of the Application Insights instance"
  value       = azurerm_application_insights.shared.id
}

output "app_insights_instrumentation_key" {
  description = "Instrumentation key for Application Insights"
  value       = azurerm_application_insights.shared.instrumentation_key
  sensitive   = true
}

output "app_insights_connection_string" {
  description = "Connection string for Application Insights"
  value       = azurerm_application_insights.shared.connection_string
  sensitive   = true
}

output "openai_deployment_name" {
  description = "Name of the GPT-5.1 chat deployment"
  value       = azurerm_cognitive_deployment.gpt51_chat.name
}

output "openai_api_version" {
  description = "API version for Azure OpenAI"
  value       = "2025-04-01-preview"
}

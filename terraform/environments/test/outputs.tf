output "resource_group_name" {
  description = "The name of the resource group"
  value       = module.cloudex.resource_group_name
}

output "backend_url" {
  description = "The URL of the backend container app"
  value       = module.cloudex.backend_url
}

output "container_registry_login_server" {
  description = "The URL for the container registry"
  value       = module.cloudex.container_registry_login_server
}

output "ai_services_endpoint" {
  description = "Azure AI Services endpoint"
  value       = module.cloudex.ai_services_endpoint
}

output "key_vault_uri" {
  description = "The URI of the Key Vault"
  value       = module.cloudex.key_vault_uri
}

output "frontend_app_client_id" {
  description = "Azure AD application client ID for frontend"
  value       = module.cloudex.frontend_app_client_id
}

output "backend_sp_client_id" {
  description = "Backend service principal client ID"
  value       = module.cloudex.backend_sp_client_id
}

output "container_app_name" {
  description = "The name of the backend container app"
  value       = module.cloudex.container_app_name
}

output "container_app_identity_client_id" {
  description = "Container App managed identity client ID"
  value       = module.cloudex.container_app_identity_client_id
}

output "static_web_app_name" {
  description = "Static Web App name"
  value       = module.cloudex.static_web_app_name
}

output "azure_search_endpoint" {
  description = "Azure Search service endpoint (shared)"
  value       = module.cloudex.azure_search_endpoint
}

output "subscription_id" {
  description = "Azure subscription ID"
  value       = module.cloudex.subscription_id
}

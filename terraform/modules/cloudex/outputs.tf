output "resource_group_name" {
  description = "The name of the resource group"
  value       = azurerm_resource_group.main.name
}

output "resource_group_id" {
  description = "The ID of the resource group"
  value       = azurerm_resource_group.main.id
}

output "subscription_id" {
  description = "The Azure subscription ID"
  value       = var.subscription_id
}

output "container_registry_login_server" {
  description = "The URL for the container registry"
  value       = azurerm_container_registry.main.login_server
}

output "container_registry_admin_username" {
  description = "Admin username for the container registry"
  value       = azurerm_container_registry.main.admin_username
}

output "container_registry_admin_password" {
  description = "Admin password for the container registry"
  value       = azurerm_container_registry.main.admin_password
  sensitive   = true
}

output "container_app_name" {
  description = "The name of the backend container app"
  value       = azurerm_container_app.backend.name
}

output "backend_url" {
  description = "The URL of the backend container app"
  value       = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
}

output "ai_services_endpoint" {
  description = "Azure AI Services endpoint (shared)"
  value       = data.azurerm_cognitive_account.shared_ai.endpoint
}

output "ai_services_key" {
  description = "Azure AI Services primary key (shared) - SENSITIVE"
  value       = data.azurerm_cognitive_account.shared_ai.primary_access_key
  sensitive   = true
}

output "openai_deployment_name" {
  description = "OpenAI deployment name"
  value       = "gpt-5.1-chat"
}

output "openai_api_version" {
  description = "OpenAI API version"
  value       = "2025-04-01-preview"
}

output "azure_search_endpoint" {
  description = "Azure Search service endpoint (shared)"
  value       = "https://${data.azurerm_search_service.shared.name}.search.windows.net"
}

output "azure_search_primary_key" {
  description = "Azure Search service primary key (shared) - SENSITIVE"
  value       = data.azurerm_search_service.shared.primary_key
  sensitive   = true
}

output "azure_search_index_name" {
  description = "Azure Search index name"
  value       = "microsoft-docs"
}

output "redis_hostname" {
  description = "Redis cache hostname"
  value       = azurerm_redis_cache.main.hostname
}

output "redis_ssl_port" {
  description = "Redis cache SSL port"
  value       = azurerm_redis_cache.main.ssl_port
}

output "redis_primary_key" {
  description = "Redis cache primary key - SENSITIVE"
  value       = azurerm_redis_cache.main.primary_access_key
  sensitive   = true
}

output "application_insights_connection_string" {
  description = "Application Insights connection string (shared)"
  value       = data.azurerm_application_insights.shared.connection_string
  sensitive   = true
}

output "backend_sp_client_id" {
  description = "Backend service principal client ID"
  value       = azuread_application.backend_service_principal.client_id
}

output "backend_sp_client_secret" {
  description = "Backend service principal client secret - SENSITIVE"
  value       = azuread_application_password.backend_service_principal.value
  sensitive   = true
}

output "key_vault_uri" {
  description = "The URI of the Key Vault"
  value       = azurerm_key_vault.main.vault_uri
}

output "key_vault_name" {
  description = "The name of the Key Vault"
  value       = azurerm_key_vault.main.name
}

output "frontend_app_client_id" {
  description = "Azure AD application client ID for frontend"
  value       = azuread_application.cloud_portal.client_id
}

output "tenant_id" {
  description = "Azure AD tenant ID"
  value       = var.tenant_id
}

output "static_web_app_url" {
  description = "Static Web App default URL"
  value       = "https://${azurerm_static_web_app.frontend.default_host_name}"
}

output "static_web_app_name" {
  description = "Static Web App name"
  value       = azurerm_static_web_app.frontend.name
}

output "static_web_app_api_key" {
  description = "Static Web App API key for deployment"
  value       = azurerm_static_web_app.frontend.api_key
  sensitive   = true
}

output "container_app_identity_principal_id" {
  description = "Container App managed identity principal ID"
  value       = azurerm_user_assigned_identity.container_app.principal_id
}

output "container_app_identity_client_id" {
  description = "Container App managed identity client ID"
  value       = azurerm_user_assigned_identity.container_app.client_id
}

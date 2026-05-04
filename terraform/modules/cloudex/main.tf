terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
      configuration_aliases = [azurerm.shared]
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }
}

# Resource Group
resource "azurerm_resource_group" "main" {
  name     = "cloudex-${var.environment}-rg"
  location = var.location
  tags     = var.tags
}

# Reference shared AI services resource group
data "azurerm_resource_group" "shared" {
  provider = azurerm.shared
  name = "cloudex-shared-rg"
}

# Reference shared Log Analytics Workspace
data "azurerm_log_analytics_workspace" "shared" {
  provider            = azurerm.shared
  name                = "cloudex-shared-logs"
  resource_group_name = data.azurerm_resource_group.shared.name
}

# Reference shared Application Insights
data "azurerm_application_insights" "shared" {
  provider            = azurerm.shared
  name                = "cloudex-shared-insights"
  resource_group_name = data.azurerm_resource_group.shared.name
}

# Container Registry
resource "azurerm_container_registry" "main" {
  name                = "cloudex${var.environment}acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = var.environment == "prod" ? "Standard" : "Basic"
  admin_enabled       = true
  tags                = var.tags
}

# Use existing VNet infrastructure
data "azurerm_resource_group" "vnet" {
  name = "platform-tools-${var.environment}-vnet-rg-sdc"
}

data "azurerm_virtual_network" "main" {
  name                = "platform-tools-${var.environment}-vnet-sdc"
  resource_group_name = data.azurerm_resource_group.vnet.name
}

data "azurerm_route_table" "main" {
  name                = "platform-tools-${var.environment}-rt-sdc"
  resource_group_name = data.azurerm_resource_group.vnet.name
}

# Subnet for Container Apps Environment
resource "azurerm_subnet" "container_apps" {
  name                 = "cloudex-${var.environment}-ca-subnet"
  resource_group_name  = data.azurerm_resource_group.vnet.name
  virtual_network_name = data.azurerm_virtual_network.main.name
  address_prefixes     = [var.container_apps_subnet_cidr]
  
  delegation {
    name = "container-app-delegation"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Associate route table with Container Apps subnet
resource "azurerm_subnet_route_table_association" "container_apps" {
  subnet_id      = azurerm_subnet.container_apps.id
  route_table_id = data.azurerm_route_table.main.id
}

# Key Vault with RBAC-only authentication (Public)
resource "azurerm_key_vault" "main" {
  name                       = "cloudex-${var.environment}-kv"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  
  # Enable RBAC authorization only
  rbac_authorization_enabled = true
  
  # Public access enabled (no VNet restrictions)
  public_network_access_enabled = true
  network_acls {
    default_action = "Allow"
    bypass         = "AzureServices"
  }
  
  tags = var.tags
}

# Get current user for Key Vault access
data "azurerm_client_config" "current" {}

# RBAC role assignments for Key Vault
resource "azurerm_role_assignment" "current_user_kv_admin" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Reference shared Azure AI Services (OpenAI)
data "azurerm_cognitive_account" "shared_ai" {
  provider            = azurerm.shared
  name                = "cloudex-shared-ai"
  resource_group_name = data.azurerm_resource_group.shared.name
}

# Redis Cache (environment-specific)
resource "azurerm_redis_cache" "main" {
  name                = "cloudex-${var.environment}-redis"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  capacity            = var.environment == "prod" ? 2 : 1
  family              = var.environment == "prod" ? "C" : "C"
  sku_name            = var.environment == "prod" ? "Standard" : "Basic"
  non_ssl_port_enabled = false
  minimum_tls_version = "1.2"
  
  redis_configuration {
    authentication_enabled = true
  }
  
  tags = var.tags
}

# Reference shared Azure AI Search Service
data "azurerm_search_service" "shared" {
  provider            = azurerm.shared
  name                = "cloudex-shared-search"
  resource_group_name = data.azurerm_resource_group.shared.name
}

# Static Web App for Frontend
resource "azurerm_static_web_app" "frontend" {
  name                = "cloudex-${var.environment}-swa"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.frontend_location
  sku_tier            = var.environment == "prod" ? "Standard" : "Free"
  sku_size            = var.environment == "prod" ? "Standard" : "Free"
  
  tags = var.tags
  
  lifecycle {
    ignore_changes = [
      repository_url,
      repository_branch
    ]
  }
}

# Container Apps Environment (Private with VNet integration)
resource "azurerm_container_app_environment" "main" {
  name                       = "cloudex-${var.environment}-cae"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = data.azurerm_log_analytics_workspace.shared.id
  infrastructure_subnet_id   = azurerm_subnet.container_apps.id
  internal_load_balancer_enabled = true
  tags                       = var.tags
  
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    maximum_count         = 0
    minimum_count         = 0
  }
  
  lifecycle {
    ignore_changes = [
      infrastructure_resource_group_name
    ]
  }
}

# User Assigned Managed Identity for Container App
resource "azurerm_user_assigned_identity" "container_app" {
  name                = "cloudex-${var.environment}-backend-id"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

# RBAC role assignments for Container App to access Key Vault
resource "azurerm_role_assignment" "container_app_kv_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.container_app.principal_id
}

# RBAC role assignment for Container App to pull images from ACR
resource "azurerm_role_assignment" "container_app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.container_app.principal_id
}

# Container App for Backend API (Bare - configured via GitHub Actions)
resource "azurerm_container_app" "backend" {
  name                         = "cloudex-${var.environment}-backend-ca"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.container_app.id]
  }
  
  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.container_app.id
  }
  
  template {
    min_replicas = var.environment == "prod" ? 2 : 1
    max_replicas = var.environment == "prod" ? 10 : 3
    
    container {
      name   = "backend"
      image  = "${azurerm_container_registry.main.login_server}/cloudex-backend:latest"
      cpu    = var.environment == "prod" ? 1.0 : 0.5
      memory = var.environment == "prod" ? "2Gi" : "1Gi"
    }
  }
  
  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"
    
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  
  tags = var.tags
  
  depends_on = [
    azurerm_role_assignment.container_app_kv_secrets,
    azurerm_role_assignment.container_app_acr_pull
  ]
  
  lifecycle {
    ignore_changes = [
      template[0].container[0].image,
      template[0].container[0].env,
      secret
    ]
  }
}

# Key Vault Secrets - Only sensitive values
resource "azurerm_key_vault_secret" "openai_api_key" {
  name         = "openai-api-key"
  value        = data.azurerm_cognitive_account.shared_ai.primary_access_key
  key_vault_id = azurerm_key_vault.main.id
  
  depends_on = [azurerm_role_assignment.current_user_kv_admin]
}

resource "azurerm_key_vault_secret" "azure_search_key" {
  name         = "azure-search-key"
  value        = data.azurerm_search_service.shared.primary_key
  key_vault_id = azurerm_key_vault.main.id
  
  depends_on = [azurerm_role_assignment.current_user_kv_admin]
}

resource "azurerm_key_vault_secret" "redis_url" {
  name         = "redis-url"
  value        = "redis://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:${azurerm_redis_cache.main.ssl_port}?ssl=true"
  key_vault_id = azurerm_key_vault.main.id
  
  depends_on = [azurerm_role_assignment.current_user_kv_admin]
}

resource "azurerm_key_vault_secret" "backend_sp_client_secret" {
  name         = "backend-sp-client-secret"
  value        = azuread_application_password.backend_service_principal.value
  key_vault_id = azurerm_key_vault.main.id
  
  depends_on = [azurerm_role_assignment.current_user_kv_admin]
}

resource "azurerm_key_vault_secret" "appinsights_connection_string" {
  name         = "appinsights-connection-string"
  value        = data.azurerm_application_insights.shared.connection_string
  key_vault_id = azurerm_key_vault.main.id
  
  depends_on = [azurerm_role_assignment.current_user_kv_admin]
}

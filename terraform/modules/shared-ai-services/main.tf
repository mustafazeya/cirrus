# Shared AI Services Module
# This module creates AI services that are shared across test and prod environments

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# Resource Group for shared services
resource "azurerm_resource_group" "shared" {
  name     = var.resource_group_name
  location = var.location

  tags = {
    Environment = "shared"
    ManagedBy   = "Terraform"
    Purpose     = "Shared AI Services"
  }
}

# Azure AI Services (Multi-service account with GPT-5.1 support)
resource "azurerm_ai_services" "main" {
  name                = var.ai_services_name
  location            = azurerm_resource_group.shared.location
  resource_group_name = azurerm_resource_group.shared.name
  sku_name            = var.ai_services_sku
  
  custom_subdomain_name = var.ai_services_name
  
  local_authentication_enabled      = true
  public_network_access             = "Enabled"
  outbound_network_access_restricted = false
  
  tags = {
    Environment = "shared"
    ManagedBy   = "Terraform"
  }
}

# GPT-5.1 Chat Model Deployment
resource "azurerm_cognitive_deployment" "gpt51_chat" {
  name                 = "gpt-5.1-chat"
  cognitive_account_id = azurerm_ai_services.main.id
  
  model {
    format  = "OpenAI"
    name    = "gpt-5.1-chat"
    version = "2025-11-13"
  }
  
  sku {
    name     = "GlobalStandard"
    capacity = 150
  }
  
  version_upgrade_option = "OnceNewDefaultVersionAvailable"
}

# Azure AI Search
resource "azurerm_search_service" "shared" {
  name                = var.search_service_name
  resource_group_name = azurerm_resource_group.shared.name
  location            = azurerm_resource_group.shared.location
  sku                 = var.search_service_sku
  replica_count       = var.search_replica_count
  partition_count     = var.search_partition_count

  tags = {
    Environment = "shared"
    ManagedBy   = "Terraform"
  }
}

# Log Analytics Workspace for shared services
resource "azurerm_log_analytics_workspace" "shared" {
  name                = var.log_analytics_name
  location            = azurerm_resource_group.shared.location
  resource_group_name = azurerm_resource_group.shared.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days

  tags = {
    Environment = "shared"
    ManagedBy   = "Terraform"
  }
}

# Application Insights for shared services
resource "azurerm_application_insights" "shared" {
  name                = var.app_insights_name
  location            = azurerm_resource_group.shared.location
  resource_group_name = azurerm_resource_group.shared.name
  workspace_id        = azurerm_log_analytics_workspace.shared.id
  application_type    = "web"

  tags = {
    Environment = "shared"
    ManagedBy   = "Terraform"
  }
}

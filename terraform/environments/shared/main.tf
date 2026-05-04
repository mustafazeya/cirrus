terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  backend "azurerm" {
    subscription_id      = "67874868-54bc-43f4-916a-5e23c3631ee3"
    resource_group_name  = "management-rg"
    storage_account_name = "sascdxprodtfstatesa"
    container_name       = "tfbackend"
    key                  = "infra/cloudex/shared.tfstate"
    tenant_id            = "5581c9a8-168b-45f0-abd4-d375da99bf9f"
    use_azuread_auth     = true
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id
  tenant_id       = "5581c9a8-168b-45f0-abd4-d375da99bf9f"
}

# Create shared AI services
module "shared_ai_services" {
  source = "../../modules/shared-ai-services"

  resource_group_name    = "cloudex-shared-rg"
  location               = "swedencentral"
  ai_services_name       = "cloudex-shared-ai"
  ai_services_sku        = "S0"
  search_service_name    = "cloudex-shared-search"
  search_service_sku     = "standard"
  search_replica_count   = 1
  search_partition_count = 1
  log_analytics_name     = "cloudex-shared-logs"
  log_retention_days     = 30
  app_insights_name      = "cloudex-shared-insights"
}

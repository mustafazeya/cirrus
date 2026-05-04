terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.0"
    }
  }

  backend "azurerm" {
    subscription_id      = "67874868-54bc-43f4-916a-5e23c3631ee3"
    resource_group_name  = "management-rg"
    storage_account_name = "sascdxprodtfstatesa"
    container_name       = "tfbackend"
    key                  = "infra/cloudex/prod.tfstate"
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
  tenant_id       = var.tenant_id
}

provider "azuread" {
  tenant_id = var.tenant_id
}

module "cloudex" {
  source = "../../modules/cloudex"
  
  providers = {
    azurerm.shared = azurerm
  }
  
  environment               = "prod"
  location                  = var.location
  frontend_location         = var.frontend_location
  tenant_id                 = var.tenant_id
  subscription_id           = var.subscription_id
  management_group_id       = var.management_group_id
  container_apps_subnet_cidr = var.container_apps_subnet_cidr
  frontend_redirect_uris    = var.frontend_redirect_uris
  
  tags = {
    Environment = "prod"
    Project     = "CloudexPortal"
    Owner       = "Platform Team"
    ManagedBy   = "Terraform"
  }
}

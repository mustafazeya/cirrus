locals {
  common_tags = [
    "Environment:${var.environment}",
    "Project:CloudexPortal",
    "ManagedBy:Terraform"
  ]
  
  backend_tags = [
    "Environment:${var.environment}",
    "Project:CloudexPortal-Backend",
    "ManagedBy:Terraform"
  ]
}

# Azure AD Application Registration for Frontend SSO
resource "azuread_application" "cloud_portal" {
  display_name = "CloudexPortal-${var.environment}-sso"
  description  = "Cloudex AI Portal Application Registration for Frontend SSO"
  
  single_page_application {
    redirect_uris = var.frontend_redirect_uris
  }

  oauth2_post_response_required = false
  fallback_public_client_enabled = false

  # Azure Log analytics API
  required_resource_access {
    resource_app_id = "797f4846-ba00-4fd7-ba43-dac1f8f63013"
    
    resource_access {
      id   = "41094075-9dad-400e-a0bd-54e686782033"
      type = "Scope"
    }
  }

  # Microsoft Graph
  required_resource_access {
    resource_app_id = "00000003-0000-0000-c000-000000000000"
    
    resource_access {
      id   = "5f8c59db-677d-491f-a6b8-5f174b11ec1d" # Group.Read.All (Delegated)
      type = "Scope"
    }
    resource_access {
      id   = "a154be20-db9c-4678-8ab7-66f6cc099a59" # User.Read.All (Delegated)
      type = "Scope"
    }
    resource_access {
      id   = "5b567255-7703-4780-807c-7be8301ae99b" # Group.Read.All (Application)
      type = "Role"
    }
    resource_access {
      id   = "df021288-bdef-4463-88db-98f22de89214" # User.Read.All (Application)
      type = "Role"
    }
  }

  # Azure Service Management
  required_resource_access {
    resource_app_id = "ca7f3f0b-7d91-482c-8e09-c5d840d0eac5"
    
    resource_access {
      id   = "e8dac03d-d467-4a7e-9293-9cca7df08b31" # user_impersonation
      type = "Scope"
    }
  }

  tags = local.common_tags
  
  lifecycle {
    ignore_changes = [
      app_role
    ]
  }
}

# Data source to look up Azure AD users for owners
data "azuread_users" "owners" {
  user_principal_names = [
    "mohammad.mustafa@sas.se"
  ]
}

# Create a service principal for the application 
resource "azuread_service_principal" "cloud_portal" {
  client_id = azuread_application.cloud_portal.client_id
  app_role_assignment_required = true
  owners = data.azuread_users.owners.object_ids
  
  tags = local.common_tags
}

# Service Principal for Backend API (read-only access)
resource "azuread_application" "backend_service_principal" {
  display_name = "CloudexPortal-Backend-${var.environment}-identity"
  description  = "Service Principal for CloudPortal backend API read-only access"
  
  sign_in_audience = "AzureADMyOrg"
  
  tags = local.backend_tags
}

# Create service principal for the backend application
resource "azuread_service_principal" "backend_service_principal" {
  client_id = azuread_application.backend_service_principal.client_id
  app_role_assignment_required = false
  
  tags = local.backend_tags
}

# Create a client secret for the backend service principal
resource "azuread_application_password" "backend_service_principal" {
  application_id = azuread_application.backend_service_principal.id
  display_name   = "CloudPortal Backend Secret"
  end_date_relative = "17520h" # 2 years from creation
}

# NOTE: Management Group role assignment removed due to insufficient permissions
# To enable Management Group access, assign the Reader role manually:
# az role assignment create --assignee <service-principal-object-id> --role Reader --scope /providers/Microsoft.Management/managementGroups/<management-group-id>

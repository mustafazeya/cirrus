"""
Diagnostic Settings Service
Checks Azure Monitor diagnostic settings to determine log table mode (Dedicated vs AzureDiagnostics)
for any Azure resource. Works generically across all Azure resource types.

Uses direct REST API with management token for authentication (dual auth pattern).
"""

import logging
import time
from config import settings
import requests
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Global diagnostic settings cache (shared across all instances)
_diagnostic_settings_cache = {}
_cache_ttl = settings.DIAGNOSTIC_SETTINGS_CACHE_TTL  # sourced from config.py

class DiagnosticSettingsService:
    """
    Service to check diagnostic settings for Azure resources and determine
    which Log Analytics workspace(s) receive logs and in what mode.
    
    Uses REST API with management token from user_info for authentication.
    Supports dual auth pattern: management token for diagnostics, LA token for queries.
    """
    
    def __init__(self):
        """Initialize the diagnostic settings service."""
        # Using global cache - no instance cache needed
        pass
    
    async def get_log_mode_for_resource(
        self,
        resource_id: str,
        user_info: Dict[str, Any],
        subscription_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Check diagnostic settings for a resource and determine log mode(s).
        
        Args:
            resource_id: Full Azure resource ID (e.g., /subscriptions/.../resourceGroups/.../providers/.../azureFirewalls/...)
            user_info: User info containing management_token for API authentication
            subscription_id: Optional subscription ID. If not provided, will be extracted from resource_id.
            
        Returns:
            {
                'resource_id': str,
                'resource_name': str,
                'resource_type': str,
                'has_diagnostics': bool,
                'primary': {  # Dedicated mode (preferred) or only mode available
                    'workspace_id': str,
                    'workspace_name': str,
                    'workspace_subscription': str,
                    'mode': 'Dedicated' | 'AzureDiagnostics',
                    'diagnostic_setting_name': str,
                    'enabled_log_categories': List[str],
                    'enabled_metric_categories': List[str]
                } | None,
                'fallback': {  # AzureDiagnostics mode (if both modes exist)
                    'workspace_id': str,
                    'workspace_name': str,
                    'workspace_subscription': str,
                    'mode': 'AzureDiagnostics',
                    'diagnostic_setting_name': str,
                    'enabled_log_categories': List[str],
                    'enabled_metric_categories': List[str]
                } | None,
                'all_settings': List[Dict]  # Raw diagnostic settings for reference
            }
        """
        
        global _diagnostic_settings_cache, _cache_ttl
        
        # Check cache first
        cache_key = f"diag_settings:{resource_id}"
        if cache_key in _diagnostic_settings_cache:
            cached_data, cached_time = _diagnostic_settings_cache[cache_key]
            if (time.time() - cached_time) < _cache_ttl:
                logger.info(f" Using cached diagnostic settings")
                return cached_data
        
        # Get management token from user_info (dual auth pattern)
        management_token = user_info.get('management_token') or user_info.get('token')
        if not management_token:
            logger.error(" No management token found in user_info")
            return self._empty_result(resource_id)
        
        # Extract subscription if not provided
        if not subscription_id:
            subscription_id = self._extract_subscription_id(resource_id)
        
        resource_name = self._extract_resource_name(resource_id)
        resource_type = self._extract_resource_type(resource_id)
        
        result = {
            'resource_id': resource_id,
            'resource_name': resource_name,
            'resource_type': resource_type,
            'has_diagnostics': False,
            'primary': None,
            'fallback': None,
            'all_settings': []
        }
        
        try:
            logger.info(f" Checking diagnostic settings for resource: {resource_name} ({resource_type})")
            
            # Call Azure Management API directly with REST (same as query_service.py pattern)
            api_version = "2021-05-01-preview"
            url = f"https://management.azure.com{resource_id}/providers/microsoft.insights/diagnosticSettings?api-version={api_version}"
            
            headers = {
                "Authorization": f"Bearer {management_token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Process each diagnostic setting
            dedicated_settings = []
            azurediag_settings = []
            
            if "value" in data:
                for setting in data["value"]:
                    properties = setting.get("properties", {})
                    workspace_id = properties.get("workspaceId")
                    
                    if not workspace_id:
                        continue  # Skip settings without Log Analytics workspace
                    
                    # Check logAnalyticsDestinationType to determine mode
                    # "Dedicated" = resource-specific tables (e.g., AZFWDnsQuery)
                    # null/empty = AzureDiagnostics mode (legacy)
                    destination_type = properties.get("logAnalyticsDestinationType")
                    mode = 'Dedicated' if destination_type == 'Dedicated' else 'AzureDiagnostics'
                    
                    setting_info = {
                        'diagnostic_setting_name': setting.get("name", "unknown"),
                        'workspace_id': workspace_id,
                        'workspace_name': self._extract_workspace_name(workspace_id),
                        'workspace_subscription': self._extract_subscription_id(workspace_id),
                        'mode': mode,
                        'log_categories': [
                            l['category'] for l in properties.get('logs', [])
                            if l.get('enabled', False)
                        ],
                        'metric_categories': [
                            m['category'] for m in properties.get('metrics', [])
                            if m.get('enabled', False)
                        ],
                    }
                    
                    result['all_settings'].append(setting_info)
                    
                    # Categorize by mode
                    if mode == 'Dedicated':
                        dedicated_settings.append(setting_info)
                    else:
                        azurediag_settings.append(setting_info)
            
            # Determine primary and fallback based on what's available
            # Priority: Dedicated mode > AzureDiagnostics mode
            if dedicated_settings and azurediag_settings:
                # Both modes exist - Dedicated is primary, AzureDiagnostics is fallback
                result['primary'] = dedicated_settings[0]
                result['fallback'] = azurediag_settings[0]
                result['has_diagnostics'] = True
                logger.info(f" Found BOTH modes - Primary: {result['primary']['mode']} ({result['primary']['workspace_name']}), "
                          f"Fallback: {result['fallback']['mode']} ({result['fallback']['workspace_name']})")
            elif dedicated_settings:
                # Only Dedicated mode
                result['primary'] = dedicated_settings[0]
                result['has_diagnostics'] = True
                logger.info(f" Found Dedicated mode only - Workspace: {result['primary']['workspace_name']}")
            elif azurediag_settings:
                # Only AzureDiagnostics mode
                result['primary'] = azurediag_settings[0]
                result['has_diagnostics'] = True
                logger.info(f" Found AzureDiagnostics mode only - Workspace: {result['primary']['workspace_name']}")
            else:
                logger.info(f" No diagnostic settings with Log Analytics found for {resource_name}")
            
            # Cache the result
            _diagnostic_settings_cache[cache_key] = (result, time.time())
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f" Resource not found or no diagnostic settings: {resource_id}")
            else:
                logger.error(f" HTTP error checking diagnostic settings for {resource_id}: {e}")
        except Exception as e:
            logger.error(f" Error checking diagnostic settings for {resource_id}: {e}")
        
        return result
    
    def _empty_result(self, resource_id: str) -> Dict[str, Any]:
        """Return empty result structure."""
        return {
            'resource_id': resource_id,
            'resource_name': self._extract_resource_name(resource_id),
            'resource_type': self._extract_resource_type(resource_id),
            'has_diagnostics': False,
            'primary': None,
            'fallback': None,
            'all_settings': []
        }
    
    def _extract_subscription_id(self, resource_id: str) -> str:
        """Extract subscription ID from Azure resource ID."""
        parts = resource_id.split('/')
        try:
            sub_index = parts.index('subscriptions') + 1
            return parts[sub_index]
        except (ValueError, IndexError):
            return ""
    
    def _extract_resource_name(self, resource_id: str) -> str:
        """Extract resource name from Azure resource ID."""
        return resource_id.split('/')[-1] if resource_id else "unknown"
    
    def _extract_resource_type(self, resource_id: str) -> str:
        """Extract resource type from Azure resource ID."""
        parts = resource_id.split('/')
        try:
            providers_index = parts.index('providers') + 1
            if providers_index < len(parts) - 1:
                provider = parts[providers_index]
                resource_type = parts[providers_index + 1]
                return f"{provider}/{resource_type}"
        except (ValueError, IndexError):
            pass
        return "unknown"
    
    def _extract_workspace_name(self, workspace_id: str) -> str:
        """Extract workspace name from workspace resource ID."""
        return workspace_id.split('/')[-1] if workspace_id else "unknown"
    

    
    def clear_cache(self):
        """Clear the global diagnostic settings cache (useful for testing or manual refresh)."""
        global _diagnostic_settings_cache
        _diagnostic_settings_cache.clear()
        logger.info(" Diagnostic settings cache cleared")

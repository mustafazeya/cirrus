import logging
from typing import List, Dict, Any, Optional
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.costmanagement import CostManagementClient
from config import settings
from azure.mgmt.advisor import AdvisorManagementClient
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError, HttpResponseError
from tenacity import retry, stop_after_attempt, wait_exponential
import asyncio
from datetime import datetime, timedelta
from auth import AzureAuthHandler
from services.cache_service import cache_service
from services.background_prefetch_service import background_prefetch_service
from services.user_rbac_service import user_rbac_service

logger = logging.getLogger(__name__)

class ResourceService:
    """Service for managing Azure resources using user's credentials and RBAC"""
    
    def __init__(self):
        self.auth_handler = AzureAuthHandler()
        # Fallback credential for service operations (when no user token)
        self.fallback_credential = DefaultAzureCredential()
    
    async def get_user_accessible_subscriptions(
        self, 
        user_info: Dict[str, Any],
        page: int = 1,
        page_size: int = 20,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """
        Get subscriptions that the user has access to based on their RBAC permissions
        Uses prefetched data filtered by user's actual permissions
        Supports pagination for users with many subscriptions
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            logger.debug(f"Getting accessible subscriptions for user {user_id}")
            
            # Get all prefetched subscriptions (cached by service principal)
            all_subscriptions = await background_prefetch_service.get_cached_subscriptions()
            
            if not all_subscriptions:
                logger.warning("No prefetched subscriptions found, triggering immediate fetch")
                # If no prefetched data, trigger an immediate fetch
                all_subscriptions = await background_prefetch_service.prefetch_all_subscriptions()
            
            # Filter subscriptions based on user's RBAC permissions
            accessible_subscriptions = await user_rbac_service.filter_subscriptions_by_user_access(
                user_info, all_subscriptions
            )
            
            # Apply pagination
            total_count = len(accessible_subscriptions)
            total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_subscriptions = accessible_subscriptions[start_idx:end_idx]
            
            logger.debug(f"User {user_id} has access to {total_count} subscriptions (showing page {page})")
            
            return {
                "subscriptions": paginated_subscriptions,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
            }
            
        except ClientAuthenticationError as e:
            logger.error(f"Authentication failed: {e}")
            raise ClientAuthenticationError("Access denied. Please check your permissions.")
        except Exception as e:
            logger.error(f"Failed to get accessible subscriptions: {e}")
            raise
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_resource_groups(self, subscription_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get all resource groups in a subscription that the user has access to
        Uses prefetched data filtered by user's RBAC permissions
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            logger.debug(f"Getting resource groups for user {user_id}, subscription {subscription_id}")
            
            # Get prefetched resource groups for this subscription
            cached_resource_groups = await background_prefetch_service.get_cached_resource_groups(subscription_id)
            
            if not cached_resource_groups:
                logger.warning(f"No prefetched resource groups found for subscription {subscription_id}, triggering immediate fetch")
                # If no prefetched data, trigger an immediate fetch
                cached_resource_groups = await background_prefetch_service.prefetch_resource_groups_for_subscription(subscription_id)
            
            # Filter resource groups based on user's RBAC permissions
            # For now, we return all cached resource groups since the service principal should only cache
            # what's accessible, but in future we could add more granular RBAC filtering here
            accessible_resource_groups = await user_rbac_service.filter_resource_groups_by_user_access(
                user_info, subscription_id, cached_resource_groups
            )
            
            result = {
                "subscription_id": subscription_id,
                "resource_groups": accessible_resource_groups,
                "count": len(accessible_resource_groups)
            }
            
            logger.debug(f"Returning {len(accessible_resource_groups)} resource groups for user {user_id}, subscription {subscription_id}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to get resource groups for subscription {subscription_id}: {e}")
            # Fallback to live API call if prefetch fails
            return await self._get_resource_groups_live(subscription_id, user_info)
    
    async def _get_resource_groups_live(self, subscription_id: str, user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fallback method to get resource groups via live API call
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            cache_key = f"resource_groups_live:{subscription_id}"
            
            # Try to get from cache first (shorter TTL for live calls)
            cached_result = await cache_service.get(user_id, cache_key)
            if cached_result:
                logger.debug(f"Returning cached live resource groups for user {user_id}, subscription {subscription_id}")
                return cached_result
            
            # Get user token from user_info
            user_token = user_info.get('token')
            if not user_token:
                raise ValueError("No token found in user_info")
                
            # Get user credential
            user_credential = await self.auth_handler.get_user_credential(user_token)
            
            # Use user's credential to access resources
            resource_client = ResourceManagementClient(user_credential, subscription_id)
            
            resource_groups = []
            try:
                async for rg in self._list_resource_groups_async(resource_client):
                    resource_groups.append({
                        "name": rg.name,
                        "location": rg.location,
                        "id": rg.id,
                        "tags": rg.tags or {},
                        "properties": getattr(rg, 'properties', {}),
                        "managed_by": getattr(rg, 'managed_by', None)
                    })
            except HttpResponseError as e:
                if e.status_code == 403:
                    raise ClientAuthenticationError(f"Access denied to subscription {subscription_id}. You don't have the required permissions.")
                raise
            
            result = {
                "subscription_id": subscription_id,
                "resource_groups": resource_groups,
                "count": len(resource_groups)
            }
            
            # Cache the results for 10 minutes (shorter TTL for live calls)
            await cache_service.set(user_id, cache_key, result, ttl=settings.RESOURCE_GROUP_CACHE_TTL)
            logger.info(f"Cached {len(resource_groups)} live resource groups for user {user_id}, subscription {subscription_id}")
            
            return result
            
        except ClientAuthenticationError:
            logger.error(f"Access denied for subscription {subscription_id}")
            raise
        except Exception as e:
            logger.error(f"Failed to get live resource groups for subscription {subscription_id}: {e}")
            raise
    
    async def _list_resource_groups_async(self, resource_client):
        """Async wrapper for listing resource groups"""
        loop = asyncio.get_event_loop()
        resource_groups = await loop.run_in_executor(
            None, 
            lambda: list(resource_client.resource_groups.list())
        )
        for rg in resource_groups:
            yield rg
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_resources(
        self, 
        subscription_id: str, 
        resource_group: Optional[str] = None,
        resource_type: Optional[str] = None,
        user_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Get resources with optional filtering by resource group and type
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown') if user_info else 'unknown'
            
            # Create cache key that includes filters for granular caching
            cache_key_parts = [f"resources:{subscription_id}"]
            if resource_group:
                cache_key_parts.append(f"rg:{resource_group}")
            if resource_type:
                cache_key_parts.append(f"type:{resource_type}")
            cache_key = ":".join(cache_key_parts)
            
            # Try to get from cache first
            cached_result = await cache_service.get(user_id, cache_key)
            if cached_result:
                logger.info(f" CACHE HIT: Returning cached resources for user {user_id}, key: {cache_key}, count: {len(cached_result.get('resources', []))}")
                return cached_result
            
            logger.info(f" CACHE MISS: Fetching fresh resources for user {user_id}, key: {cache_key}")
            
            # Get user token from user_info
            user_token = user_info.get('token') if user_info else None
            if not user_token:
                raise ValueError("No token found in user_info")
                
            # Get user credential
            user_credential = await self.auth_handler.get_user_credential(user_token)
            
            resource_client = ResourceManagementClient(user_credential, subscription_id)
            
            resources = []
            
            if resource_group:
                # Get resources from specific resource group
                resources_iter = resource_client.resources.list_by_resource_group(resource_group)
            else:
                # Get all resources in subscription
                resources_iter = resource_client.resources.list()
            
            async for resource in self._list_resources_async(resources_iter):
                # Apply resource type filter if specified
                if resource_type and not resource.type.lower().endswith(resource_type.lower()):
                    continue
                
                resource_info = {
                    "id": resource.id,
                    "name": resource.name,
                    "type": resource.type,
                    "location": resource.location,
                    "resource_group": resource.id.split('/')[4] if len(resource.id.split('/')) > 4 else "",
                    "tags": resource.tags or {},
                    "sku": getattr(resource, 'sku', None),
                    "kind": getattr(resource, 'kind', None)
                }
                
                # Add resource-specific properties
                if hasattr(resource, 'properties') and resource.properties:
                    resource_info["properties"] = self._extract_safe_properties(resource.properties)
                
                resources.append(resource_info)
            
            result = {
                "subscription_id": subscription_id,
                "resource_group": resource_group,
                "resource_type": resource_type,
                "resources": resources,
                "count": len(resources)
            }
            
            # Cache resources for longer - they don't change that frequently
            # Use longer TTL for specific resource group/type filters since they're more stable
            ttl = settings.RESOURCE_CACHE_TTL_FILTERED if resource_group or resource_type else settings.RESOURCE_CACHE_TTL_ALL
            await cache_service.set(user_id, cache_key, result, ttl=ttl)
            logger.info(f" CACHED: {len(resources)} resources for user {user_id}, key: {cache_key}, TTL: {ttl}s")
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to get resources: {e}")
            raise
    
    async def _list_resources_async(self, resources_iter):
        """Async wrapper for listing resources"""
        loop = asyncio.get_event_loop()
        resources = await loop.run_in_executor(None, lambda: list(resources_iter))
        for resource in resources:
            yield resource
    
    def _extract_safe_properties(self, properties) -> Dict[str, Any]:
        """
        Extract safe properties from resource, excluding sensitive information
        """
        safe_properties = {}
        
        # Define safe property keys that can be exposed
        safe_keys = [
            'provisioningState', 'status', 'state', 'location', 'version',
            'tier', 'capacity', 'replica', 'instances', 'hostname', 'fqdn',
            'publicIPAddress', 'privateIPAddress', 'adminUsername', 'osType',
            'vmSize', 'storageProfile', 'networkProfile', 'enabledFeatures'
        ]
        
        if hasattr(properties, '__dict__'):
            for key, value in properties.__dict__.items():
                if key.lower() in [k.lower() for k in safe_keys]:
                    # Convert complex objects to string representation
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        safe_properties[key] = value
                    else:
                        safe_properties[key] = str(value)
        
        return safe_properties
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def get_cost_analysis(
        self, 
        subscription_id: str, 
        time_period: str = "ThisMonth",
        user_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Get cost analysis for a subscription
        """
        try:
            # Get user token from user_info
            user_token = user_info.get('token') if user_info else None
            if not user_token:
                raise ValueError("No token found in user_info")
                
            # Get user credential
            user_credential = await self.auth_handler.get_user_credential(user_token)
            
            cost_client = CostManagementClient(user_credential)
            
            # Define scope
            scope = f"/subscriptions/{subscription_id}"
            
            # Define time period
            time_periods = {
                "ThisMonth": self._get_current_month_period(),
                "LastMonth": self._get_last_month_period(),
                "Last7Days": self._get_last_n_days_period(7),
                "Last30Days": self._get_last_n_days_period(30)
            }
            
            time_frame = time_periods.get(time_period, time_periods["ThisMonth"])
            
            # Execute cost query using Azure Cost Management API
            # NOTE: Azure Cost Management API is very slow (can take 60+ seconds)
            # For now, return structured mock data until we implement caching/background processing
            # TODO: Implement background job to fetch and cache cost data periodically
            
            # Real API call (commented out due to performance issues):
            # query_def = QueryDefinition(...)
            # result = cost_client.query.usage(scope, query_def)
            
            # For now, return empty/zero costs to avoid timeout
            # The dashboard will show "Month-to-date costs" message
            cost_data = {
                "total_cost": 0.0,
                "currency": "SEK",
                "time_period": time_period,
                "breakdown_by_resource_group": [],
                "daily_costs": []
            }
            
            logger.info(f" Returning placeholder cost data for subscription {subscription_id} (API too slow)")
            
            return cost_data
            
        except Exception as e:
            logger.error(f"Failed to get cost analysis: {e}")
            raise
    
    def _get_current_month_period(self) -> Dict[str, str]:
        """Get current month time period"""
        from utils.timezone_utils import now_cet
        now = now_cet().replace(tzinfo=None)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return {
            "from": start_of_month.isoformat() + "Z",
            "to": now.isoformat() + "Z"
        }
    
    def _get_last_month_period(self) -> Dict[str, str]:
        """Get last month time period"""
        from utils.timezone_utils import now_cet
        now = now_cet().replace(tzinfo=None)
        start_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_of_last_month = start_of_current_month - timedelta(days=1)
        start_of_last_month = end_of_last_month.replace(day=1)
        
        return {
            "from": start_of_last_month.isoformat() + "Z",
            "to": end_of_last_month.isoformat() + "Z"
        }
    
    def _get_last_n_days_period(self, days: int) -> Dict[str, str]:
        """Get last N days time period"""
        from utils.timezone_utils import now_cet
        now = now_cet().replace(tzinfo=None)
        start_date = now - timedelta(days=days)
        
        return {
            "from": start_date.isoformat() + "Z",
            "to": now.isoformat() + "Z"
        }
    
    async def get_resource_recommendations(
        self, 
        subscription_id: str,
        user_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Get Azure Advisor recommendations for resources
        """
        try:
            # Get user token from user_info
            user_token = user_info.get('token') if user_info else None
            if not user_token:
                raise ValueError("No token found in user_info")
                
            # Get user credential
            user_credential = await self.auth_handler.get_user_credential(user_token)
            
            advisor_client = AdvisorManagementClient(user_credential, subscription_id)
            
            recommendations = []
            
            # This would normally call the Advisor API
            # For MVP, return mock recommendations
            mock_recommendations = [
                {
                    "id": "rec-001",
                    "category": "Cost",
                    "title": "Resize underutilized virtual machines",
                    "description": "VM 'web-server-01' has low CPU utilization. Consider downsizing.",
                    "impact": "High",
                    "resource_name": "web-server-01",
                    "potential_savings": "$45.50/month"
                },
                {
                    "id": "rec-002", 
                    "category": "Security",
                    "title": "Enable disk encryption",
                    "description": "VM disks should be encrypted for security compliance.",
                    "impact": "Medium",
                    "resource_name": "database-server",
                    "potential_savings": "N/A"
                }
            ]
            
            return {
                "subscription_id": subscription_id,
                "recommendations": mock_recommendations,
                "count": len(mock_recommendations)
            }
            
        except Exception as e:
            logger.error(f"Failed to get recommendations: {e}")
            raise
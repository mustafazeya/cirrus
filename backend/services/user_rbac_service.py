"""
User RBAC Filtering Service
Determines what Azure resources a user has access to based on their RBAC permissions
"""

import logging
from typing import List, Dict, Any, Set
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.resource import SubscriptionClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from auth import UserTokenCredential
from services.cache_service import cache_service
from config import settings

logger = logging.getLogger(__name__)

class UserRBACService:
    """
    Service to determine user's RBAC permissions and filter resources accordingly
    """
    
    def __init__(self):
        self.cache_ttl = settings.USER_PERMISSIONS_CACHE_TTL
    
    async def get_user_accessible_subscriptions(self, user_info: Dict[str, Any]) -> List[str]:
        """
        Get list of subscription IDs that the user has access to
        Uses the user's token to query Azure Management API
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            cache_key = f"user_subscriptions:{user_id}"
            
            # Try cache first
            cached_subs = await cache_service.get(user_id, cache_key)
            if cached_subs:
                logger.debug(f"Returning cached accessible subscriptions for user {user_id}")
                return cached_subs
            
            # Get user's access token
            user_token = user_info.get('token')
            if not user_token:
                logger.error("No access token found in user_info")
                return []
            
            # Create credential from user token
            user_credential = UserTokenCredential(user_token)
            
            # Use subscription client to list accessible subscriptions
            subscription_client = SubscriptionClient(user_credential)
            
            accessible_subscription_ids = []
            
            try:
                # List subscriptions the user has access to
                subscriptions = subscription_client.subscriptions.list()
                
                for subscription in subscriptions:
                    accessible_subscription_ids.append(subscription.subscription_id)
                    
            except Exception as e:
                logger.error(f"Failed to list subscriptions for user {user_id}: {e}")
                return []
            
            # Cache the results
            await cache_service.set(user_id, cache_key, accessible_subscription_ids, ttl=self.cache_ttl)
            
            logger.debug(f"User {user_id} has access to {len(accessible_subscription_ids)} subscriptions")
            return accessible_subscription_ids
            
        except Exception as e:
            logger.error(f"Failed to get user accessible subscriptions: {e}")
            return []
    
    async def get_user_accessible_resource_groups(self, user_info: Dict[str, Any], subscription_id: str) -> List[str]:
        """
        Get list of resource group names that the user has access to in a specific subscription
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            cache_key = f"user_rgs:{subscription_id}:{user_id}"
            
            # Try cache first
            cached_rgs = await cache_service.get(user_id, cache_key)
            if cached_rgs:
                logger.info(f"Returning cached accessible resource groups for user {user_id} in subscription {subscription_id}")
                return cached_rgs
            
            # For now, if user has access to subscription, assume they can see all resource groups
            # In future, this could be enhanced to check specific RG-level permissions
            accessible_subscription_ids = await self.get_user_accessible_subscriptions(user_info)
            
            if subscription_id not in accessible_subscription_ids:
                logger.warning(f"User {user_id} does not have access to subscription {subscription_id}")
                return []
            
            # Return all resource group names (will be filtered from prefetched data)
            # This is a simplified approach - in production you might want more granular RG-level checking
            accessible_rgs = ["*"]  # Wildcard means all RGs in the subscription
            
            # Cache the results
            await cache_service.set(user_id, cache_key, accessible_rgs, ttl=self.cache_ttl)
            
            return accessible_rgs
            
        except Exception as e:
            logger.error(f"Failed to get user accessible resource groups: {e}")
            return []
    
    async def filter_subscriptions_by_user_access(self, user_info: Dict[str, Any], all_subscriptions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter the prefetched subscription list to only include subscriptions the user has access to
        """
        try:
            # Get user's accessible subscription IDs
            accessible_sub_ids = await self.get_user_accessible_subscriptions(user_info)
            
            if not accessible_sub_ids:
                return []
            
            # Filter the prefetched subscriptions
            filtered_subscriptions = [
                sub for sub in all_subscriptions 
                if sub.get("subscription_id") in accessible_sub_ids
            ]
            
            logger.debug(f"Filtered {len(all_subscriptions)} prefetched subscriptions to {len(filtered_subscriptions)} accessible to user")
            return filtered_subscriptions
            
        except Exception as e:
            logger.error(f"Failed to filter subscriptions by user access: {e}")
            return []
    
    async def filter_resource_groups_by_user_access(self, user_info: Dict[str, Any], subscription_id: str, all_resource_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filter the prefetched resource group list to only include RGs the user has access to
        """
        try:
            # Get user's accessible resource groups for this subscription
            accessible_rgs = await self.get_user_accessible_resource_groups(user_info, subscription_id)
            
            if not accessible_rgs:
                return []
            
            # If wildcard access, return all
            if "*" in accessible_rgs:
                return all_resource_groups
            
            # Filter the prefetched resource groups
            filtered_rgs = [
                rg for rg in all_resource_groups 
                if rg.get("name") in accessible_rgs
            ]
            
            logger.info(f"Filtered {len(all_resource_groups)} prefetched resource groups to {len(filtered_rgs)} accessible to user")
            return filtered_rgs
            
        except Exception as e:
            logger.error(f"Failed to filter resource groups by user access: {e}")
            return []

# Global instance
user_rbac_service = UserRBACService()
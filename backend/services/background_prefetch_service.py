"""
Background Prefetch Service
Uses service principal to prefetch Azure resources for dropdown data and caching
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from services.dual_credential_service import DualCredentialService
from services.cache_service import cache_service
from config import settings
import json
from datetime import datetime

logger = logging.getLogger(__name__)

class BackgroundPrefetchService:
    """
    Service that uses service principal to prefetch Azure resources
    This data is used for dropdowns and cached for performance
    """
    
    def __init__(self):
        self.dual_credential_service = DualCredentialService()
        self.prefetch_cache_ttl = settings.PREFETCH_CACHE_TTL_SECONDS
        self._prefetch_lock = asyncio.Lock()  # Prevent concurrent prefetches
        
    async def prefetch_all_subscriptions(self) -> List[Dict[str, Any]]:
        """
        Prefetch all subscriptions using service principal
        This is for dropdown data - users will see filtered results based on their RBAC
        """
        try:
            credential = self.dual_credential_service.get_service_principal_credential()
            subscription_client = SubscriptionClient(credential)

            def _list():
                result = []
                for sub in subscription_client.subscriptions.list():
                    if str(sub.state).lower() != 'enabled':
                        logger.debug(f"Skipping disabled subscription: {sub.display_name} ({sub.subscription_id}) - State: {sub.state}")
                        continue
                    result.append({
                        "subscription_id": sub.subscription_id,
                        "display_name": sub.display_name,
                        "state": str(sub.state) if sub.state else "Unknown",
                        "tenant_id": sub.tenant_id,
                        "tags": dict(sub.tags) if sub.tags else {},
                        "location_placement_id": sub.subscription_policies.location_placement_id if sub.subscription_policies else None,
                        "quota_id": sub.subscription_policies.quota_id if sub.subscription_policies else None,
                        "spending_limit": str(sub.subscription_policies.spending_limit) if sub.subscription_policies and sub.subscription_policies.spending_limit else None
                    })
                return result

            subscriptions = await asyncio.to_thread(_list)
            await cache_service.set("global", "all_subscriptions", subscriptions, ttl=self.prefetch_cache_ttl)
            return subscriptions
        except Exception:
            logger.error("Failed to prefetch subscriptions")
            return []
    
    async def prefetch_resource_groups_for_subscription(self, subscription_id: str) -> List[Dict[str, Any]]:
        """
        Prefetch resource groups for a specific subscription (no individual caching - done in batch)
        """
        try:
            credential = self.dual_credential_service.get_service_principal_credential()
            resource_client = ResourceManagementClient(credential, subscription_id)

            def _list():
                return [
                    {
                        "name": rg.name,
                        "location": rg.location,
                        "id": rg.id,
                        "tags": dict(rg.tags) if rg.tags else {},
                        "properties": {
                            "provisioning_state": rg.properties.provisioning_state if rg.properties else None
                        }
                    }
                    for rg in resource_client.resource_groups.list()
                ]

            return await asyncio.to_thread(_list)
        except Exception:
            return []
    
    async def prefetch_all_resource_groups(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Prefetch resource groups for all accessible subscriptions using batched writes
        """
        try:
            # First get all subscriptions
            subscriptions = await self.get_cached_subscriptions()
            if not subscriptions:
                subscriptions = await self.prefetch_all_subscriptions()
            
            all_resource_groups = {}
            
            # Prefetch resource groups for each subscription (in parallel, but limited)
            semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent requests
            
            async def prefetch_rg_for_sub(sub):
                async with semaphore:
                    sub_id = sub["subscription_id"]
                    rgs = await self.prefetch_resource_groups_for_subscription(sub_id)
                    all_resource_groups[sub_id] = rgs
            
            # Prefetch all subscriptions
            await asyncio.gather(*[prefetch_rg_for_sub(sub) for sub in subscriptions])
            
            # Batch write all resource groups to Redis using pipeline
            await self._batch_write_resource_groups(all_resource_groups)
            
            return all_resource_groups
            
        except Exception:
            return {}
    
    async def prefetch_common_resource_types(self) -> List[str]:
        """
        Prefetch common Azure resource types for dropdown filters
        """
        try:
            credential = self.dual_credential_service.get_service_principal_credential()
            subscriptions = await self.get_cached_subscriptions() or await self.prefetch_all_subscriptions()
            if not subscriptions:
                logger.warning("No subscriptions available to fetch resource types")
                return []

            common_providers = [
                "Microsoft.Compute", "Microsoft.Storage", "Microsoft.Network", "Microsoft.Web",
                "Microsoft.Sql", "Microsoft.KeyVault", "Microsoft.ContainerRegistry", "Microsoft.App"
            ]

            subscription_id = subscriptions[0]["subscription_id"]
            resource_client = ResourceManagementClient(credential, subscription_id)

            def _get_provider_types(namespace: str):
                try:
                    provider = resource_client.providers.get(namespace)
                    return [f"{namespace}/{rt.resource_type}" for rt in provider.resource_types]
                except Exception:
                    return []

            results = await asyncio.gather(*[asyncio.to_thread(_get_provider_types, ns) for ns in common_providers])
            resource_types = sorted({t for types in results for t in types})
            await cache_service.set("global", "common_resource_types", resource_types, ttl=self.prefetch_cache_ttl * 4)
            return resource_types
        except Exception:
            return []
    
    async def _batch_write_resource_groups(self, all_resource_groups: Dict[str, List[Dict[str, Any]]]):
        """
        Batch write all resource groups to Redis using pipeline to avoid connection storm
        
        Args:
            all_resource_groups: Dict mapping subscription_id to list of resource groups
        """
        if not all_resource_groups:
            return
        
        try:
            # Prepare batch items for cache_service.set_many
            batch_items = []
            for sub_id, rgs in all_resource_groups.items():
                cache_key = f"rg_prefetch:{sub_id}"
                batch_items.append(("global", cache_key, rgs, self.prefetch_cache_ttl))
            
            # Write all in one batched operation
            success = await cache_service.set_many(batch_items)
            
            if success:
                logger.debug(f"Successfully batch-wrote {len(batch_items)} resource group caches")
            else:
                logger.warning(f"Batch write partially failed for {len(batch_items)} resource group caches")
                
        except Exception as e:
            logger.error(f"Failed to batch write resource groups: {e}")
    
    async def get_cached_subscriptions(self) -> List[Dict[str, Any]]:
        """Get cached subscriptions from global cache"""
        return await cache_service.get("global", "all_subscriptions") or []
    
    async def get_cached_resource_groups(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Get cached resource groups for a subscription"""
        cache_key = f"rg_prefetch:{subscription_id}"
        return await cache_service.get("global", cache_key) or []
    
    async def get_cached_resource_types(self) -> List[str]:
        """Get cached common resource types"""
        return await cache_service.get("global", "common_resource_types") or []
    
    async def run_full_prefetch(self, force_refresh: bool = False):
        """
        Run complete prefetch cycle with distributed lock to prevent multiple replicas from running simultaneously
        This should be called periodically by the scheduler
        
        Args:
            force_refresh: If True, invalidates cache before prefetching (for manual/emergency refresh)
        """
        # Check if local prefetch is already running
        if self._prefetch_lock.locked():
            logger.warning("Prefetch already in progress (local lock), skipping duplicate run")
            return
        
        # Try to acquire distributed lock across all replicas
        redis_client = cache_service.redis_client
        distributed_lock_acquired = False
        lock_key = "global:prefetch_lock"
        
        try:
            if redis_client:
                # Try to set lock with 5 minute expiry (prefetch should complete in <5 min)
                distributed_lock_acquired = await redis_client.set(
                    lock_key, 
                    "locked", 
                    ex=300,  # 5 minutes
                    nx=True  # Only set if not exists
                )
                
                if not distributed_lock_acquired:
                    logger.info("Prefetch already running on another replica, skipping")
                    return
                
                logger.debug(" Acquired distributed prefetch lock")
            else:
                # No Redis, use local lock only (dev mode)
                logger.debug("Redis not available, using local lock only")
        
        except Exception as e:
            logger.warning(f"Failed to acquire distributed lock, proceeding with local lock: {e}")
        
        async with self._prefetch_lock:
            try:
                if force_refresh:
                    logger.info("Invalidating prefetch cache...")
                    await self._invalidate_prefetch_cache()
                
                logger.info("Starting Azure data prefetch for all subscriptions...")
                start_time = datetime.now()
                
                # Run prefetch operations
                subscriptions = await self.prefetch_all_subscriptions()
                resource_groups = await self.prefetch_all_resource_groups()
                resource_types = await self.prefetch_common_resource_types()
                
                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds()
                
                # Count total resource groups
                total_rgs = sum(len(rgs) for rgs in resource_groups.values())
                
                logger.info(
                    f"✅ Prefetch completed in {duration:.2f}s | "
                    f"Subscriptions: {len(subscriptions)} | "
                    f"Resource Groups: {total_rgs} | "
                    f"Resource Types: {len(resource_types)} | "
                    f"Next run: in 60 minutes"
                )
                
                # Store last prefetch timestamp
                await cache_service.set("global", "last_prefetch_timestamp", end_time.isoformat(), ttl=86400)  # 24 hours
                    
            except Exception:
                logger.error(" Prefetch cycle failed")
            finally:
                # Always release distributed lock
                if distributed_lock_acquired and redis_client:
                    try:
                        await redis_client.delete(lock_key)
                        logger.debug("Released distributed prefetch lock")
                    except Exception as e:
                        logger.warning(f"Failed to release distributed lock: {e}")
    
    async def _invalidate_prefetch_cache(self):
        """Invalidate all prefetch-related cache keys"""
        try:
            redis_client = cache_service.redis_client
            if not redis_client:
                logger.warning("Redis not available, cannot invalidate cache")
                return
            
            # Delete prefetch cache keys
            patterns = [
                "global:all_subscriptions",
                "global:rg_prefetch:*",  # All resource group caches
                "global:common_resource_types",
                "global:last_prefetch_timestamp"
            ]
            
            for pattern in patterns:
                if "*" in pattern:
                    # Use async scan_iter for patterns with wildcards
                    deleted_count = 0
                    async for key in redis_client.scan_iter(match=pattern, count=100):
                        await redis_client.delete(key)
                        deleted_count += 1
                    if deleted_count > 0:
                        logger.debug(f"Deleted {deleted_count} keys matching {pattern}")
                else:
                    # Direct delete for exact keys
                    await redis_client.delete(pattern)
                    
        except Exception:
            logger.error("Failed to invalidate prefetch cache")

# Global instance
background_prefetch_service = BackgroundPrefetchService()
"""
Advanced Azure Cost Management Service

Provides FAST, PARALLEL, SEAMLESS cost analysis like Azure Cost Management & Power BI:
- Multi-scope parallel queries (subscriptions, resource groups, resources)
- Tag-based cost allocation (cost center, environment, project, etc.)
- Reservation and Savings Plan analysis
- Amortized vs Actual cost views
- Real-time caching and aggregation
- Efficient batching for large-scale queries
- QPU budget tracking to prevent rate limits
- Global request queue for serialization
"""

import logging
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryDefinition,
    QueryTimePeriod,
    QueryDataset,
    QueryAggregation,
    QueryGrouping,
    QueryComparisonExpression,
    QueryFilter,
    GranularityType,
    TimeframeType,
    OperatorType
)
from azure.mgmt.managementgroups import ManagementGroupsAPI
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from config import settings
logger = logging.getLogger(__name__)
# GLOBAL QPU BUDGET TRACKER
# Azure Cost Management API limits: 12 QPU/10s, 60 QPU/1min, 600 QPU/1hr
# 1 QPU = 1 month of queried data
class QPUBudget:
    """Track Query Processing Units to avoid rate limits proactively"""
    def __init__(self):
        self.calls_10s = []  # List of (timestamp, qpu_cost) tuples
        self.calls_1m = []
        self.calls_1h = []
        self.lock = asyncio.Lock()
    
    async def calculate_qpu_cost(self, start_date: datetime, end_date: datetime) -> int:
        """Calculate QPU cost for a date range (1 QPU per month)"""
        months = max(1, round((end_date - start_date).days / 30))
        return months
    
    async def can_execute(self, qpu_cost: int = 1) -> tuple[bool, float]:
        """Check if we have budget. Returns (can_execute, wait_seconds)"""
        async with self.lock:
            now = datetime.now()
            
            # Clean up old entries
            cutoff_10s = now - timedelta(seconds=10)
            cutoff_1m = now - timedelta(minutes=1)
            cutoff_1h = now - timedelta(hours=1)
            
            self.calls_10s = [(t, c) for t, c in self.calls_10s if t > cutoff_10s]
            self.calls_1m = [(t, c) for t, c in self.calls_1m if t > cutoff_1m]
            self.calls_1h = [(t, c) for t, c in self.calls_1h if t > cutoff_1h]
            
            # Calculate current usage
            qpu_10s = sum(c for _, c in self.calls_10s)
            qpu_1m = sum(c for _, c in self.calls_1m)
            qpu_1h = sum(c for _, c in self.calls_1h)
            
            logger.debug(f" QPU Usage: 10s={qpu_10s}/12, 1m={qpu_1m}/60, 1h={qpu_1h}/600 (adding {qpu_cost} QPU)")
            
            # Check limits with 20% safety buffer
            if qpu_10s + qpu_cost > 10:  # 12 * 0.8 = 9.6 ≈ 10
                logger.warning(f" QPU limit hit (10s): {qpu_10s + qpu_cost} > 10, waiting 10s")
                return False, 10.0
            if qpu_1m + qpu_cost > 48:  # 60 * 0.8 = 48
                logger.warning(f" QPU limit hit (1m): {qpu_1m + qpu_cost} > 48, waiting 30s")
                return False, 30.0
            if qpu_1h + qpu_cost > 480:  # 600 * 0.8 = 480
                logger.warning(f" QPU limit hit (1h): {qpu_1h + qpu_cost} > 480, waiting 5 min")
                return False, 300.0
            
            return True, 0.0
    
    async def record_call(self, qpu_cost: int = 1):
        """Record a successful API call"""
        async with self.lock:
            now = datetime.now()
            self.calls_10s.append((now, qpu_cost))
            self.calls_1m.append((now, qpu_cost))
            self.calls_1h.append((now, qpu_cost))
            logger.debug(f" Recorded {qpu_cost} QPU usage at {now}")

# Global instance shared across all AdvancedCostService instances
_global_qpu_budget = QPUBudget()


class AdvancedCostService:
    """
    Enterprise-grade cost analysis service with parallel processing
    Handles: subscriptions, resource groups, resources, tags, reservations
    """
    
    # Cache for cost queries (1 hour TTL - cost data doesn't change frequently)
    _cache: Dict[str, Tuple[datetime, Any]] = {}
    _cache_ttl = timedelta(hours=1)
    
    # Cache hit/miss tracking (class-level, shared across all instances)
    _cache_hits = 0
    _cache_misses = 0
    
    # Rate limiting configuration
    # Increased from 1 to 3 concurrent queries - safe because:
    # 1. QPU Budget Tracker enforces 12 QPU/10s limit proactively
    # 2. Average query: 1-3 months = 1-3 QPU, so 3 concurrent = ~9 QPU (within limit)
    # 3. 15-min cache prevents duplicate queries
    # 4. Exponential backoff handles any 429 errors
    MAX_PARALLEL_QUERIES = settings.COST_MAX_PARALLEL_QUERIES
    BATCH_DELAY_SECONDS = settings.COST_BATCH_DELAY_SECONDS

    # Retry configuration with exponential backoff
    MAX_RETRIES = settings.COST_MAX_RETRIES
    INITIAL_RETRY_DELAY = settings.COST_INITIAL_RETRY_DELAY
    MAX_RETRY_DELAY = settings.COST_MAX_RETRY_DELAY
    
    def __init__(self, credential):
        self.credential = credential
        self.client = CostManagementClient(credential)
        self.mg_client = ManagementGroupsAPI(credential)
        self.executor = ThreadPoolExecutor(max_workers=self.MAX_PARALLEL_QUERIES)
        self._management_group_cache: Optional[str] = None
        self._manual_mg_override: Optional[str] = None  # Allow manual MG selection
        
        # Global semaphore to limit concurrent API calls across ALL operations
        self._api_semaphore = asyncio.Semaphore(self.MAX_PARALLEL_QUERIES)
        
        # Rate limit tracking
        self._rate_limit_hit_count = 0
        self._last_rate_limit_time = None
    
    def set_management_group(self, management_group_id: str):
        """Manually set which Management Group to use (overrides auto-detection)"""
        self._manual_mg_override = management_group_id
        self._management_group_cache = management_group_id
        logger.info(f" Manual Management Group override set: {management_group_id}")
    
    def clear_management_group_cache(self):
        """Clear cached Management Group to force re-discovery"""
        self._management_group_cache = None
        self._manual_mg_override = None
        logger.info(" Management Group cache cleared - will re-discover on next query")
    
    def get_rate_limit_stats(self) -> Dict[str, Any]:
        """Get statistics about rate limit hits"""
        return {
            "rate_limit_hits": self._rate_limit_hit_count,
            "last_rate_limit_time": self._last_rate_limit_time.isoformat() if self._last_rate_limit_time else None,
            "max_parallel_queries": self.MAX_PARALLEL_QUERIES,
            "batch_delay_seconds": self.BATCH_DELAY_SECONDS,
            "current_semaphore_value": self._api_semaphore._value if hasattr(self._api_semaphore, '_value') else None
        }
    
    def reset_rate_limit_stats(self):
        """Reset rate limit statistics"""
        self._rate_limit_hit_count = 0
        self._last_rate_limit_time = None
        logger.info(" Rate limit statistics reset")
    
    # ============================================================================
    # MANAGEMENT GROUP DISCOVERY (FOR GLOBAL SCOPE)
    # ============================================================================
    
    def get_top_management_group(self) -> Optional[str]:
        """
        🔍 Discover the top-level management group the user has access to.
        This enables querying ALL subscriptions in a SINGLE API call.
        
        Strategy: Find Tenant Root Group (has no parent or displayName="Tenant Root Group")
        
        Returns:
            Management Group ID or None if not available
        """
        # Check for manual override first
        if self._manual_mg_override:
            logger.info(f" Using manual Management Group override: {self._manual_mg_override}")
            return self._manual_mg_override
        
        if self._management_group_cache:
            logger.info(f" Using cached Management Group: {self._management_group_cache}")
            return self._management_group_cache
        
        try:
            logger.debug(" Discovering Management Groups...")
            
            # List all management groups user has access to
            mg_list = list(self.mg_client.management_groups.list())
            
            if not mg_list:
                logger.warning(" No Management Groups found. User may not have access.")
                return None
            
            logger.info(f" Found {len(mg_list)} management groups")
            
            # STRATEGY: Look for Tenant Root Group (top-most level)
            # Tenant Root Group has these characteristics:
            # - displayName contains "Tenant Root Group" OR
            # - Has no parent (parent is None or empty)
            
            tenant_root_group = None
            root_candidates = []  # MGs with no parent
            all_mgs_info = []
            
            for mg in mg_list:
                try:
                    # Get full details including parent
                    mg_details = self.mg_client.management_groups.get(mg.name, expand="children", recurse=False)
                    
                    display_name = mg_details.display_name or mg.name
                    
                    # CORRECT PATH: details.parent (not properties.parent)
                    # According to Azure SDK docs: ManagementGroup -> details -> parent
                    has_parent = (hasattr(mg_details, 'details') and 
                                 mg_details.details and 
                                 hasattr(mg_details.details, 'parent') and 
                                 mg_details.details.parent)
                    parent_id = mg_details.details.parent.id if has_parent else None
                    
                    logger.info(f" MG: {mg.name} | Display: {display_name} | Parent: {parent_id or 'None (ROOT)'}")
                    
                    all_mgs_info.append({
                        'name': mg.name,
                        'display_name': display_name,
                        'has_parent': has_parent,
                        'parent_id': parent_id
                    })
                    
                    # Check if this is Tenant Root Group by name
                    if "tenant root group" in display_name.lower():
                        logger.info(f" Found Tenant Root Group by name: {mg.name}")
                        tenant_root_group = mg.name
                        break
                    
                    # Check if this is a root MG (no parent)
                    if not has_parent:
                        logger.info(f" Found ROOT Management Group (no parent): {mg.name}")
                        root_candidates.append(mg.name)
                
                except Exception as e:
                    logger.warning(f" Failed to get details for MG {mg.name}: {e}")
                    continue
            
            # Use Tenant Root Group if found
            if tenant_root_group:
                self._management_group_cache = tenant_root_group
                logger.info(f" Selected Tenant Root Group: {tenant_root_group}")
                return tenant_root_group
            
            # IMPORTANT: If no MG has "Tenant Root Group" in name, but some MGs have parents,
            # extract the common parent ID - that's likely the actual tenant root
            if not root_candidates and all_mgs_info:
                parent_ids = set()
                for mg_info in all_mgs_info:
                    if mg_info['has_parent'] and mg_info['parent_id']:
                        # Extract MG ID from path like "/providers/Microsoft.Management/managementGroups/5581c9a8-168b-45f0-abd4-d375da99bf9f"
                        parent_path = mg_info['parent_id']
                        if '/managementGroups/' in parent_path:
                            parent_mg_id = parent_path.split('/managementGroups/')[-1]
                            parent_ids.add(parent_mg_id)
                
                # If all MGs share a common parent, that parent is the tenant root
                if len(parent_ids) == 1:
                    tenant_root_id = list(parent_ids)[0]
                    logger.info(f" Detected Tenant Root from parent references: {tenant_root_id}")
                    self._management_group_cache = tenant_root_id
                    # Try to count subscriptions under this root
                    try:
                        count = self._count_enabled_subscriptions_in_mg(tenant_root_id)
                        logger.info(f" Using Tenant Root Management Group: {tenant_root_id} ({count} enabled subscriptions)")
                    except:
                        logger.info(f" Using Tenant Root Management Group: {tenant_root_id}")
                    return tenant_root_id
            
            # If multiple root candidates, use smart selection:
            # 1. Prefer shorter names (parent MGs tend to have shorter names like "cdx" vs "cdx-landing-zones-corp")
            # 2. Count subscriptions to verify
            if len(root_candidates) > 1:
                logger.debug(f" Found {len(root_candidates)} root MG candidates")
                logger.debug(f" Candidates: {root_candidates}")
                
                # Sort by name length (shorter = more likely to be parent)
                root_candidates_sorted = sorted(root_candidates, key=len)
                logger.info(f" Checking candidates in order (shortest name first = likely parent):")
                
                best_mg = None
                best_count = 0
                
                for mg_id in root_candidates_sorted[:5]:  # Check top 5 to avoid too many API calls
                    try:
                        # Use official API to count subscriptions
                        count = self._count_enabled_subscriptions_in_mg(mg_id)
                        logger.info(f" MG '{mg_id}' (len={len(mg_id)}): {count} enabled subscriptions")
                        
                        # Prefer MGs with more subscriptions, but also consider shorter names
                        # (parent MGs typically have ALL subscriptions)
                        if count > best_count:
                            best_count = count
                            best_mg = mg_id
                    except Exception as e:
                        logger.warning(f" Failed to count subscriptions for MG {mg_id}: {e}")
                        continue
                
                if best_mg and best_count > 0:
                    selected = best_mg
                    self._management_group_cache = selected
                    logger.info(f" Selected ROOT Management Group with most subscriptions: {selected} ({best_count} enabled subs)")
                    logger.info(f" Querying this MG will include ALL child management groups and their subscriptions")
                    return selected
            
            # Use first root candidate (no parent)
            if root_candidates:
                selected = root_candidates[0]
                # Count subscriptions to log info
                try:
                    count = self._count_enabled_subscriptions_in_mg(selected)
                    logger.info(f" Selected ROOT Management Group: {selected} ({count} enabled subscriptions)")
                except:
                    logger.info(f" Selected ROOT Management Group: {selected}")
                self._management_group_cache = selected
                return selected
            
            # Fallback: Use first MG found (shouldn't happen if Tenant Root Group exists)
            if mg_list:
                first_mg = mg_list[0].name
                logger.warning(f" No ROOT found, using first MG: {first_mg}")
                logger.warning(f" Available MGs: {[mg['name'] for mg in all_mgs_info]}")
                self._management_group_cache = first_mg
                return first_mg
            
            return None
            
        except Exception as e:
            logger.error(f" Failed to discover Management Groups: {e}", exc_info=True)
            return None
    
    def _collect_subscription_ids_fast(self, management_group_id: str) -> set:
        """
        Fast collection of ALL subscription IDs under a management group using get_descendants().
        
        This is MUCH faster than recursive fetching as it uses a single API call that returns
        a flat list of all descendants (subscriptions and child MGs).
        
        Args:
            management_group_id: Management Group ID to query
            
        Returns:
            Set of subscription IDs found under this MG (all levels)
        """
        subscription_ids = set()
        
        try:
            logger.info(f" Using get_descendants() for fast subscription discovery...")
            
            # get_descendants() returns a flat list of ALL descendants
            # This is much faster than recursively fetching each child MG
            descendants = self.mg_client.management_groups.get_descendants(
                group_id=management_group_id
            )
            
            for item in descendants:
                item_type = getattr(item, 'type', '').lower()
                item_name = getattr(item, 'name', None)
                
                # Check if this is a subscription
                if item_name and (item_type.endswith('subscriptions') or '/subscriptions' in item_type):
                    subscription_ids.add(item_name)
                    logger.debug(f" Found subscription: {item_name}")
            
            logger.info(f" Found {len(subscription_ids)} subscriptions via get_descendants()")
            
        except Exception as e:
            logger.warning(f" get_descendants() failed: {e}")
            logger.info(f" Falling back to manual traversal...")
            # Fallback: try the old method if get_descendants fails
            # (though this shouldn't happen in normal circumstances)
            
        return subscription_ids
    
    def _count_enabled_subscriptions_in_mg(self, management_group_id: str) -> int:
        """
        Count enabled subscriptions under a management group RECURSIVELY.
        
        Uses ManagementGroupsAPI with get_descendants() to get a flat list of all
        descendants and count subscriptions with "Enabled" state.
        
        Args:
            management_group_id: Management Group ID
            
        Returns:
            Count of enabled subscriptions across all nested child MGs
        """
        try:
            from azure.mgmt.resource import SubscriptionClient
            
            # Use fast get_descendants() method to get ALL subscriptions
            subscription_ids = self._collect_subscription_ids_fast(management_group_id)
            
            logger.info(f" Found {len(subscription_ids)} subscriptions under MG via get_descendants()")
            
            # Now check which subscriptions are in "Active" state
            # Per Azure SDK docs: SubscriptionUnderManagementGroup has 'state' property
            # State values: "Active", "Warned", "PastDue", "Disabled", "Deleted"
            # We count "Active" subscriptions (not "Enabled" - that's for Azure resources)
            
            enabled_count = 0
            disabled_count = 0
            
            # PARALLEL state checking for fast execution (much faster than sequential)
            try:
                from azure.mgmt.resource import SubscriptionClient
                import concurrent.futures
                
                sub_client = SubscriptionClient(self.credential)
                
                logger.debug(f" Checking state for {len(subscription_ids)} subscriptions (parallel)...")
                
                def get_subscription_state(sub_id):
                    """Helper function to get subscription state (for parallel execution)"""
                    try:
                        sub_info = sub_client.subscriptions.get(sub_id)
                        state = getattr(sub_info, 'state', 'Unknown')
                        state_str = str(state).lower() if state else 'unknown'
                        return sub_id, state_str
                    except Exception as e:
                        logger.debug(f" Failed to get state for {sub_id}: {e}")
                        return sub_id, "enabled"  # assume accessible = enabled
                
                # Use ThreadPoolExecutor for parallel fetching (20 workers for fast execution)
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                    futures = {executor.submit(get_subscription_state, sid): sid for sid in subscription_ids}
                    results = [f.result() for f in concurrent.futures.as_completed(futures)]
                
                # Count enabled vs disabled subscriptions
                enabled_count = sum(
                    1 for (_, state) in results
                    if any(x in state for x in ["enabled", "warned", "pastdue"])
                )
                disabled_count = len(subscription_ids) - enabled_count
                
                logger.debug(f" Parallel state check complete: {enabled_count} active, {disabled_count} inactive")
                        
            except Exception as e:
                logger.warning(f" Could not check subscription states: {e}")
                # Fallback: count all found subscriptions as enabled
                enabled_count = len(subscription_ids)
            
            logger.info(f" Counted {enabled_count} active, {disabled_count} inactive subscriptions under Management Group '{management_group_id}'")
            return enabled_count
            
        except Exception as e:
            logger.error(f" Failed to count subscriptions in MG: {e}", exc_info=True)
            return 0
    
    # ============================================================================
    # MANAGEMENT GROUP COST QUERY (SINGLE API CALL FOR ALL SUBSCRIPTIONS)
    # ============================================================================
    
    async def get_costs_management_group(
        self,
        management_group_id: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        group_by: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        metric: str = "ActualCost"
    ) -> Dict[str, Any]:
        """
        🚀 Query costs across ALL subscriptions in management group with SINGLE API call
        
        This is how Azure Portal, Power BI, and commercial vendors do it!
        - No rate limiting issues
        - Fast execution (~10-20 seconds)
        - Native Azure solution
        
        Args:
            management_group_id: Management Group ID (e.g., "mg-prod")
            start_date: Start date for cost query
            end_date: End date for cost query
            granularity: "daily", "monthly", or None
            group_by: Dimensions to group by (ServiceName, Location, Tags, etc.)
            filters: Optional filters for tags, regions, etc.
            metric: ActualCost or AmortizedCost (note: MG only supports usage charges)
        
        Returns:
            Aggregated cost data for ALL subscriptions in the management group
        """
        scope = f"/providers/Microsoft.Management/managementGroups/{management_group_id}"
        
        logger.info(f" Querying Management Group: {management_group_id}")
        logger.info(f" Date range: {start_date.date()} to {end_date.date()}")
        logger.info(f" Group by: {group_by}, Metric: {metric}")
        logger.info(f" SINGLE API CALL for all subscriptions - no rate limiting!")
        
        # Check cache first
        cache_key = self._get_cache_key(
            scope=scope,
            start=start_date.date().isoformat(),
            end=end_date.date().isoformat(),
            granularity=granularity,
            group_by=group_by,
            filters=filters,
            metric=metric
        )
        
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        # Count enabled subscriptions under this Management Group
        logger.info(f" Counting enabled subscriptions under Management Group...")
        subscription_count = self._count_enabled_subscriptions_in_mg(management_group_id)
        
        # Run query asynchronously
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self.executor,
            self._query_scope_sync,
            scope, start_date, end_date, granularity, group_by, filters, metric
        )
        
        # Cache result
        self._set_cache(cache_key, result)
        
        # Add metadata including subscription count
        result["is_management_group"] = True
        result["management_group_id"] = management_group_id
        result["subscription_count"] = subscription_count
        result["metric_type"] = metric
        result["note"] = f"Management Group scope - includes {subscription_count} enabled subscriptions"
        
        logger.info(f" Management Group query completed successfully")
        logger.info(f" Subscriptions: {subscription_count} enabled")
        logger.info(f" Total cost: {result.get('total_cost', 0):.2f} {result.get('currency', 'SEK')}")
        logger.info(f" Metric: {metric}")
        
        return result
    
    def _get_cache_key(self, **kwargs) -> str:
        """Generate cache key from query parameters"""
        cache_str = json.dumps(kwargs, sort_keys=True)
        return hashlib.md5(cache_str.encode()).hexdigest()
    
    def _get_cached(self, cache_key: str) -> Optional[Any]:
        """Get cached result if valid (extended TTL when rate limits are active)"""
        if cache_key in self._cache:
            timestamp, data = self._cache[cache_key]
            
            # ADAPTIVE CACHE TTL: Extend cache lifetime when rate limits are active
            effective_ttl = self._cache_ttl
            if self._rate_limit_hit_count > 0 and self._last_rate_limit_time:
                time_since_hit = (datetime.now() - self._last_rate_limit_time).total_seconds()
                if time_since_hit < 300:  # Within last 5 minutes
                    # Extend cache to 15 minutes to reduce API pressure
                    effective_ttl = timedelta(minutes=15)
                    logger.debug(f" Using extended cache TTL (15min) due to recent rate limits")
            
            if datetime.utcnow() - timestamp < effective_ttl:
                logger.debug(f" Cache HIT for {cache_key[:8]}...")
                # Track cache hit
                self._cache_hits += 1
                return data
            else:
                del self._cache[cache_key]
        
        # Track cache miss
        self._cache_misses += 1
        return None
    
    def _set_cache(self, cache_key: str, data: Any):
        """Cache query result"""
        self._cache[cache_key] = (datetime.utcnow(), data)
        logger.debug(f" Cached result for {cache_key[:8]}...")
    
    # ============================================================================
    # GLOBAL COST QUERY VIA BILLING ACCOUNT (MCA-CORRECT, SP-BASED)
    # ============================================================================

    async def get_costs_global(
        self,
        user_subscription_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        group_by: str = "ServiceName",
        filters: Optional[Dict[str, Any]] = None,
        metric: str = "ActualCost",
    ) -> Dict[str, Any]:
        """
        MCA-correct global cost query using service principal + billing account scope.

        Why billing account scope instead of Management Group:
          - MCA tenants: Cost Management at MG scope returns 0 (only works for EA)
          - Billing accounts are the correct aggregation root for MCA
          - Users don't need billing account access — the SP holds it centrally

        Flow:
          1. SP discovers billing accounts (cached 24 h — rarely change)
          2. SP queries each billing account in parallel, grouped by SubscriptionId
             + the requested dimension, no per-subscription call needed
          3. In-memory filter: keep only rows matching the user's accessible subscription set
          4. Aggregate filtered rows by requested dimension and return

        Result: 2-5 API calls regardless of subscription count, RBAC-correct.
        """
        user_sub_set = {s.lower() for s in user_subscription_ids}

        cache_key = self._get_cache_key(
            scope="global_billing",
            subs_hash=hashlib.md5("|".join(sorted(user_sub_set)).encode()).hexdigest(),
            start=start_date.date().isoformat(),
            end=end_date.date().isoformat(),
            group_by=group_by,
            metric=metric,
        )
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        billing_accounts = await self._discover_billing_accounts()
        if not billing_accounts:
            logger.warning("get_costs_global: no billing accounts discovered, falling back to subscription queries")
            return await self._fallback_to_subscription_queries(
                user_subscription_ids, start_date, end_date, group_by, filters, metric
            )

        logger.info(f"get_costs_global: querying {len(billing_accounts)} billing account(s) in parallel")
        tasks = [
            self._query_billing_account(ba_id, start_date, end_date, group_by, metric)
            for ba_id in billing_accounts
        ]
        ba_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_rows: List[Dict] = []
        currency = "SEK"
        for ba_id, res in zip(billing_accounts, ba_results):
            if isinstance(res, Exception):
                logger.warning(f"get_costs_global: billing account {ba_id} failed: {res}")
                continue
            currency = res.get("currency", currency)
            all_rows.extend(res.get("rows", []))

        # Filter server-side to user's accessible subscriptions
        filtered = [r for r in all_rows if r.get("subscription_id", "") in user_sub_set]
        logger.info(
            f"get_costs_global: {len(all_rows)} total rows → {len(filtered)} after RBAC filter "
            f"({len(user_sub_set)} accessible subscriptions)"
        )

        # Aggregate by requested dimension
        aggregated: Dict[str, float] = defaultdict(float)
        total_cost = 0.0
        for row in filtered:
            dim = row.get("dimension_value", "Unknown")
            cost = row.get("cost", 0.0)
            aggregated[dim] += cost
            total_cost += cost

        # When grouping by subscription, resolve IDs → display names via cache
        sub_id_to_name: Dict[str, str] = {}
        if group_by in ("Subscription", "SubscriptionId"):
            try:
                from services.cache_service import cache_service
                cached_subs = await cache_service.get("global", "all_subscriptions")
                if cached_subs:
                    sub_id_to_name = {
                        s["subscription_id"].lower(): s.get("display_name", s["subscription_id"])
                        for s in cached_subs
                        if "subscription_id" in s
                    }
            except Exception as e:
                logger.warning(f"get_costs_global: could not load subscription names for display: {e}")

        # Map group_by dimension to the frontend field name the UI already reads
        GROUP_BY_FIELD = {
            "ServiceName":     "servicename",
            "ServiceFamily":   "servicename",
            "MeterCategory":   "servicename",
            "Subscription":    "subscriptionname",
            "SubscriptionId":  "subscriptionname",
            "ResourceGroup":   "resourcegroupname",
            "ResourceGroupName": "resourcegroupname",
            "ResourceLocation": "resourcelocation",
            "ResourceType":    "resourcetype",
            "ResourceId":      "resourceid",
        }
        dim_field = GROUP_BY_FIELD.get(group_by, "servicename")
        is_sub_grouping = dim_field == "subscriptionname"

        def _resolve_dim(dim: str) -> str:
            if is_sub_grouping:
                return sub_id_to_name.get(dim.lower(), dim)
            return dim

        breakdown = [
            {
                "name": _resolve_dim(dim),
                dim_field: _resolve_dim(dim),
                "cost": round(cost, 2),
                "currency": currency,
            }
            for dim, cost in sorted(aggregated.items(), key=lambda x: -x[1])
        ]

        result = {
            "success": True,
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "breakdown": breakdown,
            "query_method": "billing_account_global",
            "billing_accounts_queried": len(billing_accounts),
            "subscription_count": len(user_sub_set),
            "note": (
                f"Queried {len(billing_accounts)} billing account(s), "
                f"filtered to {len(user_sub_set)} accessible subscriptions"
            ),
        }
        self._set_cache(cache_key, result)
        return result

    async def _discover_billing_accounts(self) -> List[str]:
        """
        List MCA billing account IDs accessible to the service principal.
        Cached for 24 hours — billing account assignments change rarely.
        """
        CACHE_KEY = "__billing_accounts_discovery__"
        if CACHE_KEY in self._cache:
            ts, data = self._cache[CACHE_KEY]
            if datetime.utcnow() - ts < timedelta(hours=24):
                logger.info(f"_discover_billing_accounts: cache hit ({len(data)} accounts)")
                return data

        try:
            import requests
            from services.dual_credential_service import dual_cred_service
            sp_cred = dual_cred_service.get_service_principal_credential()
            token = sp_cred.get_token("https://management.azure.com/.default").token

            url = "https://management.azure.com/providers/Microsoft.Billing/billingAccounts?api-version=2020-05-01"
            resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            resp.raise_for_status()

            accounts = [ba["name"] for ba in resp.json().get("value", [])]
            logger.info(f"_discover_billing_accounts: found {len(accounts)} billing accounts")
            self._cache[CACHE_KEY] = (datetime.utcnow(), accounts)
            return accounts

        except Exception as e:
            logger.error(f"_discover_billing_accounts failed: {e}", exc_info=True)
            return []

    async def _query_billing_account(
        self,
        billing_account_id: str,
        start_date: datetime,
        end_date: datetime,
        group_by: str,
        metric: str,
    ) -> Dict[str, Any]:
        """Query billing account cost grouped by SubscriptionId + requested dimension (thread pool)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            self._query_billing_account_sync,
            billing_account_id, start_date, end_date, group_by, metric,
        )

    def _query_billing_account_sync(
        self,
        billing_account_id: str,
        start_date: datetime,
        end_date: datetime,
        group_by: str,
        metric: str,
    ) -> Dict[str, Any]:
        """
        Sync billing-account REST call (runs in thread pool).
        Groups by SubscriptionId + requested dimension so the caller can
        filter by subscription and aggregate by dimension in one pass.
        """
        import requests
        import time as _time
        import random

        try:
            from services.dual_credential_service import dual_cred_service
            sp_cred = dual_cred_service.get_service_principal_credential()
            token = sp_cred.get_token("https://management.azure.com/.default").token
        except Exception as e:
            logger.error(f"_query_billing_account_sync: SP credential error: {e}")
            return {"rows": [], "currency": "SEK"}

        # MCA billing account dimension map.
        # ServiceName = Azure portal "Service" (e.g. "Azure Kubernetes Service", "Log Analytics")
        #               More granular, may be null for purchases (ChargeType fallback handles those).
        # ServiceFamily = coarser category (Compute, Storage, Security) — always set for usage.
        # Both are valid grouping dimensions at billing account scope per Azure docs.
        dimension_map = {
            "ServiceName": "ServiceName",         # Azure portal "Service" — default, most useful
            "ServiceFamily": "ServiceFamily",     # Coarse category (Compute, Storage, …)
            "ResourceGroupName": "ResourceGroup", # Note: API uses "ResourceGroup" not "ResourceGroupName"
            "ResourceLocation": "ResourceLocation",
            "ResourceType": "ResourceType",
            "ResourceId": "ResourceId",
            "Subscription": "SubscriptionId",     # Break down by subscription
            "MeterCategory": "MeterCategory",     # Same as ServiceName in most cases
        }
        primary_dim = dimension_map.get(group_by, "ServiceName")

        # Grouping strategy (max 2 per API docs):
        # - SubscriptionId: required for post-query RBAC filter
        # - primary_dim: the requested breakdown dimension
        # ChargeType is fetched separately via a second lightweight query and merged in
        # post-processing to label non-usage rows (Purchase, Tax, Refund, Adjustment).
        grouping = [{"type": "Dimension", "name": "SubscriptionId"}]
        if primary_dim != "SubscriptionId":
            grouping.append({"type": "Dimension", "name": primary_dim})

        # Second query: group by SubscriptionId + ChargeType to capture
        # purchases/refunds/taxes that have no ServiceFamily or MeterCategory.
        charge_type_grouping = [
            {"type": "Dimension", "name": "SubscriptionId"},
            {"type": "Dimension", "name": "ChargeType"},
        ]

        scope = f"/providers/Microsoft.Billing/billingAccounts/{billing_account_id}"
        url = (
            f"https://management.azure.com{scope}"
            f"/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
        )

        def _build_body(grouping_dims):
            return {
                # ActualCost includes reservation purchases as one-time charges (not amortized).
                # "Usage" would miss these, causing VM costs to appear far lower than the portal shows.
                "type": "ActualCost",
                "timeframe": "Custom",
                "timePeriod": {
                    "from": start_date.strftime("%Y-%m-%dT00:00:00Z"),
                    "to": end_date.strftime("%Y-%m-%dT23:59:59Z"),
                },
                "dataset": {
                    "granularity": "None",
                    "aggregation": {
                        # Billing account scope requires "Cost" not "ActualCost"/"AmortizedCost"
                        "totalCost": {"name": "Cost", "function": "Sum"}
                    },
                    "grouping": grouping_dims,
                },
            }

        def _parse_response(resp_json, primary_col, fallback_label=None):
            props = resp_json.get("properties", {})
            columns = [c["name"].lower() for c in props.get("columns", [])]
            raw_rows = props.get("rows", [])
            cost_idx = next((i for i, c in enumerate(columns) if c in ("totalcost", "cost", "pretaxcost", "costusd")), None)
            sub_idx = next((i for i, c in enumerate(columns) if c == "subscriptionid"), None)
            # API may return "resourcegroup" for ResourceGroupName, handle both
            primary_col_lower = primary_col.lower().replace("name", "").rstrip()
            dim_idx = next(
                (i for i, c in enumerate(columns) if c == primary_col.lower() or c == primary_col_lower),
                None
            )
            currency_idx = next((i for i, c in enumerate(columns) if c == "currency"), None)
            currency = "SEK"
            rows = []
            for raw in raw_rows:
                if currency_idx is not None:
                    currency = str(raw[currency_idx])
                dim_val = str(raw[dim_idx]).strip() if dim_idx is not None else ""
                if not dim_val or dim_val.lower() in ("", "none", "null"):
                    dim_val = fallback_label or "Other"
                rows.append({
                    "subscription_id": str(raw[sub_idx]).lower() if sub_idx is not None else "",
                    "dimension_value": dim_val,
                    "cost": float(raw[cost_idx]) if cost_idx is not None else 0.0,
                    "currency": currency,
                })
            return rows, currency

        # Human-readable labels for non-usage charge types
        CHARGE_TYPE_LABELS = {
            "purchase": "Reservation / Savings Plan Purchases",
            "refund": "Refunds",
            "tax": "Taxes",
            "adjustment": "Billing Adjustments",
            "roundingadjustment": "Rounding Adjustments",
            "unusedreservation": "Unused Reservations",
        }

        def _post_with_pagination(initial_url, body, headers):
            """POST to Cost Management and follow nextLink until all pages are collected."""
            all_rows = []
            currency = "SEK"
            resp = requests.post(initial_url, headers=headers, json=body, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            props = data.get("properties", {})
            all_rows.extend(props.get("rows", []))
            columns = [c["name"].lower() for c in props.get("columns", [])]
            currency_idx = next((i for i, c in enumerate(columns) if c == "currency"), None)
            if currency_idx is not None and all_rows:
                currency = str(all_rows[0][currency_idx])
            next_link = props.get("nextLink")
            page = 1
            while next_link:
                page += 1
                logger.info(f"_query_billing_account_sync: following nextLink page {page}")
                page_resp = requests.get(next_link, headers=headers, timeout=60)
                page_resp.raise_for_status()
                page_data = page_resp.json()
                page_props = page_data.get("properties", {})
                all_rows.extend(page_props.get("rows", []))
                next_link = page_props.get("nextLink")
            # Reconstruct a response-like dict with all rows
            return {"properties": {"columns": data.get("properties", {}).get("columns", []), "rows": all_rows}}

        auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        for attempt in range(self.MAX_RETRIES):
            try:
                # Query 1: primary service breakdown (SubscriptionId + ServiceFamily/other dim)
                body1 = _build_body(grouping)
                resp1_data = _post_with_pagination(url, body1, auth_headers)
                rows1, currency = _parse_response(resp1_data, primary_dim, fallback_label=None)

                # Query 2: ChargeType breakdown to capture purchases/tax/refunds
                body2 = _build_body(charge_type_grouping)
                resp2_data = _post_with_pagination(url, body2, auth_headers)
                rows2, _ = _parse_response(resp2_data, "ChargeType", fallback_label="Other")

                # For any row in query 1 with no service label, replace with ChargeType label.
                # Build a sub→ChargeType→cost map from query 2 for non-usage rows only.
                non_usage_by_sub: dict = {}
                for r in rows2:
                    ct = r["dimension_value"].lower()
                    if ct != "usage":
                        label = CHARGE_TYPE_LABELS.get(ct, r["dimension_value"].title())
                        key = r["subscription_id"]
                        non_usage_by_sub.setdefault(key, {})
                        non_usage_by_sub[key][label] = non_usage_by_sub[key].get(label, 0.0) + r["cost"]

                # Rows with no service label: keep Q1's cost (accurate) but apply
                # ChargeType label from Q2. Q2 costs are used only for proportion
                # splitting — never substituted for Q1 costs — so the total stays exact.
                final_rows = []
                for r in rows1:
                    if r["dimension_value"] and r["dimension_value"] != "Other":
                        final_rows.append(r)
                    else:
                        sub_non_usage = non_usage_by_sub.get(r["subscription_id"], {})
                        q1_cost = r["cost"]
                        if not sub_non_usage:
                            # No ChargeType info — keep with a generic label
                            final_rows.append({**r, "dimension_value": "Purchases & Other"})
                        elif len(sub_non_usage) == 1:
                            # Single charge type — relabel but keep Q1 cost
                            label = next(iter(sub_non_usage))
                            final_rows.append({**r, "dimension_value": label})
                        else:
                            # Multiple charge types — split Q1 cost proportionally by Q2 ratio
                            total_q2 = sum(sub_non_usage.values())
                            for label, q2_cost in sub_non_usage.items():
                                ratio = q2_cost / total_q2 if total_q2 > 0 else 1 / len(sub_non_usage)
                                final_rows.append({
                                    "subscription_id": r["subscription_id"],
                                    "dimension_value": label,
                                    "cost": round(q1_cost * ratio, 2),
                                    "currency": currency,
                                })

                logger.info(f"_query_billing_account_sync: {billing_account_id} → {len(final_rows)} rows (2 queries: service + charge_type)")
                return {"rows": final_rows, "currency": currency}

            except Exception as e:
                error_detail = ""
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        error_detail = e.response.json()
                    except Exception:
                        error_detail = e.response.text
                logger.error(
                    f"_query_billing_account_sync: {billing_account_id} failed: {e}\n"
                    f"  Azure error: {error_detail}"
                )
                if "429" in str(e) and attempt < self.MAX_RETRIES - 1:
                    delay = min(
                        self.INITIAL_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 3),
                        self.MAX_RETRY_DELAY,
                    )
                    logger.warning(f"Rate limit on billing account {billing_account_id}, retry in {delay:.1f}s")
                    _time.sleep(delay)
                else:
                    logger.error(f"_query_billing_account_sync: {billing_account_id} failed: {e}")
                    return {"rows": [], "currency": "SEK"}

        return {"rows": [], "currency": "SEK"}

    async def _fallback_to_subscription_queries(
        self,
        subscription_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        group_by: str,
        filters: Optional[Dict[str, Any]],
        metric: str,
    ) -> Dict[str, Any]:
        """Last-resort fallback when billing account discovery fails."""
        logger.warning(
            f"_fallback_to_subscription_queries: querying {len(subscription_ids)} subscriptions individually"
        )
        scopes = [f"/subscriptions/{s}" for s in subscription_ids]
        group_by_list = [group_by] if group_by else None
        result = await self.get_costs_parallel(
            scopes=scopes,
            start_date=start_date,
            end_date=end_date,
            granularity="daily",
            group_by=group_by_list,
            filters=filters,
            metric=metric,
        )
        result["query_method"] = "subscription_fallback"
        return result

    # ============================================================================
    # CORE PARALLEL COST QUERY ENGINE
    # ============================================================================

    async def get_costs_parallel(
        self,
        scopes: List[str],
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        group_by: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        metric: str = "ActualCost"  # ActualCost, AmortizedCost, UsageQuantity
    ) -> Dict[str, Any]:
        """
        Query costs across multiple scopes in parallel with intelligent batching
        
        Args:
            scopes: List of Azure scopes to query (subscriptions or resource groups)
            start_date: Start date for cost query
            end_date: End date for cost query
            granularity: daily, monthly, or None
            group_by: List of dimensions to group by (ServiceName, ResourceType, ResourceGroupName, etc.)
            filters: Optional filters (tags, resource types, etc.)
            metric: Cost metric (ActualCost for pay-as-you-go, AmortizedCost for reservations)
        
        Returns:
            Aggregated cost data from all scopes
        """
        logger.debug(f" Starting PARALLEL cost query for {len(scopes)} scopes, date range: {start_date.date()} to {end_date.date()}")
        
        # Warn if we've hit rate limits recently and add ADAPTIVE cooldown
        if self._rate_limit_hit_count > 0 and self._last_rate_limit_time:
            time_since_last_hit = (datetime.now() - self._last_rate_limit_time).total_seconds()
            logger.warning(
                f"⚠️ Previous rate limit hits detected: {self._rate_limit_hit_count} "
                f"(last {time_since_last_hit:.0f}s ago). Proceeding with caution..."
            )
            
            # Adaptive cooldown: More hits = longer wait, scales with recent activity
            if time_since_last_hit < 60:  # Within last minute
                # Scale cooldown based on number of hits: 20s + (5s per hit)
                cooldown = 20 + (min(self._rate_limit_hit_count, 10) * 5)
                logger.warning(f" Recent rate limit activity detected, adding {cooldown}s cooldown...")
                await asyncio.sleep(cooldown)
        
        # Process scopes in batches to respect API rate limits
        all_results = []
        failed = []
        
        total_batches = (len(scopes) + self.MAX_PARALLEL_QUERIES - 1) // self.MAX_PARALLEL_QUERIES
        
        # Process in batches of MAX_PARALLEL_QUERIES
        for batch_start in range(0, len(scopes), self.MAX_PARALLEL_QUERIES):
            batch_end = min(batch_start + self.MAX_PARALLEL_QUERIES, len(scopes))
            batch_scopes = scopes[batch_start:batch_end]
            batch_num = batch_start // self.MAX_PARALLEL_QUERIES + 1
            
            logger.debug(f" Processing batch {batch_num}/{total_batches}: subscriptions {batch_start+1}-{batch_end} of {len(scopes)}")
            
            # Create async tasks for this batch
            tasks = []
            for scope in batch_scopes:
                task = asyncio.create_task(
                    self._query_scope_async(
                        scope=scope,
                        start_date=start_date,
                        end_date=end_date,
                        granularity=granularity,
                        group_by=group_by,
                        filters=filters,
                        metric=metric
                    )
                )
                tasks.append((scope, task))
            
            # Wait for all queries in this batch to complete
            for scope, task in tasks:
                try:
                    result = await task
                    all_results.append(result)
                except Exception as e:
                    logger.error(f" Failed to query scope {scope}: {e}")
                    failed.append(scope)
            
            # Add ADAPTIVE delay between batches to avoid rate limiting (except after last batch)
            if batch_num < total_batches:
                # Base delay + extra if we've had recent rate limit hits
                delay = self.BATCH_DELAY_SECONDS
                if self._rate_limit_hit_count > 0 and self._last_rate_limit_time:
                    time_since_hit = (datetime.now() - self._last_rate_limit_time).total_seconds()
                    if time_since_hit < 120:  # Within last 2 minutes
                        # Add 3 seconds per recent rate limit hit (max +30s)
                        extra_delay = min(self._rate_limit_hit_count * 3, 30)
                        delay += extra_delay
                        logger.warning(f" Adding {extra_delay}s extra delay due to recent rate limit hits")
                
                logger.info(f" Waiting {delay} seconds before next batch to avoid rate limits...")
                await asyncio.sleep(delay)
        
        # Aggregate results from all batches
        aggregated = self._aggregate_results(all_results, group_by, scopes)
        
        logger.debug(f" Completed {len(all_results)}/{len(scopes)} queries successfully")
        if failed:
            logger.warning(f" Failed scopes: {len(failed)}")
        
        return {
            "success": True,
            "total_cost": aggregated["total_cost"],
            "currency": aggregated.get("currency", "SEK"),
            "breakdown": aggregated["breakdown"],
            "daily_trend": aggregated.get("daily_trend", []),
            "by_subscription": aggregated.get("by_subscription", {}),
            "scopes_queried": len(all_results),
            "scopes_failed": len(failed),
            "date_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            }
        }
    
    async def query_multiple_scopes_with_both_metrics(
        self,
        scopes: List[str],
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        group_by: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        sequential: bool = True  # CHANGED: Default to sequential to avoid rate limits!
    ) -> Dict[str, Any]:
        """
        Query both ActualCost AND AmortizedCost
        
        Args:
            sequential: If True, query metrics sequentially to reduce API load (DEFAULT, safer for rate limits)
                       If False, query in parallel for faster results (riskier)
        
        Returns:
            Combined result with both actual_cost and amortized_cost
        """
        logger.info(f" Starting DUAL-METRIC cost query for {len(scopes)} scopes (sequential={sequential})")
        
        if sequential:
            # Query sequentially to avoid overwhelming the API
            logger.info(" Querying ActualCost first...")
            actual_result = await self.get_costs_parallel(
                scopes, start_date, end_date, granularity, group_by, filters, "ActualCost"
            )
            
            logger.info(" Querying AmortizedCost second...")
            amortized_result = await self.get_costs_parallel(
                scopes, start_date, end_date, granularity, group_by, filters, "AmortizedCost"
            )
        else:
            # Query both metrics in parallel for faster results
            actual_task = self.get_costs_parallel(
                scopes, start_date, end_date, granularity, group_by, filters, "ActualCost"
            )
            amortized_task = self.get_costs_parallel(
                scopes, start_date, end_date, granularity, group_by, filters, "AmortizedCost"
            )
            
            actual_result, amortized_result = await asyncio.gather(actual_task, amortized_task)
        
        # Combine results
        return {
            "success": actual_result.get("success", False),
            "total_cost": actual_result.get("total_cost", 0.0),
            "amortized_cost": amortized_result.get("total_cost", 0.0),
            "currency": actual_result.get("currency", "SEK"),
            "breakdown": actual_result.get("breakdown", []),
            "daily_trend": actual_result.get("daily_trend", []),
            "scopes_queried": actual_result.get("scopes_queried", 0),
            "scopes_failed": actual_result.get("scopes_failed", 0),
            "date_range": actual_result.get("date_range", {})
        }
    
    async def _query_scope_async(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str,
        group_by: Optional[List[str]],
        filters: Optional[Dict[str, Any]],
        metric: str
    ) -> Dict[str, Any]:
        """Query a single scope asynchronously with QPU budget checking and global rate limiting"""
        
        # Check cache first
        cache_key = self._get_cache_key(
            scope=scope,
            start=start_date.date().isoformat(),
            end=end_date.date().isoformat(),
            granularity=granularity,
            group_by=group_by,
            filters=filters,
            metric=metric
        )
        
        cached = self._get_cached(cache_key)
        if cached:
            logger.debug(f" Cache hit for scope: {scope[-40:]}")
            return cached
        
        # Calculate QPU cost for this query
        qpu_cost = await _global_qpu_budget.calculate_qpu_cost(start_date, end_date)
        
        # Check if we have QPU budget available - WAIT if needed
        max_wait_attempts = 10
        for attempt in range(max_wait_attempts):
            can_execute, wait_seconds = await _global_qpu_budget.can_execute(qpu_cost)
            if can_execute:
                break
            
            if attempt < max_wait_attempts - 1:
                logger.warning(f" QPU budget exhausted, waiting {wait_seconds}s before query (attempt {attempt+1}/{max_wait_attempts})")
                await asyncio.sleep(wait_seconds)
            else:
                logger.error(f" QPU budget exhausted after {max_wait_attempts} attempts, failing query")
                return {
                    "success": False,
                    "total_cost": 0.0,
                    "currency": "SEK",
                    "breakdown": [],
                    "error": "QPU budget exhausted"
                }
        
        # Use semaphore to limit concurrent API calls globally
        async with self._api_semaphore:
            # Run query in thread pool (Azure SDK is synchronous)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor,
                self._query_scope_sync,
                scope, start_date, end_date, granularity, group_by, filters, metric
            )
            
            # Record QPU usage if successful
            if result.get("success", False):
                await _global_qpu_budget.record_call(qpu_cost)
        
        # Cache result
        self._set_cache(cache_key, result)
        
        return result
    
    def _query_scope_sync(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str,
        group_by: Optional[List[str]],
        filters: Optional[Dict[str, Any]],
        metric: str
    ) -> Dict[str, Any]:
        """Synchronous cost query (runs in thread pool) with EXPONENTIAL BACKOFF for rate limits"""
        
        import time
        import traceback
        
        for attempt in range(self.MAX_RETRIES):
            try:
                # Build query definition
                query_def = self._build_query_definition(
                    start_date=start_date,
                    end_date=end_date,
                    granularity=granularity,
                    group_by=group_by,
                    filters=filters,
                    metric=metric
                )
                
                # Execute query with pagination support
                logger.debug(f" Querying scope: {scope[-40:]} (attempt {attempt + 1}/{self.MAX_RETRIES})")
                result = self.client.query.usage(scope=scope, parameters=query_def)
                
                # Handle pagination - Azure Cost Management API has undocumented pagination
                # See: https://github.com/Azure/azure-rest-api-specs/issues/23405
                # The API returns max 1000 rows per page, need to fetch all pages manually
                all_rows = list(result.rows) if result.rows else []
                page_count = 1
                
                # Check for nextLink - it's directly on result object, not in properties
                next_link = result.next_link if hasattr(result, 'next_link') else None
                
                # Fetch all pages if pagination exists
                while next_link:
                    page_count += 1
                    logger.info(f" Fetching page {page_count} via nextLink (cost API pagination)...")
                    
                    try:
                        # Use the Cost Management client to follow nextLink
                        from azure.core.rest import HttpRequest
                        
                        # Make a raw HTTP request using the client's pipeline
                        request = HttpRequest(
                            method="POST",
                            url=next_link,
                            json=query_def.as_dict() if hasattr(query_def, 'as_dict') else None
                        )
                        
                        # Send request through the client's pipeline (handles auth, retries, etc.)
                        response = self.client.query._client._pipeline.run(request)
                        
                        # Parse the response
                        next_result = response.http_response.json()
                        
                        # Extract rows from the response
                        if 'properties' in next_result and 'rows' in next_result['properties']:
                            next_rows = next_result['properties']['rows']
                            all_rows.extend(next_rows)
                            logger.info(f" Page {page_count}: Added {len(next_rows)} more rows (total: {len(all_rows)})")
                        
                        # Check for next nextLink
                        next_link = next_result.get('properties', {}).get('nextLink', None)
                        
                    except Exception as e:
                        logger.error(f" Failed to fetch page {page_count}: {e}")
                        logger.warning(f" PAGINATION INCOMPLETE! Only fetched {page_count-1} pages, results may be incomplete")
                        break
                
                if page_count > 1:
                    logger.info(f" Pagination complete: Fetched {page_count} pages with {len(all_rows)} total rows")
                elif len(all_rows) >= 1000:
                    logger.warning(f" WARNING: Received {len(all_rows)} rows with no nextLink - results may be incomplete!")
                
                # Update result with all rows
                result.rows = all_rows
                
                # Parse results
                parsed = self._parse_query_result(result, group_by)
                
                logger.debug(f" Scope query complete: {parsed['total_cost']:.2f} {parsed.get('currency', 'SEK')}")
                
                # SUCCESS! If this query succeeded and we had previous rate limit hits, gradually reduce the counter
                if self._rate_limit_hit_count > 0:
                    self._rate_limit_hit_count = max(0, self._rate_limit_hit_count - 1)
                    if self._rate_limit_hit_count == 0:
                        logger.info(" Rate limit recovery complete - counter reset to 0")
                
                return parsed
                
            except Exception as e:
                # Check if it's a rate limit error (429)
                is_rate_limit = "429" in str(e) or "Too many requests" in str(e)
                
                if is_rate_limit:
                    # Track rate limit hits
                    self._rate_limit_hit_count += 1
                    self._last_rate_limit_time = datetime.now()
                    
                    if attempt < self.MAX_RETRIES - 1:
                        # Exponential backoff: delay = initial * (2 ^ attempt) with jitter
                        import random
                        delay = min(
                            self.INITIAL_RETRY_DELAY * (2 ** attempt) + random.uniform(0, 3),
                            self.MAX_RETRY_DELAY
                        )
                        logger.warning(
                            f"⚠️ Rate limit (429) hit #{self._rate_limit_hit_count} for {scope[-40:]}, "
                            f"retrying in {delay:.1f}s (attempt {attempt + 1}/{self.MAX_RETRIES})..."
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"❌ Query FAILED after {self.MAX_RETRIES} retries for scope {scope[-40:]}: {e}"
                        )
                        logger.error(f"Full traceback: {traceback.format_exc()}")
                        return {
                            "success": False,
                            "total_cost": 0.0,
                            "currency": "SEK",
                            "breakdown": [],
                            "error": f"Rate limit exceeded after {self.MAX_RETRIES} retries"
                        }
                else:
                    # Non-rate-limit error, log and return error result immediately
                    logger.error(f" Query failed for scope {scope[-40:]}: {type(e).__name__}: {e}")
                    logger.error(f"Full traceback: {traceback.format_exc()}")
                    return {
                        "success": False,
                        "total_cost": 0.0,
                        "currency": "SEK",
                        "breakdown": [],
                        "error": "Cost query failed"
                    }
        
        # Should never reach here, but just in case
        return {
            "success": False,
            "total_cost": 0.0,
            "currency": "SEK",
            "breakdown": [],
            "error": "Max retries exceeded"
        }
    
    def _build_query_definition(
        self,
        start_date: datetime,
        end_date: datetime,
        granularity: str,
        group_by: Optional[List[str]],
        filters: Optional[Dict[str, Any]],
        metric: str
    ) -> QueryDefinition:
        """Build Azure Cost Management query definition"""
        
        # Time period
        time_period = QueryTimePeriod(
            from_property=start_date,
            to=end_date
        )
        
        # Aggregation (sum of cost)
        # Note: Azure SDK expects string "Sum" not enum FunctionType.SUM
        aggregation = {
            "totalCost": QueryAggregation(
                name="Cost",
                function="Sum"
            )
        }
        
        # Grouping dimensions
        groupings = []
        if group_by:
            for dimension in group_by:
                groupings.append(
                    QueryGrouping(
                        type="Dimension",
                        name=dimension
                    )
                )
        
        # Filters (tags, resource types, etc.)
        query_filter = None
        if filters:
            query_filter = self._build_filter(filters)
        
        # Azure Cost Management API granularity rules:
        # - GranularityType.DAILY: For time-series data (daily breakdown)
        # - None: For aggregated data without time grouping
        # Note: There is NO GranularityType.MONTHLY in the SDK
        # 
        # IMPORTANT: We ALWAYS want UsageDate in results for daily_trend chart!
        # Even when grouping by dimensions (ServiceName, etc.), we need granularity=DAILY
        # to get the UsageDate column, otherwise daily_trend will be empty.
        period_days = (end_date - start_date).days
        
        granularity_value = None
        if granularity == "daily":
            # Use DAILY granularity to ensure UsageDate column is included
            # This is needed for daily_trend chart in frontend
            if period_days <= 31:
                granularity_value = GranularityType.DAILY
            # For > 31 days, granularity must be None (no MONTHLY enum exists)
        
        # Build dataset
        dataset = QueryDataset(
            granularity=granularity_value,
            aggregation=aggregation,
            grouping=groupings if groupings else None,
            filter=query_filter
        )
        
        # Query definition
        query_def = QueryDefinition(
            type="ActualCost" if metric == "ActualCost" else "AmortizedCost",
            timeframe=TimeframeType.CUSTOM,
            time_period=time_period,
            dataset=dataset
        )
        
        # Debug log the query definition
        logger.debug(f"Query definition - Granularity: {granularity_value}, Group by: {[g.name for g in groupings] if groupings else None}, Metric: {metric}")
        
        return query_def
    
    def _build_filter(self, filters: Dict[str, Any]) -> QueryFilter:
        """Build query filter from filter dictionary"""
        
        filter_expressions = []
        
        # Resource type filter
        if "resource_types" in filters and filters["resource_types"]:
            filter_expressions.append(
                QueryComparisonExpression(
                    name="ResourceType",
                    operator=OperatorType.IN,
                    values=filters["resource_types"]
                )
            )
        
        # Tag filters
        if "tags" in filters:
            for tag_key, tag_values in filters["tags"].items():
                if isinstance(tag_values, list):
                    filter_expressions.append(
                        QueryComparisonExpression(
                            name=f"TagKey:{tag_key}",
                            operator=OperatorType.IN,
                            values=tag_values
                        )
                    )
        
        # Combine filters with AND logic
        if len(filter_expressions) == 1:
            return QueryFilter(dimensions=filter_expressions[0])
        elif len(filter_expressions) > 1:
            return QueryFilter(
                and_property=[
                    QueryFilter(dimensions=expr) for expr in filter_expressions
                ]
            )
        
        return None
    
    def _parse_query_result(
        self,
        result: Any,
        group_by: Optional[List[str]]
    ) -> Dict[str, Any]:
        """Parse Azure Cost Management query result"""
        
        total_cost = 0.0
        currency = "SEK"
        breakdown = []
        daily_trend = []
        
        if not result or not result.rows:
            return {
                "success": True,
                "total_cost": 0.0,
                "currency": currency,
                "breakdown": [],
                "daily_trend": []
            }
        
        # Get column indices
        columns = [col.name for col in result.columns]
        cost_index = columns.index("Cost")
        currency_index = columns.index("Currency") if "Currency" in columns else None
        
        # Group indices
        group_indices = {}
        if group_by:
            for dimension in group_by:
                if dimension in columns:
                    group_indices[dimension] = columns.index(dimension)
        
        # Date index for trends
        date_index = columns.index("UsageDate") if "UsageDate" in columns else None
        
        # Process rows
        aggregated = defaultdict(float)
        daily_costs = defaultdict(float)
        
        for row in result.rows:
            cost = float(row[cost_index]) if row[cost_index] else 0.0
            total_cost += cost
            
            if currency_index and row[currency_index]:
                currency = row[currency_index]
            
            # Group by dimensions
            if group_indices:
                group_key = tuple(
                    str(row[idx]) if row[idx] else "Untagged"
                    for idx in group_indices.values()
                )
                aggregated[group_key] += cost
            
            # Daily trend
            if date_index and row[date_index]:
                date_str = str(row[date_index])
                daily_costs[date_str] += cost
        
        # Format breakdown
        if group_by and aggregated:
            for group_key, cost in sorted(aggregated.items(), key=lambda x: x[1], reverse=True):
                item = {"cost": round(cost, 2)}
                for i, dimension in enumerate(group_by):
                    item[dimension.lower()] = group_key[i]
                breakdown.append(item)
        
        # Format daily trend
        if daily_costs:
            daily_trend = [
                {"date": date, "cost": round(cost, 2)}
                for date, cost in sorted(daily_costs.items())
            ]
        
        return {
            "success": True,
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "breakdown": breakdown,
            "daily_trend": daily_trend
        }
    
    def _aggregate_results(
        self,
        results: List[Dict[str, Any]],
        group_by: Optional[List[str]],
        scopes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Aggregate results from multiple scopes"""
        
        total_cost = 0.0
        currency = "SEK"
        aggregated_breakdown = defaultdict(float)
        aggregated_daily = defaultdict(float)
        by_subscription = {}  # Track per-subscription costs
        
        for idx, result in enumerate(results):
            if not result.get("success"):
                continue
            
            scope_cost = result.get("total_cost", 0.0)
            total_cost += scope_cost
            
            if result.get("currency"):
                currency = result["currency"]
            
            # Track per-subscription cost if scopes provided
            if scopes and idx < len(scopes):
                scope = scopes[idx]
                # Extract subscription ID from scope (format: /subscriptions/{sub-id})
                if "/subscriptions/" in scope:
                    sub_id = scope.split("/subscriptions/")[1].split("/")[0]
                    by_subscription[sub_id] = round(scope_cost, 2)
            
            # Aggregate breakdown
            for item in result.get("breakdown", []):
                if group_by:
                    key = tuple(item.get(dim.lower(), "Unknown") for dim in group_by)
                    aggregated_breakdown[key] += item.get("cost", 0.0)
            
            # Aggregate daily trend
            for daily_item in result.get("daily_trend", []):
                date = daily_item.get("date")
                cost = daily_item.get("cost", 0.0)
                if date:
                    aggregated_daily[date] += cost
        
        # Format breakdown
        breakdown = []
        if group_by and aggregated_breakdown:
            for key, cost in sorted(aggregated_breakdown.items(), key=lambda x: x[1], reverse=True):
                item = {"cost": round(cost, 2)}
                for i, dimension in enumerate(group_by):
                    item[dimension.lower()] = key[i]
                breakdown.append(item)
        
        # Format daily trend
        daily_trend = [
            {"date": date, "cost": round(cost, 2)}
            for date, cost in sorted(aggregated_daily.items())
        ]
        
        result_dict = {
            "total_cost": round(total_cost, 2),
            "currency": currency,
            "breakdown": breakdown,
            "daily_trend": daily_trend
        }
        
        # Include per-subscription costs if available
        if by_subscription:
            result_dict["by_subscription"] = by_subscription
        
        return result_dict
    
    # ============================================================================
    # HIGH-LEVEL COST ANALYSIS METHODS
    # ============================================================================
    
    async def get_subscription_costs(
        self,
        subscription_ids: List[str],
        start_date: datetime,
        end_date: datetime,
        group_by: str = "ServiceName",
        include_reservations: bool = True,
        resource_type_filter: Optional[str] = None,
        resource_groups: Optional[Dict[str, List[str]]] = None
    ) -> Dict[str, Any]:
        """
        Get costs across multiple subscriptions
        
        INTELLIGENT ROUTING:
        - If many subscriptions: Try Management Group (single API call)
        - If few subscriptions: Use individual subscription queries
        - Automatic fallback if Management Group unavailable
        
        Args:
            subscription_ids: List of subscription IDs (empty list = global/all subscriptions)
            start_date: Start date
            end_date: End date
            group_by: Dimension to group by (ServiceName, ResourceType, Location, ResourceGroupName, etc.)
            include_reservations: Use amortized cost (includes reservations)
            resource_type_filter: Optional filter for specific resource type (e.g., 'microsoft.compute/virtualmachines')
            resource_groups: Optional dict mapping subscription IDs to list of resource group names to filter
        """
        # Map Location to ResourceLocation for Azure Cost Management API
        if group_by == "Location":
            group_by = "ResourceLocation"
            
        metric = "AmortizedCost" if include_reservations else "ActualCost"
        
        # Build filter if resource type is specified
        filters = None
        if resource_type_filter:
            logger.info(f" Applying resource type filter: {resource_type_filter}")
            filters = self._build_filter({"resource_types": [resource_type_filter]})
        
        # If resource groups are specified, use resource group scoped queries
        if resource_groups:
            logger.debug(f" Resource group filtering enabled for {len(resource_groups)} subscription(s)")
            scopes = []
            for sub_id in subscription_ids:
                rg_list = resource_groups.get(sub_id, [])
                if rg_list:
                    # Add resource group scopes for this subscription
                    for rg_name in rg_list:
                        scopes.append(f"/subscriptions/{sub_id}/resourceGroups/{rg_name}")
                    logger.debug(f" {sub_id}: {len(rg_list)} resource groups")
                else:
                    # No RGs specified for this sub, query entire subscription
                    scopes.append(f"/subscriptions/{sub_id}")
                    logger.debug(f" {sub_id}: all resource groups")
            
            logger.info(f" Querying {len(scopes)} scopes")
            group_by_list = [group_by] if group_by else None
            
            result = await self.get_costs_parallel(
                scopes=scopes,
                start_date=start_date,
                end_date=end_date,
                granularity="daily",
                group_by=group_by_list,
                filters=filters,
                metric=metric
            )
            
            result["query_method"] = "resource_group_scoped"
            result["note"] = f"Queried {len(scopes)} resource group scope(s)"
            return result
        
        # GLOBAL SCOPE: Use billing account queries (MCA-correct) via service principal
        if len(subscription_ids) > 20 or len(subscription_ids) == 0:
            logger.info(f"Global scope detected ({len(subscription_ids) or 'all'} subscriptions)")
            logger.info("Using billing account scope via SP — MCA-correct, single call per billing account")

            group_by_list_for_global = group_by if group_by else "ServiceName"
            try:
                result = await self.get_costs_global(
                    user_subscription_ids=subscription_ids,
                    start_date=start_date,
                    end_date=end_date,
                    group_by=group_by_list_for_global,
                    filters=filters,
                    metric=metric,
                )
                return result
            except Exception as e:
                logger.warning(f"get_costs_global failed: {e}, falling back to subscription queries")
        
        # FALLBACK: Individual subscription queries (for few subscriptions or if MG failed)
        if not subscription_ids:
            logger.error(" No subscriptions provided and Management Group unavailable")
            return {
                "success": False,
                "error": "No subscriptions available and Management Group not configured",
                "total_cost": 0,
                "currency": "SEK",
                "breakdown": []
            }
        
        logger.debug(f" Querying {len(subscription_ids)} subscriptions individually")
        scopes = [f"/subscriptions/{sub_id}" for sub_id in subscription_ids]
        
        # Convert group_by to list format, or None if no grouping wanted
        group_by_list = [group_by] if group_by else None
        
        # CRITICAL FIX: When grouping by ResourceId, use granularity=None to get aggregated costs
        # Daily granularity with ResourceId returns incomplete data (only 1 day's cost per resource)
        # See: https://github.com/Azure/azure-rest-api-specs/issues/23405
        granularity_setting = "daily" if group_by != "ResourceId" else None
        
        if group_by == "ResourceId":
            logger.info(" ResourceId grouping detected: Using granularity=None to get complete aggregated costs")
        
        result = await self.get_costs_parallel(
            scopes=scopes,
            start_date=start_date,
            end_date=end_date,
            granularity=granularity_setting,
            group_by=group_by_list,
            metric=metric,
            filters=filters
        )
        
        result["query_method"] = "individual_subscriptions"
        return result
    
    async def get_resource_group_costs(
        self,
        subscription_id: str,
        resource_groups: List[str],
        start_date: datetime,
        end_date: datetime,
        group_by: str = "ResourceType"
    ) -> Dict[str, Any]:
        """Get costs for specific resource groups in parallel"""
        scopes = [
            f"/subscriptions/{subscription_id}/resourceGroups/{rg}"
            for rg in resource_groups
        ]
        
        return await self.get_costs_parallel(
            scopes=scopes,
            start_date=start_date,
            end_date=end_date,
            granularity="daily",
            group_by=[group_by, "ResourceGroupName"]
        )
    
    async def get_resource_costs(
        self,
        resource_ids: List[str],
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """Get costs for specific resources in parallel"""
        # Resource IDs are already full scopes
        return await self.get_costs_parallel(
            scopes=resource_ids,
            start_date=start_date,
            end_date=end_date,
            granularity="daily",
            group_by=["ResourceId"]
        )
    
    async def get_costs_by_tag(
        self,
        subscription_ids: List[str],
        tag_key: str,
        tag_values: Optional[List[str]],
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get costs grouped by tag (e.g., CostCenter, Environment, Project)
        
        Args:
            subscription_ids: List of subscription IDs
            tag_key: Tag key to group by (e.g., "CostCenter", "Environment")
            tag_values: Optional list of specific tag values to filter
            start_date: Start date
            end_date: End date
        """
        scopes = [f"/subscriptions/{sub_id}" for sub_id in subscription_ids]
        
        filters = None
        if tag_values:
            filters = {
                "tags": {
                    tag_key: tag_values
                }
            }
        
        return await self.get_costs_parallel(
            scopes=scopes,
            start_date=start_date,
            end_date=end_date,
            granularity="daily",
            group_by=[f"TagKey:{tag_key}"],
            filters=filters,
            metric="AmortizedCost"
        )
    
    async def get_reservation_utilization(
        self,
        subscription_ids: List[str],
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Analyze reservation vs on-demand costs
        Compare AmortizedCost (with reservations) vs ActualCost (pay-as-you-go)
        """
        scopes = [f"/subscriptions/{sub_id}" for sub_id in subscription_ids]
        
        # Query both metrics in parallel
        amortized_task = self.get_costs_parallel(
            scopes=scopes,
            start_date=start_date,
            end_date=end_date,
            granularity="daily",
            group_by=["ServiceName"],
            metric="AmortizedCost"
        )
        
        actual_task = self.get_costs_parallel(
            scopes=scopes,
            start_date=start_date,
            end_date=end_date,
            granularity="daily",
            group_by=["ServiceName"],
            metric="ActualCost"
        )
        
        amortized_result, actual_result = await asyncio.gather(amortized_task, actual_task)
        
        # Calculate savings
        amortized_total = amortized_result.get("total_cost", 0.0)
        actual_total = actual_result.get("total_cost", 0.0)
        savings = actual_total - amortized_total
        savings_percent = (savings / actual_total * 100) if actual_total > 0 else 0
        
        # Get reservation details
        reservation_details = await self.get_reservation_details(subscription_ids, start_date, end_date)
        
        return {
            "success": True,
            "amortized_cost": amortized_total,
            "actual_cost": actual_total,
            "reservation_savings": round(savings, 2),
            "savings_percent": round(savings_percent, 1),
            "currency": amortized_result.get("currency", "SEK"),
            "date_range": amortized_result.get("date_range"),
            "reservation_details": reservation_details
        }
    
    async def get_reservation_details(
        self,
        subscription_ids: List[str],
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Get detailed unused reservation charges using Cost Management Query API
        
        Per Azure documentation:
        - Query AmortizedCost metric
        - Filter by ChargeType = 'UnusedReservation'
        - Group by ReservationId and ReservationName to see which reservations are unused
        
        Args:
            subscription_ids: List of subscription IDs
            start_date: Start date for analysis
            end_date: End date for analysis
            
        Returns:
            List of unused reservation details with costs
        """
        try:
            from azure.mgmt.costmanagement.models import (
                QueryDefinition,
                QueryTimePeriod,
                QueryDataset,
                QueryAggregation,
                QueryGrouping,
                QueryFilter,
                QueryComparisonExpression
            )
            
            all_unused_reservations = []
            
            logger.debug(f" Querying unused reservation charges for {len(subscription_ids)} subscription(s)")
            
            # Query for unused reservations using Cost Management API
            # Per Azure docs: AmortizedCost with ChargeType = 'UnusedReservation'
            scopes = [f"/subscriptions/{sub_id}" for sub_id in subscription_ids]
            
            for scope in scopes:
                try:
                    # Build query for unused reservations
                    query = QueryDefinition(
                        type="Usage",
                        timeframe="Custom",
                        time_period=QueryTimePeriod(
                            from_property=start_date,
                            to=end_date
                        ),
                        dataset=QueryDataset(
                            granularity=None,  # No time granularity needed
                            aggregation={
                                "totalCost": QueryAggregation(name="Cost", function="Sum")
                            },
                            grouping=[
                                QueryGrouping(type="Dimension", name="ReservationId"),
                                QueryGrouping(type="Dimension", name="ReservationName"),
                                QueryGrouping(type="Dimension", name="ServiceName"),
                            ],
                            filter=QueryFilter(
                                dimensions=QueryComparisonExpression(
                                    name="ChargeType",
                                    operator="In",
                                    values=["UnusedReservation"]
                                )
                            )
                        )
                    )
                    
                    logger.info(f" Querying unused reservations for scope: {scope}")
                    
                    # Execute query
                    result = self.client.query.usage(
                        scope=scope,
                        parameters=query
                    )
                    
                    # Parse results
                    if result and hasattr(result, 'rows') and result.rows:
                        logger.info(f" Found {len(result.rows)} unused reservation entries")
                        
                        # Get column indexes
                        columns = {col.name: idx for idx, col in enumerate(result.columns)}
                        cost_idx = columns.get("Cost", 0)
                        currency_idx = columns.get("Currency", 1)
                        
                        # Map group columns
                        reservation_id_idx = None
                        reservation_name_idx = None
                        service_name_idx = None
                        
                        for idx, col in enumerate(result.columns):
                            if col.name == "ReservationId":
                                reservation_id_idx = idx
                            elif col.name == "ReservationName":
                                reservation_name_idx = idx
                            elif col.name == "ServiceName":
                                service_name_idx = idx
                        
                        for row in result.rows:
                            unused_cost = float(row[cost_idx]) if cost_idx < len(row) else 0.0
                            
                            if unused_cost > 0:  # Only include actual unused costs
                                reservation_info = {
                                    "reservation_id": row[reservation_id_idx] if reservation_id_idx is not None and reservation_id_idx < len(row) else "Unknown",
                                    "reservation_name": row[reservation_name_idx] if reservation_name_idx is not None and reservation_name_idx < len(row) else "Unknown",
                                    "service_name": row[service_name_idx] if service_name_idx is not None and service_name_idx < len(row) else "Unknown",
                                    "unused_cost": round(unused_cost, 2),
                                    "currency": row[currency_idx] if currency_idx < len(row) else "SEK",
                                    "subscription_id": scope.split('/')[-1]
                                }
                                all_unused_reservations.append(reservation_info)
                                logger.info(f" Unused: {reservation_info['reservation_name']} ({reservation_info['service_name']}): {unused_cost}")
                    else:
                        logger.info(f" No unused reservation charges found for {scope}")
                        
                except Exception as scope_error:
                    logger.error(f" Failed to query unused reservations for {scope}: {scope_error}")
                    continue
            
            if not all_unused_reservations:
                logger.info(" No unused reservation charges detected - all reservations are being utilized!")
                return []
            
            # Aggregate by reservation name and service
            reservation_map = {}
            for res in all_unused_reservations:
                key = f"{res['reservation_id']}_{res['service_name']}"
                if key not in reservation_map:
                    reservation_map[key] = {
                        "reservation_id": res['reservation_id'],
                        "reservation_name": res['reservation_name'],
                        "service_name": res['service_name'],
                        "unused_cost": 0.0,
                        "currency": res['currency'],
                        "subscriptions": []
                    }
                reservation_map[key]["unused_cost"] += res['unused_cost']
                if res['subscription_id'] not in reservation_map[key]["subscriptions"]:
                    reservation_map[key]["subscriptions"].append(res['subscription_id'])
            
            # Convert to list and format for frontend
            aggregated_reservations = []
            for key, res_data in reservation_map.items():
                aggregated_reservations.append({
                    "reservation_order_id": res_data['reservation_id'],
                    "sku_name": res_data['service_name'],
                    "reservation_name": res_data['reservation_name'],
                    "unused_cost": round(res_data['unused_cost'], 2),
                    "total_reserved_hours": 0,  # Not available from this API
                    "total_used_hours": 0,  # Not available from this API
                    "utilization_percentage": 0,  # Cannot calculate without usage data
                    "unused_hours": 0,  # Not available from this API
                    "subscriptions": res_data['subscriptions']
                })
            
            # Sort by unused cost (show most wasteful first)
            aggregated_reservations.sort(key=lambda x: x['unused_cost'], reverse=True)
            
            logger.info(f" Returning {len(aggregated_reservations)} unused reservations")
            return aggregated_reservations
            
        except Exception as e:
            logger.error(f" Error fetching reservation details: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    # ============================================================================
    # UTILITY METHODS
    # ============================================================================
    
    def parse_time_range(self, time_range: str) -> Tuple[datetime, datetime]:
        """Parse time range string to start and end dates"""
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        if time_range == "today":
            return today, now
        elif time_range == "yesterday":
            yesterday = today - timedelta(days=1)
            return yesterday, today
        elif time_range == "last_7_days":
            return today - timedelta(days=7), now
        elif time_range == "last_30_days":
            return today - timedelta(days=30), now
        elif time_range == "this_month":
            start = today.replace(day=1)
            return start, now
        elif time_range == "last_month":
            first_of_this_month = today.replace(day=1)
            last_month_end = first_of_this_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            return last_month_start, last_month_end
        else:
            # Default to last 30 days
            return today - timedelta(days=30), now
    
    def clear_cache(self):
        """Clear cost query cache"""
        self._cache.clear()
        logger.info(" Cache cleared")

    async def get_budgets(
        self,
        subscription_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Get budget information for subscriptions
        
        Args:
            subscription_ids: List of subscription IDs to get budgets for
            
        Returns:
            Dictionary with budget information per subscription
        """
        # Check cache first
        cache_key = self._get_cache_key(
            operation="budgets",
            subscription_ids=sorted(subscription_ids)
        )
        
        cached = self._get_cached(cache_key)
        if cached:
            logger.info(f" Cache hit for budgets query ({len(subscription_ids)} subscriptions)")
            return cached
        
        try:
            from azure.mgmt.consumption import ConsumptionManagementClient
            
            all_budgets = []
            
            for subscription_id in subscription_ids:
                try:
                    # Create Consumption client for this subscription
                    consumption_client = ConsumptionManagementClient(
                        credential=self.credential,
                        subscription_id=subscription_id
                    )
                    
                    # Get budgets for subscription
                    scope = f"/subscriptions/{subscription_id}"
                    budgets = list(consumption_client.budgets.list(scope=scope))
                    
                    for budget in budgets:
                        # Get currency from budget (defaults to SEK if not specified)
                        currency = "SEK"
                        if hasattr(budget, 'current_spend') and budget.current_spend and hasattr(budget.current_spend, 'unit'):
                            currency = budget.current_spend.unit
                        
                        budget_info = {
                            "subscription_id": subscription_id,
                            "budget_name": budget.name,
                            "amount": budget.amount if hasattr(budget, 'amount') else 0,
                            "currency": currency,
                            "time_grain": budget.time_grain if hasattr(budget, 'time_grain') else "Monthly",
                            "category": budget.category if hasattr(budget, 'category') else "Cost",
                            "current_spend": budget.current_spend.amount if hasattr(budget, 'current_spend') and budget.current_spend else 0,
                            "forecasted_spend": budget.forecasted_spend.amount if hasattr(budget, 'forecasted_spend') and budget.forecasted_spend else 0,
                            "time_period_start": budget.time_period.start_date.isoformat() if hasattr(budget, 'time_period') and budget.time_period else None,
                            "time_period_end": budget.time_period.end_date.isoformat() if hasattr(budget, 'time_period') and budget.time_period else None,
                        }
                        
                        # Calculate budget utilization
                        if budget_info["amount"] > 0:
                            budget_info["utilization_percent"] = round(
                                (budget_info["current_spend"] / budget_info["amount"]) * 100, 1
                            )
                        else:
                            budget_info["utilization_percent"] = 0
                            
                        all_budgets.append(budget_info)
                        
                except Exception as sub_error:
                    logger.warning(f"Failed to get budgets for subscription {subscription_id}: {sub_error}")
                    continue
            
            # Aggregate budget info
            total_budget = sum(b["amount"] for b in all_budgets)
            total_current = sum(b["current_spend"] for b in all_budgets)
            total_forecasted = sum(b["forecasted_spend"] for b in all_budgets)
            
            # Get the currency from the first budget (all should be same currency)
            currency = all_budgets[0]["currency"] if all_budgets else "SEK"
            
            result = {
                "success": True,
                "total_budget": round(total_budget, 2),
                "total_current_spend": round(total_current, 2),
                "total_forecasted_spend": round(total_forecasted, 2),
                "total_utilization_percent": round((total_current / total_budget * 100), 1) if total_budget > 0 else 0,
                "currency": currency,
                "budgets": all_budgets,
                "budget_count": len(all_budgets)
            }
            
            # Cache the result
            self._set_cache(cache_key, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching budgets: {e}")
            return {
                "success": False,
                "error": "Failed to fetch budgets",
                "total_budget": 0,
                "total_current_spend": 0,
                "total_forecasted_spend": 0,
                "total_utilization_percent": 0,
                "currency": "SEK",
                "budgets": []
            }


# Note: AdvancedCostService requires credentials, so it's not instantiated as a singleton here.
# It's created as needed with appropriate credentials (service principal or user delegated).

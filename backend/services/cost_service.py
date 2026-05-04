"""
Azure Cost Management Service

Provides intelligent cost analysis with:
- Multi-scope support (subscription, resource group, resource)
- Time-based trend analysis
- Cost deviation detection
- Service/resource breakdown
- Comparative analysis
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
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
    TimeframeType
)

logger = logging.getLogger(__name__)


class CostAnalysisService:
    """Service for intelligent Azure cost analysis"""
    
    def __init__(self, credential):
        self.credential = credential
        self.client = CostManagementClient(credential)
    
    async def analyze_costs(
        self,
        scope: str,
        time_range: str = "last_30_days",
        granularity: str = "daily",
        resource_group: Optional[str] = None,
        resource_ids: Optional[List[str]] = None,
        group_by: str = "service",
        custom_start: Optional[str] = None,
        custom_end: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Intelligent cost analysis with trends and insights
        
        Args:
            scope: Azure scope (e.g., /subscriptions/{id})
            time_range: Time period for analysis (ignored if custom_start/custom_end provided)
            granularity: daily, weekly, monthly
            resource_group: Optional resource group filter
            resource_ids: Optional specific resource IDs (will query RG and filter)
            group_by: Grouping dimension (service, resource_group, resource)
            custom_start: Custom start date (YYYY-MM-DD) - overrides time_range
            custom_end: Custom end date (YYYY-MM-DD) - overrides time_range
        
        Returns:
            Comprehensive cost analysis with trends and insights
        """
        try:
            # Store resource_ids for filtering in breakdown methods
            self._filter_resource_ids = resource_ids
            
            # Get date ranges - use custom dates if provided, otherwise parse time_range
            if custom_start and custom_end:
                # Parse custom dates from string format (YYYY-MM-DD)
                from datetime import datetime
                start_date = datetime.fromisoformat(custom_start)
                end_date = datetime.fromisoformat(custom_end)
                logger.info(f"Using custom date range: {custom_start} to {custom_end}")
            else:
                # Parse time range
                start_date, end_date = self._parse_time_range(time_range)
            
            # Build appropriate scope (resource IDs will query at RG level)
            full_scope, query_resource_group = self._build_scope(scope, resource_group, resource_ids)
            
            # For resource-scoped queries, skip daily breakdown query (use breakdown only)
            # This avoids API limitations with granularity for resource-level scopes
            if resource_ids:
                logger.info(f" Resource-scoped query: Skipping daily breakdown, will use resource breakdown only")
                current_costs = {"total_cost": 0, "currency": "USD", "daily_breakdown": [], "num_days": (end_date - start_date).days + 1}
                previous_costs = {"total_cost": 0, "currency": "USD", "daily_breakdown": [], "num_days": 0}
            else:
                # Get current period costs (time-series)
                current_costs = await self._query_costs(
                    full_scope, start_date, end_date, granularity
                )
            
            # Get previous period for comparison
            # For monthly comparisons, go back by calendar months to handle different month lengths
            # E.g., Sept 1-30 (30 days) -> Aug 1-31 (31 days), not Aug 2-31 (30 days)
            
            # Calculate previous comparison period
            # For "this_month" or "this_year" queries, compare against the FULL previous period
            # For other queries, go back the same number of days
            is_month_to_date = (time_range == "this_month" and start_date.day == 1)
            is_year_to_date = (time_range == "this_year" and start_date.month == 1 and start_date.day == 1)
            is_full_month = (start_date.day == 1 and (end_date + timedelta(days=1)).day == 1)
            
            if is_month_to_date:
                # Comparing "this month" (partial) against FULL previous month
                if start_date.month == 1:
                    prev_start = start_date.replace(year=start_date.year - 1, month=12, day=1)
                    prev_end = start_date - timedelta(days=1)  # Dec 31
                else:
                    prev_start = start_date.replace(month=start_date.month - 1, day=1)
                    # Last day of previous month
                    prev_end = start_date - timedelta(days=1)
                    
            elif is_year_to_date:
                # Comparing "this year" (partial) against FULL previous year
                prev_start = start_date.replace(year=start_date.year - 1, month=1, day=1)
                prev_end = start_date.replace(year=start_date.year - 1, month=12, day=31)
                
            elif is_full_month:
                # Full month comparison: compare against previous full month
                if start_date.month == 1:
                    prev_start = start_date.replace(year=start_date.year - 1, month=12, day=1)
                else:
                    prev_start = start_date.replace(month=start_date.month - 1, day=1)
                # Last day of previous month
                prev_end = start_date - timedelta(days=1)
                
            else:
                # For non-monthly periods, go back same number of days
                period_duration = (end_date - start_date) + timedelta(days=1)
                prev_end = start_date - timedelta(days=1)
                prev_start = prev_end - period_duration + timedelta(days=1)
            
            current_days = (end_date - start_date).days + 1
            prev_days = (prev_end - prev_start).days + 1
            
            logger.info(f" Current period: {start_date.date()} to {end_date.date()} ({current_days} days)")
            logger.info(f" Previous period: {prev_start.date()} to {prev_end.date()} ({prev_days} days)")
            
            # Query previous period (skip for resource-scoped queries)
            if not resource_ids:
                previous_costs = await self._query_costs(
                    full_scope, prev_start, prev_end, granularity
                )
            else:
                # For resource-scoped, initialize empty - will be filled from breakdown
                previous_costs = {"total_cost": 0, "currency": "USD", "daily_breakdown": [], "num_days": prev_days}
            
            # Get cost breakdown based on group_by parameter
            # For resource_ids filter, always group by ResourceId first
            if resource_ids:
                # Resource-scoped: Group by ResourceId and filter
                logger.info(f" Resource-scoped query: Grouping by ResourceId and filtering")
                primary_breakdown = await self._get_resource_breakdown(
                    full_scope, start_date, end_date
                )
                previous_primary_breakdown = await self._get_resource_breakdown(
                    full_scope, prev_start, prev_end
                )
                
                # Filter to specific resources
                primary_breakdown = self._filter_to_resources(primary_breakdown, resource_ids)
                previous_primary_breakdown = self._filter_to_resources(previous_primary_breakdown, resource_ids)
                
                breakdown_type = "resource"
                logger.info(f" Filtered to {len(primary_breakdown)} resource cost entries")
                
            elif group_by == "resource_group":
                primary_breakdown = await self._get_resource_group_breakdown(
                    full_scope, start_date, end_date
                )
                previous_primary_breakdown = await self._get_resource_group_breakdown(
                    full_scope, prev_start, prev_end
                )
                breakdown_type = "resource_group"
            elif group_by == "resource":
                primary_breakdown = await self._get_resource_breakdown(
                    full_scope, start_date, end_date
                )
                previous_primary_breakdown = await self._get_resource_breakdown(
                    full_scope, prev_start, prev_end
                )
                breakdown_type = "resource"
            else:  # Default to service
                primary_breakdown = await self._get_service_breakdown(
                    full_scope, start_date, end_date
                )
                previous_primary_breakdown = await self._get_service_breakdown(
                    full_scope, prev_start, prev_end
                )
                breakdown_type = "service"
            
            # Get service breakdown for cost calculation fallback (skip for resource-scoped queries)
            if breakdown_type != "service" and not resource_ids:
                service_breakdown = await self._get_service_breakdown(
                    full_scope, start_date, end_date
                )
                previous_service_breakdown = await self._get_service_breakdown(
                    full_scope, prev_start, prev_end
                )
            elif resource_ids:
                # For resource-scoped queries, service breakdown would show RG-level data (misleading)
                service_breakdown = []
                previous_service_breakdown = []
            else:
                service_breakdown = primary_breakdown
                previous_service_breakdown = previous_primary_breakdown
            
            logger.info(f"=== Cost Calculation Summary ===")
            logger.info(f"Current Period ({start_date.date()} to {end_date.date()}):")
            logger.info(f"  - Daily breakdown total: {current_costs.get('total_cost', 0):.2f}")
            logger.info(f"  - {breakdown_type.replace('_', ' ').title()}s found: {len(primary_breakdown)}")
            
            logger.info(f"Previous Period ({prev_start.date()} to {prev_end.date()}):")
            logger.info(f"  - Daily breakdown total: {previous_costs.get('total_cost', 0):.2f}")
            logger.info(f"  - {breakdown_type.replace('_', ' ').title()}s found: {len(previous_primary_breakdown)}")
            
            # For resource-scoped queries, recalculate totals from filtered breakdown
            if resource_ids and primary_breakdown:
                resource_total = sum(item.get("cost", 0) for item in primary_breakdown)
                if resource_total > 0:
                    logger.info(f" Resource-scoped: Recalculating from filtered breakdown: {resource_total:.2f}")
                    current_costs["total_cost"] = round(resource_total, 2)
                    if primary_breakdown:
                        current_costs["currency"] = primary_breakdown[0].get("currency", "USD")
                    current_costs["num_days"] = (end_date - start_date).days + 1
                    
            if resource_ids and previous_primary_breakdown:
                prev_resource_total = sum(item.get("cost", 0) for item in previous_primary_breakdown)
                if prev_resource_total > 0:
                    logger.info(f" Resource-scoped previous: Recalculating from filtered breakdown: {prev_resource_total:.2f}")
                    previous_costs["total_cost"] = round(prev_resource_total, 2)
                    if previous_primary_breakdown:
                        previous_costs["currency"] = previous_primary_breakdown[0].get("currency", "USD")
                    previous_costs["num_days"] = (prev_end - prev_start).days + 1
            
            # If daily breakdown failed, use service breakdown aggregation (for non-resource queries)
            if not resource_ids and current_costs.get("total_cost", 0) == 0 and service_breakdown:
                service_total = sum(s.get("cost", 0) for s in service_breakdown)
                if service_total > 0:
                    logger.info(f" Daily breakdown empty, using service breakdown: {service_total:.2f} SEK")
                    current_costs["total_cost"] = round(service_total, 2)
                    current_costs["currency"] = service_breakdown[0].get("currency", "USD")
                    current_costs["num_days"] = (end_date - start_date).days + 1
            elif current_costs.get("total_cost", 0) > 0 and service_breakdown:
                # Compare daily vs service breakdown for analysis
                service_total = sum(s.get("cost", 0) for s in service_breakdown)
                daily_total = current_costs.get("total_cost", 0)
                diff = abs(service_total - daily_total)
                if diff > 1.0:  # More than 1 currency unit difference
                    diff_percent = (diff / max(service_total, daily_total)) * 100
                    logger.warning(
                        f"📊 Daily vs Service breakdown difference: {diff:.2f} ({diff_percent:.2f}%) - "
                        f"Daily: {daily_total:.2f}, Service sum: {service_total:.2f}"
                    )
            
            # Same for previous period
            if not resource_ids and previous_costs.get("total_cost", 0) == 0 and previous_service_breakdown:
                prev_service_total = sum(s.get("cost", 0) for s in previous_service_breakdown)
                if prev_service_total > 0:
                    logger.info(f" Previous period using service breakdown: {prev_service_total:.2f} SEK")
                    previous_costs["total_cost"] = round(prev_service_total, 2)
                    previous_costs["currency"] = previous_service_breakdown[0].get("currency", "USD")
                    previous_costs["num_days"] = (prev_end - prev_start).days + 1
            elif previous_costs.get("total_cost", 0) > 0 and previous_service_breakdown:
                # Compare previous period as well
                prev_service_total = sum(s.get("cost", 0) for s in previous_service_breakdown)
                prev_daily_total = previous_costs.get("total_cost", 0)
                prev_diff = abs(prev_service_total - prev_daily_total)
                if prev_diff > 1.0:
                    prev_diff_percent = (prev_diff / max(prev_service_total, prev_daily_total)) * 100
                    logger.warning(
                        f"📊 Previous period difference: {prev_diff:.2f} ({prev_diff_percent:.2f}%) - "
                        f"Daily: {prev_daily_total:.2f}, Service sum: {prev_service_total:.2f}"
                    )
            
            # Now calculate analysis with correct totals for both periods
            analysis = self._analyze_cost_trends(
                current_costs, previous_costs, granularity
            )
            
            # Add date metadata to previous_period for comparison_summary
            previous_costs["start_date"] = prev_start.strftime("%Y-%m-%d")
            previous_costs["end_date"] = prev_end.strftime("%Y-%m-%d")
            
            # Build return with appropriate breakdown field name
            result = {
                "success": True,
                "scope": full_scope,
                "time_period": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                    "granularity": granularity
                },
                "current_period": current_costs,
                "previous_period": previous_costs,
                "analysis": analysis,
                "query_type": "cost_analysis"
            }
            
            # Add resource scope metadata if filtering to specific resources
            if resource_ids:
                result["query_scope"] = "resource"
                result["resource_id"] = resource_ids[0]
                result["resource_name"] = resource_ids[0].split('/')[-1] if '/' in resource_ids[0] else resource_ids[0]
                if query_resource_group:
                    result["resource_group_name"] = query_resource_group
            
            # Add breakdown based on group_by parameter
            if breakdown_type == "resource_group":
                result["resource_group_breakdown"] = primary_breakdown
                result["service_breakdown"] = service_breakdown  # Still include for reference
            elif breakdown_type == "resource":
                result["resource_breakdown"] = primary_breakdown
                # For resource-scoped queries, omit service breakdown (it shows RG-level data)
                # Only include if NOT filtering to specific resources
                if not resource_ids:
                    result["service_breakdown"] = service_breakdown
            else:
                result["service_breakdown"] = service_breakdown
            
            return result
            
        except Exception as e:
            logger.error(f"Cost analysis failed: {e}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            raise
    
    def _parse_time_range(self, time_range: str) -> tuple[datetime, datetime]:
        """Parse time range string to start/end dates
        
        Supports:
        - Relative: today, yesterday, this_month, last_month, last_7_days, last_30_days, last_90_days, this_year
        - Specific months: january, february, march, april, may, june, july, august, september, october, november, december
        - Month with year: "october 2024", "jan 2025"
        """
        # Use yesterday as default end date since today's costs aren't always finalized
        from utils.timezone_utils import now_cet
        today = now_cet().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        end_date = today - timedelta(days=1)  # Yesterday (default for most ranges)
        time_range_lower = time_range.lower().strip()
        
        # Predefined relative ranges
        if time_range_lower == "today":
            start_date = today
            end_date = today  # Include today for explicit "today" queries
        elif time_range_lower == "yesterday":
            start_date = end_date
        elif time_range_lower == "last_7_days":
            start_date = end_date - timedelta(days=6)  # 7 days including end_date
        elif time_range_lower == "last_30_days":
            start_date = end_date - timedelta(days=29)  # 30 days including end_date
        elif time_range_lower == "last_90_days":
            start_date = end_date - timedelta(days=89)  # 90 days including end_date
        elif time_range_lower == "this_month":
            # For "this month", include today to get most current data available
            # Azure Cost Management API updates throughout the day
            start_date = today.replace(day=1)
            end_date = today  # Include today for current month queries
        elif time_range_lower == "last_month":
            first_day_this_month = today.replace(day=1)  # Use today, not end_date
            last_day_last_month = first_day_this_month - timedelta(days=1)
            start_date = last_day_last_month.replace(day=1)
            end_date = last_day_last_month
        elif time_range_lower == "this_year":
            # For "this year", include today to get most current data
            start_date = today.replace(month=1, day=1)
            end_date = today  # Include today for current year queries
        else:
            # Try to parse specific month names (with optional year)
            month_names = {
                'january': 1, 'jan': 1,
                'february': 2, 'feb': 2,
                'march': 3, 'mar': 3,
                'april': 4, 'apr': 4,
                'may': 5,
                'june': 6, 'jun': 6,
                'july': 7, 'jul': 7,
                'august': 8, 'aug': 8,
                'september': 9, 'sep': 9, 'sept': 9,
                'october': 10, 'oct': 10,
                'november': 11, 'nov': 11,
                'december': 12, 'dec': 12
            }
            
            parsed_month = None
            parsed_year = None
            
            # Try "month year" format (e.g., "october 2024")
            parts = time_range_lower.split()
            if len(parts) >= 1:
                month_part = parts[0]
                if month_part in month_names:
                    parsed_month = month_names[month_part]
                    # Check if year is provided
                    if len(parts) >= 2 and parts[1].isdigit():
                        parsed_year = int(parts[1])
            
            if parsed_month:
                # Use current year if not specified
                target_year = parsed_year if parsed_year else today.year
                
                # If asking for a future month in current year, assume previous year
                if not parsed_year and parsed_month > today.month:
                    target_year = today.year - 1
                
                # Calculate first and last day of the target month
                start_date = datetime(target_year, parsed_month, 1)
                
                # Get last day of month by going to next month's first day, then back one day
                if parsed_month == 12:
                    next_month = datetime(target_year + 1, 1, 1)
                else:
                    next_month = datetime(target_year, parsed_month + 1, 1)
                end_date = next_month - timedelta(days=1)
            else:
                # Default to last 30 days
                start_date = end_date - timedelta(days=29)
        
        return start_date, end_date
    
    def _build_scope(
        self,
        base_scope: str,
        resource_group: Optional[str],
        resource_ids: Optional[List[str]]
    ) -> tuple[str, Optional[str]]:
        """
        Build appropriate Azure scope for cost query
        
        For resource-scoped queries, returns resource group scope to query at RG level
        and filter results to specific resources (Azure Cost Management limitation)
        
        Returns:
            Tuple of (scope, resource_group_name)
        """
        if resource_ids:
            # Extract resource group from resource ID
            # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/{type}/{name}
            # Note: Azure resource IDs use lowercase "resourcegroups" in some cases
            resource_id = resource_ids[0]
            resource_id_lower = resource_id.lower()
            
            if "/resourcegroups/" in resource_id_lower:
                # Case-insensitive split
                parts = resource_id_lower.split("/resourcegroups/")
                if len(parts) > 1:
                    rg_part = parts[1].split("/")[0]
                    # Query at resource group level, will filter to specific resource later
                    scope = f"{base_scope}/resourceGroups/{rg_part}"
                    logger.info(f" Resource-scoped query: Querying RG '{rg_part}' and will filter to resource")
                    return scope, rg_part
            # Fallback if can't parse - should not happen with valid resource IDs
            logger.error(f" Could not parse resource group from resource_id: {resource_id}")
            logger.error(f" Cannot query resource-level scope directly - Azure API limitation")
            raise ValueError(f"Invalid resource ID format: {resource_id}")
        elif resource_group:
            # Resource group scope
            return f"{base_scope}/resourceGroups/{resource_group}", resource_group
        else:
            # Subscription scope
            return base_scope, None
    
    def _filter_to_resources(
        self,
        breakdown: List[Dict[str, Any]],
        resource_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Filter breakdown items to only include specified resources
        
        Args:
            breakdown: Cost breakdown with resourceid or resource_id fields
            resource_ids: List of resource IDs to filter to
            
        Returns:
            Filtered breakdown list
        """
        if not resource_ids or not breakdown:
            return breakdown
        
        # Normalize resource IDs to lowercase for comparison
        target_ids_lower = [rid.lower() for rid in resource_ids]
        
        filtered = []
        for item in breakdown:
            # Check both 'resourceid' (from resource breakdown) and 'resource_id' fields
            item_id = item.get("resourceid") or item.get("resource_id") or ""
            if item_id.lower() in target_ids_lower:
                filtered.append(item)
        
        return filtered
    
    async def _query_costs(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str
    ) -> Dict[str, Any]:
        """Query Azure Cost Management API"""
        try:
            # Calculate period duration
            period_days = (end_date - start_date).days
            
            # Azure Cost Management API granularity rules (matching advanced_cost_service.py):
            # - GranularityType.DAILY: For time-series data (daily breakdown) without grouping
            # - None: For aggregated data with dimension grouping (ServiceName, ResourceId, etc.)
            # - For resource-scoped queries at RG level with ResourceId grouping: use None
            
            # Since we're querying for daily breakdown (time-series), use DAILY for <= 31 days
            # But Azure has limitations: for very short periods (< 7 days) or certain scopes, use None
            if period_days > 31:
                # For longer periods, don't use granularity (aggregate only)
                api_granularity = None
                logger.info(f"Period is {period_days} days, using no granularity (aggregated view)")
            elif period_days <= 31:
                # For shorter periods, try DAILY
                api_granularity = GranularityType.DAILY
            else:
                # Default
                api_granularity = GranularityType.DAILY
            
            # Build query
            query_def = QueryDefinition(
                type="ActualCost",
                timeframe=TimeframeType.CUSTOM,
                time_period=QueryTimePeriod(
                    from_property=start_date,
                    to=end_date
                ),
                dataset=QueryDataset(
                    granularity=api_granularity,
                    aggregation={
                        "totalCost": QueryAggregation(name="Cost", function="Sum")
                    }
                )
            )
            
            # Execute query
            result = self.client.query.usage(scope=scope, parameters=query_def)
            
            # Parse results
            total_cost = 0.0
            daily_costs = []
            
            logger.info(f"Daily breakdown query for {start_date.date()} to {end_date.date()} returned {len(result.rows) if result.rows else 0} rows")
            
            if result.rows:
                for row in result.rows:
                    # Row format: [cost, date, currency]
                    cost = float(row[0]) if row[0] else 0.0
                    date = row[1] if len(row) > 1 else None
                    currency = row[2] if len(row) > 2 else "USD"
                    
                    total_cost += cost
                    daily_costs.append({
                        "date": str(date),
                        "cost": round(cost, 2),
                        "currency": currency
                    })
                    logger.debug(f"  - {date}: {cost:.2f} {currency}")
                
                logger.info(f"Total from daily breakdown: {total_cost:.2f}")
            else:
                logger.warning(f"Daily breakdown returned no rows (might require MONTHLY granularity)")
            
            # Calculate actual number of days in the period (inclusive)
            actual_num_days = (end_date - start_date).days + 1
            
            return {
                "total_cost": round(total_cost, 2),
                "currency": daily_costs[0]["currency"] if daily_costs else "USD",
                "daily_breakdown": daily_costs,
                "num_days": actual_num_days  # Use actual date range, not row count
            }
            
        except Exception as e:
            logger.error(f"Cost query failed: {e}")
            # Return empty result with correct date range
            actual_num_days = (end_date - start_date).days + 1
            return {
                "total_cost": 0.0,
                "currency": "USD",
                "daily_breakdown": [],
                "num_days": actual_num_days,
                "error": "Cost query failed"
            }
    
    async def _get_total_cost_raw(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Any]:
        """
        Get raw total cost without any grouping or granularity
        This provides the most accurate total from Azure Cost Management
        """
        try:
            query_def = QueryDefinition(
                type="ActualCost",
                timeframe=TimeframeType.CUSTOM,
                time_period=QueryTimePeriod(
                    from_property=start_date,
                    to=end_date
                ),
                dataset=QueryDataset(
                    granularity=None,  # No time grouping
                    aggregation={
                        "totalCost": QueryAggregation(name="Cost", function="Sum")
                    }
                    # No grouping at all - just raw total
                )
            )
            
            result = self.client.query.usage(scope=scope, parameters=query_def)
            
            if result.rows and len(result.rows) > 0:
                row = result.rows[0]
                cost = float(row[0]) if row[0] else 0.0
                currency = row[1] if len(row) > 1 else "USD"
                
                logger.info(f"Raw total cost query: {cost:.2f} {currency}")
                return {
                    "total_cost": round(cost, 2),
                    "currency": currency
                }
            
            return {"total_cost": 0.0, "currency": "USD"}
            
        except Exception as e:
            logger.error(f"Raw total cost query failed: {e}")
            return {"total_cost": 0.0, "currency": "USD"}
    
    async def _get_service_breakdown(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """Get cost breakdown by Azure service"""
        try:
            query_def = QueryDefinition(
                type="ActualCost",
                timeframe=TimeframeType.CUSTOM,
                time_period=QueryTimePeriod(
                    from_property=start_date,
                    to=end_date
                ),
                dataset=QueryDataset(
                    granularity=None,  # No time grouping
                    aggregation={
                        "totalCost": QueryAggregation(name="Cost", function="Sum")
                    },
                    grouping=[
                        QueryGrouping(type="Dimension", name="ServiceName")
                    ]
                )
            )
            
            result = self.client.query.usage(scope=scope, parameters=query_def)
            
            services = []
            total_from_services = 0.0
            if result.rows:
                logger.info(f"Service breakdown query returned {len(result.rows)} services")
                for row in result.rows:
                    cost = float(row[0]) if row[0] else 0.0
                    service_name = row[1] if len(row) > 1 else "Unknown"
                    currency = row[2] if len(row) > 2 else "USD"
                    
                    total_from_services += cost
                    services.append({
                        "service": service_name,
                        "cost": round(cost, 2),
                        "currency": currency
                    })
                    logger.debug(f"  - {service_name}: {cost:.2f} {currency}")
                
                logger.info(f"Total calculated from service breakdown: {total_from_services:.2f}")
            
            # Sort by cost descending
            services.sort(key=lambda x: x["cost"], reverse=True)
            return services
            
        except Exception as e:
            logger.error(f"Service breakdown query failed: {e}")
            return []
    
    async def _get_resource_group_breakdown(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """Get cost breakdown by resource group"""
        try:
            query_def = QueryDefinition(
                type="ActualCost",
                timeframe=TimeframeType.CUSTOM,
                time_period=QueryTimePeriod(
                    from_property=start_date,
                    to=end_date
                ),
                dataset=QueryDataset(
                    granularity=None,  # No time grouping
                    aggregation={
                        "totalCost": QueryAggregation(name="Cost", function="Sum")
                    },
                    grouping=[
                        QueryGrouping(type="Dimension", name="ResourceGroupName")
                    ]
                )
            )
            
            result = self.client.query.usage(scope=scope, parameters=query_def)
            
            resource_groups = []
            total_from_rgs = 0.0
            if result.rows:
                logger.info(f"Resource group breakdown query returned {len(result.rows)} resource groups")
                for row in result.rows:
                    cost = float(row[0]) if row[0] else 0.0
                    rg_name = row[1] if len(row) > 1 else "Unknown"
                    currency = row[2] if len(row) > 2 else "USD"
                    
                    total_from_rgs += cost
                    resource_groups.append({
                        "resource_group": rg_name,
                        "cost": round(cost, 2),
                        "currency": currency
                    })
                    logger.debug(f"  - {rg_name}: {cost:.2f} {currency}")
                
                logger.info(f"Total calculated from resource group breakdown: {total_from_rgs:.2f}")
            
            # Sort by cost descending
            resource_groups.sort(key=lambda x: x["cost"], reverse=True)
            return resource_groups
            
        except Exception as e:
            logger.error(f"Resource group breakdown query failed: {e}")
            return []
    
    async def _get_resource_breakdown(
        self,
        scope: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """Get cost breakdown by resource"""
        try:
            query_def = QueryDefinition(
                type="ActualCost",
                timeframe=TimeframeType.CUSTOM,
                time_period=QueryTimePeriod(
                    from_property=start_date,
                    to=end_date
                ),
                dataset=QueryDataset(
                    granularity=None,
                    aggregation={
                        "totalCost": QueryAggregation(name="Cost", function="Sum")
                    },
                    grouping=[
                        QueryGrouping(type="Dimension", name="ResourceId")
                    ]
                )
            )
            
            result = self.client.query.usage(scope=scope, parameters=query_def)
            
            resources = []
            if result.rows:
                for row in result.rows:
                    cost = float(row[0]) if row[0] else 0.0
                    resource_id = row[1] if len(row) > 1 else "Unknown"
                    currency = row[2] if len(row) > 2 else "USD"
                    
                    # Extract resource name from ID
                    resource_name = resource_id.split("/")[-1] if "/" in resource_id else resource_id
                    
                    resources.append({
                        "resource_id": resource_id,
                        "resource_name": resource_name,
                        "cost": round(cost, 2),
                        "currency": currency
                    })
            
            # Sort by cost descending
            resources.sort(key=lambda x: x["cost"], reverse=True)
            return resources
            
        except Exception as e:
            logger.error(f"Resource breakdown query failed: {e}")
            return []
    
    def _analyze_cost_trends(
        self,
        current: Dict[str, Any],
        previous: Dict[str, Any],
        granularity: str
    ) -> Dict[str, Any]:
        """Analyze cost trends and detect anomalies"""
        current_total = current.get("total_cost", 0)
        previous_total = previous.get("total_cost", 0)
        
        # Calculate change
        if previous_total > 0:
            change_percent = ((current_total - previous_total) / previous_total) * 100
            change_amount = current_total - previous_total
            has_comparison = True
        elif current_total > 0:
            # No previous data - can't calculate meaningful percentage
            change_percent = 0
            change_amount = current_total
            has_comparison = False
        else:
            # Both periods are zero
            change_percent = 0
            change_amount = 0
            has_comparison = False
        
        # Determine trend
        if not has_comparison:
            trend = "no_comparison"
            trend_description = "No previous period data available for comparison"
        elif abs(change_percent) < 5:
            trend = "stable"
            trend_description = "Costs are relatively stable"
        elif change_percent > 0:
            trend = "increasing"
            if change_percent > 20:
                trend_description = f"⚠️ Significant cost increase of {change_percent:.1f}%"
            else:
                trend_description = f"Costs increased by {change_percent:.1f}%"
        else:
            trend = "decreasing"
            trend_description = f"Costs decreased by {abs(change_percent):.1f}%"
        
        # Detect daily anomalies
        daily_costs = current.get("daily_breakdown", [])
        anomalies = self._detect_anomalies(daily_costs)
        
        # Calculate daily average
        if daily_costs:
            daily_avg = current_total / len(daily_costs)
        else:
            daily_avg = 0
        
        return {
            "trend": trend,
            "trend_description": trend_description,
            "change_percent": round(change_percent, 2),
            "change_amount": round(change_amount, 2),
            "daily_average": round(daily_avg, 2),
            "anomalies": anomalies,
            "insights": self._generate_insights(
                current, previous, change_percent, anomalies
            )
        }
    
    def _detect_anomalies(self, daily_costs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect cost anomalies/spikes"""
        if len(daily_costs) < 3:
            return []
        
        # Calculate mean and std deviation
        costs = [d["cost"] for d in daily_costs]
        mean_cost = sum(costs) / len(costs)
        variance = sum((x - mean_cost) ** 2 for x in costs) / len(costs)
        std_dev = variance ** 0.5
        
        # Find anomalies (>2 standard deviations from mean)
        anomalies = []
        threshold = mean_cost + (2 * std_dev)
        
        for day in daily_costs:
            if day["cost"] > threshold:
                deviation_percent = ((day["cost"] - mean_cost) / mean_cost) * 100
                anomalies.append({
                    "date": day["date"],
                    "cost": day["cost"],
                    "expected_cost": round(mean_cost, 2),
                    "deviation_percent": round(deviation_percent, 2),
                    "severity": "high" if day["cost"] > mean_cost + (3 * std_dev) else "medium"
                })
        
        return anomalies
    
    def _generate_insights(
        self,
        current: Dict[str, Any],
        previous: Dict[str, Any],
        change_percent: float,
        anomalies: List[Dict[str, Any]]
    ) -> List[str]:
        """Generate actionable insights from cost data"""
        insights = []
        
        current_total = current.get("total_cost", 0)
        previous_total = previous.get("total_cost", 0)
        
        # Check if we have valid comparison data
        has_comparison = previous_total > 0
        
        # Cost increase insights (only if we have comparison data)
        if has_comparison and change_percent > 20:
            insights.append(
                f"🔴 Cost increased by {change_percent:.1f}% compared to previous period. "
                f"Review recent resource deployments or usage changes."
            )
        elif has_comparison and change_percent > 10:
            insights.append(
                f"🟡 Moderate cost increase of {change_percent:.1f}%. "
                f"Monitor for continued growth."
            )
        elif has_comparison and change_percent < -10:
            insights.append(
                f"🟢 Cost decreased by {abs(change_percent):.1f}%. "
                f"Good optimization or reduced usage."
            )
        
        # Anomaly insights
        if anomalies:
            high_anomalies = [a for a in anomalies if a["severity"] == "high"]
            if high_anomalies:
                dates = ", ".join([a["date"] for a in high_anomalies[:3]])
                insights.append(
                    f"⚠️ Cost spikes detected on {dates}. "
                    f"Investigate resource usage during these periods."
                )
        
        # Daily average insights
        daily_avg = current.get("total_cost", 0) / max(current.get("num_days", 1), 1)
        if daily_avg > 100:
            insights.append(
                f"💰 Daily average cost: ${daily_avg:.2f}. "
                f"Consider reserved instances or savings plans for consistent workloads."
            )
        
        # Zero cost warning
        if current_total == 0:
            insights.append(
                "ℹ️ No cost data available for this period. "
                "Data may be delayed or scope may have no chargeable resources."
            )
        
        return insights

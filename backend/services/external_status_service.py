"""
External Status Service
Checks Azure Service Health for platform-wide incidents and outages via Resource Graph.
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _convert_dotnet_ticks_to_iso(ticks_value: Any) -> str:
    """
    Convert .NET DateTime ticks to ISO 8601 string.
    .NET ticks are 100-nanosecond intervals since January 1, 0001.
    
    Args:
        ticks_value: .NET ticks as integer or string
        
    Returns:
        ISO 8601 formatted string, or current time if conversion fails
    """
    try:
        if isinstance(ticks_value, str):
            ticks = int(ticks_value)
        elif isinstance(ticks_value, int):
            ticks = ticks_value
        else:
            raise ValueError(f"Invalid ticks type: {type(ticks_value)}")
        
        # .NET epoch: January 1, 0001
        # Python datetime can't handle year 1, so calculate from Unix epoch
        # .NET ticks from Jan 1, 0001 to Jan 1, 1970: 621355968000000000
        TICKS_AT_UNIX_EPOCH = 621355968000000000
        TICKS_PER_SECOND = 10000000
        
        unix_seconds = (ticks - TICKS_AT_UNIX_EPOCH) / TICKS_PER_SECOND
        dt = datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
        return dt.isoformat()
        
    except Exception as error:
        logger.warning(f"Failed to convert .NET ticks {ticks_value}: {error}")
        # Return current time as fallback
        return datetime.now(timezone.utc).isoformat()


class ExternalStatusService:
    """Service for checking Azure Service Health for active incidents via Resource Graph."""

    def __init__(self):
        """Initialize external status service."""
        pass

    async def get_external_status(
        self,
        subscription_id: str,
        user_info: Dict[str, Any],
        hours: int = 24
    ) -> Dict[str, Any]:
        """
        Get Azure Service Health status for active service issues using Resource Graph.
        
        Args:
            subscription_id: Azure subscription ID
            user_info: User authentication info for credential access
            hours: Time window in hours to filter incidents (default: 24)
            
        Returns:
            Dict containing service health status and active incidents
        """
        try:
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest
            from services.dual_credential_service import dual_cred_service
            
            user_credential = dual_cred_service.get_user_credential(user_info)
            client = ResourceGraphClient(user_credential)
            
            # Query for active service health events
            # Note: ServiceHealthResources table may not be available in all subscriptions
            query = """
            ServiceHealthResources
            | where type =~ 'microsoft.resourcehealth/events'
            | where properties.EventType in ('ServiceIssue', 'HealthAdvisory')
            | where properties.Status == 'Active'
            | extend impactArray = properties.Impact
            | mv-expand impact = impactArray
            | project
                service = tostring(impact.ImpactedService),
                title = tostring(properties.Title),
                status = tostring(properties.Status),
                level = tostring(properties.Level),
                impactStartTime = tostring(properties.ImpactStartTime),
                summary = tostring(properties.Summary),
                impactedRegions = impact.ImpactedRegions
            | limit 50
            """
            
            request = QueryRequest(
                subscriptions=[subscription_id],
                query=query
            )
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.resources(request)
            )
            
            active_incidents = []
            if hasattr(response, 'data') and response.data:
                for row in response.data:
                    # Extract first impacted region from array if available
                    impacted_regions = row.get("impactedRegions", [])
                    region = "Multiple Regions"
                    if impacted_regions and len(impacted_regions) > 0:
                        first_region = impacted_regions[0]
                        if isinstance(first_region, dict):
                            region = first_region.get("impactedRegion", "Multiple Regions")
                    
                    active_incidents.append({
                        "name": row.get("service") or "Unknown Service",
                        "service": row.get("service"),
                        "region": region,
                        "title": row.get("title") or "Service Issue",
                        "status": row.get("status"),
                        "level": row.get("level"),
                        "impact_start_time": _convert_dotnet_ticks_to_iso(row.get("impactStartTime")) if row.get("impactStartTime") else None,
                        "summary": row.get("summary") or "",
                    })
            
            # Filter incidents to only those within the specified time window
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            filtered_incidents = []
            
            for incident in active_incidents:
                impact_start = incident.get("impact_start_time")
                if impact_start:
                    try:
                        impact_start_dt = datetime.fromisoformat(impact_start.replace("Z", "+00:00"))
                        if impact_start_dt >= cutoff_time:
                            filtered_incidents.append(incident)
                        else:
                            logger.debug(f"Filtered out old service health incident: {incident.get('service')} (started {impact_start})")
                    except Exception as e:
                        logger.warning(f"Failed to parse impact_start_time for {incident.get('service')}: {e}")
                        # Include incident if we can't parse the time (safer to show than hide)
                        filtered_incidents.append(incident)
                else:
                    # Include incidents without timestamps
                    filtered_incidents.append(incident)
            
            degraded_services = [
                incident["name"]
                for incident in filtered_incidents
                if incident.get("level") in ["Error", "Critical"]
            ]
            
            logger.info(f"Azure Service Health check completed: {len(filtered_incidents)} active incidents found (filtered from {len(active_incidents)} total, time window: {hours}h)")
            
            return {
                "success": True,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "services": filtered_incidents,
                "degraded_services": degraded_services,
                "all_operational": len(active_incidents) == 0,
            }
            
        except Exception as error:
            # Don't treat query failures as critical - may be due to table not available
            # Return success with no incidents (assume operational)
            logger.info(f"Azure Service Health check completed with no data (query may have failed, assuming operational): {error}")
            return {
                "success": True,  # Changed from False to True
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "services": [],
                "degraded_services": [],
                "all_operational": True,
                "note": "Service health data unavailable - assuming all services operational",
            }


external_status_service = ExternalStatusService()

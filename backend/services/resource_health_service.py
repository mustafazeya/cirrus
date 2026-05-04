"""
Azure Resource Health Service
Provides resource health and availability status information
"""
from typing import List, Dict, Any
import logging
import asyncio

logger = logging.getLogger(__name__)


class ResourceHealthService:
    """Service for Azure Resource Health data"""
    
    def __init__(self):
        """Initialize resource health service"""
        pass
    
    def _get_user_credential(self, user_info: Dict[str, Any]):
        """Get credential from user token"""
        from services.dual_credential_service import dual_cred_service
        return dual_cred_service.get_user_credential(user_info)
    
    async def _get_resource_health_via_rest_api(
        self,
        resource_id: str,
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Get resource health via Azure Resource Health SDK
        This is a fallback when Resource Graph doesn't have data
        """
        try:
            from azure.mgmt.resourcehealth import MicrosoftResourceHealth
            from azure.core.exceptions import ResourceNotFoundError, HttpResponseError
            
            user_credential = self._get_user_credential(user_info)
            
            # Extract subscription ID from resource ID
            # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/{type}/{name}
            parts = resource_id.split('/')
            if len(parts) < 3 or parts[1] != 'subscriptions':
                logger.error(f"Invalid resource ID format: {resource_id}")
                return {
                    "success": True,
                    "resource_id": resource_id,
                    "availability_state": "Unknown",
                    "message": "Invalid resource ID format",
                    "supported": False
                }
            
            subscription_id = parts[2]
            
            logger.info(f" Using Resource Health SDK for subscription: {subscription_id}")
            
            # Create Resource Health client
            client = MicrosoftResourceHealth(user_credential, subscription_id)
            loop = asyncio.get_event_loop()
            
            # Get current availability status
            # The resource_uri is the full resource ID (keep leading slash)
            resource_uri = resource_id
            
            try:
                # Get current availability status using SDK
                availability_status = await loop.run_in_executor(
                    None,
                    lambda: client.availability_statuses.get_by_resource(
                        resource_uri=resource_uri
                    )
                )
                
                # Extract properties (note: SDK has typo 'occured_time' instead of 'occurred_time')
                props = availability_status.properties
                return {
                    "success": True,
                    "resource_id": resource_id,
                    "availability_state": props.availability_state if props else "Unknown",
                    "reason_type": props.reason_type if props else None,
                    "summary": props.summary if props else None,
                    "detailed_status": props.detailed_status if props else None,
                    "occurred_time": props.occured_time.isoformat() if props and props.occured_time else None,  # Note: SDK typo 'occured_time'
                    "reason_chronicity": props.reason_chronicity if props else None,
                    "reported_time": props.reported_time.isoformat() if props and props.reported_time else None,
                    "supported": True
                }
                
            except ResourceNotFoundError:
                # Resource doesn't support health monitoring
                logger.info(f"Resource health not supported (404) for {resource_id}")
                return {
                    "success": True,
                    "resource_id": resource_id,
                    "availability_state": "Unknown",
                    "message": "Resource health monitoring is not available for this resource type.",
                    "supported": False
                }
            except HttpResponseError as http_err:
                # Other HTTP errors (401, 403, etc.)
                logger.warning(f"Resource Health API error {http_err.status_code}: {http_err.message}")
                return {
                    "success": True,
                    "resource_id": resource_id,
                    "availability_state": "Unknown",
                    "message": f"Unable to retrieve health status: {http_err.message}",
                    "supported": False
                }
                    
        except Exception as e:
            logger.error(f"Resource Health SDK fallback failed: {e}", exc_info=True)
            return {
                "success": True,
                "resource_id": resource_id,
                "availability_state": "Unknown",
                "message": "Failed to retrieve health status",
                "supported": False
            }
    
    async def _get_health_for_resource_group_via_sdk(
        self,
        subscription_id: str,
        resource_group: str,
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Get resource health for all resources in a resource group using SDK
        SDK provides better coverage than Resource Graph for resource groups
        """
        try:
            from azure.mgmt.resourcehealth import MicrosoftResourceHealth
            from azure.core.exceptions import HttpResponseError
            
            user_credential = self._get_user_credential(user_info)
            client = MicrosoftResourceHealth(user_credential, subscription_id)
            loop = asyncio.get_event_loop()
            
            logger.info(f" Using SDK list_by_resource_group for: {resource_group}")
            
            # List all health statuses in the resource group
            availability_statuses = await loop.run_in_executor(
                None,
                lambda: list(client.availability_statuses.list_by_resource_group(
                    resource_group_name=resource_group
                ))
            )
            
            resources = []
            summary = {
                "total": 0,
                "available": 0,
                "unavailable": 0,
                "degraded": 0,
                "unknown": 0
            }
            
            for status in availability_statuses:
                props = status.properties
                state = (props.availability_state if props else "Unknown").lower()
                
                # Extract target resource ID from status.id
                # Format: /subscriptions/.../resourceGroups/.../providers/.../RESOURCE/providers/Microsoft.ResourceHealth/availabilityStatuses/current
                # We need to remove the "/providers/Microsoft.ResourceHealth/availabilityStatuses/current" suffix
                resource_id = status.id
                if "/providers/Microsoft.ResourceHealth/availabilityStatuses/" in resource_id:
                    resource_id = resource_id.split("/providers/Microsoft.ResourceHealth/availabilityStatuses/")[0]
                
                resources.append({
                    "resource_id": resource_id,
                    "availability_state": props.availability_state if props else "Unknown",
                    "reason_type": props.reason_type if props else None,
                    "summary": props.summary if props else None,
                    "detailed_status": props.detailed_status if props else None,
                    "occurred_time": props.occured_time.isoformat() if props and props.occured_time else None
                })
                
                summary["total"] += 1
                if 'available' in state:
                    summary["available"] += 1
                elif 'unavailable' in state:
                    summary["unavailable"] += 1
                elif 'degraded' in state:
                    summary["degraded"] += 1
                else:
                    summary["unknown"] += 1
            
            health_pct = (summary["available"] / summary["total"] * 100) if summary["total"] > 0 else 100.0
            
            logger.info(
                f"✅ SDK Resource Health: {health_pct:.1f}% healthy | "
                f"Total: {summary['total']}, Available: {summary['available']}"
            )
            
            return {
                "success": True,
                "resources": resources,
                "summary": summary,
                "health_percentage": round(health_pct, 1),
                "source": "sdk"  # Indicate data source
            }
            
        except HttpResponseError as http_err:
            logger.warning(f"SDK list_by_resource_group failed: {http_err}", exc_info=True)
            return {
                "success": False,
                "error": "Failed to retrieve resource group health",
                "message": "Failed to retrieve resource group health. Please try again later or contact support if the problem persists."
            }
        except Exception as e:
            logger.error(f"SDK resource group health failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": "Failed to retrieve resource group health",
                "message": "Failed to retrieve resource group health"
            }
    
    async def get_resource_health(
        self,
        user_info: Dict[str, Any],
        resource_id: str = None,
        resource_group: str = None,
        subscription_ids: List[str] = None
    ) -> Dict[str, Any]:
        """
        Get resource health for specific resource, resource group, or subscription(s)
        
        Strategy:
        - Single resource: Try Resource Graph → SDK fallback
        - Resource group: Use SDK (better coverage)
        - Subscription(s): Use Resource Graph (faster for bulk queries)
        
        Args:
            user_info: User authentication information
            resource_id: Specific resource ID (most specific)
            resource_group: Resource group name (requires subscription_ids)
            subscription_ids: List of subscription IDs to query
            
        Returns:
            Dictionary containing health status information
        """
        try:
            logger.info(f" Getting resource health - Resource: {resource_id}, RG: {resource_group}, Subs: {subscription_ids}")
            
            # STRATEGY: Use SDK for resource groups (better coverage)
            if resource_group and subscription_ids and not resource_id:
                subscription_id = subscription_ids[0]  # Resource group belongs to one subscription
                return await self._get_health_for_resource_group_via_sdk(
                    subscription_id, resource_group, user_info
                )
            
            # For single resources and subscriptions, use Resource Graph
            user_credential = self._get_user_credential(user_info)
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest
            
            client = ResourceGraphClient(user_credential)
            loop = asyncio.get_event_loop()
            
            # Build query based on scope
            if resource_id:
                # Query specific resource
                query = f"""
                    HealthResources
                    | where type =~ 'microsoft.resourcehealth/availabilitystatuses'
                    | extend targetResourceId = tolower(tostring(properties.targetResourceId))
                    | where targetResourceId == tolower('{resource_id}')
                    | extend availabilityState = tostring(properties.availabilityState),
                             reasonType = tostring(properties.reasonType),
                             summary = tostring(properties.summary),
                             detailedStatus = tostring(properties.detailedStatus),
                             occurredTime = todatetime(properties.occurredTime),
                             reasonChronicity = tostring(properties.reasonChronicity),
                             reportedTime = todatetime(properties.reportedTime)
                    | project targetResourceId, availabilityState, reasonType, summary, detailedStatus, occurredTime, reasonChronicity, reportedTime
                    | take 1
                """
            elif resource_group and subscription_ids:
                # Query all resources in resource group
                query = f"""
                    HealthResources
                    | where type =~ 'microsoft.resourcehealth/availabilitystatuses'
                    | extend targetResourceId = tostring(properties.targetResourceId),
                             availabilityState = tostring(properties.availabilityState),
                             resourceGroup = tostring(split(properties.targetResourceId, '/')[4])
                    | where subscriptionId in ({','.join([f"'{sub}'" for sub in subscription_ids])})
                    | where tolower(resourceGroup) == tolower('{resource_group}')
                    | extend reasonType = tostring(properties.reasonType),
                             summary = tostring(properties.summary),
                             detailedStatus = tostring(properties.detailedStatus),
                             occurredTime = todatetime(properties.occurredTime)
                    | project targetResourceId, availabilityState, reasonType, summary, detailedStatus, occurredTime
                """
            elif subscription_ids:
                # Query all resources in subscription(s)
                query = f"""
                    HealthResources
                    | where type =~ 'microsoft.resourcehealth/availabilitystatuses'
                    | where subscriptionId in ({','.join([f"'{sub}'" for sub in subscription_ids])})
                    | extend targetResourceId = tostring(properties.targetResourceId),
                             availabilityState = tostring(properties.availabilityState),
                             reasonType = tostring(properties.reasonType),
                             summary = tostring(properties.summary),
                             detailedStatus = tostring(properties.detailedStatus),
                             occurredTime = todatetime(properties.occurredTime)
                    | project targetResourceId, availabilityState, reasonType, summary, detailedStatus, occurredTime
                """
            else:
                return {
                    "success": False,
                    "error": "Must provide either resource_id, resource_group (with subscription), or subscription_ids"
                }
            
            # Execute query
            request = QueryRequest(subscriptions=subscription_ids or [], query=query)
            response = await loop.run_in_executor(None, client.resources, request)
            
            if not hasattr(response, 'data') or not response.data:
                logger.warning(f"No health data found in Resource Graph for scope")
                
                # If specific resource, try direct REST API call as fallback
                if resource_id:
                    logger.info(" Falling back to Resource Health REST API")
                    return await self._get_resource_health_via_rest_api(resource_id, user_info)
                
                # For multiple resources, return empty results
                logger.warning(f"No health data found for scope - Resource Graph returned no results")
                
                return {
                    "success": True,
                    "resources": [],
                    "summary": {
                        "total": 0,
                        "available": 0,
                        "unavailable": 0,
                        "degraded": 0,
                        "unknown": 0
                    },
                    "message": "No health data available for the specified scope"
                }
            
            # Process results
            if resource_id:
                # Single resource result
                row = response.data[0]
                return {
                    "success": True,
                    "resource_id": row.get('targetResourceId'),
                    "availability_state": row.get('availabilityState'),
                    "reason_type": row.get('reasonType'),
                    "summary": row.get('summary'),
                    "detailed_status": row.get('detailedStatus'),
                    "occurred_time": row.get('occurredTime'),
                    "reason_chronicity": row.get('reasonChronicity'),
                    "reported_time": row.get('reportedTime'),
                    "supported": True
                }
            else:
                # Multiple resources - aggregate
                resources = []
                summary = {
                    "total": 0,
                    "available": 0,
                    "unavailable": 0,
                    "degraded": 0,
                    "unknown": 0
                }
                
                for row in response.data:
                    state = (row.get('availabilityState') or 'Unknown').lower()
                    
                    resources.append({
                        "resource_id": row.get('targetResourceId'),
                        "availability_state": row.get('availabilityState'),
                        "reason_type": row.get('reasonType'),
                        "summary": row.get('summary'),
                        "detailed_status": row.get('detailedStatus'),
                        "occurred_time": row.get('occurredTime')
                    })
                    
                    summary["total"] += 1
                    if 'available' in state:
                        summary["available"] += 1
                    elif 'unavailable' in state:
                        summary["unavailable"] += 1
                    elif 'degraded' in state:
                        summary["degraded"] += 1
                    else:
                        summary["unknown"] += 1
                
                health_pct = (summary["available"] / summary["total"] * 100) if summary["total"] > 0 else 100.0
                
                logger.info(
                    f"✅ Resource Health: {health_pct:.1f}% healthy "
                    f"| Available: {summary['available']}, Unavailable: {summary['unavailable']}, "
                    f"Degraded: {summary['degraded']}, Unknown: {summary['unknown']}"
                )
                
                return {
                    "success": True,
                    "resources": resources,
                    "summary": summary,
                    "health_percentage": round(health_pct, 1),
                    "source": "resource_graph",
                    "limited_coverage": True,  # Resource Graph may have limited coverage for subscriptions
                    "coverage_note": "Note: Subscription-level queries use Azure Resource Graph which may not include all resource types. For complete coverage, select a specific resource group."
                }
                
        except Exception as e:
            logger.error(f" Failed to get resource health: {e}", exc_info=True)
            return {
                "success": False,
                "error": "Failed to retrieve resource health",
                "message": "Failed to retrieve resource health"
            }
    
    async def get_resource_health_summary(self, subscription_ids: List[str], user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get resource health summary across subscriptions using Resource Graph
        
        Args:
            subscription_ids: List of subscription IDs to query
            user_info: User authentication information
            
        Returns:
            Dictionary containing health status counts and by_subscription breakdown
        """
        try:
            logger.info(f" Fetching resource health for {len(subscription_ids)} subscription(s)")
            
            user_credential = self._get_user_credential(user_info)
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest
            
            client = ResourceGraphClient(user_credential)
            loop = asyncio.get_event_loop()
            
            # Query Resource Health events - try multiple approaches
            # First, try the HealthResources table
            query = """
                HealthResources
                | where type =~ 'microsoft.resourcehealth/availabilitystatuses'
                | extend subscriptionId = tostring(subscriptionId),
                         resourceId = tostring(properties.targetResourceId),
                         availabilityState = tostring(properties.availabilityState),
                         reasonType = tostring(properties.reasonType),
                         summary = tostring(properties.summary),
                         detailedStatus = tostring(properties.detailedStatus),
                         occurredTime = todatetime(properties.occurredTime)
                | where isnotempty(availabilityState)
                | project subscriptionId, resourceId, availabilityState, reasonType, summary, detailedStatus, occurredTime
            """
            
            request = QueryRequest(subscriptions=subscription_ids, query=query)
            
            try:
                response = await loop.run_in_executor(None, client.resources, request)
                logger.info(f"HealthResources query returned {len(response.data) if hasattr(response, 'data') and response.data else 0} rows")
            except Exception as query_error:
                logger.warning(f"HealthResources query failed: {query_error}")
                # Try alternative query using Resources table
                query_alt = """
                    Resources
                    | where type =~ 'microsoft.resourcehealth/availabilitystatuses'
                    | extend subscriptionId = tostring(subscriptionId),
                             resourceId = tostring(properties.targetResourceId),
                             availabilityState = tostring(properties.availabilityState)
                    | where isnotempty(availabilityState)
                    | project subscriptionId, resourceId, availabilityState
                """
                request = QueryRequest(subscriptions=subscription_ids, query=query_alt)
                response = await loop.run_in_executor(None, client.resources, request)
                logger.info(f"Resources (alt) query returned {len(response.data) if hasattr(response, 'data') and response.data else 0} rows")
            
            if not hasattr(response, 'data') or not response.data:
                logger.warning("No resource health data found")
                return {
                    "available": 0,
                    "unavailable": 0,
                    "degraded": 0,
                    "unknown": 0,
                    "total": 0,
                    "by_subscription": [],
                    "message": "No health data available"
                }
            
            # Aggregate by subscription and status
            per_subscription = {}
            total_available = 0
            total_unavailable = 0
            total_degraded = 0
            total_unknown = 0
            
            for row in response.data:
                sid = row.get('subscriptionId') or 'unknown'
                state = (row.get('availabilityState') or 'Unknown').lower()
                
                if sid not in per_subscription:
                    per_subscription[sid] = {
                        "available": 0,
                        "unavailable": 0,
                        "degraded": 0,
                        "unknown": 0
                    }
                
                if 'available' in state:
                    per_subscription[sid]["available"] += 1
                    total_available += 1
                elif 'unavailable' in state:
                    per_subscription[sid]["unavailable"] += 1
                    total_unavailable += 1
                elif 'degraded' in state:
                    per_subscription[sid]["degraded"] += 1
                    total_degraded += 1
                else:
                    per_subscription[sid]["unknown"] += 1
                    total_unknown += 1
            
            total = total_available + total_unavailable + total_degraded + total_unknown
            
            # Build per-subscription list
            by_subscription = []
            for sid, counts in per_subscription.items():
                sub_total = counts["available"] + counts["unavailable"] + counts["degraded"] + counts["unknown"]
                health_pct = (counts["available"] / sub_total * 100) if sub_total > 0 else 100.0
                
                by_subscription.append({
                    "subscription_id": sid,
                    "available": counts["available"],
                    "unavailable": counts["unavailable"],
                    "degraded": counts["degraded"],
                    "unknown": counts["unknown"],
                    "total": sub_total,
                    "health_percentage": round(health_pct, 1)
                })
            
            by_subscription = sorted(by_subscription, key=lambda x: x['health_percentage'])
            
            overall_health_pct = (total_available / total * 100) if total > 0 else 100.0
            
            logger.info(
                f"✅ Resource Health: {overall_health_pct:.1f}% healthy "
                f"| Available: {total_available}, Unavailable: {total_unavailable}, "
                f"Degraded: {total_degraded}, Unknown: {total_unknown}"
            )
            
            return {
                "available": total_available,
                "unavailable": total_unavailable,
                "degraded": total_degraded,
                "unknown": total_unknown,
                "total": total,
                "overall_health_percentage": round(overall_health_pct, 1),
                "by_subscription": by_subscription
            }
            
        except Exception as e:
            logger.error(f" Failed to get resource health: {e}", exc_info=True)
            raise


# Singleton instance
resource_health_service = ResourceHealthService()

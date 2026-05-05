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
    
    async def _get_health_for_subscription_via_sdk(
        self,
        subscription_id: str,
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Get resource health for all resources in a subscription using the SDK.
        Uses list_by_subscription_id which calls the Resource Health REST API
        directly — full coverage regardless of Defender or resource type.
        Returns counts plus detail records for non-available resources.
        """
        try:
            from azure.mgmt.resourcehealth import MicrosoftResourceHealth
            from azure.core.exceptions import HttpResponseError

            user_credential = self._get_user_credential(user_info)
            client = MicrosoftResourceHealth(user_credential, subscription_id)
            loop = asyncio.get_event_loop()

            logger.info(f" SDK list_by_subscription_id for: {subscription_id}")

            statuses = await loop.run_in_executor(
                None,
                lambda: list(client.availability_statuses.list_by_subscription_id())
            )

            counts = {"available": 0, "unavailable": 0, "degraded": 0, "unknown": 0}
            non_healthy = []

            for status in statuses:
                props = status.properties
                raw_state = (props.availability_state if props else "Unknown") or "Unknown"
                state_lower = raw_state.lower()

                # Strip the Resource Health suffix to get the actual resource ID
                resource_id = status.id or ""
                if "/providers/Microsoft.ResourceHealth/availabilityStatuses/" in resource_id:
                    resource_id = resource_id.split("/providers/Microsoft.ResourceHealth/availabilityStatuses/")[0]

                # Extract readable resource name and type from the resource ID
                parts = resource_id.strip("/").split("/")
                resource_name = parts[-1] if parts else resource_id
                # Type is providers/{namespace}/{type} — parts[-3] is namespace, parts[-2] is type
                resource_type = f"{parts[-3]}/{parts[-2]}" if len(parts) >= 3 else "Unknown"

                if "available" in state_lower and "unavailable" not in state_lower:
                    counts["available"] += 1
                elif "unavailable" in state_lower:
                    counts["unavailable"] += 1
                elif "degraded" in state_lower:
                    counts["degraded"] += 1
                else:
                    counts["unknown"] += 1

                    non_healthy.append({
                        "resource_id": resource_id,
                        "resource_name": resource_name,
                        "resource_type": resource_type,
                        "subscription_id": subscription_id,
                        "availability_state": raw_state,
                        "reason_type": props.reason_type if props else None,
                        "summary": props.summary if props else None,
                        "occurred_time": props.occured_time.isoformat() if props and props.occured_time else None,
                    })

            total = sum(counts.values())
            # Exclude Unknown from the denominator — Unknown means the resource type
            # is not supported by the Resource Health API, not that it is unhealthy.
            # Calculating percentage only over resources that CAN report health.
            health_denominator = total - counts["unknown"]
            health_pct = (counts["available"] / health_denominator * 100) if health_denominator > 0 else 100.0

            logger.info(
                f"  {subscription_id}: {total} resources, "
                f"{counts['available']} available, {counts['unavailable']} unavailable, "
                f"{counts['degraded']} degraded, {counts['unknown']} unknown"
            )

            return {
                "subscription_id": subscription_id,
                **counts,
                "total": total,
                "health_percentage": round(health_pct, 1),
                "non_healthy_resources": non_healthy,
                "error": None
            }

        except HttpResponseError as e:
            logger.warning(f"  {subscription_id}: HTTP {e.status_code} — {e.message}")
            return {
                "subscription_id": subscription_id,
                "available": 0, "unavailable": 0, "degraded": 0, "unknown": 0,
                "total": 0,
                "health_percentage": 100.0,
                "non_healthy_resources": [],
                "error": f"HTTP {e.status_code}: {e.message}"
            }
        except Exception as e:
            logger.warning(f"  {subscription_id}: {e}")
            return {
                "subscription_id": subscription_id,
                "available": 0, "unavailable": 0, "degraded": 0, "unknown": 0,
                "total": 0,
                "health_percentage": 100.0,
                "non_healthy_resources": [],
                "error": str(e)
            }

    async def get_resource_health_summary(self, subscription_ids: List[str], user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get resource health summary across subscriptions.
        Uses the Resource Health SDK (list_by_subscription_id) per subscription
        for full coverage — all resource types, all subscriptions, no Defender dependency.
        """
        try:
            logger.info(f" Fetching resource health for {len(subscription_ids)} subscription(s) via SDK")

            results = await asyncio.gather(*[
                self._get_health_for_subscription_via_sdk(sid, user_info)
                for sid in subscription_ids
            ])

            total_available = sum(r["available"] for r in results)
            total_unavailable = sum(r["unavailable"] for r in results)
            total_degraded = sum(r["degraded"] for r in results)
            total_unknown = sum(r["unknown"] for r in results)
            total = sum(r["total"] for r in results)

            # Collect all non-healthy resources across subscriptions, sorted by severity
            all_non_healthy = []
            for r in results:
                all_non_healthy.extend(r.get("non_healthy_resources", []))

            state_order = {"Unavailable": 0, "Degraded": 1, "Unknown": 2}
            all_non_healthy.sort(key=lambda x: state_order.get(x["availability_state"], 99))

            by_subscription = sorted(
                [{"subscription_id": r["subscription_id"], "available": r["available"],
                  "unavailable": r["unavailable"], "degraded": r["degraded"],
                  "unknown": r["unknown"], "total": r["total"],
                  "health_percentage": r["health_percentage"], "error": r.get("error")}
                 for r in results],
                key=lambda x: x["health_percentage"]
            )

            overall_health_pct = (total_available / (total - total_unknown) * 100) if (total - total_unknown) > 0 else 100.0

            logger.info(
                f"✅ Resource Health: {overall_health_pct:.1f}% healthy "
                f"| Available: {total_available}, Unavailable: {total_unavailable}, "
                f"Degraded: {total_degraded}, Unknown: {total_unknown}, "
                f"Non-healthy resources: {len(all_non_healthy)}"
            )

            result = {
                "available": total_available,
                "unavailable": total_unavailable,
                "degraded": total_degraded,
                "unknown": total_unknown,
                "total": total,
                "overall_health_percentage": round(overall_health_pct, 1),
                "by_subscription": by_subscription,
                "non_healthy_resources": all_non_healthy
            }

            return result

        except Exception as e:
            logger.error(f" Failed to get resource health summary: {e}", exc_info=True)
            raise


# Singleton instance
resource_health_service = ResourceHealthService()

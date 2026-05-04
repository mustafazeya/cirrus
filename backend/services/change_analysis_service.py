"""
Change Analysis Service - Query Azure Resource Graph for resource changes
"""
import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone
import httpx
from config import settings

logger = logging.getLogger(__name__)

class ChangeAnalysisService:
    """Service to query Azure Resource changes using Azure Resource Graph"""
    
    def __init__(self):
        self.resource_graph_url = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01"
    
    async def get_resource_changes(
        self,
        subscription_ids: List[str],
        hours: int,
        user_info: Dict[str, Any],
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> Dict[str, Any]:
        """
        Get resource changes for specified subscriptions in the last N hours
        
        Args:
            subscription_ids: List of subscription IDs to query
            hours: Number of hours to look back (default 24)
            user_info: User authentication info containing access token
            
        Returns:
            Dict containing changes data and statistics
        """
        try:
            token = user_info.get('token') or user_info.get('management_token')
            if not token:
                logger.error("No authentication token provided")
                return {
                    "success": False,
                    "message": "Authentication token required",
                    "changes": []
                }
            
            if start_time and end_time:
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=timezone.utc)
                if end_time.tzinfo is None:
                    end_time = end_time.replace(tzinfo=timezone.utc)

                start_time_iso = start_time.isoformat().replace('+00:00', 'Z')
                end_time_iso = end_time.isoformat().replace('+00:00', 'Z')
                time_filter = f"changeTime between (datetime({start_time_iso}) .. datetime({end_time_iso}))"
                log_window = f"custom window {start_time_iso} to {end_time_iso}"
            else:
                time_filter = f"changeTime > ago({hours}h)"
                log_window = f"last {hours} hours"

            # Build KQL query for resource changes
            query = f"""
            resourcechanges
            | extend changeTime = todatetime(properties.changeAttributes.timestamp)
            | where {time_filter}
            | extend changeType = tostring(properties.changeType)
            | extend targetResourceId = tostring(properties.targetResourceId)
            | extend targetResourceType = tostring(properties.targetResourceType)
            | extend targetResourceName = tostring(split(targetResourceId, '/')[8])
            | extend resourceGroup = tostring(split(targetResourceId, '/')[4])
            | extend subscriptionId = tostring(split(targetResourceId, '/')[2])
            | extend initiatedBy = tostring(properties.changeAttributes.changedBy)
            | extend correlationId = tostring(properties.changeAttributes.correlationId)
            | extend clientType = tostring(properties.changeAttributes.clientType)
            | project 
                changeTime,
                changeType,
                targetResourceName,
                targetResourceType,
                resourceGroup,
                subscriptionId,
                targetResourceId,
                initiatedBy,
                clientType,
                correlationId,
                changes = properties.changes
            | order by changeTime desc
            | limit 1000
            """
            
            # Build request body
            request_body = {
                "subscriptions": subscription_ids,
                "query": query.strip()
            }
            
            # Make API request
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            logger.info(f"Querying resource changes for {len(subscription_ids)} subscription(s) in {log_window}")
            
            async with httpx.AsyncClient(timeout=settings.CHANGE_ANALYSIS_TIMEOUT) as client:
                response = await client.post(
                    self.resource_graph_url,
                    headers=headers,
                    json=request_body
                )
                
                if response.status_code != 200:
                    logger.error(f"Resource Graph API error: {response.status_code} - {response.text}")
                    return {
                        "success": False,
                        "message": f"Failed to query changes: {response.status_code}",
                        "changes": []
                    }
                
                data = response.json()
                logger.debug(f"Raw response data type: {type(data)}")
                
                # Azure Resource Graph returns: {'data': [...], 'totalRecords': N, ...}
                if isinstance(data, dict) and 'data' in data:
                    changes = data['data']
                    logger.debug(f"Found {len(changes)} changes in response")
                elif isinstance(data, list):
                    changes = data
                    logger.debug(f"Response is direct list with {len(changes)} changes")
                else:
                    logger.error(f"Unexpected response format: {type(data)}")
                    changes = []
                
                
                # Changes are already in object format
                structured_changes = changes if isinstance(changes, list) else []
                
                # Calculate statistics
                total_changes = len(structured_changes)
                change_types = {}
                resource_types = {}
                
                for change in structured_changes:
                    change_type = change.get('changeType', 'Unknown')
                    resource_type = change.get('targetResourceType', 'Unknown')
                    
                    change_types[change_type] = change_types.get(change_type, 0) + 1
                    resource_types[resource_type] = resource_types.get(resource_type, 0) + 1
                
                logger.debug(f"Found {total_changes} resource changes")
                
                return {
                    "success": True,
                    "total_changes": total_changes,
                    "changes": structured_changes[:1000],  # Limit to 1000 for display
                    "statistics": {
                        "by_change_type": change_types,
                        "by_resource_type": dict(sorted(resource_types.items(), key=lambda x: x[1], reverse=True)[:10]),
                        "time_range_hours": hours
                    },
                    "total_in_period": data.get('totalRecords', total_changes)
                }
                
        except httpx.TimeoutException:
            logger.error("Timeout querying resource changes")
            return {
                "success": False,
                "message": "Request timed out",
                "changes": []
            }
        except Exception as e:
            logger.error(f"Error querying resource changes: {e}", exc_info=True)
            return {
                "success": False,
                "message": "Failed to query resource changes",
                "changes": []
            }

# Singleton instance
change_analysis_service = ChangeAnalysisService()

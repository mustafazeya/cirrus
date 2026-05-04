"""
User Access Information Service

Provides information about user's Azure RBAC permissions and scope access.
Used to display what access the user currently has on selected resources/scopes.
"""

import logging
from typing import Dict, List, Any, Optional
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.resource import SubscriptionClient
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
from auth import UserTokenCredential
from services.cache_service import cache_service
from services.advanced_cost_service import AdvancedCostService
from services.dual_credential_service import dual_cred_service
from services.graph_service import graph_service
from config import settings

logger = logging.getLogger(__name__)

class UserAccessService:
    """
    Service to retrieve and format user's RBAC and scope access information
    """
    
    def __init__(self):
        self.cache_ttl = settings.USER_PERMISSIONS_CACHE_TTL
    
    async def get_user_access_info(
        self,
        user_info: Dict[str, Any],
        scope_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Get comprehensive access information for the user based on their current scope.
        
        Args:
            user_info: User authentication info
            scope_context: Current scope context including:
                - global_scope: bool
                - subscription_id: Optional[str]
                - resource_group: Optional[str]
                - resource_id: Optional[str]
        
        Returns:
            Dict containing:
                - scope_type: "global" | "subscription" | "resource_group" | "resource"
                - scope_name: Friendly name of the scope
                - role_assignments: List of role assignments at this scope
                - accessible_subscriptions_count: Number of accessible subscriptions
                - management_group_id: Management group ID (if global scope)
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            
            # Determine scope type
            scope_type, scope_name = self._determine_scope_type(scope_context)
            
            # Get role assignments based on scope
            role_assignments = await self._get_role_assignments(
                user_info,
                scope_context
            )
            
            # Get accessible subscriptions count
            accessible_subs_count = await self._get_accessible_subscriptions_count(user_info)
            
            # Build response based on scope type
            access_info = {
                "scope_type": scope_type,
                "scope_name": scope_name,
                "role_assignments": role_assignments,
                "accessible_subscriptions_count": accessible_subs_count,
                "user_id": user_id,
                "user_email": user_info.get('preferred_username', 'unknown')
            }
            
            # Add scope-specific information
            if scope_type == "global" and accessible_subs_count > 1:
                # Get top management group
                mg_info = await self._get_management_group_info(user_info)
                if mg_info:
                    access_info["management_group"] = mg_info
            
            if scope_context and scope_context.get("subscription_id"):
                access_info["subscription_id"] = scope_context["subscription_id"]
            
            if scope_context and scope_context.get("resource_group"):
                access_info["resource_group"] = scope_context["resource_group"]
            
            if scope_context and scope_context.get("resource_id"):
                access_info["resource_id"] = scope_context["resource_id"]
            
            return {
                "success": True,
                "access_info": access_info
            }
            
        except Exception as e:
            logger.error(f"Failed to get user access info: {e}", exc_info=True)
            return {
                "success": False,
                "error": "Failed to retrieve user access information"
            }
    
    def _determine_scope_type(
        self,
        scope_context: Optional[Dict[str, Any]]
    ) -> tuple[str, str]:
        """Determine scope type and friendly name from context"""
        if not scope_context:
            return "subscription", "Default Subscription"
        
        # Check for specific resource
        if scope_context.get("resource_id"):
            resource_id = scope_context["resource_id"]
            resource_name = resource_id.split('/')[-1] if '/' in resource_id else resource_id
            return "resource", f"Resource: {resource_name}"
        
        # Check for resource group
        if scope_context.get("resource_group"):
            rg_name = scope_context["resource_group"]
            return "resource_group", f"Resource Group: {rg_name}"
        
        # Check for global scope
        if scope_context.get("global_scope", False):
            return "global", "Global Scope (All Subscriptions)"
        
        # Check for specific subscription
        if scope_context.get("subscription_id"):
            return "subscription", "Subscription Scope"
        
        return "subscription", "Default Subscription"
    
    async def _get_role_assignments(
        self,
        user_info: Dict[str, Any],
        scope_context: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Get user's role assignments at the current scope level.
        Returns a list of roles with their scope and permissions.
        """
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            
            # Build cache key based on scope
            cache_key_parts = [f"user_roles:{user_id}"]
            
            if scope_context:
                if scope_context.get("resource_id"):
                    cache_key_parts.append(f"resource:{scope_context['resource_id']}")
                elif scope_context.get("resource_group"):
                    cache_key_parts.append(f"rg:{scope_context['resource_group']}")
                    if scope_context.get("subscription_id"):
                        cache_key_parts.append(f"sub:{scope_context['subscription_id']}")
                elif scope_context.get("subscription_id"):
                    cache_key_parts.append(f"sub:{scope_context['subscription_id']}")
            
            cache_key = ":".join(cache_key_parts)
            
            # Try cache first
            cached_roles = await cache_service.get(user_id, cache_key)
            if cached_roles:
                logger.info(f"Returning cached role assignments for {cache_key}")
                return cached_roles
            
            # Get user's access token
            user_token = user_info.get('token')
            if not user_token:
                logger.error("No access token found in user_info")
                return []
            
            # Create credential from user token
            user_credential = UserTokenCredential(user_token)
            
            # Determine which subscription to query
            subscription_id = None
            
            if scope_context:
                if scope_context.get("subscription_id"):
                    subscription_id = scope_context["subscription_id"]
                elif scope_context.get("resource_id"):
                    # Extract subscription from resource_id
                    resource_id = scope_context["resource_id"]
                    parts = resource_id.split("/")
                    if len(parts) > 2 and parts[1] == "subscriptions":
                        subscription_id = parts[2]
            
            # If no subscription specified, get first accessible subscription
            if not subscription_id:
                subscription_client = SubscriptionClient(user_credential)
                subscriptions = list(subscription_client.subscriptions.list())
                if subscriptions:
                    subscription_id = subscriptions[0].subscription_id
                else:
                    logger.warning("No accessible subscriptions found")
                    return []
            
            # Query role assignments for the specific subscription
            logger.info(f" Querying role assignments for subscription {subscription_id}")
            
            # Query role assignments
            auth_client = AuthorizationManagementClient(user_credential, subscription_id)
            
            # Get ALL role assignments in the subscription and filter for user and groups
            # We'll get the principal_id from each assignment and if it's not the user, it might be a group
            group_object_ids = []
            role_assignments = []
            seen_assignments = set()  # Track unique assignments to avoid duplicates
            
            try:
                # First, get all assignments for the user directly
                user_filter = f"assignedTo('{user_id}')"
                logger.debug(f"  Querying with filter: {user_filter}")
                
                user_assignments = auth_client.role_assignments.list_for_subscription(
                    filter=user_filter
                )
                
                assignment_count = 0
                for assignment in user_assignments:
                    assignment_count += 1
                    assignment_key = f"{assignment.role_definition_id}:{assignment.scope}"
                    logger.debug(f" Assignment {assignment_count}: scope={assignment.scope}, principal={assignment.principal_id}")
                    
                    if assignment_key in seen_assignments:
                        logger.debug(f" Skipping duplicate assignment")
                        continue
                    seen_assignments.add(assignment_key)
                    
                    # Determine if this is directly assigned or through a group
                    is_group_assignment = assignment.principal_id != user_id
                    logger.debug(f" Principal match: user={user_id}, assignment={assignment.principal_id}, is_group={is_group_assignment}")
                    
                    if is_group_assignment and assignment.principal_id not in group_object_ids:
                        group_object_ids.append(assignment.principal_id)
                        logger.debug(f" Detected group assignment via principal: {assignment.principal_id}")
                    
                    # Get role definition to get the role name
                    try:
                        role_def = auth_client.role_definitions.get_by_id(
                            assignment.role_definition_id
                        )
                        logger.debug(f" Role: {role_def.role_name} ({role_def.role_type})")
                        
                        # Filter by scope if needed
                        assignment_scope = assignment.scope.lower()
                        include_assignment = True
                        
                        # Management Group assignments always apply (inherited to all subscriptions/RGs/resources)
                        is_management_group = "/providers/microsoft.management/managementgroups/" in assignment_scope
                        
                        if scope_context and not is_management_group:
                            if scope_context.get("resource_id"):
                                # Only include assignments at or above resource level
                                resource_id_lower = scope_context["resource_id"].lower()
                                include_assignment = (
                                    assignment_scope == resource_id_lower or
                                    resource_id_lower.startswith(assignment_scope + "/")
                                )
                            elif scope_context.get("resource_group"):
                                # Only include assignments at or above RG level
                                rg_name = scope_context["resource_group"]
                                sub_id = scope_context.get("subscription_id", subscription_id)
                                rg_scope = f"/subscriptions/{sub_id}/resourcegroups/{rg_name}".lower()
                                # Include if it's at this RG, or at subscription level, or RG contains this scope
                                include_assignment = (
                                    assignment_scope == rg_scope or
                                    rg_scope.startswith(assignment_scope + "/") or
                                    assignment_scope.startswith(rg_scope) or
                                    # Include subscription-level assignments
                                    (f"/subscriptions/{sub_id}" == assignment_scope)
                                )
                            elif scope_context.get("subscription_id"):
                                # At subscription level, include subscription and above
                                sub_id = scope_context["subscription_id"]
                                sub_scope = f"/subscriptions/{sub_id}".lower()
                                include_assignment = (
                                    assignment_scope == sub_scope or
                                    sub_scope.startswith(assignment_scope + "/")
                                )
                        
                        if include_assignment:
                            logger.debug(f" Including assignment: {role_def.role_name} at {assignment.scope}")
                            
                            # group_name will be resolved after the loop via Graph API
                            group_name = None
                            
                            role_assignments.append({
                                "role_name": role_def.role_name,
                                "role_type": role_def.role_type,
                                "scope": assignment.scope,
                                "scope_level": self._get_scope_level(assignment.scope),
                                "permissions": self._summarize_permissions(role_def),
                                "inherited": is_group_assignment,
                                "group_id": assignment.principal_id if is_group_assignment else None,
                                "group_name": group_name
                            })
                        else:
                            logger.debug(f" Excluding assignment: {role_def.role_name} at {assignment.scope} (scope filter)")
                    
                    except Exception as e:
                        logger.warning(f" Failed to get role definition: {e}")
                        continue
                
                logger.info(f" Found {len(role_assignments)} role assignments (processed {assignment_count}, {len(group_object_ids)} via groups)")
            
            except Exception as e:
                logger.error(f"Failed to list role assignments: {e}")
                return []
            
            # Resolve group GUIDs to display names via Graph API (SP credential)
            if group_object_ids:
                resolved_names = await graph_service.resolve_group_names(group_object_ids)
                for ra in role_assignments:
                    gid = ra.get("group_id")
                    if gid and gid in resolved_names:
                        ra["group_name"] = resolved_names[gid]
            
            # Cache the results
            await cache_service.set(user_id, cache_key, role_assignments, ttl=self.cache_ttl)
            
            return role_assignments
            
        except Exception as e:
            logger.error(f"Failed to get role assignments: {e}", exc_info=True)
            return []
    
    def _get_scope_level(self, scope: str) -> str:
        """Determine the level of a scope (management group, subscription, resource group, resource)"""
        scope_lower = scope.lower()
        
        if "/providers/microsoft.management/managementgroups/" in scope_lower:
            return "Management Group"
        elif "/subscriptions/" in scope_lower:
            if "/resourcegroups/" in scope_lower:
                if "/providers/" in scope_lower:
                    return "Resource"
                return "Resource Group"
            return "Subscription"
        
        return "Unknown"
    
    def _summarize_permissions(self, role_def: Any) -> Dict[str, Any]:
        """Summarize permissions from a role definition"""
        try:
            # Extract key permissions
            actions = []
            not_actions = []
            data_actions = []
            
            if hasattr(role_def, 'permissions') and role_def.permissions:
                for perm in role_def.permissions[:3]:  # Only first 3 to keep it concise
                    if hasattr(perm, 'actions'):
                        actions.extend(list(perm.actions)[:5])  # Max 5 actions
                    if hasattr(perm, 'not_actions'):
                        not_actions.extend(list(perm.not_actions)[:5])
                    if hasattr(perm, 'data_actions'):
                        data_actions.extend(list(perm.data_actions)[:5])
            
            return {
                "actions": actions[:5],  # Limit to 5 for display
                "not_actions": not_actions[:5],
                "data_actions": data_actions[:5],
                "description": role_def.description if hasattr(role_def, 'description') else None
            }
        except Exception as e:
            logger.warning(f"Failed to summarize permissions: {e}")
            return {}
    
    async def _get_accessible_subscriptions_count(self, user_info: Dict[str, Any]) -> int:
        """Get count of accessible subscriptions for the user"""
        try:
            user_id = user_info.get('oid') or user_info.get('sub', 'unknown')
            cache_key = f"user_subs_count_v2:{user_id}"  # v2 to bust old cache with all subscriptions
            
            # Try cache first
            cached_count = await cache_service.get(user_id, cache_key)
            if cached_count is not None:
                return cached_count
            
            # Get user's access token
            user_token = user_info.get('token')
            if not user_token:
                return 0
            
            user_credential = UserTokenCredential(user_token)
            subscription_client = SubscriptionClient(user_credential)
            
            # Get all subscriptions and filter for enabled ones only
            subscriptions = list(subscription_client.subscriptions.list())
            enabled_subscriptions = [sub for sub in subscriptions if sub.state and sub.state.lower() == 'enabled']
            count = len(enabled_subscriptions)
            
            logger.info(f"User has {count} enabled subscriptions out of {len(subscriptions)} total subscriptions")
            
            # Cache the count
            await cache_service.set(user_id, cache_key, count, ttl=self.cache_ttl)
            
            return count
            
        except Exception as e:
            logger.error(f"Failed to get subscriptions count: {e}")
            return 0
    
    async def _get_management_group_info(self, user_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get top management group information for global scope"""
        try:
            # Use AdvancedCostService to discover management group
            credential = dual_cred_service.get_user_credential(user_info)
            cost_service = AdvancedCostService(credential)
            
            mg_id = cost_service.get_top_management_group()
            
            if mg_id:
                return {
                    "management_group_id": mg_id,
                    "display_name": "Top Management Group",
                    "description": "Highest level management group with access to all subscriptions"
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get management group info: {e}")
            return None
    
# Global instance
user_access_service = UserAccessService()

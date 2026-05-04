import logging
from typing import Dict, Any, List, Optional
import json
import asyncio
from datetime import datetime, timedelta
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.exceptions import KustoServiceError
from azure.identity import DefaultAzureCredential
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception


def _is_transient_error(exc: BaseException) -> bool:
    """Only retry transient errors — skip permanent ARG/auth failures."""
    non_transient = ("InvalidQuery", "BadRequest", "Forbidden", "Unauthorized", "QueryHasNestedSubQuery", "Functions_Argument")
    return not any(k in str(exc) for k in non_transient)
from openai import AsyncAzureOpenAI
from config import settings

logger = logging.getLogger(__name__)

def _is_child_resource_type(resource_type: str) -> bool:
    """
    Detect if a resource type is likely a child resource.
    Child resources have 3+ segments in their type path.
    
    Examples:
    - Parent: Microsoft.Network/virtualNetworks (2 segments) -> False
    - Child: Microsoft.Network/virtualNetworks/virtualNetworkPeerings (3 segments) -> True
    - Child: Microsoft.Network/networkSecurityGroups/securityRules (3 segments) -> True
    
    Args:
        resource_type: Azure resource type string
        
    Returns:
        True if likely a child resource, False otherwise
    """
    if not resource_type:
        return False
    
    # Count segments after splitting by '/'
    segments = resource_type.split('/')
    return len(segments) >= 3


def _extract_parent_type_and_child_property(resource_type: str) -> tuple[str, str]:
    """
    Extract parent resource type and potential child property name from a child resource type.
    
    Examples:
    - Input: "Microsoft.Network/virtualNetworks/virtualNetworkPeerings"
      Output: ("Microsoft.Network/virtualNetworks", "virtualNetworkPeerings")
    - Input: "Microsoft.Network/networkSecurityGroups/securityRules"
      Output: ("Microsoft.Network/networkSecurityGroups", "securityRules")
    
    Args:
        resource_type: Child resource type string
        
    Returns:
        Tuple of (parent_type, child_property_name)
    """
    if not resource_type or '/' not in resource_type:
        return resource_type, ""
    
    segments = resource_type.split('/')
    if len(segments) < 3:
        return resource_type, ""
    
    # Parent type is first 2 segments
    parent_type = '/'.join(segments[:2])
    # Child property is the last segment (convert to camelCase property name)
    child_property = segments[-1]
    
    return parent_type, child_property


class QueryService:
    """Service for executing queries against Azure data sources"""
    
    def __init__(self):
        self.resource_graph_endpoint = "https://management.azure.com"
        
        # Initialize OpenAI client for AI-powered explanations (both modes)
        self.openai_client = None
        if settings.OPENAI_API_KEY and settings.OPENAI_API_BASE:
            # Configure httpx client with proper timeouts
            import httpx
            http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(90.0, connect=10.0)  # 90s total, 10s connect
            )
            
            self.openai_client = AsyncAzureOpenAI(
                api_key=settings.OPENAI_API_KEY,
                azure_endpoint=settings.OPENAI_API_BASE,
                api_version=settings.OPENAI_API_VERSION,
                http_client=http_client,
                max_retries=0,  # Disable retry to avoid exponential backoff delays
                timeout=90.0  # 90 second timeout for complex queries
            )
            logger.info(" AI explanations enabled for query service")
        else:
            logger.warning(" AI explanations disabled - will use basic formatting")
        
    def _get_user_credential(self, user_info: Dict[str, Any], token_type: str = 'management'):
        """
        Get credential from user token based on API type
        Uses dual_credential_service for centralized credential management
        """
        from services.dual_credential_service import dual_cred_service
        return dual_cred_service.get_user_credential(user_info, token_type)
        
    @retry(stop=stop_after_attempt(settings.QUERY_RETRY_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=settings.QUERY_RETRY_MIN_WAIT, max=settings.QUERY_RETRY_MAX_WAIT), retry=retry_if_exception(_is_transient_error))
    async def execute_query(
        self, 
        structured_query: Dict[str, Any], 
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a structured query against Azure Resource Graph
        """
        try:
            query_type = structured_query.get("type", "resource_graph")
            
            if query_type == "resource_graph":
                return await self._execute_resource_graph_query(
                    structured_query, subscription_ids, user_info
                )
            elif query_type == "log_analytics":
                return await self._execute_log_analytics_query(
                    structured_query, user_info
                )
            elif query_type == "azure_metrics":
                # Check if multi-resource query
                if structured_query.get("multi_resource"):
                    return await self._execute_multi_resource_metrics_query(
                        structured_query, subscription_ids, user_info
                    )
                return await self._execute_metrics_query(
                    structured_query, user_info
                )
            elif query_type == "cost_analysis":
                return await self._execute_cost_query(
                    structured_query, subscription_ids, user_info
                )
            elif query_type == "resource_health":
                return await self._execute_health_query(
                    structured_query, subscription_ids, user_info
                )
            elif query_type == "policy_compliance":
                return await self._execute_policy_compliance_query(
                    structured_query, subscription_ids, user_info
                )
            elif query_type == "security_score":
                return await self._execute_security_score_query(
                    structured_query, subscription_ids, user_info
                )
            else:
                raise ValueError(f"Unsupported query type: {query_type}")
                
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            raise
    
    async def _execute_resource_graph_query(
        self, 
        query: Dict[str, Any], 
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute Azure Resource Graph query using the Azure SDK
        """
        try:
            # Get the KQL query (either pre-generated or build from structured query)
            kql_query = query.get("kql_query") or query.get("query")
            
            if not kql_query:
                # Build KQL from structured query if no KQL provided
                kql_query = self._build_resource_graph_kql(query)
            
            # Get user credential (management token for Resource Graph API)
            user_credential = self._get_user_credential(user_info, token_type='management')
            
            # Import Resource Graph client
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
            import asyncio
            
            # Create Resource Graph client
            client = ResourceGraphClient(user_credential)
            
            # Determine query scope strategy
            # For global scope (many subscriptions), use None to query ALL accessible subscriptions
            # Azure Resource Graph will automatically filter based on user's RBAC permissions
            if len(subscription_ids) > 50:
                # Global scope: Let Azure Resource Graph query all accessible subscriptions
                # This is more efficient than passing 100+ subscription IDs
                logger.info(f"Global scope detected ({len(subscription_ids)} subscriptions) - using tenant-wide query (subscriptions=None)")
                query_scope_subscriptions = None  # Query ALL accessible subscriptions
            else:
                # Specific scope: Use explicit subscription IDs
                logger.info(f"Query scope: {len(subscription_ids)} subscriptions (using explicit subscription IDs)")
                query_scope_subscriptions = subscription_ids
            
            # Collect all results with pagination
            all_results = []
            skip_token = None
            page_count = 0
            max_pages = query.get("max_pages", 20)  # Callers can restrict pagination (e.g. agentic loop uses 3)
            
            while page_count < max_pages:
                page_count += 1
                
                # Build query request with skip token if available
                query_options = QueryRequestOptions(
                    result_format="objectArray",
                    skip_token=skip_token,
                    top=1000  # Request maximum results per page
                )
                
                # Build query request
                query_request = QueryRequest(
                    subscriptions=query_scope_subscriptions,
                    query=kql_query,
                    options=query_options
                )
                
                # Execute query (synchronous call wrapped in async)
                def run_query():
                    return client.resources(query_request)
                
                response = await asyncio.get_event_loop().run_in_executor(None, run_query)
                
                # Extract results from this page
                page_results = response.data if hasattr(response, 'data') else []
                all_results.extend(page_results)
                
                # Check for skip token and total records
                skip_token = response.skip_token if hasattr(response, 'skip_token') else None
                total_records_from_api = response.total_records if hasattr(response, 'total_records') else None
                result_truncated = response.result_truncated if hasattr(response, 'result_truncated') else None
                
                logger.info(f"Resource Graph query page {page_count} returned {len(page_results)} resources (total so far: {len(all_results)})")
                logger.info(f"API response - skip_token: {skip_token is not None}, total_records: {total_records_from_api}, result_truncated: {result_truncated}")
                
                # Check if there are more pages
                if not skip_token:
                    # No more pages
                    break
            
            # Extract final results
            results = all_results
            total_records = response.total_records if hasattr(response, 'total_records') else len(results)
            
            logger.info(f"Resource Graph query executed successfully, returned {len(results)} total resources across {page_count} pages")
            
            # SMART FALLBACK: Detect child resource queries that return 0 results
            if len(results) == 0 and query.get("natural_language_query"):
                # Check if the query contains a potential child resource type
                child_type_detected = self._detect_child_resource_in_query(kql_query)
                
                if child_type_detected:
                    parent_type, child_property = child_type_detected
                    logger.info(f" Zero results for potential child resource. Detected parent: {parent_type}, child: {child_property}")
                    logger.info(f"Attempting automatic retry with mv-expand pattern...")
                    
                    # Attempt to regenerate with child resource guidance
                    try:
                        from services.ai_service import AIService
                        ai_service = AIService()
                        
                        if ai_service.is_available():
                            # Build child resource context
                            child_resource_context = {
                                "is_child_resource": True,
                                "parent_type": parent_type,
                                "child_property": child_property,
                                "subscription_ids": subscription_ids,
                                "resource_group": query.get("resource_group")
                            }
                            
                            # Regenerate query with child resource guidance
                            new_kql = await ai_service._regenerate_resource_graph_kql_with_docs(
                                natural_query=query["natural_language_query"],
                                failed_query=kql_query,
                                error_message=f"Query returned 0 results. This appears to be a child resource query. Child resources don't appear as standalone rows.",
                                resource_context=child_resource_context
                            )
                            
                            if new_kql and new_kql != kql_query:
                                logger.info(f" Retrying with child resource pattern: {new_kql[:250]}...")
                                
                                # Retry with new query
                                query_request = QueryRequest(
                                    subscriptions=query_scope_subscriptions,
                                    query=new_kql,
                                    options=QueryRequestOptions(result_format="objectArray")
                                )
                                
                                retry_response = await asyncio.get_event_loop().run_in_executor(
                                    None, 
                                    lambda: client.resources(query_request)
                                )
                                
                                retry_results = retry_response.data if hasattr(retry_response, 'data') else []
                                retry_total = retry_response.total_records if hasattr(retry_response, 'total_records') else len(retry_results)
                                
                                if len(retry_results) > 0:
                                    logger.info(f" Child resource fallback succeeded! Found {len(retry_results)} results")
                                    return {
                                        "success": True,
                                        "results": retry_results,
                                        "total_records": retry_total,
                                        "query": new_kql,
                                        "query_type": "resource_graph",
                                        "child_resource_fallback": True
                                    }
                                else:
                                    logger.info(f" Child resource fallback still returned 0 results")
                    except Exception as fallback_error:
                        logger.warning(f"Child resource fallback failed: {fallback_error}")
            
            return {
                "success": True,
                "results": results,
                "total_records": total_records,
                "query": kql_query,
                "query_type": "resource_graph"
            }
            
        except Exception as e:
            logger.error(f"Resource Graph query failed: {e}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            
            # Check if this is a query syntax error that we can retry
            error_str = str(e)
            if "InvalidQuery" in error_str or "JoinOperator" in error_str:
                logger.info("Detected query syntax error, attempting to regenerate query")
                
                # Try to regenerate the query using AI with error context
                try:
                    from services.ai_service import AIService
                    ai_service = AIService()
                    
                    if ai_service.is_available() and query.get("natural_language_query"):
                        # Extract resource context
                        resource_context = {
                            "subscription_ids": subscription_ids,
                            "resource_group": query.get("resource_group")
                        }
                        
                        # Regenerate query with error context
                        new_kql = await ai_service._regenerate_resource_graph_kql_with_docs(
                            natural_query=query["natural_language_query"],
                            failed_query=kql_query,
                            error_message=error_str,
                            resource_context=resource_context
                        )
                        
                        if new_kql and new_kql != kql_query:
                            logger.info(f"Retrying with regenerated query: {new_kql[:200]}...")
                            
                            # Retry with new query
                            query_request = QueryRequest(
                                subscriptions=subscription_ids,
                                query=new_kql,
                                options=QueryRequestOptions(result_format="objectArray")
                            )
                            
                            response = await asyncio.get_event_loop().run_in_executor(
                                None, 
                                lambda: client.resources(query_request)
                            )
                            
                            results = response.data if hasattr(response, 'data') else []
                            total_records = response.total_records if hasattr(response, 'total_records') else len(results)
                            
                            logger.info(f"Regenerated query succeeded, returned {len(results)} resources")
                            
                            return {
                                "success": True,
                                "results": results,
                                "total_records": total_records,
                                "query": new_kql,
                                "query_type": "resource_graph",
                                "regenerated": True
                            }
                except Exception as retry_error:
                    logger.error(f"Query regeneration failed: {retry_error}")
            
            raise
    
    async def _ai_explain_resource_health(self, health_data: Dict[str, Any], is_single: bool) -> str:
        """AI generates contextual explanation for resource health"""
        if not self.openai_client:
            return "Resource health data retrieved"
        
        try:
            from config import settings
            
            system_prompt = """You are an Azure infrastructure expert. Explain resource health status in markdown. Use emoji. For multiple resources include percentage and status breakdown. 2-3 sentences max."""
            
            if is_single:
                availability = health_data.get("availability_state", "Unknown")
                summary = health_data.get("summary", "")
                reason = health_data.get("reason_type", "")
                occurred = health_data.get("occurred_time", "")
                
                user_message = f"""Explain this resource health status:
- State: {availability}
- Summary: {summary}
- Reason: {reason}
- Since: {occurred}"""
            else:
                summary = health_data.get("summary", {})
                health_pct = health_data.get("health_percentage", 0)
                
                user_message = f"""Explain this overall health status:
- Health: {health_pct}%
- Available: {summary.get('available', 0)}
- Unavailable: {summary.get('unavailable', 0)}
- Degraded: {summary.get('degraded', 0)}
- Unknown: {summary.get('unknown', 0)}"""
            
            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=300
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.warning(f"AI health explanation failed: {e}")
            if is_single:
                return f"Status: {health_data.get('availability_state', 'Unknown')}"
            else:
                return f"Health: {health_data.get('health_percentage', 0)}%"
    
    async def _execute_policy_compliance_query(
        self,
        structured_query: Dict[str, Any],
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Azure Policy Compliance query with AI-powered explanation"""
        try:
            from services.governance_service import GovernanceService
            governance_service = GovernanceService()
            
            logger.info(f" Policy compliance for {len(subscription_ids)} subscription(s)")
            
            # Call existing service to get real Azure data
            data = await governance_service.get_policy_compliance(subscription_ids, user_info)
            
            # Let AI generate the explanation (NO hardcoded thresholds!)
            if self.openai_client:
                msg = await self._ai_explain_policy_compliance(data)
            else:
                # Minimal fallback only if AI unavailable
                pct = data.get("overall_compliance_percentage", 0)
                comp = data.get("compliant_resources", 0)
                non_comp = data.get("non_compliant_resources", 0)
                total = data.get("total_resources", 0)
                msg = f"**Policy Compliance: {pct:.1f}%** - {comp} compliant, {non_comp} non-compliant ({total} total)"
            
            return {
                "success": True,
                "data": data,
                "message": msg,
                "query_type": "policy_compliance",
                "total_records": data.get("total_resources", 0)
            }
        except Exception as e:
            # Log detailed error and stack trace server-side, but do not expose it to clients
            logger.error(f"Policy compliance failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": "Failed to get policy compliance",
                "query_type": "policy_compliance"
            }
    
    async def _execute_security_score_query(
        self,
        structured_query: Dict[str, Any],
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Security Score query with AI-powered explanation"""
        try:
            from services.governance_service import GovernanceService
            governance_service = GovernanceService()
            
            logger.info(f" Security score for {len(subscription_ids)} subscription(s)")
            
            # Call existing service to get real Azure data
            data = await governance_service.get_security_score(subscription_ids, user_info)
            
            # Let AI generate the explanation (NO hardcoded thresholds!)
            if self.openai_client:
                msg = await self._ai_explain_security_score(data)
            else:
                # Minimal fallback only if AI unavailable
                curr = data.get("current_score", 0)
                max_score = data.get("max_score", 0)
                pct = data.get("overall_score", 0)
                msg = f"**Security Score: {curr}/{max_score} ({pct:.1f}%)**"
            
            return {
                "success": True,
                "data": data,
                "message": msg,
                "query_type": "security_score",
                "total_records": 1
            }
            # Log detailed error and stack trace server-side, but do not expose it to clients
        except Exception as e:
            logger.error(f"Security score failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": "Failed to get security score",
                "query_type": "security_score"
            }
    
    async def _ai_explain_policy_compliance(self, compliance_data: Dict[str, Any]) -> str:
        """AI generates contextual explanation for policy compliance (NO hardcoded thresholds)"""
        # Prepare fallback message first
        pct = compliance_data.get("overall_compliance_percentage", 0)
        compliant = compliance_data.get("compliant_resources", 0)
        non_compliant = compliance_data.get("non_compliant_resources", 0)
        total = compliance_data.get("total_resources", 0)
        fallback = f"**Policy Compliance: {pct:.1f}%** - {compliant} compliant, {non_compliant} non-compliant ({total} total)"
        
        if not self.openai_client:
            return fallback
        
        try:
            system_prompt = """You are an Azure governance expert. Explain Azure Policy compliance concisely in markdown with emoji and 1-2 actionable insights. 2-3 sentences max."""
            
            overall_pct = compliance_data.get('overall_compliance_percentage', 0)
            compliant = compliance_data.get('compliant_resources', 0)
            non_compliant = compliance_data.get('non_compliant_resources', 0)
            exempt = compliance_data.get('exempt_resources', 0)
            total = compliance_data.get('total_resources', 0)
            
            user_message = (
                f"Explain this Azure Policy compliance data:\n\n"
                f"Overall Compliance: {overall_pct:.1f}%\n"
                f"Compliant Resources: {compliant:,}\n"
                f"Non-Compliant Resources: {non_compliant:,}\n"
                f"Exempt Resources: {exempt:,}\n"
                f"Total Resources: {total:,}\n\n"
                f"Provide a clear explanation with appropriate emoji and actionable insights."
            )
            
            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=300
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.warning(f"AI policy explanation failed: {e}")
            return fallback
    
    async def _ai_explain_security_score(self, security_data: Dict[str, Any]) -> str:
        """AI generates contextual explanation for security score (NO hardcoded thresholds)"""
        # Prepare fallback message first
        current = security_data.get("current_score", 0)
        maximum = security_data.get("max_score", 0)
        percentage = security_data.get("overall_score", 0)
        fallback = f"**Security Score: {current:.1f}/{maximum:.1f} ({percentage:.1f}%)**"
        
        if not self.openai_client:
            return fallback
        
        try:
            # Extract scores - governance service returns them at top level
            current = security_data.get("current_score", 0)
            maximum = security_data.get("max_score", 0)
            percentage = security_data.get("overall_score", 0)  # Already calculated
            
            system_prompt = """You are a Microsoft Defender for Cloud expert. Explain Secure Score concisely in markdown with emoji and 1-2 actionable security insights. 2-3 sentences max."""
            
            user_message = (
                f"Explain this Microsoft Defender Secure Score:\n\n"
                f"Current Score: {current}\n"
                f"Maximum Score: {maximum}\n"
                f"Percentage: {percentage:.1f}%\n\n"
                f"Provide a clear explanation with appropriate emoji and security insights."
            )
            
            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=300
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.warning(f"AI security explanation failed: {e}")
            return fallback  # Return fallback instead of None
    
    async def _ai_match_metric_name(self, requested_metric: str, valid_metrics: List[str]) -> Optional[str]:
        """
        Use AI to intelligently match the requested metric name to the best available metric.
        This handles semantic understanding, synonyms, and user intent without hardcoded mappings.
        
        Args:
            requested_metric: The metric name the user/AI requested
            valid_metrics: List of valid metric names for this resource type
            
        Returns:
            The best matching metric name, or None if no good match found
        """
        if not self.openai_client:
            logger.warning("AI metric matching not available - OpenAI client not initialized")
            return None
        
        try:
            # Limit to top 50 metrics for token efficiency
            metrics_list = valid_metrics[:50]
            
            system_prompt = """You are an Azure Monitor expert. Match the requested metric to the best available metric based on what the user intends to measure.

Consider semantic meaning, not just string similarity. Only match if you're confident they measure the same thing.

Return ONLY the exact metric name from the available list, or "NONE" if no suitable match exists."""
            
            user_message = f"""Requested: "{requested_metric}"

Available: {', '.join(metrics_list)}

Best match?"""
            
            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=200
            )
            
            result = response.choices[0].message.content.strip()
            
            # Validate the result is in the valid metrics list
            if result == "NONE":
                logger.info(f" AI: No suitable match found for '{requested_metric}'")
                return None
            
            if result in valid_metrics:
                logger.info(f" AI Match: '{requested_metric}' → '{result}' (AI decision)")
                return result
            else:
                logger.warning(f" AI returned invalid metric: '{result}', not in valid list")
                return None
                
        except Exception as e:
            logger.warning(f"AI metric matching failed: {e}")
            return None
    
    def _build_resource_graph_kql(self, query: Dict[str, Any]) -> str:
        """
        Build KQL query for Azure Resource Graph
        """
        base_table = "Resources"
        
        # Extract query components
        resource_types = query.get("resource_types", [])
        locations = query.get("locations", [])
        resource_groups = query.get("resource_groups", [])
        filters = query.get("filters", [])
        
        # Build WHERE clauses
        where_clauses = []
        
        if resource_types:
            type_filter = " or ".join([f"type =~ '{rt}'" for rt in resource_types])
            where_clauses.append(f"({type_filter})")
        
        if locations:
            location_filter = " or ".join([f"location =~ '{loc}'" for loc in locations])
            where_clauses.append(f"({location_filter})")
        
        if resource_groups:
            rg_filter = " or ".join([f"resourceGroup =~ '{rg}'" for rg in resource_groups])
            where_clauses.append(f"({rg_filter})")
        
        # Add custom filters
        for filter_item in filters:
            where_clauses.append(filter_item)
        
        # Build complete query
        kql_parts = [base_table]
        
        if where_clauses:
            kql_parts.append(f"| where {' and '.join(where_clauses)}")
        
        # Add projections
        projections = query.get("projections", [
            "name", "type", "location", "resourceGroup", "subscriptionId", "id"
        ])
        kql_parts.append(f"| project {', '.join(projections)}")
        
        # Add sorting
        order_by = query.get("order_by", "name asc")
        kql_parts.append(f"| order by {order_by}")
        
        # Add limit
        limit = query.get("limit", 100)
        kql_parts.append(f"| limit {limit}")
        
        return "\n".join(kql_parts)
    
    def _detect_child_resource_in_query(self, kql_query: str) -> Optional[tuple[str, str]]:
        """
        Detect if a KQL query is attempting to query a child resource type.
        Returns (parent_type, child_property) if detected, None otherwise.
        
        Examples:
        - Query: where type =~ "Microsoft.Network/virtualNetworks/virtualNetworkPeerings"
          Returns: ("Microsoft.Network/virtualNetworks", "virtualNetworkPeerings")
        - Query: where type =~ "Microsoft.Network/virtualNetworks"
          Returns: None (not a child resource)
        """
        import re
        
        # Look for type filter patterns in the query
        # Pattern: type =~ "Microsoft.Provider/resourceType/childType"
        type_pattern = r'type\s*=~\s*["\']([^"\']+)["\']'
        matches = re.findall(type_pattern, kql_query, re.IGNORECASE)
        
        for resource_type in matches:
            if _is_child_resource_type(resource_type):
                parent_type, child_property = _extract_parent_type_and_child_property(resource_type)
                return (parent_type, child_property)
        
        return None
    
    @retry(stop=stop_after_attempt(settings.QUERY_RETRY_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=settings.QUERY_RETRY_MIN_WAIT, max=settings.QUERY_RETRY_MAX_WAIT))
    async def execute_kql_query(
        self, 
        kql_query: str, 
        workspace_id: str,
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute KQL query against Log Analytics workspace using real Azure Monitor Query client
        """
        try:
            # Get user credential (LA token for Log Analytics queries)
            user_credential = self._get_user_credential(user_info, token_type='loganalytics')
            
            # Import Azure Monitor Query client
            from azure.monitor.query import LogsQueryClient
            from azure.monitor.query import LogsQueryStatus
            from datetime import datetime, timedelta
            import asyncio
            
            # Create the logs query client
            client = LogsQueryClient(user_credential)
            
            # The Azure Monitor Query API can accept either:
            # 1. Full resource ID: /subscriptions/.../workspaces/name
            # 2. Workspace customer ID (GUID)
            # If we have a full resource ID, try it first
            query_workspace_id = workspace_id
            
            # Log the workspace we're querying
            logger.info(f"Querying workspace: {query_workspace_id}")
            
            # Execute the query (synchronous call wrapped in async)
            logger.info(f"Executing KQL query: {kql_query[:200]}...")
            
            # Query with default timespan of last 24 hours
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=24)
            
            # Run synchronous query in thread pool
            def run_query():
                return client.query_workspace(
                    workspace_id=query_workspace_id,
                    query=kql_query,
                    timespan=(start_time, end_time)
                )
            
            response = await asyncio.get_event_loop().run_in_executor(None, run_query)
            
            if response.status == LogsQueryStatus.SUCCESS:
                # Convert results to list of dictionaries
                results = []
                if response.tables:
                    table = response.tables[0]  # Get first table
                    for row in table.rows:
                        row_dict = {}
                        for i, column in enumerate(table.columns):
                            row_dict[column.name] = row[i]
                        results.append(row_dict)
                
                logger.info(f"KQL query executed successfully, returned {len(results)} rows")
                
                return {
                    "success": True,
                    "query": kql_query,
                    "workspace_id": workspace_id,
                    "results": results,
                    "row_count": len(results),
                    "execution_time_ms": getattr(response, 'execution_time', 0)
                }
            else:
                logger.error(f"KQL query failed with status: {response.status}")
                return {
                    "success": False,
                    "query": kql_query,
                    "workspace_id": workspace_id,
                    "error": f"Query failed with status: {response.status}",
                    "results": [],
                    "row_count": 0
                }
                
        except Exception as e:
            logger.error("KQL query execution failed", exc_info=True)
            return {
                "success": False,
                "query": kql_query,
                "workspace_id": workspace_id,
                "message": "Log Analytics query failed",
                "error": "Log Analytics query failed",
                "results": [],
                "row_count": 0,
            }
    
    async def discover_workspaces_for_query(
        self,
        structured_query: Dict[str, Any],
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Discover Log Analytics workspaces that might contain relevant data
        """
        try:
            # Get management credential for Resource Management API
            # Workspace discovery requires Management token, not LA token
            management_token = user_info.get('management_token')
            if management_token:
                from auth import UserTokenCredential
                user_credential = UserTokenCredential(management_token)
            else:
                # Fallback to default credential
                logger.warning("No management token available for workspace discovery")
                from azure.identity import DefaultAzureCredential
                user_credential = DefaultAzureCredential()
            
            from azure.mgmt.resource import ResourceManagementClient
            
            workspaces = []
            
            # Search for Log Analytics workspaces in the subscriptions
            for subscription_id in subscription_ids:
                try:
                    client = ResourceManagementClient(user_credential, subscription_id)
                    
                    # Find Log Analytics workspaces
                    logger.info(f"Searching for Log Analytics workspaces in subscription {subscription_id}")
                    resources = client.resources.list(
                        filter="resourceType eq 'Microsoft.OperationalInsights/workspaces'"
                    )
                    
                    resource_count = 0
                    for resource in resources:
                        resource_count += 1
                        workspace_info = {
                            "workspace_id": resource.id.split('/')[-1],
                            "name": resource.name,
                            "resource_group": resource.id.split('/')[4],
                            "subscription_id": subscription_id,
                            "location": resource.location,
                            "full_id": resource.id
                        }
                        workspaces.append(workspace_info)
                        logger.info(f"Found workspace: {workspace_info['name']} in {workspace_info['resource_group']}")
                    
                    if resource_count == 0:
                        logger.warning(f"No Log Analytics workspaces found in subscription {subscription_id}")
                        
                except Exception as e:
                    logger.warning(f"Failed to list workspaces in subscription {subscription_id}: {e}")
                    continue
            
            logger.info(f"Discovered {len(workspaces)} Log Analytics workspaces")
            return workspaces
            
        except Exception as e:
            logger.error(f"Workspace discovery failed: {e}")
            return []
    
    async def find_diagnostic_settings_workspaces(
        self,
        resource_id: str,
        user_info: Dict[str, Any]
    ) -> List[str]:
        """Find Log Analytics workspaces for diagnostic settings"""
        try:
            from services.diagnostic_settings_service import DiagnosticSettingsService
            
            diag_service = DiagnosticSettingsService()
            diag_info = await diag_service.get_log_mode_for_resource(resource_id, user_info)
            
            workspaces = []
            
            # Extract workspace IDs from all settings (primary + fallback)
            if diag_info.get('primary'):
                workspace_id = diag_info['primary']['workspace_id']
                workspaces.append(workspace_id)
            
            if diag_info.get('fallback'):
                workspace_id = diag_info['fallback']['workspace_id']
                if workspace_id not in workspaces:  # Avoid duplicates
                    workspaces.append(workspace_id)
            return workspaces
            
        except Exception as e:
            logger.error(f"Failed to get diagnostic settings for resource {resource_id}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []
    
    async def get_diagnostic_settings_with_mode(
        self,
        resource_id: str,
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Get full diagnostic settings info with mode detection (Dedicated vs AzureDiagnostics).
        
        Returns:
            {
                'resource_id': str,
                'resource_name': str,
                'resource_type': str,
                'has_diagnostics': bool,
                'primary': {
                    'workspace_id': str,
                    'workspace_name': str,
                    'mode': 'Dedicated' | 'AzureDiagnostics'
                } | None,
                'fallback': {...} | None
            }
        """
        try:
            from services.diagnostic_settings_service import DiagnosticSettingsService
            
            diag_service = DiagnosticSettingsService()
            return await diag_service.get_log_mode_for_resource(resource_id, user_info)
            
        except Exception as e:
            logger.error(f"Failed to get diagnostic settings with mode for resource {resource_id}: {e}")
            return {
                'resource_id': resource_id,
                'has_diagnostics': False,
                'primary': None,
                'fallback': None
            }
    
    async def resolve_log_analytics_workspace(
        self,
        kql_query: str,
        subscription_ids: List[str],
        user_info: Dict[str, Any],
        resource_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """
        Single shared resolver for Log Analytics workspace IDs and log mode.

        Azure has two completely different diagnostic-settings mechanisms:

        A) ACTIVITY LOGS (AzureActivity table)
           Subscription-scoped platform events (ARM operations, who did what).
           Routing configured at the SUBSCRIPTION level:
             GET /subscriptions/{sub}/providers/microsoft.insights/diagnosticSettings
           Category groups: "audit" or "allLogs"; named: "Administrative", "Security", etc.
           Target workspace can be in a DIFFERENT subscription (central logging pattern).
           Mode is always "ActivityLog" — AzureActivity table, no Dedicated/AzureDiagnostics split.
           → handled by find_activity_log_workspaces()

        B) RESOURCE LOGS (AzureDiagnostics or resource-specific Dedicated tables)
           A resource's own telemetry (App Service access logs, Key Vault audit events, etc.).
           Routing configured at the RESOURCE level:
             GET /{resourceId}/providers/microsoft.insights/diagnosticSettings
           logAnalyticsDestinationType determines the table mode:
             "Dedicated"  → resource-specific tables (e.g. AZFWDnsQuery, AppServiceHTTPLogs)
             null/missing  → AzureDiagnostics (legacy combined table)
           → handled by get_diagnostic_settings_with_mode() via DiagnosticSettingsService

        Resolution order:
        1. AzureActivity query + subscription_ids
           → subscription-level diagnostic settings (cross-subscription aware)
           → returns log_mode = "ActivityLog"
        2. resource_id provided (non-AzureActivity resource query)
           → resource-level diagnostic settings, preserving Dedicated vs AzureDiagnostics mode
           → returns log_mode = "Dedicated" | "AzureDiagnostics"
        3. Last resort: ARM listing of workspaces within the subscriptions
           → returns log_mode = "unknown"

        Returns:
            {"workspace_id": str, "log_mode": str} or None if not found.
        """
        is_activity_log = "AzureActivity" in kql_query

        # Step 1 — subscription-level diagnostic settings for Activity Logs.
        # These can point to a workspace in a completely different subscription.
        if is_activity_log and subscription_ids:
            for sub_id in subscription_ids:
                try:
                    ws_ids = await self.find_activity_log_workspaces(sub_id, user_info)
                    if ws_ids:
                        logger.info(f"resolve_log_analytics_workspace: activity-log diag settings ({sub_id}): {ws_ids[0]}")
                        return {"workspace_id": ws_ids[0], "log_mode": "ActivityLog"}
                except Exception:
                    logger.warning(f"resolve_log_analytics_workspace: activity-log diag settings failed for {sub_id}", exc_info=True)

        # Step 2 — resource-level diagnostic settings, preserving the Dedicated/AzureDiagnostics
        # mode so callers can generate the correct KQL table name.
        if resource_id and not is_activity_log:
            try:
                diag_info = await self.get_diagnostic_settings_with_mode(resource_id, user_info)
                if diag_info.get("has_diagnostics"):
                    primary = diag_info.get("primary")
                    if primary:
                        logger.info(f"resolve_log_analytics_workspace: resource diag settings ({resource_id}): {primary['workspace_id']} mode={primary['mode']}")
                        return {"workspace_id": primary["workspace_id"], "log_mode": primary["mode"]}
            except Exception:
                logger.warning("resolve_log_analytics_workspace: resource diag settings lookup failed", exc_info=True)

        # Step 3 — ARM listing: find any workspace deployed within these subscriptions.
        if subscription_ids:
            try:
                workspaces = await self.discover_workspaces_for_query({}, subscription_ids, user_info)
                if workspaces:
                    logger.info(f"resolve_log_analytics_workspace: ARM listing fallback: {workspaces[0]['name']}")
                    return {"workspace_id": workspaces[0]["full_id"], "log_mode": "unknown"}
            except Exception:
                logger.warning("resolve_log_analytics_workspace: ARM workspace listing failed", exc_info=True)

        logger.warning("resolve_log_analytics_workspace: no workspace found")
        return None

    async def find_activity_log_workspaces(self, subscription_id: str, user_info: Dict[str, Any]) -> List[str]:
        """
        Find Log Analytics workspaces configured for subscription Activity Logs
        Activity Logs have subscription-level diagnostic settings, not resource-level
        Returns full workspace resource IDs
        """
        try:
            # Get MANAGEMENT token for diagnostic settings API (not LA token!)
            user_token = user_info.get('management_token') or user_info.get('token')
            if not user_token:
                logger.error("No management token found in user_info")
                return []
            
            logger.info(f"Using token for Activity Log diagnostic settings (Management API)")
            
            import requests
            
            # Build the REST API URL for subscription diagnostic settings
            api_version = "2021-05-01-preview"
            url = f"https://management.azure.com/subscriptions/{subscription_id}/providers/microsoft.insights/diagnosticSettings?api-version={api_version}"
            
            headers = {
                "Authorization": f"Bearer {user_token}",
                "Content-Type": "application/json"
            }
            
            logger.info(f"Fetching Activity Log diagnostic settings for subscription: {subscription_id}")
            
            # Make the REST API call
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 404:
                logger.info(f"No Activity Log diagnostic settings configured for subscription {subscription_id}")
                return []
            
            response.raise_for_status()
            data = response.json()
            
            workspaces = []
            
            # Parse the response to extract workspace IDs
            if "value" in data:
                for setting in data["value"]:
                    workspace_id = setting.get("properties", {}).get("workspaceId")
                    if workspace_id:
                        # Check if Activity Log categories are enabled.
                        # Azure uses categoryGroup "audit" or "allLogs", or explicit category names.
                        # An empty logs array with a workspaceId means all categories are routed.
                        logs = setting.get("properties", {}).get("logs", [])
                        has_activity_log = not logs or any(
                            log.get("categoryGroup") in ("allLogs", "audit") or
                            log.get("category") in ["Administrative", "Security", "ServiceHealth", "Alert", "Recommendation", "Policy", "Autoscale", "ResourceHealth"]
                            for log in logs if log.get("enabled")
                        )
                        
                        if has_activity_log:
                            workspaces.append(workspace_id)
                            workspace_name = workspace_id.split("/")[-1]
                            logger.info(f"Found Activity Log workspace in diagnostic setting '{setting.get('name')}': {workspace_name}")
            
            logger.info(f"Found {len(workspaces)} workspaces configured for Activity Logs in subscription {subscription_id}")
            return workspaces
            
        except requests.HTTPError as e:
            logger.error(f"HTTP error getting Activity Log diagnostic settings: {e.response.status_code} - {e.response.text if hasattr(e, 'response') else str(e)}")
            return []
        except Exception as e:
            logger.error(f"Failed to get Activity Log diagnostic settings for subscription {subscription_id}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return []
    
    async def _execute_log_analytics_query(
        self, 
        query: Dict[str, Any],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute Log Analytics query
        """
        try:
            workspace_id = query.get("workspace_id", "")
            kql_query = query.get("kql_query", "")
            
            if not workspace_id or not kql_query:
                raise ValueError("workspace_id and kql_query are required")
            
            return await self.execute_kql_query(kql_query, workspace_id, user_info)
            
        except Exception as e:
            logger.error(f"Log Analytics query failed: {e}")
            raise
    
    async def _execute_cost_query(
        self, 
        query: Dict[str, Any], 
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute cost analysis query using Azure Cost Management API
        """
        try:
            from services.cost_service import CostAnalysisService
            
            # Get user credential (management token for Cost Management API)
            user_credential = self._get_user_credential(user_info, token_type='management')
            
            # Initialize cost service with user credential
            cost_service = CostAnalysisService(user_credential)
            
            # Use the first subscription_id (support for single subscription cost queries)
            subscription_id = subscription_ids[0] if subscription_ids else None
            if not subscription_id:
                # Try to get from query spec
                subscription_id = query.get("subscription_id")
            
            if not subscription_id:
                return {
                    "error": "subscription_id is required for cost analysis",
                    "suggestion": "Please select a subscription"
                }
            
            # Extract query parameters
            scope_type = query.get("scope", "subscription")
            time_range = query.get("time_range", "this_month")
            granularity = query.get("granularity", "Daily")
            resource_group = query.get("resource_group")
            resource_ids = query.get("resource_ids")
            compare_periods = query.get("compare_periods", False)
            custom_start = query.get("custom_start")  # e.g., "2025-10-01"
            custom_end = query.get("custom_end")  # e.g., "2025-10-31"
            
            # Build full Azure scope path
            scope = f"/subscriptions/{subscription_id}"
            
            logger.info(f"Executing cost query: scope_type={scope_type}, scope={scope}, time_range={time_range}, granularity={granularity}, compare_periods={compare_periods}, custom_dates={custom_start} to {custom_end if custom_start else 'N/A'}")
            
            # Execute cost analysis (always includes previous period comparison)
            cost_results = await cost_service.analyze_costs(
                scope=scope,
                time_range=time_range,
                granularity=granularity,
                resource_group=resource_group,
                resource_ids=resource_ids,
                custom_start=custom_start,
                custom_end=custom_end
            )
            
            # If compare_periods flag is set, format response to highlight comparison
            if compare_periods and cost_results.get("success"):
                current_total = cost_results.get("current_period", {}).get("total_cost", 0)
                previous_total = cost_results.get("previous_period", {}).get("total_cost", 0)
                trend = cost_results.get("analysis", {}).get("trend", "stable")
                change_percent = cost_results.get("analysis", {}).get("change_percent", 0)
                change_amount = cost_results.get("analysis", {}).get("change_amount", 0)
                currency = cost_results.get("current_period", {}).get("currency", "USD")
                
                # Get period information
                time_period = cost_results.get("time_period", {})
                current_start = time_period.get("start", "")
                current_end = time_period.get("end", "")
                
                # Get previous period dates from the actual cost analysis result
                # These were calculated correctly in cost_service.py with proper month logic
                previous_period = cost_results.get("previous_period", {})
                previous_start = ""
                previous_end = ""
                
                # Try to extract dates from the previous_period metadata if available
                # Otherwise fall back to calculation (for backward compatibility)
                if "start_date" in previous_period or "time_period" in cost_results:
                    # Some cost services might include start_date/end_date in previous_period
                    previous_start = previous_period.get("start_date", "")
                    previous_end = previous_period.get("end_date", "")
                
                # If not available in metadata, calculate from num_days
                if not previous_start or not previous_end:
                    try:
                        start_dt = datetime.fromisoformat(current_start.replace("Z", ""))
                        prev_num_days = previous_period.get("num_days", 0)
                        
                        if prev_num_days > 0:
                            # Calculate backward from current start
                            prev_end_dt = start_dt - timedelta(days=1)
                            prev_start_dt = prev_end_dt - timedelta(days=prev_num_days - 1)
                            previous_start = prev_start_dt.strftime("%Y-%m-%d")
                            previous_end = prev_end_dt.strftime("%Y-%m-%d")
                    except Exception as e:
                        logger.warning(f"Could not calculate previous period dates: {e}")
                        previous_start = ""
                        previous_end = ""
                
                # Generate descriptive labels for comparison periods
                current_label = "Current Period"
                previous_label = "Previous Period"
                
                if time_range == "this_month":
                    # For "this month", show actual month names
                    try:
                        # Parse current period start date
                        if current_start:
                            current_dt = datetime.fromisoformat(current_start.replace("Z", "").replace("+00:00", ""))
                            current_label = f"This Month ({current_dt.strftime('%B %Y')})"
                        
                        # Parse previous period start date
                        if previous_start:
                            prev_dt = datetime.fromisoformat(f"{previous_start}T00:00:00" if "T" not in previous_start else previous_start)
                            previous_label = f"Last Month ({prev_dt.strftime('%B %Y')})"
                    except Exception as e:
                        logger.warning(f"Could not format month labels: {e}")
                        current_label = "This Month"
                        previous_label = "Last Month"
                        
                elif time_range == "last_month":
                    try:
                        if current_start:
                            current_dt = datetime.fromisoformat(current_start.replace("Z", "").replace("+00:00", ""))
                            current_label = current_dt.strftime('%B %Y')
                            
                            # Calculate two months back
                            first_day = current_dt.replace(day=1)
                            prev_month_end = first_day - timedelta(days=1)
                            previous_label = prev_month_end.strftime('%B %Y')
                    except Exception as e:
                        logger.warning(f"Could not format month labels: {e}")
                        current_label = "Last Month"
                        previous_label = "Two Months Ago"
                        
                elif time_range == "this_year":
                    try:
                        if current_start:
                            current_dt = datetime.fromisoformat(current_start.replace("Z", "").replace("+00:00", ""))
                            current_label = f"This Year ({current_dt.year})"
                            previous_label = f"Last Year ({current_dt.year - 1})"
                    except Exception as e:
                        logger.warning(f"Could not format year labels: {e}")
                        current_label = "This Year"
                        previous_label = "Last Year"
                
                # Add comparison summary for UI
                cost_results["comparison_summary"] = {
                    "current_period": {
                        "total": current_total,
                        "start_date": current_start.split("T")[0] if current_start else "",
                        "end_date": current_end.split("T")[0] if current_end else "",
                        "label": current_label
                    },
                    "previous_period": {
                        "total": previous_total,
                        "start_date": previous_start,
                        "end_date": previous_end,
                        "label": previous_label
                    },
                    "difference": change_amount,
                    "change_percent": change_percent,
                    "trend": trend,
                    "currency": currency,
                    "is_comparison_query": True
                }
                logger.info(f"Comparison result: {current_total:.2f} vs {previous_total:.2f} {currency} ({change_percent:+.1f}%)")
            
            return {
                "query_type": "cost_analysis",
                "subscription_id": subscription_id,
                "scope": scope,
                "scope_type": scope_type,
                "time_range": time_range,
                "compare_periods": compare_periods,
                "results": cost_results,
                "execution_time": "N/A"
            }
            
        except Exception as e:
            logger.error(f"Cost query failed: {e}")
            return {
                "error": "Cost query execution failed",
                "query_type": "cost_analysis"
            }
    
    async def _execute_metrics_query(
        self,
        query: Dict[str, Any],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute Azure Monitor Metrics query for a specific resource
        """
        try:
            resource_id = query.get("resource_id")
            metric_names = query.get("metric_names", [])
            aggregation = query.get("aggregation", "Average")
            time_range = query.get("time_range", "PT1H")  # ISO 8601 duration
            interval = query.get("interval", "PT5M")
            
            if not resource_id:
                raise ValueError("resource_id is required for metrics queries")
            
            if not metric_names:
                raise ValueError("At least one metric name is required")
            
            logger.info(f"Executing metrics query for resource: {resource_id}")
            logger.info(f"Metrics: {metric_names}, Aggregation: {aggregation}, Time Range: {time_range}")
            
            # Get user credential (management token for Metrics API)
            user_credential = self._get_user_credential(user_info, token_type='management')
            
            # Import Azure Monitor Query client
            from azure.monitor.query import MetricsQueryClient
            from datetime import datetime, timedelta
            import re
            
            # Create Metrics Query client
            client = MetricsQueryClient(user_credential)
            
            # Parse ISO 8601 duration to timedelta
            def parse_iso_duration(duration: str) -> timedelta:
                """Parse ISO 8601 duration (PT1H, PT5M, P1D, etc.) to timedelta"""
                # PT1H = 1 hour, PT5M = 5 minutes, PT30S = 30 seconds, P1D = 1 day
                pattern = r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
                match = re.match(pattern, duration)
                if not match:
                    logger.warning(f"Could not parse duration {duration}, defaulting to 1 hour")
                    return timedelta(hours=1)
                
                days = int(match.group(1) or 0)
                hours = int(match.group(2) or 0)
                minutes = int(match.group(3) or 0)
                seconds = int(match.group(4) or 0)
                
                return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
            
            # Calculate time range
            duration = parse_iso_duration(time_range)
            end_time = datetime.utcnow()
            start_time = end_time - duration
            
            # Execute query (synchronous call wrapped in async)
            def run_metrics_query():
                return client.query_resource(
                    resource_uri=resource_id,
                    metric_names=metric_names,
                    timespan=(start_time, end_time),
                    granularity=interval,
                    aggregations=[aggregation]
                )
            
            response = await asyncio.get_event_loop().run_in_executor(None, run_metrics_query)
            
            # Parse results
            results = []
            for metric in response.metrics:
                metric_data = {
                    "name": metric.name,
                    "unit": metric.unit,
                    "timeseries": []
                }
                
                for timeseries in metric.timeseries:
                    series_data = {
                        "data": []
                    }
                    
                    for data_point in timeseries.data:
                        point = {
                            "timestamp": data_point.timestamp.isoformat(),
                        }
                        
                        # Add all available aggregations
                        if hasattr(data_point, 'average') and data_point.average is not None:
                            point['average'] = data_point.average
                        if hasattr(data_point, 'total') and data_point.total is not None:
                            point['total'] = data_point.total
                        if hasattr(data_point, 'minimum') and data_point.minimum is not None:
                            point['minimum'] = data_point.minimum
                        if hasattr(data_point, 'maximum') and data_point.maximum is not None:
                            point['maximum'] = data_point.maximum
                        if hasattr(data_point, 'count') and data_point.count is not None:
                            point['count'] = data_point.count
                        
                        series_data["data"].append(point)
                    
                    metric_data["timeseries"].append(series_data)
                
                results.append(metric_data)
            
            logger.info(f"Metrics query executed successfully, returned {len(results)} metrics")
            
            return {
                "success": True,
                "results": results,
                "total_records": sum(len(m["timeseries"][0]["data"]) for m in results if m["timeseries"]),
                "query_spec": {
                    "resource_id": resource_id,
                    "metric_names": metric_names,
                    "aggregation": aggregation,
                    "time_range": time_range,
                    "interval": interval
                },
                "query_type": "azure_metrics"
            }
            
        except Exception as e:
            error_msg = str(e)
            
            # Check if this is a metric name error with valid metrics list
            if "Failed to find metric configuration" in error_msg and "Valid metrics:" in error_msg:
                try:
                    # Extract the invalid metric name and valid metrics from error message
                    import re
                    # Match metric names that may contain spaces (e.g., "Network In", "Percentage CPU")
                    metric_match = re.search(r'metric: ([^,]+),', error_msg)
                    valid_metrics_match = re.search(r'Valid metrics: (.+?)(?:Code:|$)', error_msg, re.DOTALL)
                    
                    if metric_match and valid_metrics_match:
                        invalid_metric = metric_match.group(1).strip()
                        valid_metrics_str = valid_metrics_match.group(1).strip()
                        valid_metrics = [m.strip() for m in valid_metrics_str.split(',') if m.strip()]
                        
                        logger.warning(f" Invalid metric '{invalid_metric}'. Found in request: {metric_names}")
                        
                        # Check ALL metrics and fix ANY that are invalid (not just the first one)
                        corrected_metric_names = []
                        corrections_made = {}
                        
                        for metric_name in metric_names:
                            if metric_name not in valid_metrics:
                                # This metric needs correction
                                logger.warning(f" '{metric_name}' is invalid - finding best match in {len(valid_metrics)} valid metrics...")
                                best_match = await self._ai_match_metric_name(metric_name, valid_metrics)
                                if best_match:
                                    corrected_metric_names.append(best_match)
                                    corrections_made[metric_name] = best_match
                                    logger.info(f" AI Match: '{metric_name}' → '{best_match}' (AI decision)")
                                else:
                                    # No match found, keep original (will likely fail again)
                                    corrected_metric_names.append(metric_name)
                            else:
                                # This metric is already valid
                                corrected_metric_names.append(metric_name)
                        
                        if corrections_made:
                            logger.info(f" Corrected {len(corrections_made)} metric(s): {corrections_made}")
                            
                            # Retry with ALL corrected metric names
                            def run_corrected_query():
                                return client.query_resource(
                                    resource_uri=resource_id,
                                    metric_names=corrected_metric_names,
                                    timespan=(start_time, end_time),
                                    granularity=interval,
                                    aggregations=[aggregation]
                                )
                            
                            response = await asyncio.get_event_loop().run_in_executor(None, run_corrected_query)
                            
                            # Parse results (same logic as above)
                            results = []
                            for metric in response.metrics:
                                metric_data = {
                                    "name": metric.name,
                                    "unit": metric.unit,
                                    "timeseries": []
                                }
                                
                                for timeseries in metric.timeseries:
                                    series_data = {"data": []}
                                    
                                    for data_point in timeseries.data:
                                        point = {"timestamp": data_point.timestamp.isoformat()}
                                        
                                        if hasattr(data_point, 'average') and data_point.average is not None:
                                            point['average'] = data_point.average
                                        if hasattr(data_point, 'total') and data_point.total is not None:
                                            point['total'] = data_point.total
                                        if hasattr(data_point, 'minimum') and data_point.minimum is not None:
                                            point['minimum'] = data_point.minimum
                                        if hasattr(data_point, 'maximum') and data_point.maximum is not None:
                                            point['maximum'] = data_point.maximum
                                        if hasattr(data_point, 'count') and data_point.count is not None:
                                            point['count'] = data_point.count
                                        
                                        series_data["data"].append(point)
                                    
                                    metric_data["timeseries"].append(series_data)
                                
                                results.append(metric_data)
                            
                            logger.info(f" Metrics query succeeded with corrected name '{best_match}'")
                            
                            return {
                                "success": True,
                                "results": results,
                                "total_records": sum(len(m["timeseries"][0]["data"]) for m in results if m["timeseries"]),
                                "query_spec": {
                                    "resource_id": resource_id,
                                    "metric_names": corrected_metric_names,
                                    "aggregation": aggregation,
                                    "time_range": time_range,
                                    "interval": interval
                                },
                                "query_type": "azure_metrics",
                                "metric_name_corrected": f"{invalid_metric} → {best_match}"
                            }
                        else:
                            # No match found - provide helpful suggestions
                            logger.error(f" Could not find suitable match for '{invalid_metric}'")
                            logger.error(f"   Valid metrics for this resource: {', '.join(valid_metrics[:10])}{' ...' if len(valid_metrics) > 10 else ''}")
                            
                            # Try to give AI helpful suggestions based on semantic meaning
                            suggestions = []
                            invalid_words = re.findall(r'[A-Z][a-z]*', invalid_metric)  # CamelCase split
                            for word in invalid_words:
                                for vm in valid_metrics:
                                    if word.lower() in vm.lower() and vm not in suggestions:
                                        suggestions.append(vm)
                                        if len(suggestions) >= 3:
                                            break
                                if len(suggestions) >= 3:
                                    break
                            
                            if suggestions:
                                logger.info(f" Similar metrics you might want: {', '.join(suggestions)}")
                
                except Exception as retry_error:
                    logger.error(f"Failed to auto-correct metric name: {retry_error}")
            
            logger.error(f"Metrics query failed: {e}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            raise
    
    async def _execute_multi_resource_metrics_query(
        self,
        query: Dict[str, Any],
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute Azure Monitor Metrics query for multiple resources of the same type
        Steps:
        1. Use Resource Graph to list all resources of the specified type
        2. Query metrics for each resource
        3. Aggregate results
        """
        try:
            resource_type = query.get("resource_type", "microsoft.compute/virtualmachines")
            metric_names = query.get("metric_names", [])
            aggregation = query.get("aggregation", "Average")
            time_range = query.get("time_range", "PT1H")
            interval = query.get("interval", "PT5M")
            
            logger.info(f"Executing multi-resource metrics query for type: {resource_type}")
            logger.info(f"Metrics: {metric_names}, Aggregation: {aggregation}")
            
            # Step 1: Get all resources of this type using Resource Graph (management token)
            user_credential = self._get_user_credential(user_info, token_type='management')
            
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
            
            client = ResourceGraphClient(user_credential)
            
            # Build query to get all resources of the specified type
            resource_query = f"""
            Resources
            | where type =~ "{resource_type}"
            | project id, name, location, resourceGroup, subscriptionId
            | limit 100
            """
            
            request = QueryRequest(
                subscriptions=subscription_ids,
                query=resource_query,
                options=QueryRequestOptions(
                    result_format="objectArray"
                )
            )
            
            logger.info(f"Fetching resources with query: {resource_query}")
            response = client.resources(request)
            resources = response.data
            
            if not resources:
                return {
                    "success": True,
                    "results": [],
                    "total_records": 0,
                    "message": f"No resources found of type {resource_type}",
                    "query_type": "azure_metrics",
                    "multi_resource": True
                }
            
            logger.info(f"Found {len(resources)} resources to query")
            
            # Step 2: Query metrics for each resource
            from azure.monitor.query import MetricsQueryClient
            from datetime import datetime, timedelta
            import asyncio
            
            metrics_client = MetricsQueryClient(user_credential)
            
            # Parse time range
            def parse_iso_duration(duration: str) -> timedelta:
                """Parse ISO 8601 duration to timedelta (PT1H = 1 hour, PT5M = 5 minutes)"""
                import re
                pattern = r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
                match = re.match(pattern, duration)
                if not match:
                    return timedelta(hours=1)
                
                days = int(match.group(1) or 0)
                hours = int(match.group(2) or 0)
                minutes = int(match.group(3) or 0)
                seconds = int(match.group(4) or 0)
                
                return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
            
            duration = parse_iso_duration(time_range)
            end_time = datetime.utcnow()
            start_time = end_time - duration
            
            # Query metrics for each resource (with error handling per resource)
            all_results = []
            failed_resources = []
            
            for resource in resources:
                try:
                    resource_id = resource["id"]
                    resource_name = resource["name"]
                    
                    logger.info(f"Querying metrics for resource: {resource_name}")
                    
                    # Query metrics - aggregations should be a list of MetricAggregationType
                    from azure.monitor.query import MetricAggregationType
                    
                    # Map string aggregation to enum
                    agg_map = {
                        "Average": MetricAggregationType.AVERAGE,
                        "Total": MetricAggregationType.TOTAL,
                        "Count": MetricAggregationType.COUNT,
                        "Minimum": MetricAggregationType.MINIMUM,
                        "Maximum": MetricAggregationType.MAXIMUM
                    }
                    agg_type = agg_map.get(aggregation, MetricAggregationType.AVERAGE)
                    
                    response = metrics_client.query_resource(
                        resource_uri=resource_id,
                        metric_names=metric_names,
                        timespan=(start_time, end_time),
                        granularity=interval,
                        aggregations=[agg_type]
                    )
                    
                    # Parse response
                    for metric in response.metrics:
                        metric_data = {
                            "resource_id": resource_id,
                            "resource_name": resource_name,
                            "resource_group": resource.get("resourceGroup"),
                            "location": resource.get("location"),
                            "metric_name": metric.name,
                            "unit": metric.unit,
                            "timeseries": []
                        }
                        
                        for timeseries in metric.timeseries:
                            ts_data = []
                            
                            for data_point in timeseries.data:
                                point = {
                                    "timestamp": data_point.timestamp.isoformat() if data_point.timestamp else None
                                }
                                
                                # Add all available aggregations
                                if hasattr(data_point, 'average') and data_point.average is not None:
                                    point["average"] = data_point.average
                                if hasattr(data_point, 'minimum') and data_point.minimum is not None:
                                    point["minimum"] = data_point.minimum
                                if hasattr(data_point, 'maximum') and data_point.maximum is not None:
                                    point["maximum"] = data_point.maximum
                                if hasattr(data_point, 'total') and data_point.total is not None:
                                    point["total"] = data_point.total
                                if hasattr(data_point, 'count') and data_point.count is not None:
                                    point["count"] = data_point.count
                                
                                ts_data.append(point)
                            
                            metric_data["timeseries"].append({"data": ts_data})
                        
                        all_results.append(metric_data)
                
                except Exception as e:
                    logger.warning(f"Failed to query metrics for {resource.get('name')}: {e}")
                    failed_resources.append({
                        "resource_name": resource.get("name"),
                        "resource_id": resource.get("id"),
                        "error": "Metrics query failed"
                    })
            
            # Check for resources with no metric data (likely deallocated VMs)
            resources_with_no_data = []
            for result in all_results:
                has_data = False
                for ts in result.get("timeseries", []):
                    for point in ts.get("data", []):
                        if any(k != "timestamp" for k in point.keys()):
                            has_data = True
                            break
                    if has_data:
                        break
                
                if not has_data:
                    resources_with_no_data.append({
                        "resource_name": result["resource_name"],
                        "resource_id": result["resource_id"],
                        "note": "No metric data available - VM may be stopped/deallocated"
                    })
            
            # Step 3: Calculate aggregate and per-resource statistics
            aggregate_stats = {}
            per_resource_summary = []
            
            for metric_name in metric_names:
                metric_results = [r for r in all_results if r["metric_name"] == metric_name]
                if not metric_results:
                    continue
                
                # Collect all values for overall statistics
                all_values = []
                resource_summaries = []
                
                for result in metric_results:
                    resource_values = []
                    for ts in result["timeseries"]:
                        for point in ts["data"]:
                            if "average" in point and point["average"] is not None:
                                all_values.append(point["average"])
                                resource_values.append(point["average"])
                    
                    # Per-resource summary
                    if resource_values:
                        resource_summaries.append({
                            "resource_name": result["resource_name"],
                            "resource_id": result["resource_id"],
                            "location": result["location"],
                            "resource_group": result["resource_group"],
                            "average": sum(resource_values) / len(resource_values),
                            "minimum": min(resource_values),
                            "maximum": max(resource_values),
                            "data_points": len(resource_values)
                        })
                
                # Overall statistics across all resources
                if all_values:
                    aggregate_stats[metric_name] = {
                        "overall_average": sum(all_values) / len(all_values),
                        "overall_minimum": min(all_values),
                        "overall_maximum": max(all_values),
                        "resource_count": len(metric_results),
                        "data_points": len(all_values),
                        "per_resource": sorted(resource_summaries, key=lambda x: x["average"], reverse=True)
                    }
            
            return {
                "success": True,
                "results": all_results,
                "aggregate_stats": aggregate_stats,
                "failed_resources": failed_resources,
                "resources_no_data": resources_with_no_data,
                "total_records": len(all_results),
                "resources_queried": len(resources),
                "resources_succeeded": len(resources) - len(failed_resources),
                "query_spec": {
                    "resource_type": resource_type,
                    "metric_names": metric_names,
                    "aggregation": aggregation,
                    "time_range": time_range,
                    "interval": interval
                },
                "query_type": "azure_metrics",
                "multi_resource": True
            }
            
        except Exception as e:
            logger.error(f"Multi-resource metrics query failed: {e}")
            import traceback
            logger.error(f"Stack trace: {traceback.format_exc()}")
            raise
    
    async def _execute_health_query(
        self,
        structured_query: Dict[str, Any],
        subscription_ids: List[str],
        user_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute Azure Resource Health query
        Returns availability status for resource(s)
        """
        try:
            from services.resource_health_service import resource_health_service
            
            # Extract scope from structured query
            resource_id = structured_query.get("resource_id")
            resource_group = structured_query.get("resource_group")
            
            logger.info(
                f"🏥 Executing health query - "
                f"Resource: {resource_id}, "
                f"RG: {resource_group}, "
                f"Subs: {len(subscription_ids) if subscription_ids else 0}"
            )
            
            # Call resource health service
            health_data = await resource_health_service.get_resource_health(
                user_info=user_info,
                resource_id=resource_id,
                resource_group=resource_group,
                subscription_ids=subscription_ids
            )
            
            if not health_data.get("success"):
                return {
                    "success": False,
                    "error": health_data.get("error", "Failed to retrieve health status"),
                    "message": health_data.get("message", "Unable to get resource health"),
                    "query_type": "resource_health"
                }
            
            # Format response based on scope
            if resource_id:
                # Single resource
                availability = health_data.get("availability_state", "Unknown")
                supported = health_data.get("supported", True)
                
                if not supported:
                    message = "Resource health monitoring is not available for this resource type."
                else:
                    # Let AI explain the health status
                    if self.openai_client:
                        message = await self._ai_explain_resource_health(health_data, is_single=True)
                    else:
                        # Minimal fallback
                        message = f"**Status**: {availability}"
                        if health_data.get("summary"):
                            message += f" - {health_data['summary']}"
                
                return {
                    "success": True,
                    "data": health_data,
                    "message": message,
                    "query_type": "resource_health",
                    "total_records": 1
                }
            else:
                # Multiple resources - let AI explain
                summary = health_data.get("summary", {})
                health_pct = health_data.get("health_percentage", 0)
                resources = health_data.get("resources", [])
                source = health_data.get("source", "unknown")
                
                if self.openai_client:
                    message = await self._ai_explain_resource_health(health_data, is_single=False)
                else:
                    # Minimal fallback
                    message = f"**Health**: {health_pct}% - {summary.get('available', 0)} available, {summary.get('unavailable', 0)} unavailable, {summary.get('degraded', 0)} degraded"
                
                # Add coverage note if present (mainly for subscription queries)
                if health_data.get("limited_coverage") and health_data.get("coverage_note"):
                    message += f"\n\n💡 {health_data['coverage_note']}"
                
                return {
                    "success": True,
                    "data": {
                        "resources": resources,
                        "summary": summary,
                        "health_percentage": health_pct,
                        "source": source,
                        "limited_coverage": health_data.get("limited_coverage", False),
                        "coverage_note": health_data.get("coverage_note")
                    },
                    "message": message,
                    "query_type": "resource_health",
                    "total_records": len(resources)
                }
                
        except Exception as e:
            logger.error(f"Health query execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": "Failed to retrieve resource health status",
                "message": "Failed to retrieve resource health status",
                "query_type": "resource_health"
            }
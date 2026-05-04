"""
ARCHITECTURE: Search-First with Prompt Engineering Fallback
============================================================

🔍 PRIMARY: Azure AI Search (Microsoft Learn Documentation)
   - Search indexed Microsoft docs for KQL examples, schemas, table names
   - GPT-5 extracts exact table/column names from CURRENT documentation
   - Reduces hallucination, ensures up-to-date syntax
   - Works for: Log Analytics KQL, Resource Graph, Metrics, Cost Management

📝 FALLBACK: Prompt Engineering (GPT-5 Training Knowledge)
   - Used ONLY when search fails or returns no results
   - System prompts guide GPT-5 with rules and patterns
   - Less accurate over time as Azure evolves
   - Future: Can be trimmed as search coverage improves

PRIORITY ORDER:
1. Search Microsoft Learn → Extract examples → Generate query
2. If search fails → Use prompt engineering rules
3. If both fail → Return error

This architecture allows:
- Gradual reduction of prompt complexity as search improves
- Easy identification of what works (search logs show success/failure)
- Future removal of redundant prompt engineering rules
"""

import logging
from typing import Dict, Any, List, Optional
import openai
import json
import asyncio
from datetime import datetime, date
from config import settings
from prompts.kql_generation_prompts import (
    build_resource_context,
    KQL_GENERATION_SYSTEM_PROMPT,
    RESULTS_EXPLANATION_SYSTEM_PROMPT,
    QUERY_TYPE_DETECTION_PROMPT,
    RESOURCE_GRAPH_KQL_PROMPT,
    METRICS_QUERY_PROMPT
)
from utils.arg_table_mapper import get_table_context_for_prompt

logger = logging.getLogger(__name__)


class _NoOpContext:
    """No-op context manager for when rate limiter is not initialized"""
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


class DateTimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime objects"""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

class AIService:
    """Service for AI-powered query processing and result explanation"""
    
    # Keywords that identify on-prem firewall queries (not Azure Firewall)
    ON_PREM_FIREWALL_KEYWORDS = [
        "on-prem firewall",
        "on-premise firewall",
        "on premise firewall",
        "cisco firewall",
        "checkpoint firewall",
    ]
    
    def __init__(self):
        # Check if OpenAI is configured
        if not settings.OPENAI_API_KEY or not settings.OPENAI_API_BASE:
            logger.warning("OpenAI is not configured. AI features will be disabled.")
            self.client = None
            self._rate_limiter = None
        else:
            # Azure OpenAI client configuration with timeouts for 2min total query limit
            # Connect timeout: 10s (establish TCP connection)
            # Read timeout: 90s (match overall timeout to prevent premature httpx timeouts)
            # Write timeout: 10s (send request)
            # Max retries: 0 (disabled to avoid exponential backoff delays)
            from httpx import Timeout
            import httpx
            
            # Custom httpx client with no retry to avoid exponential backoff delays
            http_client = httpx.AsyncClient(
                timeout=Timeout(
                    connect=settings.OPENAI_CONNECT_TIMEOUT,
                    read=settings.OPENAI_READ_TIMEOUT,
                    write=settings.OPENAI_WRITE_TIMEOUT,
                    pool=settings.OPENAI_POOL_TIMEOUT,
                ),
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
            )
            
            self.client = openai.AsyncAzureOpenAI(
                api_key=settings.OPENAI_API_KEY,
                azure_endpoint=settings.OPENAI_API_BASE,
                api_version=settings.OPENAI_API_VERSION,
                http_client=http_client,
                max_retries=0,  # Disable retry to avoid exponential backoff delays
                timeout=90.0  # 90 second timeout for complex queries (KQL extraction, analysis)
            )
            
            # Initialize distributed rate limiter
            # NOTE: Rate limiter must be set via set_rate_limiter() after Redis is initialized
            # If not set, calls will use _NoOpContext() (no rate limiting - backward compatible)
            self._rate_limiter = None
        
        self.deployment_name = settings.OPENAI_DEPLOYMENT_NAME
        self.api_version = settings.OPENAI_API_VERSION
    
    def is_available(self) -> bool:
        """Check if AI service is available"""
        return self.client is not None
    
    def set_rate_limiter(self, redis_client):
        """
        Initialize distributed rate limiter with Redis client.
        Called during app startup after Redis is initialized.
        
        Args:
            redis_client: Async Redis client from cache service
        """
        from .distributed_rate_limiter import DistributedRateLimiter
        
        # With 2M TPM quota, limit concurrent calls to prevent overwhelming API
        # Each call averages ~10K tokens, allowing ~200 requests/min theoretical max
        # Set to 50 concurrent globally for safety margin
        self._rate_limiter = DistributedRateLimiter(
            redis_client=redis_client,
            key_prefix="openai_rate_limit",
            max_concurrent=settings.OPENAI_MAX_CONCURRENT,
            timeout=settings.OPENAI_RATE_LIMIT_WAIT,
        )
        logger.info(" Distributed OpenAI rate limiter initialized (max 50 concurrent globally, 2M TPM quota)")
    
    def _map_to_diagnostics_resource_type(self, azure_resource_type: str) -> str:
        """
        Map Azure resource type to AzureDiagnostics ResourceType field value
        AzureDiagnostics uses UPPERCASE simplified names, not the full provider type
        """
        # Extract the resource type name from the full path
        # e.g., "Microsoft.Network/applicationGateways" -> "APPLICATIONGATEWAYS"
        if "/" in azure_resource_type:
            resource_name = azure_resource_type.split("/")[-1].upper()
            return resource_name.replace("-", "")
        return azure_resource_type.upper()
    
    async def convert_natural_language_to_query(self, natural_query: str, resource_context: Dict[str, Any] = None, user_info: Dict[str, Any] = None, original_user_query: str = None) -> Dict[str, Any]:
        """
        Convert natural language query to structured query.
        First detects if it's a Resource Graph or Log Analytics query, then generates appropriate KQL.
        
        Args:
            natural_query: User's natural language query
            resource_context: Optional resource context
            user_info: User info containing management_token for diagnostic settings API
        """
        if not self.is_available():
            logger.warning("AI service not available. Returning fallback query structure.")
            return self._create_fallback_query(natural_query)
        
        try:
            # Step 1: Detect query type (Resource Graph vs Log Analytics vs Metrics vs Cost)
            query_type = await self._detect_query_type(natural_query)
            logger.info(f"Detected query type: {query_type}")
            
            # Step 2: Generate appropriate query based on query type
            if query_type == "resource_graph":
                kql_query = await self._generate_resource_graph_kql(natural_query, resource_context, original_user_query or natural_query)
                
                if not kql_query:
                    logger.warning("Failed to generate Resource Graph KQL, using fallback")
                    return self._create_fallback_query(natural_query)
                
                return {
                    "type": "resource_graph",
                    "query_intent": natural_query,
                    "kql_query": kql_query,
                    "scope": "subscriptions",
                    "confidence": 0.9,
                    "ai_processed": True
                }
            elif query_type == "cost_analysis":
                cost_spec = await self._generate_cost_query(natural_query, resource_context)
                
                if not cost_spec:
                    logger.warning("Failed to generate cost query specification")
                    return {
                        "type": "error",
                        "error": "Failed to generate cost analysis query",
                        "suggestion": "Try: 'show me costs for last month' or 'cost breakdown by service'"
                    }
                
                return {
                    "type": "cost_analysis",
                    "query_intent": natural_query,
                    "scope": cost_spec.get("scope", "subscription"),
                    "time_range": cost_spec.get("time_range", "this_month"),
                    "granularity": cost_spec.get("granularity", "Daily"),
                    "resource_group": cost_spec.get("resource_group"),
                    "resource_ids": cost_spec.get("resource_ids"),
                    "custom_start": cost_spec.get("custom_start"),
                    "custom_end": cost_spec.get("custom_end"),
                    "compare_periods": cost_spec.get("compare_periods", False),
                    "confidence": 0.9,
                    "ai_processed": True
                }
            elif query_type == "azure_metrics":
                metrics_spec = await self._generate_metrics_query(natural_query, resource_context)
                
                # Check if it's a multi-resource query
                if metrics_spec and metrics_spec.get("multi_resource"):
                    return {
                        "type": "azure_metrics",
                        "query_intent": natural_query,
                        "multi_resource": True,
                        "resource_type": metrics_spec.get("resource_type", "microsoft.compute/virtualmachines"),
                        "metric_names": metrics_spec["metric_names"],
                        "aggregation": metrics_spec["aggregation"],
                        "time_range": metrics_spec["time_range"],
                        "interval": metrics_spec["interval"],
                        "confidence": 0.9,
                        "ai_processed": True
                    }
                
                if not metrics_spec or not metrics_spec.get("resource_id"):
                    logger.warning("Failed to generate metrics query or missing resource_id")
                    return {
                        "type": "error",
                        "error": "Metrics queries require a specific resource. Please select a resource first, or ask about 'all VMs' for multi-resource metrics.",
                        "suggestion": "Try: 'show CPU for all VMs' or select a specific resource"
                    }
                
                return {
                    "type": "azure_metrics",
                    "query_intent": natural_query,
                    "resource_id": metrics_spec["resource_id"],
                    "metric_names": metrics_spec["metric_names"],
                    "aggregation": metrics_spec["aggregation"],
                    "time_range": metrics_spec["time_range"],
                    "interval": metrics_spec["interval"],
                    "confidence": 0.9,
                    "ai_processed": True
                }
            elif query_type == "resource_health":
                # Handle resource health queries - no KQL needed, will use SDK/Resource Graph
                return {
                    "type": "resource_health",
                    "query_intent": natural_query,
                    "resource_id": resource_context.get("resource_id") if resource_context else None,
                    "resource_group": resource_context.get("resource_group") if resource_context else None,
                    "confidence": 0.9,
                    "ai_processed": True
                }
            elif query_type == "policy_compliance":
                # Handle policy compliance queries - uses Azure Policy Insights API
                return {
                    "type": "policy_compliance",
                    "query_intent": natural_query,
                    "confidence": 0.9,
                    "ai_processed": True
                }
            elif query_type == "security_score":
                # Handle security score queries - uses Microsoft Defender for Cloud API
                return {
                    "type": "security_score",
                    "query_intent": natural_query,
                    "confidence": 0.9,
                    "ai_processed": True
                }
            else:  # log_analytics
                kql_query = await self._generate_kql_from_natural_language(natural_query, resource_context, user_info)
                
                if not kql_query:
                    logger.warning("Failed to generate Log Analytics KQL, using fallback")
                    return self._create_fallback_query(natural_query)
                
                return {
                    "type": "log_analytics",
                    "query_intent": natural_query,
                    "kql_query": kql_query,
                    "workspace_id": "",
                    "scope": "workspace",
                    "requires_workspace_discovery": True,
                    "suggested_tables": [],
                    "time_range": "24h",
                    "confidence": 0.9,
                    "ai_processed": True
                }
            
        except Exception as e:
            logger.error(f"AI processing failed: {type(e).__name__}")
            return self._create_fallback_query(natural_query)
    
    async def _detect_query_type(self, natural_query: str) -> str:
        """
        Use AI to detect if this is a Resource Graph or Log Analytics query
        Returns: "resource_graph" or "log_analytics"
        """
        try:
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": QUERY_TYPE_DETECTION_PROMPT},
                    {"role": "user", "content": natural_query}
                ],
                max_completion_tokens=100
            )
            
            result_text = response.choices[0].message.content.strip()
            logger.info(f"Query type detection result: {result_text}")
            
            # Parse JSON response
            import json
            result = json.loads(result_text)
            query_type = result.get("query_type", "log_analytics")
            reason = result.get("reason", "")
            
            logger.info(f"Query routed to {query_type}: {reason}")
            return query_type
            
        except Exception as e:
            logger.error(f"Query type detection failed: {type(e).__name__}, defaulting to log_analytics")
            return "log_analytics"  # Default to log analytics on error
    
    async def _generate_resource_graph_kql(self, natural_query: str, resource_context: Dict[str, Any] = None, original_user_query: str = None, conversation_context: str = None) -> str:
        """
        Use OpenAI to generate Resource Graph KQL query from natural language
        Enhanced with Microsoft Learn examples for better accuracy
        """
        try:
            # Extract specific hints from ORIGINAL user query (not AI interpretation)
            query_for_extraction = original_user_query if original_user_query else natural_query
            logger.info(f" Extracting Resource Graph hints from query: {query_for_extraction[:100]}...")
            extracted_hints = self._extract_resource_graph_hints(query_for_extraction)
            if extracted_hints:
                logger.info(f" Extracted Resource Graph hints: {extracted_hints[:300]}...")
            else:
                logger.info(" No Resource Graph hints extracted from query")
            
            # Extract filter values (IPs, CIDRs, names, etc.) - Resource Graph specific
            extracted_filters = self._extract_resource_graph_filters(query_for_extraction)
            if extracted_filters:
                logger.info(f" Extracted filter values: {extracted_filters[:200]}...")
            
            # Get Microsoft Learn examples for Resource Graph
            azure_docs_examples = await self._get_microsoft_resource_graph_examples(natural_query)
            
            # Build context string
            context_str = ""
            if resource_context:
                if resource_context.get("resource_id"):
                    resource_id = resource_context['resource_id']
                    resource_name = resource_id.split('/')[-1] if resource_id else 'unknown'
                    context_str = f"\nCONTEXT: Filter to specific resource '{resource_name}' using: | where id =~ '{resource_id}'"
                elif resource_context.get("resource_group"):
                    context_str = f"\nCONTEXT: Filter to resource group '{resource_context['resource_group']}'"
                if resource_context.get("subscription_ids"):
                    subs = resource_context['subscription_ids']
                    sub_str = subs[0] if isinstance(subs, list) else subs
                    context_str += f"\nCONTEXT: Query scoped to subscription {sub_str}"
            
            # Combine all context
            user_message = natural_query
            if context_str or extracted_hints or extracted_filters or azure_docs_examples or conversation_context:
                parts = []
                if conversation_context:
                    parts.append(f"\nPREVIOUS CONVERSATION CONTEXT (use to scope or refine this query):\n{conversation_context}")
                if context_str:
                    parts.append(context_str)
                if extracted_hints:
                    parts.append(extracted_hints)
                if extracted_filters:
                    parts.append(extracted_filters)
                if azure_docs_examples:
                    parts.append(azure_docs_examples)
                parts.append(f"\n\nUSER QUERY: {natural_query}")
                user_message = "".join(parts)
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": RESOURCE_GRAPH_KQL_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=500
            )
            
            kql_query = response.choices[0].message.content.strip()
            
            # Clean up the response
            if kql_query.startswith("```"):
                lines = kql_query.split("\n")
                kql_query = "\n".join([line for line in lines if not line.startswith("```") and not line.startswith("kql")])
                kql_query = kql_query.strip()
            
            logger.info(f"Generated Resource Graph KQL: {kql_query}")
            
            # Post-process: Fix table name using Redis-cached mappings
            # Returns None if invalid child resource type detected
            fixed_kql = self._fix_table_from_cache(kql_query)
            
            if fixed_kql is None:
                # Invalid child resource detected - regenerate with child resource guidance
                logger.info(f" Regenerating query with child resource guidance...")
                
                # Detect parent/child from the invalid query
                import re
                type_match = re.search(r'type\s*(?:=~|==|=)\s*["\']([^"\']+)["\']', kql_query, re.IGNORECASE)
                if type_match:
                    invalid_type = type_match.group(1).lower()
                    segments = invalid_type.split('/')
                    if len(segments) >= 3:
                        parent_type = '/'.join(segments[:2])
                        child_property = segments[-1]
                        
                        # Build child resource context
                        child_resource_context = {
                            "is_child_resource": True,
                            "parent_type": parent_type,
                            "child_property": child_property
                        }
                        if resource_context:
                            child_resource_context.update(resource_context)
                        
                        # Regenerate with proper child resource guidance
                        regenerated_kql = await self._regenerate_resource_graph_kql_with_docs(
                            natural_query=natural_query,
                            failed_query=kql_query,
                            error_message=f"Invalid resource type '{invalid_type}' detected. This type doesn't exist in Azure.",
                            resource_context=child_resource_context
                        )
                        
                        if regenerated_kql:
                            logger.info(f" Regenerated with child resource pattern: {regenerated_kql[:200]}...")
                            return regenerated_kql
                
                # If regeneration fails, return original (will likely fail but at least we tried)
                logger.warning(f" Regeneration failed, returning original query")
                return kql_query
            
            return fixed_kql
            
        except Exception as e:
            logger.error(f"Failed to generate Resource Graph KQL: {type(e).__name__}")
            return ""
    
    async def _regenerate_resource_graph_kql_with_docs(
        self, 
        natural_query: str, 
        failed_query: str, 
        error_message: str,
        resource_context: Dict[str, Any] = None
    ) -> str:
        """
        Regenerate Resource Graph KQL using Azure documentation when a query fails
        """
        try:
            logger.info("Attempting to regenerate query with Azure documentation context")
            
            # Check if this is a child resource scenario
            is_child_resource = resource_context and resource_context.get("is_child_resource")
            
            if is_child_resource:
                # Enhanced error context for child resources
                parent_type = resource_context.get("parent_type", "")
                child_property = resource_context.get("child_property", "")
                
                doc_context = f"""
PREVIOUS FAILURE - CHILD RESOURCE DETECTED:
Query: {failed_query}
Error: {error_message}

⚠️ CRITICAL ISSUE: Child resources don't appear as standalone rows in Azure Resource Graph!

DETECTED CHILD RESOURCE PATTERN:
- Parent Resource Type: {parent_type}
- Child Property: {child_property}

CORRECT APPROACH FOR CHILD RESOURCES:
1. Query the PARENT resource type: {parent_type}
2. Use mv-expand to expand the child array from properties
3. Extract child fields after expansion

EXAMPLE PATTERN (VNet Peerings):
Resources
| where type =~ "Microsoft.Network/virtualNetworks"
| where resourceGroup =~ "rg-name"  # if resource group filter needed
| mv-expand peering = properties.virtualNetworkPeerings
| project
    vnetName = name,
    peeringName = peering.name,
    peeringState = peering.properties.peeringState,
    remoteVnet = peering.properties.remoteVirtualNetwork.id,
    resourceGroup, location

APPLY THIS PATTERN:
- Query parent: {parent_type}
- Expand child: properties.{child_property}
- Access child fields: childItem.name, childItem.properties.xyz

OTHER COMMON CHILD RESOURCES:
- VNet subnets: properties.subnets
- NSG rules: properties.securityRules and properties.defaultSecurityRules
- Route table routes: properties.routes
"""
            else:
                # Standard error context
                doc_context = f"""
PREVIOUS FAILURE:
Query: {failed_query}
Error: {error_message}

KEY RULES FROM AZURE RESOURCE GRAPH DOCUMENTATION:
- Resource Graph uses simplified KQL syntax
- JOIN operations are complex and error-prone - AVOID THEM
- For queries needing related resource data, use single-table approach with extend
- Extract nested IDs: | extend nicId = tostring(properties.networkProfile.networkInterfaces[0].id)
- Check existence: | where isnotempty(property)
- Focus on simple, robust queries that work reliably
"""
            
            # Build context string
            context_str = doc_context
            if resource_context:
                if resource_context.get("resource_id"):
                    resource_id = resource_context['resource_id']
                    resource_name = resource_id.split('/')[-1] if resource_id else 'unknown'
                    context_str += f"\nCONTEXT: Filter to specific resource '{resource_name}' using: | where id =~ '{resource_id}'"
                elif resource_context.get("resource_group"):
                    context_str += f"\nCONTEXT: Filter to resource group '{resource_context['resource_group']}'"
                if resource_context.get("subscription_ids"):
                    subs = resource_context['subscription_ids']
                    sub_str = subs[0] if isinstance(subs, list) else subs
                    context_str += f"\nCONTEXT: Query scoped to subscription {sub_str}"
            
            guidance = "Generate a SIMPLER query without joins." if not is_child_resource else "Use the mv-expand pattern to access child resources correctly."
            user_message = context_str + f"\n\nUSER QUERY: {natural_query}\n\n{guidance}"
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": RESOURCE_GRAPH_KQL_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=500
            )
            
            kql_query = response.choices[0].message.content.strip()
            
            # Clean up the response
            if kql_query.startswith("```"):
                lines = kql_query.split("\n")
                kql_query = "\n".join([line for line in lines if not line.startswith("```") and not line.startswith("kql")])
                kql_query = kql_query.strip()
            
            logger.info(f"Regenerated Resource Graph KQL: {kql_query}")
            return kql_query
            
        except Exception as e:
            logger.error(f"Failed to regenerate Resource Graph KQL: {type(e).__name__}")
            return ""
    
    async def _generate_metrics_query(self, natural_query: str, resource_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Use OpenAI to generate metrics query specification from natural language
        Handles both single-resource and multi-resource queries
        Enhanced with Microsoft Learn examples for better accuracy
        """
        try:
            # NEW: Get Microsoft Learn examples for Metrics
            resource_type = resource_context.get("resource_type") if resource_context else None
            azure_docs_examples = await self._get_microsoft_metrics_examples(resource_type)
            
            # Build context string
            context_str = f"\nCONTEXT:\n"
            
            # Check if specific resource is provided
            if resource_context and resource_context.get("resource_id"):
                resource_id = resource_context.get("resource_id")
                resource_type = resource_context.get("resource_type", "")
                
                context_str += f"- resource_id: {resource_id}\n"
                if resource_type:
                    context_str += f"- resource_type: {resource_type}\n"
            else:
                # No specific resource - indicate multi-resource scenario
                context_str += "- No specific resource selected\n"
                context_str += "- User may be asking about multiple resources (e.g., 'all VMs')\n"
            
            # Combine context + Azure docs examples
            user_message = context_str + azure_docs_examples + f"\nUSER QUERY: {natural_query}"
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": METRICS_QUERY_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=300
            )
            
            metrics_spec_text = response.choices[0].message.content.strip()
            
            # Clean up the response
            if metrics_spec_text.startswith("```"):
                lines = metrics_spec_text.split("\n")
                metrics_spec_text = "\n".join([line for line in lines if not line.startswith("```") and not line.startswith("json")])
                metrics_spec_text = metrics_spec_text.strip()
            
            # Parse JSON response
            import json
            metrics_spec = json.loads(metrics_spec_text)
            
            # If resource_id was in context and not in response, add it
            if resource_context and resource_context.get("resource_id") and not metrics_spec.get("resource_id"):
                metrics_spec["resource_id"] = resource_context.get("resource_id")
            
            logger.info(f"Generated metrics query: {metrics_spec}")
            return metrics_spec
            
        except Exception as e:
            logger.error(f"Failed to generate metrics query: {type(e).__name__}")
            return {}
    
    async def _generate_cost_query(self, natural_query: str, resource_context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Generate cost analysis query specification from natural language
        Enhanced with Microsoft Learn examples for better accuracy
        """
        try:
            from prompts.kql_generation_prompts import COST_ANALYSIS_PROMPT
            
            # NEW: Get Microsoft Learn examples for Cost Management
            azure_docs_examples = await self._get_microsoft_cost_examples()
            
            # Build context for cost query
            context_str = "CONTEXT:\n"
            
            if resource_context:
                subscription_id = resource_context.get("subscription_id")
                resource_group = resource_context.get("resource_group")
                resource_id = resource_context.get("resource_id")
                resource_ids = resource_context.get("resource_ids")
                
                if subscription_id:
                    context_str += f"- subscription_id: {subscription_id}\n"
                if resource_group:
                    context_str += f"- resource_group: {resource_group}\n"
                if resource_id:
                    context_str += f"- resource_id: {resource_id}\n"
                if resource_ids:
                    context_str += f"- resource_ids: {resource_ids}\n"
            else:
                context_str += "- No specific scope selected - use subscription scope by default\n"
            
            # Combine context + Azure docs examples
            user_message = context_str + azure_docs_examples + f"\nUSER QUERY: {natural_query}"
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": COST_ANALYSIS_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=300
            )
            
            cost_spec_text = response.choices[0].message.content.strip()
            
            # Clean up the response
            if cost_spec_text.startswith("```"):
                lines = cost_spec_text.split("\n")
                cost_spec_text = "\n".join([line for line in lines if not line.startswith("```") and not line.startswith("json")])
                cost_spec_text = cost_spec_text.strip()
            
            # Parse JSON response
            import json
            cost_spec = json.loads(cost_spec_text)
            
            # Add subscription_id from context if available
            if resource_context and resource_context.get("subscription_id"):
                cost_spec["subscription_id"] = resource_context.get("subscription_id")
            
            logger.info(f"Generated cost query: {cost_spec}")
            return cost_spec
            
        except Exception as e:
            logger.error(f"Failed to generate cost query: {type(e).__name__}")
            return {}
    
    async def _infer_resource_type_from_query(self, query: str) -> str:
        """
        Infer Azure resource type from natural language query
        """
        query_lower = query.lower()
        
        # Check for on-prem firewall keywords first (should NOT infer Azure Firewall)
        for keyword in self.ON_PREM_FIREWALL_KEYWORDS:
            if keyword in query_lower:
                logger.info(f" Detected on-prem firewall keyword '{keyword}' - NOT inferring Azure Firewall resource type")
                return None
        
        # Map keywords to resource types
        resource_type_mappings = {
            "azure firewall": "Microsoft.Network/azureFirewalls",  # Only match explicit "Azure Firewall"
            "vm": "Microsoft.Compute/virtualMachines",
            "virtual machine": "Microsoft.Compute/virtualMachines",
            "storage": "Microsoft.Storage/storageAccounts",
            "sql": "Microsoft.Sql/servers",
            "database": "Microsoft.Sql/servers",
            "aks": "Microsoft.ContainerService/managedClusters",
            "kubernetes": "Microsoft.ContainerService/managedClusters",
            "app service": "Microsoft.Web/sites",
            "webapp": "Microsoft.Web/sites",
            "function": "Microsoft.Web/sites",
            "load balancer": "Microsoft.Network/loadBalancers",
            "application gateway": "Microsoft.Network/applicationGateways",
            "network security group": "Microsoft.Network/networkSecurityGroups",
            "nsg": "Microsoft.Network/networkSecurityGroups",
        }
        
        for keyword, resource_type in resource_type_mappings.items():
            if keyword in query_lower:
                return resource_type
        
        return None
    
    async def _get_diagnostic_settings_context(self, resource_context: Dict[str, Any] = None, user_info: Dict[str, Any] = None) -> str:
        """
        Check diagnostic settings for the resource to determine log table mode.
        Returns context string for AI prompt with mode-specific instructions.
        
        Args:
            resource_context: Resource context containing resource_id
            user_info: User info containing management_token for diagnostic settings API
        
        Returns:
            Formatted context string with diagnostic settings info and query instructions
        """
        try:
            # Extract resource_id from context
            if not resource_context or not resource_context.get("resource_id"):
                logger.info(" No resource_id in context, skipping diagnostic settings check")
                return ""
            
            resource_id = resource_context["resource_id"]
            
            # Need user_info for management token
            if not user_info:
                logger.warning(" No user_info provided, skipping diagnostic settings check")
                return ""
            
            logger.info(f" Checking diagnostic settings for: {resource_id}")
            
            # Import and use diagnostic settings service
            from services.diagnostic_settings_service import DiagnosticSettingsService
            
            diag_service = DiagnosticSettingsService()
            diag_info = await diag_service.get_log_mode_for_resource(resource_id, user_info)
            
            if not diag_info.get('has_diagnostics'):
                logger.warning(f" No diagnostic settings found for {diag_info.get('resource_name')}")
                return ""
            
            # Store workspace IDs in resource_context to avoid redundant diagnostic checks later
            if diag_info.get('primary') and diag_info['primary'].get('workspace_id'):
                if not resource_context:
                    resource_context = {}
                resource_context['_workspace_ids_from_diagnostics'] = [diag_info['primary']['workspace_id']]
                if diag_info.get('fallback') and diag_info['fallback'].get('workspace_id'):
                    resource_context['_workspace_ids_from_diagnostics'].append(diag_info['fallback']['workspace_id'])
                logger.info(f" Stored {len(resource_context['_workspace_ids_from_diagnostics'])} workspace IDs in context")
            
            # Build context based on detected mode(s)
            context_parts = ["\n\n" + "="*80 + "\n"]
            context_parts.append("CRITICAL - DIAGNOSTIC SETTINGS OVERRIDE\n")
            context_parts.append("="*80 + "\n")
            
            resource_name = diag_info.get('resource_name', 'resource')
            
            if diag_info.get('primary'):
                primary = diag_info['primary']
                context_parts.append(f"DETECTED MODE: {primary['mode']}")
                context_parts.append(f"   Workspace: {primary['workspace_name']}\n")
                
                if primary['mode'] == 'Dedicated':
                    context_parts.append(f"MANDATORY TABLE SELECTION:")
                    context_parts.append(f"   USE: Resource-specific tables (e.g., AZFWDnsQuery, AZFWApplicationRule, AZFWNetworkRule)")
                    context_parts.append(f"   DO NOT USE: AzureDiagnostics table")
                    context_parts.append(f"   DO NOT USE: Category filters")
                    context_parts.append(f"   DO NOT USE: Resource column filters (doesn't exist in Dedicated tables)")
                    context_parts.append(f"\n   QUERY TEMPLATE:")
                    context_parts.append(f"   <ResourceSpecificTable> | where TimeGenerated > ago(5m) | take 1000")
                    context_parts.append(f"\n   NOTE: Resource-specific tables ONLY contain logs for {resource_name}")
                    context_parts.append(f"   NOTE: No resource/category filtering needed - table IS the filter")
                else:  # AzureDiagnostics
                    context_parts.append(f"MANDATORY TABLE SELECTION:")
                    context_parts.append(f"   USE: AzureDiagnostics table")
                    context_parts.append(f"   MUST FILTER: Resource == \"{resource_name.upper()}\"")
                    context_parts.append(f"   MUST FILTER: Category == \"<ExactNameFromMicrosoftLearnDocs>\"")
                    context_parts.append(f"   DO NOT ADD: ResourceType filter (redundant)")
                    context_parts.append(f"\n   QUERY TEMPLATE:")
                    context_parts.append(f"   AzureDiagnostics | where Resource == \"{resource_name.upper()}\" | where Category == \"<ExactName>\" | where TimeGenerated > ago(5m)")
                    context_parts.append(f"\n   WARNING: Category names: ONLY use exact names from Microsoft Learn docs below (case-sensitive!)")
            
            if diag_info.get('fallback'):
                fallback = diag_info['fallback']
                context_parts.append(f"\n\nFALLBACK MODE (Use only if primary fails):")
                context_parts.append(f"   Mode: {fallback['mode']}")
                context_parts.append(f"   Workspace: {fallback['workspace_name']}")
                
                if fallback['mode'] == 'Dedicated':
                    context_parts.append(f"   Template: <ResourceTable> | where TimeGenerated > ago(5m)")
                else:  # AzureDiagnostics
                    context_parts.append(f"   Template: AzureDiagnostics | where Resource == \"{resource_name.upper()}\" | where Category == \"<Name>\"")
            
            context_parts.append(f"\n" + "="*80)
            context_parts.append(f"\nWARNING: IGNORE any conflicting AzureDiagnostics examples elsewhere in this prompt")
            context_parts.append(f"WARNING: THIS DIAGNOSTIC SETTINGS SECTION OVERRIDES ALL OTHER INSTRUCTIONS")
            context_parts.append(f"WARNING: Generate query based on PRIMARY mode only (fallback is for reference)")
            context = "\n".join(context_parts)
            logger.info(f" Built diagnostic settings context ({len(context)} chars)")
            return context
            
        except Exception as e:
            logger.error(f" Failed to get diagnostic settings context: {type(e).__name__}")
            import traceback
            logger.error(traceback.format_exc())
            return ""  # Non-critical, continue without diagnostic context
    
    async def _get_table_schema_context(self, resource_context: Dict[str, Any] = None) -> str:
        """
        Get table schema directly from GitHub-cached mapping.
        Much faster and more reliable than Azure Search.
        """
        if not resource_context:
            return ""
        
        log_mode = resource_context.get('log_mode')
        if not log_mode:
            return ""
        
        try:
            from utils.log_analytics_table_mapper import log_analytics_table_mapper
            
            # Determine which tables to look up based on mode
            table_names = []
            
            if log_mode == 'Dedicated':
                # For Dedicated mode, look up resource-specific tables by resource type
                # E.g., for Microsoft.Network/azureFirewalls: AZFWNetworkRule, AZFWApplicationRule, etc.
                resource_type = resource_context.get('resource_type', '')
                
                if resource_type:
                    # Use resource type to find matching tables
                    table_names = await log_analytics_table_mapper.get_tables_for_resource_type(resource_type)
                    
                    if table_names:
                        logger.info(f" Found {len(table_names)} Dedicated table(s) for {resource_type}: {table_names[:5]}")
                    else:
                        logger.warning(f" No dedicated tables found for resource type: {resource_type}")
                else:
                    logger.warning(" No resource_type in context for Dedicated mode lookup")
            
            elif log_mode == 'AzureDiagnostics':
                # For AzureDiagnostics mode, use the AzureDiagnostics table
                table_names = ['azurediagnostics']
                logger.info(f" Looking up AzureDiagnostics table")
            
            if not table_names:
                return ""
            
            # Two-stage approach: First send all table names + descriptions for AI to choose
            if len(table_names) > 3:
                # Stage 1: Get descriptions for all tables
                table_options = []
                for table_name in table_names:
                    schema = await log_analytics_table_mapper.get_table_schema(table_name)
                    if schema:
                        desc = schema.get('description', 'No description available')
                        table_options.append(f"- {schema['table_name']}: {desc}")
                
                if table_options:
                    # Ask AI to pick the best table(s) using existing OpenAI client
                    user_query = resource_context.get('user_query') or resource_context.get('natural_query') or 'analyze logs'
                    resource_type_str = resource_context.get('resource_type', 'resource') if resource_context else 'resource'
                    
                    prompt = f"""Given this user query: "{user_query}"

Available Log Analytics tables for {resource_type_str}:

{chr(10).join(table_options)}

Select the 1-3 most relevant tables for this query. Return ONLY the table names, comma-separated.
Example: AZFWNetworkRule,AZFWApplicationRule"""
                    
                    try:
                        # Use existing OpenAI client with rate limiter
                        async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):
                            response = await self.client.chat.completions.create(
                                model=self.deployment_name,
                                messages=[{"role": "user", "content": prompt}],
                                max_completion_tokens=100
                            )
                        
                        ai_response = response.choices[0].message.content.strip()
                        
                        # Parse selected tables
                        selected = [t.strip() for t in ai_response.split(',')]
                        selected_tables = [t for t in selected if t in table_names]
                        
                        if selected_tables:
                            logger.info(f" AI selected {len(selected_tables)} table(s): {selected_tables}")
                            table_names = selected_tables[:3]  # Use AI's selection
                        else:
                            logger.warning(f" AI returned invalid tables, using keyword fallback")
                            # Keyword fallback: score tables by how many incident-relevant words
                            # appear in their description. Don't blindly take [:3] — the first
                            # 3 alphabetically are often DNS/NAT, not Network/Application rules.
                            incident_keywords = [
                                w.lower() for w in (user_query or "").split()
                                if len(w) > 3
                            ] + ["deny", "block", "allow", "rule", "network", "application", "threat"]
                            def _score(tname: str) -> int:
                                schema = None
                                desc = tname.lower()
                                return sum(1 for kw in incident_keywords if kw in desc)
                            scored = sorted(table_names, key=_score, reverse=True)
                            table_names = scored[:3]
                            logger.info(f" Keyword fallback selected: {table_names}")
                            
                    except Exception as e:
                        logger.warning(f" AI table selection failed, using first 3: {e}")
                        table_names = table_names[:3]
            
            # Stage 2: Fetch full schemas for selected tables
            context_parts = []
            for table_name in table_names[:3]:  # Limit to 3 tables to avoid bloat
                schema = await log_analytics_table_mapper.get_table_schema(table_name)
                
                if schema and schema.get('columns'):
                    context_parts.append(f"\n\n📋 TABLE: {schema['table_name']}")
                    context_parts.append(f"Columns ({len(schema['columns'])}):")
                    
                    for col in schema['columns'][:30]:  # Limit to 30 columns
                        context_parts.append(f"  - {col['name']} ({col['type']})")
                    
                    if len(schema['columns']) > 30:
                        context_parts.append(f"  ... and {len(schema['columns']) - 30} more columns")
                    
                    logger.info(f" Got schema for {table_name}: {len(schema['columns'])} columns")
            
            if not context_parts:
                return ""
            
            full_context = "\n".join(context_parts)
            logger.info(f" Built table schema context: {len(full_context)} chars")
            return full_context
            
        except Exception as e:
            logger.error(f" Failed to get table schema: {e}")
            return ""
    
    async def _get_microsoft_kql_examples(self, resource_type: str = None, user_query: str = None, resource_context: dict = None) -> str:
        """
        Get real KQL examples from Microsoft Learn using Azure AI Search
        
        Args:
            resource_type: Azure resource type (e.g., "Microsoft.Network/azureFirewalls")
            user_query: User's natural language query for more specific search
            resource_context: Full resource context including kind, sku, etc.
        """
        try:
            # Special case: On-prem firewall queries (detected by keywords)
            if user_query:
                query_lower = user_query.lower()
                is_on_prem_firewall = any(keyword in query_lower for keyword in self.ON_PREM_FIREWALL_KEYWORDS)
                
                if is_on_prem_firewall:
                    logger.info(" On-prem firewall query detected - providing CommonSecurityLog guidance")
                    return """
📚 COMMONSECURITYLOG TABLE REFERENCE:

Table: CommonSecurityLog
Format: CEF (Common Event Format) for security appliances

Common Fields:
- DeviceVendor: "Zscaler", "Cisco", "Check Point" (case-sensitive)
- DeviceProduct: Specific firewall model/product name
- Activity: Action taken (Allow, Deny, Drop, Block, Accept, Reject)
- SourceIP: Source IP address
- DestinationIP: Destination IP address  
- DestinationPort: Destination port number
- Protocol: Network protocol (TCP, UDP, ICMP)
- SourcePort: Source port number
- DeviceAction: Firewall action taken
- ApplicationProtocol: Application-level protocol
- TimeGenerated: Log timestamp

Example Query:
CommonSecurityLog
| where TimeGenerated > ago(1h)
| where DeviceVendor in~ ("Zscaler", "Cisco", "Check Point")
| where DestinationIP == "10.0.0.1"
| where Activity has_any ("Deny", "Drop", "Block")
| project TimeGenerated, DeviceVendor, SourceIP, DestinationIP, DestinationPort, Activity, Protocol
| take 1000

⚠️ CRITICAL REQUIREMENTS:
1. Use EXACT field names: DestinationIP (NOT DestinationAddress), SourceIP, DestinationPort
2. MUST end query with: | take 1000 or | limit 1000
3. ALWAYS include time filter: | where TimeGenerated > ago(...)
"""
            
            if not resource_type:
                logger.warning(" No resource_type provided to _get_microsoft_kql_examples")
                return ""
            
            # NEW: Try getting schema directly from table mapper first (faster, more reliable)
            table_schema_context = await self._get_table_schema_context(resource_context)
            
            # ADDITIONAL: Search for KQL function/operator documentation if query needs it
            # This prevents AI from hallucinating functions like "ipv4_isprivate()"
            function_guidance = ""
            if user_query:
                query_lower = user_query.lower()
                # Detect if query needs function guidance (IP operations, parsing, aggregations, etc.)
                needs_function_docs = any([
                    "ip" in query_lower and ("private" in query_lower or "public" in query_lower or "range" in query_lower),
                    "parse" in query_lower or "extract" in query_lower,
                    "regex" in query_lower or "pattern" in query_lower,
                    "aggregate" in query_lower or "count" in query_lower or "sum" in query_lower
                ])
                
                if needs_function_docs:
                    logger.info(f" Query may need KQL functions - searching documentation for guidance")
                    # Build search context based on query intent
                    search_context = "KQL functions operators"
                    if "ip" in query_lower:
                        search_context += " IP address ipv4 ipv6"
                    if "private" in query_lower or "public" in query_lower:
                        search_context += " private network RFC1918"
                    if "parse" in query_lower:
                        search_context += " string parsing"
                    if "regex" in query_lower:
                        search_context += " regex pattern matching"
                    
                    function_guidance = await self._get_kql_operator_documentation(search_context)
                    if function_guidance:
                        logger.info(f" Added KQL function guidance ({len(function_guidance)} chars)")
            
            if table_schema_context:
                logger.info(" Using direct table schema lookup")
                # Combine schema with function guidance if available
                return table_schema_context + function_guidance
            
            # Fallback to Azure Search if schema lookup fails
            if not settings.USE_AZURE_SEARCH:
                logger.warning(" USE_AZURE_SEARCH is disabled and no table schema found")
                return ""
            
            logger.info(f" Searching Microsoft Learn for KQL examples: {resource_type}")
            logger.info(f" User query context: {user_query}")
            
            # Search our indexed Microsoft docs
            from services.azure_search_service import get_azure_search_service
            search_service = get_azure_search_service()
            
            search_results = await search_service.search_kql_examples(resource_type, user_query, resource_context, top=5)
            
            if not search_results:
                logger.warning(" No search results found in Azure AI Search")
                return ""
            
            logger.info(f" Found {len(search_results)} search results from Azure AI Search")
            
            # Build context from search results (increased from 3 to 5 for better coverage)
            context_parts = []
            for i, result in enumerate(search_results[:5], 1):
                logger.info(f" Result {i}: {result['title'][:100]}...")
                logger.info(f" URL: {result['url']}")
                context_parts.append(
                    f"Source {i}: {result['title']}\n"
                    f"URL: {result['url']}\n"
                    f"{result['content'][:500]}...\n"
                )
            
            context = "\n".join(context_parts)
            logger.info(f" Built context with {len(context)} characters from {len(search_results[:5])} sources")
            
            # Use GPT-5 to extract KQL examples from the documentation snippets
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": "You are an expert at extracting KQL schema and examples from Azure documentation. Extract table names, column names, and example queries. Preserve exact syntax."},
                    {"role": "user", "content": f"""From these Microsoft Learn documentation snippets about {resource_type}{f" (kind={resource_context.get('kind')})" if resource_context and resource_context.get('kind') else ""}, extract the EXACT information as written:

USER'S QUERY: "{user_query}"

⚠️ CRITICAL: The user is asking about a specific aspect of this resource. Select the category/table that best matches what the user is asking for in their query above.

1. **Category names**: Look for tables showing "Category" or "Log category" - copy the EXACT spelling and casing
   {f"- IMPORTANT: Resource has kind={resource_context.get('kind')} - prefer categories matching '{resource_context.get('kind')}' (e.g., FrontDoorAccessLog for frontdoor)" if resource_context and resource_context.get('kind') else ""}
   - Based on the user's query above, select the most relevant category from the documentation
2. **Table names**: Which Log Analytics tables are used
3. **Column names**: Extract from TWO sources:
   a) PRIMARY: Columns used in example KQL queries (WHERE clauses, SELECT, parseurl() calls)
   b) SECONDARY: Common columns from schema tables (TimeGenerated, Resource, Category, etc.)
   ⚠️ INCLUDE BOTH - examples show usage patterns, schemas show available fields
   - If examples use parseurl() or parse functions to extract fields, note that pattern
   - For HTTP logs, look for: httpStatusCode, httpStatus, statusCode, status (with _s or _d suffix)
   - For request logs, look for: requestUri, host, hostname, serverName (with _s suffix)
4. **Example KQL queries**: Copy complete example queries

Documentation:
{context}

CRITICAL EXTRACTION RULES:
- Select the category that matches the user's query intent (analyze their query to understand what they're asking for)
- Extract columns from BOTH example queries AND schema tables
- If docs show "parseurl(requestUri_s).Host" instead of "host_s", note that pattern
- Include common HTTP fields even if not in examples: httpStatusCode_s, httpStatus_s, statusCode_s
{f"- Resource kind is '{resource_context.get('kind')}' - extract categories/queries for this specific kind" if resource_context and resource_context.get('kind') else ""}

Format your response:
- Categories: <exact names from documentation that match the user's query intent>
- Table: <name>
- Columns from examples: <columns used in example queries>
- Columns from schema: <additional columns from schema tables, especially HTTP-related fields>
- Query Patterns: <note any parseurl() or parse() patterns used>
- Examples: <complete example queries>"""}
                ],
                max_completion_tokens=2000  # Increased to capture complete column lists
            )
            
            examples_text = response.choices[0].message.content.strip()
            
            if examples_text and len(examples_text) > 50:
                logger.info(f" Extracted KQL examples from {len(search_results)} Microsoft Learn pages")
                logger.info(f" Example snippet: {examples_text[:200]}...")
                return f"""\n\n📚 OFFICIAL MICROSOFT LEARN DOCUMENTATION (Retrieved {len(search_results)} results):\n\n{examples_text}\n\n⚠️⚠️⚠️ MANDATORY INSTRUCTIONS ⚠️⚠️⚠️
1. YOU MUST use the EXACT category names shown above - copied directly from Microsoft docs (case-sensitive!)
2. YOU MUST use the EXACT table names shown above  
3. YOU MUST use ONLY column names explicitly listed above - do NOT use columns from your training data
4. If a filter requires a column NOT listed above, SKIP that filter rather than guessing the column name
5. If dedicated tables are shown (not AzureDiagnostics), use those
6. These are from CURRENT official documentation - they override ALL other sources

Generate your query using ONLY the names from the documentation above.\n\n"""
            
            logger.warning(" No usable KQL examples found in search results (text too short)")
            return ""
            
        except Exception as e:
            logger.error(f" Azure AI Search failed: {type(e).__name__}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return ""  # Non-critical failure, continue without examples
    
    async def _get_microsoft_resource_graph_examples(self, query_intent: str = None) -> str:
        """
        Fetch real Azure Resource Graph KQL examples from Microsoft Learn using Azure AI Search
        """
        if not query_intent or not settings.USE_AZURE_SEARCH:
            return ""
        
        try:
            logger.info(f" Searching for Resource Graph examples: {query_intent}")
            
            from services.azure_search_service import get_azure_search_service
            search_service = get_azure_search_service()
            
            search_results = await search_service.search_resource_graph_examples(query_intent, top=5)
            
            if not search_results:
                return ""
            
            # Build context from search results (increased from 3 to 5 for better coverage)
            context_parts = []
            for i, result in enumerate(search_results[:5], 1):
                context_parts.append(
                    f"Source {i}: {result['title']}\n{result['content'][:500]}\n"
                )
            
            context = "\n".join(context_parts)
            
            # Extract examples with GPT-5
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": "Extract Azure Resource Graph KQL examples from documentation. Show only the KQL code with exact table and column names."},
                    {"role": "user", "content": f"From these docs about '{query_intent}', extract 2-3 Resource Graph KQL examples:\n\n{context}"}
                ],
                max_completion_tokens=800
            )
            
            examples_text = response.choices[0].message.content.strip()
            
            if examples_text and len(examples_text) > 50:
                logger.info(f" Extracted Resource Graph examples from Microsoft Learn")
                return f"\n\n📚 EXAMPLES FROM MICROSOFT LEARN (Resource Graph):\n\n{examples_text}\n\n⚠️ USE EXACT TABLE AND COLUMN NAMES.\n"
            
            return ""
            
        except Exception as e:
            logger.debug(f"Azure AI Search failed: {type(e).__name__}")
            return ""
    
    async def _get_microsoft_metrics_examples(self, resource_type: str = None) -> str:
        """
        Fetch real Azure Monitor Metrics examples from Microsoft Learn using Azure AI Search
        """
        if not resource_type or not settings.USE_AZURE_SEARCH:
            return ""
        
        try:
            # Extract resource name from resource type
            resource_name = resource_type.split('/')[-1] if '/' in resource_type else resource_type
            resource_name = resource_name.replace('_', ' ').title()
            
            logger.info(f" Searching for metrics examples: {resource_name}")
            
            from services.azure_search_service import get_azure_search_service
            search_service = get_azure_search_service()
            
            search_results = await search_service.search_metrics_documentation(resource_type, top=5)
            
            if not search_results:
                return ""
            
            # Build context from search results (increased from 3 to 5 for better coverage)
            context_parts = []
            for i, result in enumerate(search_results[:5], 1):
                context_parts.append(
                    f"Source {i}: {result['title']}\n{result['content'][:500]}\n"
                )
            
            context = "\n".join(context_parts)
            
            # Extract metrics info with GPT-5
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": "Extract Azure Monitor metrics information from documentation. List available metrics with exact names, units, and descriptions."},
                    {"role": "user", "content": f"From these docs about {resource_name} ({resource_type}), list the available Azure Monitor metrics:\n\n{context}"}
                ],
                max_completion_tokens=600
            )
            
            metrics_text = response.choices[0].message.content.strip()
            
            if metrics_text and len(metrics_text) > 30:
                logger.info(f" Extracted metrics information from Microsoft Learn")
                return f"\n\n📚 AVAILABLE METRICS (from Microsoft Learn):\n\n{metrics_text}\n\n⚠️ USE EXACT METRIC NAMES FROM THIS LIST.\n"
            
            return ""
            
        except Exception as e:
            logger.debug(f"Azure AI Search failed: {type(e).__name__}")
            return ""
    
    async def _get_kql_operator_documentation(self, error_context: str) -> str:
        """
        Fetch KQL operator and syntax documentation from Microsoft Learn when syntax errors occur.
        Uses Azure AI Search to find valid operators and syntax patterns.
        
        Args:
            error_context: Context about the syntax error (operator, pattern, etc.)
        
        Returns:
            Documentation about valid KQL operators and syntax
        """
        if not settings.AZURE_SEARCH_ENDPOINT or not settings.AZURE_SEARCH_KEY:
            logger.debug("Azure AI Search not configured")
            return ""
        
        try:
            from azure.search.documents.aio import SearchClient
            from azure.core.credentials import AzureKeyCredential
            
            search_client = SearchClient(
                endpoint=settings.AZURE_SEARCH_ENDPOINT,
                index_name=settings.AZURE_SEARCH_INDEX_NAME,
                credential=AzureKeyCredential(settings.AZURE_SEARCH_KEY)
            )
            
            # Search for KQL operator documentation
            search_query = f"KQL operators syntax {error_context}"
            
            async with search_client:
                search_results = await search_client.search(
                    search_text=search_query,
                    select=["title", "content"],
                    top=5
                )
            
            results = [result async for result in search_results]
            
            if not results:
                logger.debug("No KQL operator documentation found in Azure AI Search")
                return ""
            
            # Build context from search results
            context_parts = []
            for i, result in enumerate(results[:3], 1):  # Top 3 results
                context_parts.append(
                    f"Source {i}: {result['title']}\n{result['content'][:600]}\n"
                )
            
            context = "\n".join(context_parts)
            
            # Extract operator syntax with GPT-5
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):
                response = await self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": "Extract valid KQL operators and syntax patterns from documentation. Focus on correct operator usage, negation patterns, and regex matching."},
                        {"role": "user", "content": f"From these docs, extract valid KQL operator syntax and patterns:\n\n{context}"}
                    ],
                    max_completion_tokens=500
                )
            
            operator_docs = response.choices[0].message.content.strip()
            
            if operator_docs and len(operator_docs) > 30:
                logger.info(f" Extracted KQL operator documentation from Microsoft Learn")
                return f"\n\n📚 VALID KQL OPERATORS FROM MICROSOFT DOCS:\n\n{operator_docs}\n\n⚠️ USE ONLY THESE VALID OPERATORS.\n"
            
            return ""
            
        except Exception as e:
            logger.debug(f"Azure AI Search failed: {type(e).__name__}")
            return ""
    
    async def _get_microsoft_cost_examples(self) -> str:
        """
        Fetch real Azure Cost Management examples from Microsoft Learn using Azure AI Search
        """
        if not settings.USE_AZURE_SEARCH:
            return ""
            
        try:
            logger.info(f" Searching for Cost Management examples")
            
            from services.azure_search_service import get_azure_search_service
            search_service = get_azure_search_service()
            
            search_results = await search_service.search_cost_management_docs(top=5)
            
            if not search_results:
                return ""
            
            # Build context from search results (increased from 3 to 5 for better coverage)
            context_parts = []
            for i, result in enumerate(search_results[:5], 1):
                context_parts.append(
                    f"Source {i}: {result['title']}\n{result['content'][:500]}\n"
                )
            
            context = "\n".join(context_parts)
            
            # Extract examples with GPT-5
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": "Extract Azure Cost Management API query examples from documentation. Show examples with grouping, aggregation, and filtering patterns."},
                    {"role": "user", "content": f"From these docs, extract Cost Management API query examples:\n\n{context}"}
                ],
                max_completion_tokens=600
            )
            
            examples_text = response.choices[0].message.content.strip()
            
            if examples_text and len(examples_text) > 30:
                logger.info(f" Extracted Cost Management examples from Microsoft Learn")
                return f"\n\n📚 EXAMPLES FROM MICROSOFT LEARN (Cost Management):\n\n{examples_text}\n\n⚠️ USE EXACT FIELD NAMES AND AGGREGATION METHODS.\n"
            
            return ""
            
        except Exception as e:
            logger.debug(f"Azure AI Search failed: {type(e).__name__}")
            return ""
    
    def _extract_query_filters(self, natural_query: str) -> str:
        """
        Extract specific values from query that need filtering.
        GPT-5 determines appropriate field names from documentation.
        """
        import re
        
        entities = []
        
        # Extract quoted strings
        quoted = re.findall(r"[\"'][^\"'\n]+[\"']", natural_query)
        entities.extend([q.strip('"\'\'') for q in quoted])
        
        # Extract domain-like patterns (more permissive - 1+ dots)
        domains = re.findall(r'\b([a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+)\b', natural_query, re.IGNORECASE)
        entities.extend([d for d in domains if len(d) > 3])  # Lowered from 5 to catch shorter domains
        
        # Extract IP addresses
        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', natural_query)
        entities.extend(ips)
        
        # Extract 3-digit numbers (status codes), but exclude those that are part of IP addresses
        numbers = re.findall(r'\b\d{3}\b', natural_query)
        # Filter out numbers that are part of any IP address
        filtered_numbers = []
        for num in numbers:
            is_part_of_ip = any(num in ip for ip in ips)
            if not is_part_of_ip:
                filtered_numbers.append(num)
        entities.extend(filtered_numbers)
        
        # Extract paths
        paths = re.findall(r'(/[a-zA-Z0-9/_-]+)', natural_query)
        entities.extend(paths)
        
        if not entities:
            return ""
        
        unique_entities = list(dict.fromkeys(entities))
        
        filter_str = "\n\n🎯 CRITICAL - VALUES TO FILTER:\n"
        for entity in unique_entities:
            # Add context hints for AI
            if re.match(r'^\d{3}$', entity):
                filter_str += f"  - {entity} (HTTP status code)\n"
            elif '.' in entity and not entity.replace('.', '').isdigit():
                filter_str += f"  - {entity} (domain/hostname)\n"
            else:
                filter_str += f"  - {entity}\n"
        filter_str += "\n⚠️⚠️⚠️ MANDATORY: You MUST filter by ALL values listed above. Do NOT skip any filter.\n"
        filter_str += "\n📋 FIELD NAME RESOLUTION:\n"
        filter_str += "  1. Use ONLY column names from the table schema provided above\n"
        filter_str += "  2. Check documentation examples for filter patterns and syntax\n"
        filter_str += "  3. DO NOT invent or guess column names - if schema discovery is needed, the system will auto-correct\n"
        filter_str += "\n⚠️ FIELD SUFFIX HANDLING (_s vs _d):\n"
        filter_str += "  - Azure columns may have _s (string) or _d (double/numeric) suffixes\n"
        filter_str += "  - Use flexible OR logic: (field_s == value or field_d == value)\n"
        filter_str += "  - Example: (httpStatusCode_s == '404' or toint(httpStatusCode_d) == 404)\n"
        filter_str += "  - Example: (requestUri_s contains 'domain.com' or parseurl(requestUri_s).Host contains 'domain.com')\n"
        
        return filter_str
    
    def _extract_resource_graph_filters(self, natural_query: str) -> str:
        """
        Extract filter values for Resource Graph queries.
        Focuses ONLY on: IPs and CIDRs.
        Avoids extracting descriptive words from the query itself.
        """
        import re
        
        entities = []
        
        # 1. Extract CIDR notation (must come BEFORE IP extraction to avoid splitting)
        cidrs = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b', natural_query)
        entities.extend(cidrs)
        
        # 2. Extract IP addresses (but not if part of CIDR already captured)
        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', natural_query)
        for ip in ips:
            # Only add if not part of a CIDR we already captured
            if not any(ip in cidr for cidr in cidrs):
                entities.append(ip)
        
        # 3. Extract quoted strings ONLY (explicit filter values)
        quoted = re.findall(r"[\"'][^\"'\n]+[\"']", natural_query)
        entities.extend([q.strip('"\'') for q in quoted])
        
        if not entities:
            return ""
        
        # Deduplicate while preserving order
        unique_entities = list(dict.fromkeys(entities))
        
        filter_str = "\n\n🎯 CRITICAL - FILTER VALUES:\n"
        for entity in unique_entities:
            filter_str += f"  - {entity}\n"
        filter_str += "\n⚠️ MANDATORY: Apply ALL filter values above using the most appropriate KQL operator for the data type.\n"
        filter_str += "⚠️ Use EXACT values shown - NO placeholders!\n"
        
        return filter_str
    
    def _fix_table_from_cache(self, kql_query: str) -> Optional[str]:
        """
        Fix table name in generated KQL using Redis-cached mappings.
        Extracts resource type from WHERE clause and looks up correct table.
        Supports both single type (type =~ "...") and multiple types (type in~ (...)) patterns.
        
        Returns:
            Fixed KQL query, or None if invalid child resource type detected (needs regeneration)
        """
        import re
        from utils.arg_table_mapper import get_table_for_resource_provider, extract_resource_provider, is_valid_resource_type
        
        if not kql_query:
            return kql_query
        
        # Try to extract resource type from: type =~ "..." or type == "..."
        type_match = re.search(r'type\s*(?:=~|==|=)\s*["\']([^"\']+)["\']', kql_query, re.IGNORECASE)
        
        # If not found, try to extract from: type in~ ("type1", "type2")
        if not type_match:
            type_in_match = re.search(r'type\s+in~?\s*\(["\']([^"\']+)["\']', kql_query, re.IGNORECASE)
            if type_in_match:
                type_match = type_in_match
        
        if not type_match:
            return kql_query
        
        resource_type = type_match.group(1).lower()
        logger.info(f" Detected resource type in KQL: {resource_type}")
        
        # Check if this is a valid resource type
        if not is_valid_resource_type(resource_type):
            # Before giving up, check if the ARG table mapper knows this type —
            # types like microsoft.network/firewallpolicies/rulecollectiongroups are valid
            # ARG types (map to networkresources) but aren't in is_valid_resource_type.
            from utils.arg_table_mapper import get_table_for_resource_type
            mapped_table = get_table_for_resource_type(resource_type)
            if mapped_table:
                # Valid ARG type — fix table and return
                table_match = re.match(r'^(\w+)', kql_query.strip())
                if table_match and table_match.group(1) != mapped_table:
                    logger.info(f" Fixing table via full-type lookup: {table_match.group(1)} → {mapped_table}")
                    kql_query = re.sub(f'^{table_match.group(1)}\\b', mapped_table, kql_query.strip())
                return kql_query

            logger.warning(f" Invalid/unknown resource type detected: {resource_type}")
            
            # Check if it looks like an invalid child resource type
            segments = resource_type.split('/')
            if len(segments) >= 3:
                parent_type = '/'.join(segments[:2])
                child_property = segments[-1]
                logger.warning(f" Detected invalid child resource pattern: parent={parent_type}, child={child_property}")
                logger.warning(f" This type doesn't exist in Azure! Query will return 0 results.")
                logger.info(f" Returning None to trigger regeneration with child resource guidance...")
                return None  # Signal that regeneration is needed
        
        # Get resource provider
        resource_provider = extract_resource_provider(resource_type)
        if not resource_provider:
            return kql_query
        
        # Look up correct table from cache
        correct_table = get_table_for_resource_provider(resource_provider, resource_type)
        
        # Extract current table from KQL (first word/identifier before |)
        table_match = re.match(r'^(\w+)', kql_query.strip())
        if not table_match:
            return kql_query
        
        current_table = table_match.group(1)
        
        # Replace if different
        if current_table != correct_table:
            logger.info(f" Fixing table: {current_table} → {correct_table}")
            kql_query = re.sub(f'^{current_table}\\b', correct_table, kql_query.strip())
        else:
            logger.info(f" Table already correct: {current_table}")
        
        return kql_query
    
    def _extract_resource_graph_hints(self, natural_query: str) -> str:
        """
        Extract Resource Graph query hints from natural language.
        Detects locations, tags, quoted values, and filter constraints.
        
        Note: Table and resource type detection is handled by live documentation system.
        """
        import re
        
        hints = {}
        query_lower = natural_query.lower()
        
        # 1. Extract Azure locations
        azure_locations = [
            'eastus', 'eastus2', 'westus', 'westus2', 'westus3', 'centralus', 'northcentralus', 'southcentralus',
            'westcentralus', 'northeurope', 'westeurope', 'uksouth', 'ukwest', 'francecentral', 'francesouth',
            'germanywestcentral', 'switzerlandnorth', 'norwayeast', 'swedencentral', 'eastasia', 'southeastasia',
            'japaneast', 'japanwest', 'australiaeast', 'australiasoutheast', 'brazilsouth', 'canadacentral',
            'canadaeast', 'southafricanorth', 'uaenorth', 'centralindia', 'southindia', 'koreacentral'
        ]
        
        detected_locations = [loc for loc in azure_locations if loc in query_lower]
        if detected_locations:
            hints['locations'] = detected_locations
        
        # 2. Extract quoted strings (resource names, values)
        quoted = re.findall(r"[\"'][^\"'\n]+[\"']", natural_query)
        if quoted:
            hints['quoted_values'] = [q.strip('"\'\'') for q in quoted]
        
        # 3. Detect tag filters
        tag_pattern = r'tag[s]?\s*[=:]\s*(["\']?[\w-]+["\']?)'
        tags = re.findall(tag_pattern, query_lower)
        if tags:
            hints['tags'] = [t.strip('"\'') for t in tags]
        
        # 6. Extract resource group hints
        rg_pattern = r'resource\s*group[s]?\s*[=:]\s*(["\']?[\w-]+["\']?)'
        rgs = re.findall(rg_pattern, query_lower)
        if rgs:
            hints['resource_groups'] = [rg.strip('"\'') for rg in rgs]
        
        if not hints:
            return ""
        
        # Build hint string
        hint_str = "\n\n🎯 USER SPECIFIED CONSTRAINTS (HARD REQUIREMENTS):\n"
        
        if 'locations' in hints:
            locations_list = ', '.join([f"'{loc}'" for loc in hints['locations']])
            hint_str += f"\n📍 LOCATIONS: {', '.join(hints['locations'])}\n"
            hint_str += f"   Filter: | where location in~ ({locations_list})\n"
        
        if 'quoted_values' in hints:
            hint_str += f"\n📝 FILTER VALUES: {', '.join(hints['quoted_values'])}\n"
            hint_str += "   Use these for name, id, or property filtering\n"
        
        if 'tags' in hints:
            hint_str += f"\n🏷️ TAG FILTERS: {', '.join(hints['tags'])}\n"
            hint_str += "   Filter: | where tags.tagName == 'value'\n"
        
        if 'resource_groups' in hints:
            rgs_list = ', '.join([f"'{rg}'" for rg in hints['resource_groups']])
            hint_str += f"\n📦 RESOURCE GROUPS: {', '.join(hints['resource_groups'])}\n"
            hint_str += f"   Filter: | where resourceGroup in~ ({rgs_list})\n"
        
        hint_str += "\n⚠️⚠️⚠️ CRITICAL: These are MANDATORY constraints from the user.\n"
        hint_str += "DO NOT override, ignore, or substitute these values.\n"
        
        return hint_str
    
    async def _generate_kql_from_natural_language(self, natural_query: str, resource_context: Dict[str, Any] = None, user_info: Dict[str, Any] = None, original_user_query: str = None) -> str:
        """
        Use OpenAI to generate KQL query from natural language
        Enhanced with real Microsoft Learn examples for better accuracy
        AND diagnostic settings detection for mode-aware query generation
        
        Args:
            natural_query: AI-enriched query with context
            resource_context: Optional resource context
            user_info: User info containing management_token for diagnostic settings API
            original_user_query: Original user query for filter extraction
        """
        try:
            # Cache key: (resource_id, kind, query_intent) to avoid regenerating same queries
            cache_key = None
            if resource_context and resource_context.get("resource_id"):
                resource_id = resource_context["resource_id"]
                kind = resource_context.get("kind", "")
                # Use first 100 chars of query as intent signature
                query_sig = natural_query[:100]
                cache_key = f"kql:{resource_id}:{kind}:{hash(query_sig)}"
                
                # Check cache
                if hasattr(self, '_kql_cache'):
                    cached = self._kql_cache.get(cache_key)
                    if cached:
                        logger.info(f" Using cached KQL for {kind} resource")
                        return cached
                else:
                    self._kql_cache = {}
            
            # Extract specific filters from ORIGINAL user query (not AI interpretation)
            query_for_extraction = original_user_query if original_user_query else natural_query
            logger.info(f" Extracting filters from query: {query_for_extraction[:100]}...")
            extracted_filters = self._extract_query_filters(query_for_extraction)
            if extracted_filters:
                logger.info(f" Extracted filters: {extracted_filters[:200]}...")
            else:
                logger.info(f" No filters extracted from query")
            
            # Build context string using the prompt module
            context_str = build_resource_context(resource_context, self._map_to_diagnostics_resource_type)
            
            # Get real Microsoft Learn KQL examples
            resource_type = resource_context.get("resource_type") if resource_context else None
            
            # If no resource_type from context, try to infer from the query
            if not resource_type:
                resource_type = await self._infer_resource_type_from_query(natural_query)
                logger.info(f" Inferred resource_type from query: {resource_type}")
            else:
                logger.info(f" Using resource_type from context: {resource_type}")
            
            # Fetch 'kind' field for resources that need it (e.g., Microsoft.Cdn/profiles)
            resource_kind = None
            if resource_type and resource_context and resource_context.get("resource_id") and user_info:
                resource_id = resource_context.get("resource_id")
                if "kind" not in resource_context:
                    try:
                        import httpx
                        # Use management_token if available, otherwise fall back to token
                        management_token = user_info.get("management_token") or user_info.get("token")
                        if management_token:
                            # Use newer API version that properly supports Front Door
                            api_url = f"https://management.azure.com{resource_id}?api-version=2023-05-01"
                            logger.info(f" Fetching resource kind from: {api_url[:80]}...")
                            
                            async with httpx.AsyncClient() as client:
                                headers = {"Authorization": f"Bearer {management_token}", "Content-Type": "application/json"}
                                response = await client.get(api_url, headers=headers, timeout=10.0)
                                
                                if response.status_code == 200:
                                    resource_details = response.json()
                                    if "kind" in resource_details:
                                        resource_context["kind"] = resource_details["kind"]
                                        resource_kind = resource_details["kind"]
                                        logger.info(f" Resource kind detected: {resource_details['kind']}")
                                    elif "sku" in resource_details and "name" in resource_details["sku"]:
                                        sku_name = resource_details["sku"]["name"]
                                        if "frontdoor" in sku_name.lower():
                                            resource_context["kind"] = "frontdoor"
                                            resource_kind = "frontdoor"
                                            logger.info(f" Inferred kind from SKU ({sku_name}): frontdoor")
                                        elif "cdn" in sku_name.lower():
                                            resource_context["kind"] = "cdn"
                                            resource_kind = "cdn"
                                            logger.info(f" Inferred kind from SKU ({sku_name}): cdn")
                                else:
                                    logger.warning(f" Resource fetch returned: {response.status_code}")
                    except Exception as e:
                        logger.warning(f" Could not fetch resource kind: {type(e).__name__}")
                else:
                    resource_kind = resource_context.get("kind")
            
            # NEW: Check diagnostic settings for mode detection

            diagnostic_context = await self._get_diagnostic_settings_context(resource_context, user_info)
            
            # Parse diagnostic mode to filter search results
            log_mode = None  # 'Dedicated' or 'AzureDiagnostics'
            if diagnostic_context and 'DETECTED MODE: Dedicated' in diagnostic_context:
                log_mode = 'Dedicated'
                logger.info(" Detected: Dedicated mode - will prioritize resource-specific tables")
            elif diagnostic_context and 'DETECTED MODE: AzureDiagnostics' in diagnostic_context:
                log_mode = 'AzureDiagnostics'
                logger.info(" Detected: AzureDiagnostics mode - will prioritize legacy table examples")
            
            # Pass log mode to resource_context for search filtering
            if log_mode and resource_context:
                resource_context['log_mode'] = log_mode
            
            # Build enriched search query including resource name for better AI Search results
            resource_name = resource_context.get("resource_name") if resource_context else None
            enriched_query = natural_query
            if resource_name and resource_type:
                enriched_query = f"{resource_name} ({resource_type}) {natural_query}"
                logger.info(f" Enriched search query: {enriched_query[:100]}...")
            
            # Pass the enriched query to get more specific examples
            azure_docs_examples = await self._get_microsoft_kql_examples(resource_type, enriched_query, resource_context)
            
            # Combine all context with diagnostic settings info
            # CRITICAL: Diagnostic context MUST come FIRST to override any other instructions
            # Include BOTH original user query (for context) and AI reasoning
            # Put original query LAST so it's freshest in AI's context window
            ai_reasoning_section = f"\n\nAI UNDERSTANDING: {natural_query}" if natural_query else ""
            original_query_section = f"\n\n🎯 USER QUERY: {original_user_query}" if original_user_query else f"\n\n🎯 USER QUERY: {natural_query}"
            
            # Always put diagnostic_context FIRST so it takes precedence
            user_message = natural_query
            if context_str:
                user_message = diagnostic_context + context_str + azure_docs_examples + extracted_filters + ai_reasoning_section + original_query_section
            elif azure_docs_examples:
                user_message = diagnostic_context + azure_docs_examples + extracted_filters + ai_reasoning_section + original_query_section
            else:
                user_message = diagnostic_context + extracted_filters + ai_reasoning_section + original_query_section
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                # Scale token budget: large queries (IP lists, bulk CIDRs) need more output space
                kql_token_budget = 8000 if len(user_message) > 5000 else 500
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": KQL_GENERATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_completion_tokens=kql_token_budget
            )
            
            kql_query = response.choices[0].message.content.strip()
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                logger.error(f"KQL generation truncated at {kql_token_budget} tokens — query will be incomplete. Input size: {len(user_message)}")
            
            # Clean up the response (remove code blocks if present)
            if kql_query.startswith("```"):
                lines = kql_query.split("\n")
                kql_query = "\n".join([line for line in lines if not line.startswith("```") and not line.startswith("kql")])
                kql_query = kql_query.strip()
            
            logger.info(f"Generated KQL query: {kql_query}")
            
            # Don't cache here - only cache after successful execution in ai_query_service
            # This prevents caching broken queries
            
            return kql_query
            
        except Exception as e:
            logger.error(f"Failed to generate KQL with OpenAI: {type(e).__name__}")
            return ""
    
    async def _regenerate_kql_with_schema_only(
        self, 
        natural_query: str,
        table_name: str,
        schema_columns: List[Dict[str, str]],
        diagnostic_context: str = "",
        priority_columns: List[str] = None
    ) -> str:
        """
        Lightweight KQL regeneration for schema heal - NO search, NO diagnostic checks, NO resource fetching.
        Only regenerates query with actual schema column names.
        
        Args:
            natural_query: User's query intent
            table_name: The table to query
            schema_columns: List of actual columns with name and type
            diagnostic_context: Pre-fetched diagnostic context (optional)
            priority_columns: Columns relevant to query (optional)
        """
        try:
            # Build concise schema context
            schema_text = f"\n\n🔍 ACTUAL SCHEMA FOR {table_name}:\n"
            schema_text += f"⚠️ These are the ONLY columns that exist. Use ONLY these names:\n\n"
            
            if priority_columns:
                schema_text += "📌 RELEVANT COLUMNS:\n"
                for col in schema_columns:
                    if any(p.lower() in col['name'].lower() for p in priority_columns):
                        schema_text += f"  - {col['name']} ({col['type']})\n"
                schema_text += "\n"
            
            schema_text += "📋 ALL COLUMNS:\n"
            for col in schema_columns[:50]:  # Limit to 50 to avoid token bloat
                schema_text += f"  - {col['name']} ({col['type']})\n"
            
            if len(schema_columns) > 50:
                schema_text += f"\n... and {len(schema_columns) - 50} more columns\n"
            
            schema_text += f"\n⚠️ MANDATORY: Query {table_name} table using ONLY column names listed above.\n"
            
            # Combine contexts
            prompt = f"{diagnostic_context}\n{schema_text}\n\nUSER QUERY: {natural_query}\n\nGenerate KQL query for {table_name} table."
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):
                response = await self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": KQL_GENERATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    max_completion_tokens=500
                )
            
            kql_query = response.choices[0].message.content.strip()
            
            # Clean up code blocks
            if kql_query.startswith("```"):
                lines = kql_query.split("\n")
                kql_query = "\n".join([line for line in lines if not line.startswith("```") and not line.startswith("kql")])
                kql_query = kql_query.strip()
            
            logger.info(f" Schema-heal generated query: {kql_query[:100]}...")
            return kql_query
            
        except Exception as e:
            logger.error(f" Schema-heal KQL generation failed: {type(e).__name__}")
            return ""
    
    async def explain_results(self, original_query: str, results: dict, kql_query: str = None) -> str:
        """
        Generate a natural language explanation of query results with proper code formatting
        """
        try:
            # Prepare results summary for AI
            results_summary = self._prepare_results_for_explanation(results)
            
            # Add KQL context if provided
            query_context = ""
            if kql_query:
                query_context = f"\n\nKQL Query executed: {kql_query}"
            
            # Enhanced system prompt for better code formatting
            enhanced_system_prompt = RESULTS_EXPLANATION_SYSTEM_PROMPT + """

CRITICAL CODE FORMATTING RULES:
- When showing code examples (Terraform, PowerShell, ARM templates, etc.), ALWAYS use proper markdown code blocks
- Use language-specific code fences: ```terraform, ```powershell, ```bash, ```json, ```yaml
- NEVER put multi-line code in a single line - always preserve proper formatting
- Ensure proper indentation and line breaks within code blocks
- For Terraform: Use proper resource block formatting with correct indentation
- For PowerShell: Use proper cmdlet formatting with line breaks for parameters
- For JSON/YAML: Use proper nested structure formatting
- NEVER include warning boxes, yellow warning sections, or cautionary language about examples being real data
- Do NOT generate warning boxes with phrases like "Note that these are actual info" or similar disclaimers
- Present code examples as clean, usable code without any warning boxes or cautionary sections
- Each code block should be properly formatted and ready to use without disclaimers

EXAMPLE GOOD FORMATTING:
```terraform
resource "azurerm_resource_group" "example" {
  name     = "example-resources"
  location = "West Europe"
}

resource "azurerm_storage_account" "example" {
  name                     = "examplestorageacct"
  resource_group_name      = azurerm_resource_group.example.name
  location                 = azurerm_resource_group.example.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}
```

NEVER format like this (bad):
resource "azurerm_resource_group" "example" { name = "example-resources" location = "West Europe" }

Always use the GOOD formatting approach with proper line breaks and indentation."""
            
            messages = [
                {
                    "role": "system",
                    "content": enhanced_system_prompt
                },
                {
                    "role": "user", 
                    "content": f"""Original query: "{original_query}"{query_context}

Results: {results_summary}

Please explain these results in plain English with proper markdown formatting. If providing code examples, use proper code blocks with language specification."""
                }
            ]
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=messages,
                max_completion_tokens=800  # Increased for better code formatting
            )
            
            explanation = response.choices[0].message.content.strip()
            return explanation
            
        except Exception as e:
            logger.error(f"Failed to explain results: {type(e).__name__}")
            return f"Query executed successfully. Found {len(results.get('data', []))} results."
    
    def _prepare_results_for_explanation(self, results: dict) -> str:
        """
        Prepare results data for AI explanation with comprehensive analysis
        Include statistics, patterns, and anomalies from ALL data, not just samples
        """
        # Check for error conditions
        if results.get("status") == "error" or not results.get("success", True):
            return f"Error: {results.get('error', 'Unknown error occurred')}"
        
        # Handle cost analysis format (nested results structure)
        if results.get("query_type") == "cost_analysis" and "results" in results:
            cost_data = results.get("results", {})
            
            # Check if we have actual cost data
            service_breakdown = cost_data.get("service_breakdown", [])
            current_period = cost_data.get("current_period", {})
            analysis = cost_data.get("analysis", {})
            
            if not service_breakdown and current_period.get("total_cost", 0) == 0:
                return "No cost data found for the specified period"
            
            # Prepare comprehensive cost summary
            cost_summary = {
                "query_type": "cost_analysis",
                "time_period": cost_data.get("time_period", {}),
                "total_cost": current_period.get("total_cost", 0),
                "currency": current_period.get("currency", "USD"),
                "trend": analysis.get("trend", "unknown"),
                "change_percent": analysis.get("change_percent", 0),
                "change_amount": analysis.get("change_amount", 0),
                "daily_average": analysis.get("daily_average", 0),
                "insights": analysis.get("insights", []),
                "top_services": service_breakdown[:10] if service_breakdown else [],
                "total_services": len(service_breakdown),
                "anomalies": analysis.get("anomalies", [])
            }
            
            # Include comparison_summary if available (has better labels and context)
            if "comparison_summary" in cost_data:
                cost_summary["comparison"] = cost_data["comparison_summary"]
            
            return json.dumps(cost_summary, indent=2, cls=DateTimeEncoder)
        
        # Handle Azure Monitor Metrics format (azure_metrics query type)
        if results.get("query_type") == "azure_metrics" and "results" in results:
            metrics_data = results.get("results", [])
            
            if not metrics_data:
                return "No metric data found"
            
            # Parse metrics structure and extract timeseries data for analysis
            metrics_summary = []
            all_data_points = []
            
            for metric in metrics_data:
                metric_name = metric.get("name", "Unknown")
                metric_unit = metric.get("unit", "")
                timeseries_list = metric.get("timeseries", [])
                
                # Extract all data points from all timeseries
                total_points = 0
                for timeseries in timeseries_list:
                    data_points = timeseries.get("data", [])
                    total_points += len(data_points)
                    all_data_points.extend(data_points)
                
                # Calculate statistics for this metric
                if timeseries_list and len(timeseries_list) > 0:
                    first_series = timeseries_list[0]
                    data_points = first_series.get("data", [])
                    
                    if data_points:
                        # Extract numeric values (average, total, minimum, maximum, count)
                        values = []
                        for dp in data_points:
                            # Check for different aggregation types
                            if 'average' in dp and dp['average'] is not None:
                                values.append(dp['average'])
                            elif 'total' in dp and dp['total'] is not None:
                                values.append(dp['total'])
                            elif 'maximum' in dp and dp['maximum'] is not None:
                                values.append(dp['maximum'])
                        
                        if values:
                            metric_stats = {
                                "metric": metric_name,
                                "unit": metric_unit,
                                "data_points": len(data_points),
                                "min": min(values),
                                "max": max(values),
                                "average": sum(values) / len(values),
                                "latest": values[-1] if values else None
                            }
                            metrics_summary.append(metric_stats)
            
            summary = {
                "query_type": "azure_metrics",
                "total_metrics": len(metrics_data),
                "metrics_summary": metrics_summary,
                "total_data_points": len(all_data_points),
                "sample_data_points": all_data_points[:5] if all_data_points else []
            }
            
            return json.dumps(summary, indent=2, cls=DateTimeEncoder)
        
        # Handle Azure Monitor service format (tables structure)
        if "tables" in results:
            tables = results.get("tables", [])
            if not tables or all(len(t.get("rows", [])) == 0 for t in tables):
                return "No results found"
            
            # Get first table with data
            primary_table = next((t for t in tables if len(t.get("rows", [])) > 0), None)
            if primary_table:
                rows = primary_table.get("rows", [])
                columns = [col["name"] for col in primary_table.get("columns", [])]
                
                # Analyze ALL rows for comprehensive insights
                summary = {
                    "total_records": len(rows),
                    "columns": columns,
                    "data_analysis": self._analyze_all_data(rows, columns),
                    "sample_records": [dict(zip(columns, row)) for row in rows[:5]]  # 5 samples for context
                }
                return json.dumps(summary, indent=2, cls=DateTimeEncoder)
        
        # Handle Resource Graph format (results array)
        if "results" in results and isinstance(results["results"], list):
            data = results["results"]
            if not data:
                return "No results found"
            
            columns = list(data[0].keys()) if isinstance(data[0], dict) and len(data) > 0 else []
            data_analysis = self._analyze_all_data(data, columns)
            
            # Only include columns that have data in the analysis
            columns_with_data = list(data_analysis.keys())
            
            summary = {
                "total_records": len(data),
                "columns": columns_with_data,
                "data_analysis": data_analysis,
                "sample_records": data[:5],  # 5 samples for context
                "query_type": results.get("query_type", "unknown")
            }
            return json.dumps(summary, indent=2, cls=DateTimeEncoder)
        
        # Handle legacy format (data array)
        data = results.get("data", [])
        if not data:
            return "No results found"
        
        # For KQL results, analyze ALL data
        if isinstance(data, list) and len(data) > 0:
            columns = list(data[0].keys()) if isinstance(data[0], dict) else []
            data_analysis = self._analyze_all_data(data, columns)
            
            # Only include columns that have data in the analysis
            columns_with_data = list(data_analysis.keys())
            
            summary = {
                "total_records": len(data),
                "columns": columns_with_data,  # Only columns with actual data
                "data_analysis": data_analysis,
                "sample_records": data[:5]  # 5 samples for context
            }
            return json.dumps(summary, indent=2, cls=DateTimeEncoder)
        
        # For other result types
        return json.dumps(results, indent=2, cls=DateTimeEncoder)[:1000]  # Limit size
    
    def _analyze_all_data(self, data: list, columns: list) -> dict:
        """
        Analyze data to extract patterns, statistics, and anomalies
        For large datasets (>5000 rows), uses statistical sampling for performance
        """
        total_rows = len(data)
        
        # For very large datasets, use sampling for non-critical columns
        use_sampling = total_rows > 5000
        sample_size = min(5000, total_rows) if use_sampling else total_rows
        sample_data = data[:sample_size] if use_sampling else data
        
        analysis = {
            "total_rows_analyzed": total_rows,
            "sampling_used": use_sampling,
            "sample_size": sample_size if use_sampling else total_rows
        }
        
        # Analyze each column across data (sampled or full)
        for col_idx, col_name in enumerate(columns):
            try:
                # Extract values for this column, filtering out None, empty strings, and "null"
                if isinstance(sample_data[0], dict):
                    values = [
                        row.get(col_name) for row in sample_data 
                        if row.get(col_name) is not None 
                        and row.get(col_name) != '' 
                        and str(row.get(col_name)).lower() != 'null'
                    ]
                else:
                    values = [
                        row[col_idx] for row in sample_data 
                        if len(row) > col_idx 
                        and row[col_idx] is not None 
                        and row[col_idx] != '' 
                        and str(row[col_idx]).lower() != 'null'
                    ]
                
                if not values:
                    continue
                
                col_analysis = {}
                
                # Count unique values
                unique_values = list(set(str(v) for v in values))
                col_analysis["unique_count"] = len(unique_values)
                
                # For categorical data (Status, Level, Category, etc.)
                # Limit to top 20 values to avoid huge distributions
                if any(keyword in col_name.lower() for keyword in ['status', 'level', 'category', 'result', 'code', 'type', 'severity']):
                    value_counts = {}
                    for val in values:
                        val_str = str(val)
                        value_counts[val_str] = value_counts.get(val_str, 0) + 1
                    col_analysis["value_distribution"] = dict(sorted(value_counts.items(), key=lambda x: x[1], reverse=True)[:20])
                
                # For numeric data, calculate statistics
                try:
                    numeric_values = [float(v) for v in values if self._is_numeric(v)]
                    if numeric_values and len(numeric_values) > 0:
                        col_analysis["numeric_stats"] = {
                            "min": min(numeric_values),
                            "max": max(numeric_values),
                            "avg": sum(numeric_values) / len(numeric_values),
                            "count": len(numeric_values)
                        }
                except:
                    pass
                
                # Store analysis for this column if we found interesting data
                if col_analysis:
                    analysis[col_name] = col_analysis
                    
            except Exception as e:
                logger.warning(f"Failed to analyze column {col_name}: {type(e).__name__}")
                continue
        
        return analysis
    
    def _is_numeric(self, value) -> bool:
        """Check if a value can be converted to a number"""
        try:
            float(value)
            return True
        except:
            return False
    
    def _create_fallback_query(self, natural_query: str) -> Dict[str, Any]:
        """
        Create a fallback KQL query structure when AI processing fails
        """
        query_lower = natural_query.lower()
        
        # Simple keyword-based KQL generation
        if any(keyword in query_lower for keyword in ["login", "signin", "authentication", "sign"]):
            return {
                "type": "log_analytics",
                "query_intent": natural_query,
                "kql_query": "SigninLogs | where TimeGenerated > ago(24h) | project TimeGenerated, UserPrincipalName, AppDisplayName, ConditionalAccessStatus | take 100",
                "workspace_id": "",
                "scope": "workspace",
                "requires_workspace_discovery": True,
                "suggested_tables": ["SigninLogs"],
                "time_range": "24h",
                "confidence": 0.6,
                "ai_processed": False
            }
        elif any(keyword in query_lower for keyword in ["error", "failed", "failure", "exception"]):
            return {
                "type": "log_analytics",
                "query_intent": natural_query,
                "kql_query": "AzureActivity | where TimeGenerated > ago(24h) | where ActivityStatus == 'Failed' | project TimeGenerated, Caller, OperationName, ActivityStatus | take 100",
                "workspace_id": "",
                "scope": "workspace", 
                "requires_workspace_discovery": True,
                "suggested_tables": ["AzureActivity"],
                "time_range": "24h",
                "confidence": 0.6,
                "ai_processed": False
            }
        elif any(keyword in query_lower for keyword in ["vm", "virtual machine", "compute", "server"]):
            return {
                "type": "log_analytics",
                "query_intent": natural_query,
                "kql_query": "Heartbeat | where TimeGenerated > ago(24h) | summarize LastHeartbeat = max(TimeGenerated) by Computer | take 100",
                "workspace_id": "",
                "scope": "workspace",
                "requires_workspace_discovery": True,
                "suggested_tables": ["Heartbeat"],
                "time_range": "24h",
                "confidence": 0.6,
                "ai_processed": False
            }
        elif any(keyword in query_lower for keyword in ["performance", "cpu", "memory", "disk"]):
            return {
                "type": "log_analytics", 
                "query_intent": natural_query,
                "kql_query": "Perf | where TimeGenerated > ago(1h) | where ObjectName == 'Processor' and CounterName == '% Processor Time' | project TimeGenerated, Computer, CounterValue | take 100",
                "workspace_id": "",
                "scope": "workspace",
                "requires_workspace_discovery": True,
                "suggested_tables": ["Perf"],
                "time_range": "1h", 
                "confidence": 0.6,
                "ai_processed": False
            }
        else:
            # Generic activity query
            return {
                "type": "log_analytics",
                "query_intent": natural_query,
                "kql_query": "AzureActivity | where TimeGenerated > ago(24h) | project TimeGenerated, Caller, OperationName, ActivityStatus | take 100",
                "workspace_id": "",
                "scope": "workspace",
                "requires_workspace_discovery": True,
                "suggested_tables": ["AzureActivity"],
                "time_range": "24h",
                "confidence": 0.5,
                "ai_processed": False
            }
    
    async def suggest_related_queries(self, original_query: str, results: Dict[str, Any]) -> List[str]:
        """
        Suggest related queries based on current query and results
        """
        try:
            system_prompt = """Suggest 3-5 related Azure queries that build on the current results. Return a JSON array of strings."""

            results_summary = self._prepare_results_summary(results)

            user_prompt = f"""Original query: "{original_query}"
Results summary: {results_summary}

Suggest related queries to run next."""
            
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):

            
                response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_completion_tokens=300
            )
            
            suggestions_text = response.choices[0].message.content
            
            try:
                suggestions = json.loads(suggestions_text)
                return suggestions if isinstance(suggestions, list) else []
            except json.JSONDecodeError:
                # Fallback to empty list if JSON parsing fails
                return []
                
        except Exception as e:
            logger.error(f"AI suggestions failed: {type(e).__name__}")
            return []

    async def nl_to_firewall_kql(self, natural_query: str) -> str:
        """
        Convert a natural-language description into a KQL query targeting
        Azure Firewall log tables (AZFWNetworkRule, AZFWApplicationRule,
        AZFWNatRule, AZFWThreatIntel).

        Returns the raw KQL string only — no markdown fences, no explanation.
        """
        system_prompt = """You are an Azure Firewall KQL expert.

Available tables and their key columns:
- AZFWNetworkRule: TimeGenerated, Action, SourceIp, SourcePort, DestinationIp, DestinationPort, Protocol, Rule, RuleCollection, RuleCollectionGroup, Policy
- AZFWApplicationRule: TimeGenerated, Action, SourceIp, DestinationPort, Protocol, Rule, RuleCollection, RuleCollectionGroup, Fqdn, TargetUrl, Policy
- AZFWNatRule: TimeGenerated, Action, SourceIp, DestinationIp, DestinationPort, Protocol, Rule, RuleCollection, RuleCollectionGroup, Policy
- AZFWThreatIntel: TimeGenerated, Action, SourceIp, DestinationIp, DestinationPort, Protocol, Fqdn, ThreatDescription, Policy

Rules:
- Return ONLY valid KQL. No markdown, no explanation, no code fences.
- ALWAYS use `union isfuzzy=true` when querying multiple tables — never plain `union`.
- `isfuzzy=true` is mandatory: tables may not exist in every workspace.
- A time range is always applied server-side from the UI time picker. You do NOT need to add a TimeGenerated filter unless the user explicitly asks for a specific time (e.g. "last 2 hours", "yesterday", "past 30 minutes").
- If the user specifies a time, add it as `| where TimeGenerated >= ago(Xh)` or `| where TimeGenerated between (datetime(...) .. datetime(...))`. The server will take the intersection with the UI picker — the shortest (most restrictive) time range wins, same as Azure Portal.
- Use =~ for case-insensitive string comparisons.
- For IPs use ipv4_is_in_range() when CIDR is involved.
- For MULTIPLE IPs/CIDRs (user pastes a list): use `let iocs = dynamic([...]);` then `| mv-expand ioc = iocs | where ipv4_is_in_range(DestinationIp, tostring(ioc))`. Never use `in` operator for CIDR matching.
- For counting unique values use `summarize dcount(field)` or `summarize count() by field`.
- End aggregation queries with `| order by Count desc`. End raw log queries with `| order by TimeGenerated desc`.
"""
        user_prompt = f"Write a KQL query for: {natural_query}"

        try:
            async with (self._rate_limiter if self._rate_limiter else _NoOpContext()):
                # Scale token budget: large inputs (IP lists, bulk CIDRs) need more output space
                token_budget = 16000 if len(natural_query) > 2000 else 600
                response = await self.client.chat.completions.create(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    max_completion_tokens=token_budget,
                )
            kql = response.choices[0].message.content.strip()
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                logger.error(f"nl_to_firewall_kql truncated at {token_budget} tokens — query incomplete. Input len={len(natural_query)}")
            kql = response.choices[0].message.content.strip()
            # Strip markdown fences if model wraps anyway
            if kql.startswith("```"):
                kql = "\n".join(
                    line for line in kql.splitlines()
                    if not line.strip().startswith("```")
                ).strip()
            return kql
        except Exception as exc:
            logger.error(f"nl_to_firewall_kql failed: {type(exc).__name__}")
            raise
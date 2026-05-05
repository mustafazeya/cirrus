"""
AI Query Service
Handles natural language queries for cost management and log analytics
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from contextvars import ContextVar
from datetime import datetime, timedelta
from collections import Counter
from dataclasses import dataclass

from config import settings
from .dual_credential_service import dual_cred_service
from .cache_service import cache_service
from .advanced_query_cache_service import advanced_query_cache
from .query_validator import query_validator

logger = logging.getLogger(__name__)

# Per-request state — isolated per asyncio task via ContextVar so concurrent requests
# on this singleton cannot bleed scope into each other.
_ctx_subscription_ids: ContextVar[Optional[list]] = ContextVar('_ctx_subscription_ids', default=None)
_ctx_resource_group: ContextVar[Optional[str]] = ContextVar('_ctx_resource_group', default=None)
_ctx_resource_id: ContextVar[Optional[str]] = ContextVar('_ctx_resource_id', default=None)
_ctx_user_query: ContextVar[str] = ContextVar('_ctx_user_query', default='')
_ctx_conversation_context: ContextVar[list] = ContextVar('_ctx_conversation_context', default=[])

# Resource Graph Analysis Constants — sourced from config.py / settings
from config import settings as _settings
ARG_MAX_SAMPLE_SIZE = _settings.ARG_MAX_SAMPLE_SIZE
ARG_MAX_CONTEXT_LENGTH = _settings.ARG_MAX_CONTEXT_LENGTH
ARG_REDUCED_AGGREGATION_COUNT = _settings.ARG_REDUCED_AGGREGATION_COUNT
ARG_MAX_FIELD_LENGTH_FOR_AGGREGATION = _settings.ARG_MAX_FIELD_LENGTH
ARG_MAX_COMPLETION_TOKENS = _settings.ARG_MAX_COMPLETION_TOKENS
ARG_LONG_FIELD_THRESHOLD = _settings.ARG_MAX_FIELD_LENGTH

def serialize_datetime(obj):
    """Helper function to serialize datetime objects for JSON"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)

def get_intelligent_sample(results: List[Dict[str, Any]], max_samples: int = 20) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Sample results evenly and extract all relevant fields
    Works for ANY table by dynamically detecting fields
    
    Args:
        results: List of result dictionaries
        max_samples: Maximum number of samples to return (default 20)
    
    Returns:
        Tuple of (sampled_results, relevant_fields)
    """
    if not results:
        return [], []
    
    # Get all fields from first result
    all_fields = list(results[0].keys())
    
    # Remove empty/null columns - only keep fields with actual data
    relevant_fields = []
    for field in all_fields:
        has_data = any(
            r.get(field) is not None and r.get(field) != '' and str(r.get(field)).lower() != 'null'
            for r in results
        )
        if has_data:
            relevant_fields.append(field)
    
    if len(results) <= max_samples:
        return results, relevant_fields
    
    # Evenly distributed sampling using proportional indices
    indices = [int(i * len(results) / max_samples) for i in range(max_samples)]
    sample_rows = [results[i] for i in indices]
    
    return sample_rows, relevant_fields

@dataclass
class QueryIntent:
    """Represents the parsed intent of a user query"""
    type: str  # 'cost', 'logs', 'resources', 'general'
    original_query: str
    time_range: Optional[Dict[str, str]] = None
    subscription_filter: Optional[List[str]] = None
    resource_group_filter: Optional[str] = None
    resource_filter: Optional[str] = None  # Specific resource type or ID
    workspace_name: Optional[str] = None
    confidence: float = 0.0
    generated_query: Optional[str] = None
    is_comparison: bool = False  # Whether query asks to compare two time periods
    comparison_periods: Optional[List[str]] = None  # e.g., ['this_month', 'last_month']


def _kql_with_correct_table(kql: str) -> str | None:
    """
    Inspect the resource type filter in a KQL query, ask the ARG table mapper
    which table that type belongs to, and rewrite the query to use it.
    Returns None if the query already uses the correct table or no type filter found.
    """
    import re
    from utils.arg_table_mapper import get_table_for_resource_type

    # Extract current table name (first token in the query)
    table_match = re.match(r'^(\s*)(\w+)(\s)', kql, re.IGNORECASE)
    if not table_match:
        return None
    current_table = table_match.group(2).lower()

    # Extract the resource type from 'where type =~ "..."'
    type_match = re.search(
        r"type\s*=~\s*['\"]([a-z0-9./\-]+)['\"]",
        kql,
        re.IGNORECASE,
    )
    if not type_match:
        return None

    resource_type = type_match.group(1)
    try:
        correct_table = get_table_for_resource_type(resource_type).lower()
    except Exception:
        return None

    if correct_table == current_table:
        return None  # already correct

    # Rewrite table name
    return kql[:table_match.start(2)] + correct_table + kql[table_match.end(2):]


# _kql_with_parent_type removed — the agentic loop now uses AI self-correction instead of regex surgery


class AIQueryService:
    """
    Processes natural language queries and routes them to appropriate Azure services
    """
    
    def __init__(self):
        self.settings = settings
        from services.openai_client import get_openai_client
        self.openai_client = get_openai_client()
        if not self.openai_client:
            logger.warning("OpenAI API key not configured")

    # ── Per-request context properties ──────────────────────────────────────────
    # Backed by module-level ContextVars. Each asyncio task (one per HTTP request)
    # gets its own copy, so concurrent requests on this singleton are fully isolated.
    # All existing self._current_* reads and writes work unchanged.

    @property
    def _current_subscription_ids(self) -> list:
        return _ctx_subscription_ids.get() or []

    @_current_subscription_ids.setter
    def _current_subscription_ids(self, value: list) -> None:
        _ctx_subscription_ids.set(value)

    @property
    def _current_resource_group(self) -> Optional[str]:
        return _ctx_resource_group.get()

    @_current_resource_group.setter
    def _current_resource_group(self, value: Optional[str]) -> None:
        _ctx_resource_group.set(value)

    @property
    def _current_resource_id(self) -> Optional[str]:
        return _ctx_resource_id.get()

    @_current_resource_id.setter
    def _current_resource_id(self, value: Optional[str]) -> None:
        _ctx_resource_id.set(value)

    @property
    def _current_user_query(self) -> str:
        return _ctx_user_query.get()

    @_current_user_query.setter
    def _current_user_query(self, value: str) -> None:
        _ctx_user_query.set(value)

    @property
    def _current_conversation_context(self) -> list:
        return _ctx_conversation_context.get()

    @_current_conversation_context.setter
    def _current_conversation_context(self, value: list) -> None:
        _ctx_conversation_context.set(value)

    # ────────────────────────────────────────────────────────────────────────────

    async def process_natural_query(
        self,
        user_query: str,
        user_data: Dict[str, Any],
        subscription_ids: Optional[List[str]] = None,
        resource_group: Optional[str] = None,
        resource_id: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for processing natural language queries
        Uses AI to directly generate and execute queries based on full context
        
        ChatGPT approach: Don't classify, just understand and execute
        
        Security: AI classifies intent FIRST, then keyword validation applied only to Azure operations.
        General queries (code generation, help) skip keyword checks.
        """
        try:
            # Store subscription_ids for use in execution methods
            self._current_subscription_ids = subscription_ids or []
            self._current_resource_group = resource_group
            self._current_resource_id = resource_id
            self._current_user_query = user_query  # Store for metric detection            # Debug logging
            logger.debug(f" process_natural_query called with {len(self._current_subscription_ids)} subscriptions")
            if len(self._current_subscription_ids) > 0:
                logger.debug(f" Sample subscription IDs: {self._current_subscription_ids[:3]}")
            
            # Debug conversation context
            if conversation_history:
                logger.info(f" Conversation context: {len(conversation_history)} messages")
                for i, msg in enumerate(conversation_history[-3:]):  # Show last 3
                    role = msg.get('role', 'unknown')
                    content = msg.get('content', '')[:100]  # First 100 chars
                    query_type = msg.get('query_type', 'none')
                    suggestions = msg.get('suggestions', [])
                    sugg_preview = f" | Suggestions: {suggestions}" if suggestions else ""
                    logger.info(f" Context[{i}]: [{role}] [{query_type}] {content}...{sugg_preview}")
            else:
                logger.info(f" No conversation context available")
            
            # Build full context for AI
            context = self._build_full_context(
                user_query=user_query,
                conversation_history=conversation_history,
                subscription_ids=subscription_ids,
                resource_group=resource_group,
                resource_id=resource_id
            )
            
            # Thread conversation history into user_data so _ai_understand_and_execute receives it
            user_data['conversation_history'] = conversation_history or []
            
            # Let AI decide what to do and generate the query
            ai_response = await self._ai_understand_and_execute(
                context=context,
                user_data=user_data
            )
            
            return ai_response
                
        except Exception as e:
            logger.error(f"Error processing query: {e}", exc_info=True)
            return {
                "success": False,
                "message": "An error occurred while processing your query",
                "suggestions": self._get_starter_suggestions()
            }
    
    def _build_full_context(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, Any]]],
        subscription_ids: Optional[List[str]],
        resource_group: Optional[str],
        resource_id: Optional[str]
    ) -> str:
        """Build rich context string for AI"""
        context_parts = []
        
        # Add scope information
        if subscription_ids:
            # For global scope (many subscriptions), don't include count here
            # The actual count will come from Management Group query results
            if len(subscription_ids) > 10:
                context_parts.append(f"UI Filter - Global Scope: Querying across all user-accessible subscriptions")
            else:
                context_parts.append(f"UI Filter - Subscriptions: {', '.join(subscription_ids)}")
        if resource_group:
            context_parts.append(f"UI Filter - Resource Group: {resource_group}")
        if resource_id:
            # Lowercase resource_id for Activity Log queries (_ResourceId is always lowercase in LA)
            resource_id_lower = resource_id.lower()
            context_parts.append(f"UI Filter - Resource: {resource_id_lower}")
            
            # Extract resource type from resource ID
            # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/{type}/{name}
            if "/providers/" in resource_id:
                parts = resource_id.split("/providers/")
                if len(parts) > 1:
                    type_parts = parts[1].split("/")
                    if len(type_parts) > 1:
                        resource_type = type_parts[0] + "/" + type_parts[1]
                        context_parts.append(f"UI Filter - Resource Type: {resource_type}")
        
        # NOTE: Conversation history is passed as actual message objects to OpenAI,
        # not as text in this context string. See _ai_understand_and_execute()
        
        context_parts.append(f"\n=== Current Query ===")
        
        # Limit user query to prevent token waste for very large inputs
        truncated_query = user_query[:20000]
        if len(user_query) > 20000:
            truncated_query += "... (query truncated)"
            logger.warning(f" User query truncated from {len(user_query)} to 20000 chars")
        
        context_parts.append(f"User: {truncated_query}")
        
        final_context = "\n".join(context_parts)
        
        # Final safety check: ensure total context doesn't exceed reasonable limit
        if len(final_context) > 25000:
            logger.warning(f" Context length {len(final_context)} exceeds 25000 chars, truncating")
            final_context = final_context[:25000] + "\n... (context truncated)"
        
        return final_context
    
    async def _ai_understand_and_execute(
        self,
        context: str,
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Let AI understand the full context and decide what to do
        This is how ChatGPT works - full context understanding, not rigid classification
        """
        if not self.openai_client:
            return {
                "success": False,
                "message": "AI service not available",
                "suggestions": self._get_starter_suggestions()
            }
        
        system_prompt = """You are CIRRUS, an AI assistant for Azure infrastructure. Read-only — never create, modify, or delete resources. Use the user's own Azure credentials and RBAC permissions.

AVAILABLE ACTIONS:
1. resource_graph - Query Azure Resource Graph (VMs, storage, databases, any resource)
2. cost_analysis - Query Azure Cost Management
3. cost_comparison - Compare costs between two time periods
4. log_analytics - Query Azure Log Analytics
5. azure_metrics - Query Azure Monitor Metrics (CPU, memory, network for specific resources)
6. resource_health - Query Azure Resource Health (availability status of resources)
7. policy_compliance - Query Azure Policy compliance via Resource Graph (PolicyResources table)
8. security_score - Query Microsoft Defender for Cloud security score via Resource Graph (SecurityResources table)
9. general_response - Answer questions, provide help, or generate code (Terraform, GitHub Actions, etc.)
10. agentic_analysis - Autonomous multi-step investigation where you drive the entire process: decide what to query across ALL available Azure data sources (resources, logs, metrics, health, cost, advisor, policy compliance, change history, network topology, diagnostic settings), execute queries iteratively, reason over intermediate results, and deliver a comprehensive data-backed answer. PREFER agentic for: any question about understanding, analyzing, assessing, investigating, reviewing, or auditing Azure resources; any question whose full answer benefits from combining signals from multiple sources; any open-ended or broad question; any question about "what's happening", "why", "what changed", "is this healthy", "review my X". Use single-turn (resource_graph, log_analytics, etc.) ONLY for precise, narrow lookups where a single query is clearly sufficient — e.g. "list VMs with public IPs", "show NSG rules for port 22", "do I have a rule for IP X". When in doubt between agentic and single-turn, choose agentic — it can answer simple questions in one tool call and will produce a richer answer. Examples that use agentic: "analyze my firewall rules", "investigate my environment", "review my AKS cluster", "what changed before the outage", "understand my network topology", "what's the health of my resources", "check my security posture". Examples that use single-turn: "list all VMs", "show storage accounts", "show CPU for last hour", "do I have a VM named X".

⚠️ CRITICAL SUGGESTION RULES:
When generating "suggestions" arrays, NEVER use these restricted words:
- DESTRUCTIVE: delete, remove, destroy, drop, truncate, purge, erase, wipe
- MODIFICATION: modify, alter, patch, reboot, shutdown, deallocate, terminate, failover, switchover
- CREATION: create, deploy, provision

Instead use SAFE words: show, list, view, display, find, search, analyze, explore, examine, review, check, get, fetch, query, filter, sort, group, compare

 SPECIAL: ON-PREM FIREWALL QUERIES
- Keywords: "on-prem firewall", "on-premise firewall", "Cisco firewall", "Checkpoint firewall"
- If user mentions on-prem/Cisco/Checkpoint firewalls, use action: "log_analytics" (NOT general_response)
- Queries will be automatically routed to the correct workspace
- DO NOT confuse with "Azure Firewall" (which is a different Microsoft cloud resource)

COMPARISON QUERIES:
- Detect when user wants to compare two time periods
- Keywords: "compare", "difference", "between", "vs", "versus", "last month and this month"
- Use action: "cost_comparison"
- Set comparison_periods: ["period1", "period2"]
- ⚠️ CRITICAL: For follow-up comparisons, PRESERVE the scope from UI filters (resource_id, resource_group)

CONTEXT RULES:
- UI filters are ALWAYS the active scope — apply them even when not mentioned in the message
- If UI filter shows a resource group, scope ALL queries to that RG
- If UI filter shows a specific resource, ALL queries and comparisons must preserve that resource
- Use conversation history for references like "these", "those", "them"
- Look for `💡 [I suggested: ...]` markers in previous messages to understand what options were offered
- For subscription scope: use ["<from context>"] placeholder — never list actual IDs
- Global Scope: use ["<from context>"] to represent all subscriptions

OUTPUT FORMAT (JSON):
{
    "action": "resource_graph|cost_analysis|cost_comparison|log_analytics|azure_metrics|resource_health|policy_compliance|security_score|general_response|agentic_analysis",
    "reasoning": "Why you chose this action based on context",
    "query": "KQL query to execute" (for resource_graph, log_analytics),
    "metrics": {
        "resource_id": "full Azure resource ID" (required for single resource),
        "resource_type": "e.g., microsoft.compute/virtualmachines" (for multi-resource),
        "metric_names": ["Percentage CPU", "Network In"],
        "aggregation": "Average|Maximum|Minimum",
        "time_range": "PT1H|PT6H|PT24H|P1D|P7D|P30D" (ISO 8601 duration - extract from user query!),
        "multi_resource": true|false
    } (for azure_metrics only),
    "cost_grouping": "service|service_family|resource_group|resource|resource_type|location|subscription" (for cost_analysis - what to group by.
      - 'service' = Azure service name (e.g. 'Azure Kubernetes Service', 'Log Analytics') — DEFAULT for any scope
      - 'service_family' = coarse category (Compute, Storage, Networking) — use when user asks for high-level overview
      - 'resource_group' = break down by resource group
      - 'resource' = break down by individual resource ID
      - 'resource_type' = break down by resource type (e.g. microsoft.compute/virtualmachines)
      - 'location' = break down by Azure region
      - 'subscription' = break down by subscription — useful in global scope to see which sub costs most
      All options work in global scope via billing account API),
    "include_reservations": true|false (for cost_analysis - true only if user explicitly asks for amortized cost, reservations, savings plans, or RI breakdown. Default false),
    "cost_filters": {
        "resource_type": "microsoft.compute/virtualmachines" (OPTIONAL - "VMs", "virtual machines"),
        "service_name": "Virtual Machines" (OPTIONAL - "compute service", "storage service"),
        "location": "westeurope" (OPTIONAL - "west europe", "north europe", "sweden central"),
        "resource_name_pattern": "prod" (OPTIONAL - "containing prod", "with test", "name includes"),
        "top_n": number (OPTIONAL - "top 5", "first 10", "show 3"),
        "bottom_n": number (OPTIONAL - "bottom 5", "cheapest 10", "lowest 3"),
        "min_cost": number (OPTIONAL - "over 1000", "more than 500", "above 100"),
        "max_cost": number (OPTIONAL - "under 100", "less than 500", "below 1000"),
        "sort_order": "highest|lowest|name_asc|name_desc" (OPTIONAL - default: highest for top_n, lowest for bottom_n)
    } (OPTIONAL - AI extracts ALL constraints from natural language like KQL generation. Can combine multiple filters!),
    "comparison_periods": ["period1", "period2"] (for cost_comparison only - e.g., ["last_month", "this_month"]),
    "scope": {
        "subscriptions": ["list of subscription IDs"],
        "resource_group": "resource group name or null",
        "resource_id": "full Azure resource ID or null" (if specific resource selected in UI),
        "workspace_id": "log analytics workspace ID or null" (for log_analytics),
        "time_range": "this_month|last_month|last_7_days|last_30_days|last_90_days|today|yesterday|this_year|last_N_days" (for cost_analysis - use strings, NOT date objects! For arbitrary days use format "last_N_days" where N is the number)
    },
    "message": "Natural language explanation for the user",
    "suggestions": ["3-4 contextual next steps based on what user is looking at"],
    "rca_trigger": true|false,
    "rca_hours": number (1-24, default 24),
    "rca_start": "ISO 8601 UTC string or null",
    "rca_end": "ISO 8601 UTC string or null"
}

rca_trigger: Set true when the query describes or implies an incident, degradation, or failure
that would benefit from correlating changes, logs, metrics, and health signals to find a root cause.
Time window extraction rules (max 24 hours between start and end):
- Relative time ("last 2 hours", "past 30 minutes"): set rca_hours=N, leave rca_start/rca_end null.
- Absolute range ("between 3 and 4 April", "03:00 CET to 08:00 CET on 3 April"):
  set rca_start and rca_end as UTC ISO strings, leave rca_hours null.
  Convert named timezones correctly: CET=UTC+1, CEST=UTC+2. Use current year if not specified.
  If the range exceeds 24 hours, truncate rca_end to rca_start + 24 hours.
- No time hint: set rca_hours=24, leave rca_start/rca_end null.
When rca_trigger=true, still set action and message normally — the frontend uses both.

EXAMPLES:

Context: UI Filter = rg-prod, User asks "show all resources"
Output: {
    "action": "resource_graph",
    "reasoning": "User wants all resources but UI filter is set to rg-prod, so scope to that RG only",
    "query": "Resources | where resourceGroup =~ 'rg-prod' | project name, type, location",
    "scope": {"subscriptions": ["<from context>"], "resource_group": "rg-prod", "time_range": null},
    "message": "Here are all resources in rg-prod:",
    "suggestions": ["Show costs for rg-prod", "Filter by resource type", "Show resource details"]
}

Context: UI Filter = specific VM selected, User asks "show CPU usage" or "CPU metrics"
Output: {
    "action": "azure_metrics",
    "reasoning": "User wants metrics for a specific resource (VM selected in UI filter)",
    "metrics": {
        "resource_id": "/subscriptions/xxx/resourceGroups/rg-prod/providers/Microsoft.Compute/virtualMachines/vm1",
        "metric_names": ["Percentage CPU"],
        "aggregation": "Average",
        "time_range": "PT1H",
        "multi_resource": false
    },
    "scope": {"subscriptions": ["<from context>"], "resource_group": "rg-prod", "time_range": null},
    "message": "Here's the CPU usage for vm1:",
    "suggestions": ["Show memory usage", "Show network traffic", "Show all metrics"]
}

Context: User asks "show CPU for all my VMs"
Output: {
    "action": "azure_metrics",
    "reasoning": "User wants metrics across multiple VMs, use multi-resource query",
    "metrics": {
        "resource_type": "microsoft.compute/virtualmachines",
        "metric_names": ["Percentage CPU"],
        "aggregation": "Average",
        "time_range": "PT1H",
        "multi_resource": true
    },
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "time_range": null},
    "message": "Here's the CPU usage across all your VMs:",
    "suggestions": ["Show memory for all VMs", "Filter by high CPU", "Show specific VM details"]
}

Context: User asks "break down costs by resource group" or "show costs per resource group"
Output: {
    "action": "cost_analysis",
    "reasoning": "User wants cost breakdown grouped by resource group",
    "cost_grouping": "resource_group",
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "time_range": "last_30_days"},
    "message": "Here's your cost breakdown by resource group:",
    "suggestions": ["Show costs by service", "Filter by specific resource group", "Show daily trend"]
}

Context: User asks "show me top 5 VMs" or "5 most expensive virtual machines"
Output: {
    "action": "cost_analysis",
    "reasoning": "User wants top 5 VMs by cost",
    "cost_grouping": "resource",
    "cost_filters": {
        "resource_type": "microsoft.compute/virtualmachines",
        "top_n": 5
    },
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "time_range": "this_month"},
    "message": "Here are your top 5 most expensive Virtual Machines:",
    "suggestions": ["Show all VMs", "Show top 10", "Show by resource group"]
}

Context: User asks "resources with prod in name costing over 500 kr"
Output: {
    "action": "cost_analysis",
    "reasoning": "User wants resources with 'prod' in name above cost threshold",
    "cost_grouping": "resource",
    "cost_filters": {
        "resource_name_pattern": "prod",
        "min_cost": 500
    },
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "time_range": "this_month"},
    "message": "Here are production resources costing over 500 kr:",
    "suggestions": ["Show all prod resources", "Show test resources", "Filter by service type", "Save this query as a template"]
}

Context: User asks "show me cost difference between last month and this month" or "compare last month vs this month"
Output: {
    "action": "cost_comparison",
    "reasoning": "User wants to compare costs between two time periods",
    "comparison_periods": ["last_month", "this_month"],
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "time_range": null},
    "message": "Let me compare costs between last month and this month:",
    "suggestions": ["Show detailed breakdown", "Which services increased?", "View daily trend"]
}

Context: UI Filter = specific resource (my-hub-vgw). Previous - showed cost for last_month. Current - "compare with previous month"
Output: {
    "action": "cost_comparison",
    "reasoning": "User wants to compare the specific resource's cost between two periods. MUST preserve resource context from UI filter.",
    "comparison_periods": ["last_month", "this_month"],
    "scope": {"subscriptions": ["<from context>"], "resource_group": "<from context>", "resource_id": "<from context>", "time_range": null},
    "message": "Let me compare costs for this resource between last month and this month:",
    "suggestions": ["Show cost breakdown", "View daily trend", "Analyze cost drivers"]
}

Context: UI Filter = specific resource (storage account). User asks "is this resource healthy" or "what's the health status"
Output: {
    "action": "resource_health",
    "reasoning": "User wants to check Azure Resource Health availability status for the selected resource",
    "scope": {"subscriptions": ["<from context>"], "resource_group": "<from context>", "resource_id": "<from context>"},
    "message": "Let me check the health status of this resource:",
    "suggestions": ["View metrics", "Check logs", "View cost"]
}

Context: User asks "what's my policy compliance" or "show compliance status" or "check policy violations"
Output: {
    "action": "policy_compliance",
    "reasoning": "User wants Azure Policy compliance data via Resource Graph PolicyResources table",
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "resource_id": null},
    "message": "Let me check your Azure Policy compliance status:",
    "suggestions": ["Show non-compliant resources", "View compliance by subscription", "Check security score"]
}

Context: UI Filter = subscription selected, User asks "analyze my resources for best practices" or "review my configuration" or "scan for issues"
Output: {
    "action": "resource_graph",
    "reasoning": "User wants best practices analysis. Query common resource types (VMs, DBs, Storage) with configuration properties to analyze for security, cost, and performance issues.",
    "query": "Resources | where type in~ ('microsoft.compute/virtualmachines', 'microsoft.sql/servers/databases', 'microsoft.storage/storageaccounts', 'microsoft.web/sites', 'microsoft.dbforpostgresql/flexibleservers', 'microsoft.dbformysql/flexibleservers') | project name, type, location, resourceGroup, sku.name, sku.tier, properties.hardwareProfile.vmSize, properties.storageProfile, properties.publicNetworkAccess, properties.httpsOnly, properties.minTlsVersion, tags | limit 100",
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "time_range": null},
    "message": "I'll analyze your resources for best practices across VMs, databases, storage, and web apps. This will check for:\n\n🔒 **Security**: Public access, encryption, HTTPS\n💰 **Cost**: Right-sizing, redundancy levels\n⚡ **Performance**: VM sizes, autoscaling\n🛡️ **Reliability**: Redundancy, backup configs\n\nReviewing resources now...",
    "suggestions": ["Check policy compliance", "Review security score", "Analyze costs", "View resource health"]
}

Context: User asks "what's my security score" or "check defender score" or "show secure score"
Output: {
    "action": "security_score",
    "reasoning": "User wants Microsoft Defender for Cloud security score (Secure Score)",
    "scope": {"subscriptions": ["<from context>"], "resource_group": null, "resource_id": null},
    "message": "Let me fetch your Microsoft Defender for Cloud security score:",
    "suggestions": ["View security recommendations", "Check policy compliance", "Show unhealthy resources"]
}

⚠️ TIME RANGE OPTIONS FOR COST ANALYSIS:
- "today" = Today only (partial day)
- "yesterday" = Yesterday (full day)
- "last_7_days" = Last 7 days
- "last_30_days" = Last 30 days (rolling) - ONLY if user explicitly says "last 30 days"
- "last_90_days" = Last 90 days (rolling)
- "last_N_days" = Last N days (e.g., "last_15_days", "last_45_days") - for ANY custom day count
- "this_month" = Current month from 1st to today (DEFAULT)
- "last_month" = Previous full calendar month
- "this_year" = Current year from Jan 1 to today
- Named months: "january" … "december", or "october 2024"

⚠️ CRITICAL KQL GENERATION RULES FOR RESOURCE_GRAPH ACTION:

- Use `Resources` table (most common). Case-insensitive comparison: `=~` not `==`.
- Avoid JOINs — ARG joins are error-prone and slow.
- Resource group filter: `| where resourceGroup =~ 'rg-name'`
- Common types: `microsoft.compute/virtualmachines`, `microsoft.storage/storageaccounts`, `microsoft.sql/servers`, `microsoft.web/sites`, `microsoft.keyvault/vaults`, `microsoft.network/firewallpolicies/rulecollectiongroups`

⚠️ CRITICAL KQL GENERATION RULES FOR LOG_ANALYTICS ACTION:

- AzureDiagnostics: filter by `Resource` field in **UPPERCASE** (e.g. `Resource == "MY-RESOURCE"`). Extract from last segment of resource_id, uppercase it. Do NOT use `ResourceId` — unreliable.
- AzureActivity: use `_ResourceId` (with underscore) in lowercase.
- Always end queries with `| take 1000` to prevent timeouts.
- IP/CIDR list matching: use `let iocs = dynamic([...])` + `mv-expand` + `ipv4_is_in_range(column, tostring(ioc))`. Never use `in` or `has` for CIDR matching.

EXAMPLE:
Context: UI Filter shows "/subscriptions/.../microsoft.cdn/profiles/my-frontdoor", User asks "show logs from last month"
Output: {
    "action": "log_analytics",
    "reasoning": "User wants diagnostic logs for CDN profile",
    "query": "AzureDiagnostics | where TimeGenerated > ago(30d) | where Resource == \\"MY-FRONTDOOR\\" | where Category == \\"FrontDoorAccessLog\\" | take 1000",
    "scope": {"subscriptions": ["<from context>"], "resource_id": "/subscriptions/.../my-frontdoor", "workspace_id": null},
    "message": "Here are the logs for my-frontdoor from the last month:",
    "suggestions": ["Filter by errors only", "View WAF logs", "Show last 24 hours"]
}
"""
        
        try:
            # Build messages array with proper conversation history
            messages = [{"role": "system", "content": system_prompt}]
            
            # Add actual conversation history as proper message objects
            # Use last 12 messages for richer context (ChatGPT-style)
            conversation_history = user_data.get('conversation_history', []) if user_data else []
            if conversation_history:
                logger.info(f" Processing {len(conversation_history)} messages for AI context")
                history_window = conversation_history[-12:]
                recent_cutoff = len(history_window) - 4  # last 4 messages get full treatment

                for idx, msg in enumerate(history_window):
                    role = msg.get("role", "user")
                    content = msg.get("content", "")

                    if not content or not content.strip():
                        continue
                    if role not in ["user", "assistant"]:
                        role = "user"

                    is_recent = idx >= recent_cutoff

                    if is_recent:
                        # Recent messages: keep generous content + suggestions + data digest
                        max_content_len = 1500 if role == "assistant" else 1000
                        suggestions = msg.get("suggestions")
                        if suggestions:
                            suggestions_text = "\n\n[I suggested: " + " | ".join(suggestions) + "]"
                            full_content = content[:(max_content_len - len(suggestions_text))] + suggestions_text
                        else:
                            full_content = content[:max_content_len]
                        if role == "assistant":
                            data_digest = self._build_data_digest(msg.get("query_result"))
                            if data_digest:
                                full_content = full_content[:2200 - len(data_digest)] + data_digest
                    else:
                        # Older messages: compress to first sentence / key intent only — no data digest
                        max_content_len = 200 if role == "assistant" else 150
                        full_content = content[:max_content_len]
                        if len(content) > max_content_len:
                            full_content = full_content.rstrip() + "…"

                    messages.append({"role": role, "content": full_content})

                logger.info(f" Including {len(messages)-1} conversation messages for context")
            
            # Stash processed conversation for the agentic loop (last 6 turns is enough context)
            self._current_conversation_context = messages[1:]  # strip system prompt

            # Add current context (filters + current query) as the latest user message
            messages.append({"role": "user", "content": context})

            # Scale output budget linearly with input size.
            # Minimum 1000 tokens covers simple queries. Large inputs (IP/CIDR lists, long
            # conversation histories) need proportionally more room so the JSON isn't truncated.
            _query_len = len(self._current_user_query)
            classification_token_budget = max(1000, min(_query_len // 3, 8000))

            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=messages,
                max_completion_tokens=classification_token_budget,
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            logger.info(f"AI classification finish_reason={finish_reason} content_len={len(content) if content else 0} token_budget={classification_token_budget}")
            if finish_reason == "length":
                logger.error(f"AI classification truncated at {classification_token_budget} tokens — JSON will be invalid. Raw tail: {content[-200:] if content else 'empty'}")
            if not content or not content.strip():
                logger.warning("AI classification returned empty content, treating as general request (code gen)")
                return await self._handle_general_request(self._current_user_query, user_data)
            
            try:
                ai_decision = json.loads(content)
                logger.info(f"AI Decision: {ai_decision.get('action')} - {ai_decision.get('reasoning')}")
                logger.info(
                    f"RCA trigger: {ai_decision.get('rca_trigger')} "
                    f"rca_hours={ai_decision.get('rca_hours')} "
                    f"rca_start={ai_decision.get('rca_start')} "
                    f"rca_end={ai_decision.get('rca_end')}"
                )

                # When rca_trigger is set, skip the agentic/data pipeline entirely.
                # The frontend will open the dedicated RCA panel and run the full
                # root_cause_analysis_service pipeline there.
                if ai_decision.get("rca_trigger"):
                    rca_start = ai_decision.get("rca_start")
                    rca_end = ai_decision.get("rca_end")
                    rca_hours = ai_decision.get("rca_hours")
                    # Use the AI's contextual message but normalize bullet chars to markdown
                    msg = (ai_decision.get("message") or "Running root cause analysis.")
                    msg = msg.replace("\n•", "\n-").replace(" •", "\n-")
                    return {
                        "success": True,
                        "message": msg,
                        "query_type": "rca_handoff",
                        "rca_trigger": True,
                        "rca_hours": rca_hours,
                        "rca_start": rca_start,
                        "rca_end": rca_end,
                    }

                result = await self._execute_ai_decision(ai_decision, user_data)

                # Escalate to agentic only when a single-turn data query returned 0 results.
                # Analytical routing is handled entirely by the AI router — no keyword matching.
                action = ai_decision.get("action", "")
                data = result.get("data") or {}
                if (
                    action in ("resource_graph", "log_analytics")
                    and result.get("success")
                    and self._is_empty_result(action, data)
                ):
                    logger.info(f"Single-turn {action} returned 0 results — escalating to agentic loop")
                    return await self._execute_agentic_analysis(ai_decision, user_data)

                return result
            except json.JSONDecodeError:
                logger.warning(f"AI returned non-JSON content (len={len(content)}), raw={content[:500]}")
                return await self._handle_general_request(self._current_user_query, user_data)
            
        except Exception as e:
            logger.error(f"AI understanding failed: {e}", exc_info=True)
            return await self._handle_general_request(self._current_user_query, user_data)
    
    async def _execute_ai_decision(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the action decided by AI"""
        
        action = ai_decision.get("action")
        _suggestions = ai_decision.get("suggestions") or []
        
        # Actions that require Azure filters (subscription/resource selection)
        actions_needing_filters = [
            "resource_graph",
            "cost_analysis",
            "cost_comparison",
            "log_analytics",
            "azure_metrics",
            "resource_health",
            "policy_compliance",
            "security_score",
            "agentic_analysis",
        ]
        
        # Check if filters are required but missing
        has_filters = bool(self._current_subscription_ids and len(self._current_subscription_ids) > 0)
        
        # Check if global scope is enabled from context
        context = user_data.get("context", {}) if user_data else {}
        global_scope_enabled = context.get("globalScope", False)
        
        # If action needs filters but none present
        if action in actions_needing_filters and not has_filters:
            # If Global Scope is enabled, try to fetch subscriptions silently
            if global_scope_enabled:
                logger.info(f" Global Scope enabled but no subscriptions provided - fetching silently")
                try:
                    from .resource_service import ResourceService
                    resource_service = ResourceService()
                    # resource_service expects 'token' key; user_data uses 'management_token'
                    user_info = {**user_data, "token": user_data.get("management_token") or user_data.get("token")}
                    result = await resource_service.get_user_accessible_subscriptions(user_info, page=1, page_size=1000)
                    if result and result.get("subscriptions"):
                        sub_list = result.get("subscriptions", [])
                        self._current_subscription_ids = [sub["subscription_id"] for sub in sub_list]
                        logger.info(f" Fetched {len(self._current_subscription_ids)} subscriptions for global scope")
                        has_filters = True
                    else:
                        logger.warning(f" No subscriptions returned from resource service")
                except Exception as e:
                    logger.error(f" Error fetching subscriptions: {e}")
            
            # If still no filters after attempting fetch
            if not has_filters:
                if not global_scope_enabled:
                    # User needs to select filters or enable global scope
                    logger.info(f" Action '{action}' requires filters but none selected")
                    return {
                        "success": False,
                        "query_type": "needs_filter",
                        "message": "Please select a subscription from the filter panel to query your Azure resources, or enable Global Scope to search across all subscriptions.",
                        "suggestions": [
                            "Select a subscription from the filter panel above",
                            "Enable Global Scope to query all subscriptions",
                            "Ask general questions like 'What can you do?' or 'How to optimize Azure costs?'",
                            "Try 'Review my resources for best practices' or 'Perform Well-Architected review'"
                        ]
                    }
                else:
                    # Global scope enabled but fetch failed
                    logger.warning(f" No subscriptions available even after fetch attempt")
                    return {
                        "success": False,
                        "query_type": "needs_filter",
                        "message": "No accessible subscriptions found. Please check your Azure permissions or try refreshing the page.",
                        "suggestions": [
                            "Refresh the page to reload your subscriptions",
                            "Verify your Azure role assignments and permissions",
                            "Contact your Azure administrator if you should have access to subscriptions"
                        ]
                    }
        
        # SECURITY Layer 1: Validate AI decision for safe actions only
        is_safe, reason = query_validator.validate_ai_decision(ai_decision)
        if not is_safe:
            logger.warning(f" AI decision rejected: {ai_decision.get('action')} - Reason: {reason}")
            return {
                "success": False,
                "message": f"⚠️ {reason}",
                "error": "CIRRUS supports only read-only operations",
                "suggestions": [
                    "Show me resources",
                    "Analyze costs for last month", 
                    "List all virtual machines"
                ]
            }
        
        # SECURITY Layer 2: Keyword validation for Azure operations (skip for general_response)
        action = ai_decision.get('action')
        if action != 'general_response':
            # Apply keyword validation for Azure operations
            user_query = getattr(self, '_current_user_query', '')
            is_query_safe, query_reason = query_validator.validate_natural_language_query(user_query, ai_intent=action)
            if not is_query_safe:
                logger.warning(f" Query keywords rejected for Azure operation: {query_reason}")
                safe_suggestion = query_validator.get_safe_alternative_suggestion(user_query)
                return {
                    "success": False,
                    "message": f"⚠️ {query_reason}",
                    "error": "CIRRUS is a read-only portal. Resource modifications and creations are not supported.",
                    "suggestions": [
                        safe_suggestion,
                        "CIRRUS can only query and analyze Azure resources",
                        "Use Azure Portal or Azure CLI for resource modifications"
                    ]
                }
        else:
            logger.info(f" Skipping keyword validation for general_response action")
        
        if action == "general_response":
            # Always use _generate_code_response for the final answer.
            # The classification call produces a routing JSON whose `message` field is an
            # intro-style CIRRUS overview (by design of its system prompt) — not a contextual
            # answer to the user's actual question.  _generate_code_response has a focused
            # prompt that produces the right response for any general query.
            user_query = getattr(self, '_current_user_query', '')
            conversation_history = user_data.get('conversation_history', []) if user_data else []
            return await self._generate_code_response(user_query, ai_decision, conversation_history)
        
        elif action == "resource_graph":
            # Validate KQL query
            kql_query = ai_decision.get("query", "")
            is_kql_safe, kql_reason = query_validator.validate_kql_query(kql_query)
            if not is_kql_safe:
                logger.warning(f" KQL query rejected: {kql_reason}")
                return {
                    "success": False,
                    "message": f"⚠️ {kql_reason}",
                    "error": "Generated query contains potentially unsafe operations"
                }
            return await self._execute_resource_graph_from_ai(ai_decision, user_data)
        
        elif action == "cost_analysis":
            return await self._execute_cost_analysis_from_ai(ai_decision, user_data)
        
        elif action == "cost_comparison":
            return await self._execute_cost_comparison_from_ai(ai_decision, user_data)
        
        elif action == "log_analytics":
            # Validate KQL query for Log Analytics
            kql_query = ai_decision.get("query", "")
            is_kql_safe, kql_reason = query_validator.validate_kql_query(kql_query)
            if not is_kql_safe:
                logger.warning(f" Log Analytics KQL rejected: {kql_reason}")
                return {
                    "success": False,
                    "message": f"⚠️ {kql_reason}",
                    "error": "Generated log query contains potentially unsafe operations"
                }
            return await self._execute_log_analytics_from_ai(ai_decision, user_data)
        
        elif action == "azure_metrics":
            return await self._execute_metrics_from_ai(ai_decision, user_data)
        
        elif action == "resource_health":
            return await self._execute_health_from_ai(ai_decision, user_data)
        
        elif action == "policy_compliance":
            logger.info(" Routing policy compliance query to dedicated governance service")
            return await self._execute_policy_compliance_from_ai(ai_decision, user_data)
        
        elif action == "security_score":
            logger.info(" Routing security score query to dedicated governance service")
            return await self._execute_security_score_from_ai(ai_decision, user_data)

        elif action == "agentic_analysis":
            return await self._execute_agentic_analysis(ai_decision, user_data)

        else:
            return {
                "success": False,
                "message": "I'm not sure how to help with that.",
                "suggestions": (ai_decision.get("suggestions") or self._get_starter_suggestions())
            }

    async def _execute_agentic_analysis(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Agentic read-only analysis loop.

        AI is given a single `query` tool that executes KQL against Azure Resource Graph
        or Log Analytics. AI calls it as many times as needed, reasons over the results
        directly, and writes the final answer itself. No primitives, no planning phases,
        no post-processing — AI drives everything.

        The loop is capped at 15 tool calls to prevent runaway cost.
        All generated KQL is validated as read-only before execution.
        """
        from .query_service import QueryService

        # Use the original user query as the goal so the AI has exact names/identifiers.
        # Fall back to the reasoning summary only if no original query is available.
        original_query = user_data.get("query") or getattr(self, "_current_user_query", "")
        goal = original_query or ai_decision.get("reasoning", ai_decision.get("message", ""))
        subscription_ids = getattr(self, "_current_subscription_ids", [])

        # Prepend recent conversation for context.
        # Include assistant messages only when they contain actual data (markdown tables / resource IDs)
        # so the AI can build on prior findings. Exclude conclusion-only messages ("no results found")
        # which would anchor the AI and stop it reasoning fresh.
        def _has_data(msg: dict) -> bool:
            content = msg.get("content") or ""
            return "|---|" in content or "/subscriptions/" in content or "/providers/" in content

        prior_context: list[dict] = [
            m for m in getattr(self, "_current_conversation_context", [])[-6:]
            if m.get("role") == "user" or (m.get("role") == "assistant" and _has_data(m))
        ]
        resource_id = getattr(self, "_current_resource_id", None)
        resource_group = getattr(self, "_current_resource_group", None)

        # Scope context injected into every system message so AI always knows its boundaries
        scope_note = ""
        if resource_id:
            scope_note += f"\nActive UI filter — Resource: {resource_id}"
        if resource_group:
            scope_note += f"\nActive UI filter — Resource Group: {resource_group}"
        if subscription_ids:
            if len(subscription_ids) > 10:
                # Global scope — do NOT list individual IDs, AI would treat them as exclusive filter
                scope_note += f"\nActive UI filter — Scope: Global (all accessible subscriptions, do NOT add subscriptionId filters to KQL)"
            else:
                scope_note += f"\nActive UI filter — Subscription(s): {', '.join(subscription_ids)}"

        # Always append active scope to the goal so the AI knows exactly what to query,
        # even when the user did not mention it in their message.
        if scope_note.strip():
            goal = f"{goal.strip()}\n\nActive scope:{scope_note}"

        system_message = f"""You are CIRRUS, a read-only Azure infrastructure analyst.
You have one tool: query(kql, type, ...).

Tool types and when to use them:
- type="resource_graph"        — ARM configuration, inventory, topology (ARG tables: resources, networkresources, etc.)
- type="log_analytics"         — Logs, diagnostics, access patterns. Always include `| where TimeGenerated > ago(Xh)` in the KQL.
- type="metrics"               — Live/historic performance numbers (CPU, memory, latency, RPS, error rate). Pass resource_id, metric_names, time_range (ISO 8601: PT1H, PT6H, PT24H, P7D), aggregation.
- type="change_analysis"       — Configuration changes on Azure resources (who changed what and when). Pass hours (int) for look-back window, optionally resource_id to scope to one resource.
- type="resource_health"       — Azure platform health status of a resource (Available / Degraded / Unavailable). Pass resource_id.
- type="security_score"        — Microsoft Defender for Cloud Secure Score and recommendations across subscriptions. No extra params needed.
- type="advisor"               — Azure Advisor recommendations (cost, reliability, security, performance, operational excellence). Use resource_graph KQL against the `advisorresources` table. Pass kql with filters like `| where properties.category =~ 'Cost'` or `| where id has 'my-resource-name'`.
- type="cost"                  — Azure cost analysis (actual spend by service/resource group/subscription). Pass time_range (e.g. BillingMonth, Last30Days, Custom) and optional granularity.
- type="policy_compliance"     — Azure Policy compliance state across subscriptions. Shows compliant vs non-compliant resources. No extra params needed.
- type="diagnostic_settings"   — Discover where a resource's logs and metrics flow (Log Analytics workspaces, Event Hubs, Storage). Pass resource_id. Useful to understand observability coverage before querying logs.
- type="network_topology"      — Network resource inventory via KQL against the `networkresources` ARG table. Always provide a kql query scoped to the resource types you need (VNets, NSGs, peerings, route tables, public IPs, load balancers, private endpoints, etc.). Use when analyzing network configuration, connectivity, or security posture.

AUTONOMOUS INVESTIGATION STRATEGY — apply when the goal is analytical (review, analyze, investigate, understand, assess, audit):
1. Start with resource_graph to build inventory — understand what resources exist and their configuration.
2. If resources are identified: check resource_health for availability, advisor for recommendations, change_analysis for recent changes.
3. For performance-sensitive resources (VMs, AKS, App Services, DBs): pull metrics.
4. For security review: call security_score and policy_compliance.
5. Check diagnostic_settings to understand logging coverage gaps.
6. If logs are relevant and workspace is available: query log_analytics.
7. For cost questions: call cost.
8. Synthesize ALL signals into one coherent answer — do not present each signal separately. Cross-reference findings (e.g. "resource X is Degraded in health AND has high CPU AND has 3 advisor recommendations").

Use KQL fully for resource_graph and log_analytics: mv-expand, summarize, join, extend, make_set, count(), order by.
For IP/CIDR matching in resource_graph: use `ipv4_is_in_range(literal_ip, cidr_column)` to check if a specific IP falls within CIDR ranges stored in a column (e.g. `| where ipv4_is_in_range("10.64.136.252", tostring(src))`). This is evaluated server-side by ARG and is far more accurate than fetching all rows and reasoning over them. Always cast dynamic columns with tostring() before passing to ipv4_is_in_range.
For multiple IPs/CIDRs to match against in log_analytics: use `let iocs = dynamic([...])` + `mv-expand` + `ipv4_is_in_range(column, tostring(ioc))`. `let` works in Log Analytics. Never use `in` for CIDR matching.
Read-only only. Never generate INSERT, UPDATE, DELETE, DROP, set, exec, invoke.
If a query returns 0 rows: your NEXT attempt MUST broaden the filter — replace exact values (IPs, IDs, names) with prefix/substring matches (e.g. `src has '10.64'` instead of `src =~ '10.64.136.252'`). If the prefix query also returns 0, then try a fully unfiltered query for just that resource type. Only conclude absence after a broad query returns 0. Other things to check: nested arrays requiring mv-expand, wrong table name, parent resource to query instead, IP Groups as indirect references.
Call query as many times as needed. Stop when you have enough for a specific, data-backed answer.
Format answers as proper markdown tables (header + | --- | separator + each row on its own line) and tight bullet lists — data first, explanation only when necessary. No filler. No backticks. Return all findings, not a sample. Never use "same as above" in table rows — repeat the actual value. Never end with trailing offer sentences like "I can also produce...". Every finding must be directly backed by data returned from your queries — do not infer facts not present in the results.
Max 15 tool calls. For comprehensive analysis goals, use all relevant tool types proactively — do not wait for the user to ask for each signal.
IMPORTANT: Always include `id` in resource_graph projections — required for automatic Log Analytics workspace discovery.

SCOPE RULES (non-negotiable):
- Apply ONLY the filters shown in "Active UI filter" above. Never add filters that aren't listed there.
- If scope is "Global": do NOT add any subscriptionId, resourceGroup, or id filter — query without scope restriction and let Azure RBAC handle access.
- If scope is a specific Resource Group: add `| where resourceGroup =~ '<rg>'` to resource_graph KQL.
- If scope is a specific Resource: add `| where id =~ '<resource_id>'` to resource_graph KQL.
- For log_analytics: add the resource/RG filter only if one is explicitly set in scope.
- NEVER query outside the active scope, even for context.
- If you receive a broad or vague goal (e.g. "analyze", "investigate", "check"), treat it as "give me a thorough analysis of everything within the active scope". Start immediately with a resource inventory query — do NOT ask for clarification or explain what agentic analysis is.
- Never stop mid-investigation to offer the next step — execute all relevant follow-up queries yourself within the tool call budget. If intermediate results reveal referenced resources (e.g. IP groups, policy IDs, workspace IDs), immediately use those to continue the investigation rather than presenting them as a stopping point.
{scope_note}"""

        # Tool definition
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "query",
                    "description": "Execute a read-only query against Azure data sources and return results.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kql": {
                                "type": "string",
                                "description": "KQL query string. Required for resource_graph and log_analytics. Leave empty for other types."
                            },
                            "type": {
                                "type": "string",
                                "enum": ["resource_graph", "log_analytics", "metrics", "change_analysis", "resource_health", "security_score", "advisor", "cost", "policy_compliance", "diagnostic_settings", "network_topology"],
                                "description": "Data source to query."
                            },
                            "resource_id": {
                                "type": "string",
                                "description": "Full ARM resource ID. Required for metrics and resource_health; optional filter for change_analysis."
                            },
                            "metric_names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Metric names to fetch (e.g. ['Percentage CPU', 'Network In Total']). Only for type=metrics."
                            },
                            "aggregation": {
                                "type": "string",
                                "description": "Aggregation type for metrics: Average, Maximum, Minimum, Total. Only for type=metrics."
                            },
                            "hours": {
                                "type": "integer",
                                "description": "Look-back window in hours. Only for type=change_analysis."
                            },
                            "time_range": {
                                "type": "string",
                                "description": "Time range for the query. For type=metrics: ISO 8601 duration (e.g. PT1H, PT6H, PT24H, P7D). For type=cost: billing period (e.g. BillingMonth, Last30Days, Last7Days, Custom)."
                            }
                        },
                        "required": ["type"]
                    }
                }
            }
        ]

        query_service = QueryService()

        # Cache resolved workspace so we only discover it once per agentic session
        _resolved_workspace_id: list[str | None] = [None]
        _workspace_resolved: list[bool] = [False]

        async def _resolve_workspace(messages_snapshot: list, kql_query: str = "") -> str | None:
            """Resolve a Log Analytics workspace_id.

            Resolution order:
            1. Filter context resource_id (user selected a specific resource)
            2. Resource id mined from previous ARG tool results in this session
            3. Subscription-level workspace discovery
            """
            if _workspace_resolved[0]:
                return _resolved_workspace_id[0]

            async def _from_resource(rid: str) -> str | None:
                if "/microsoft.operationalinsights/workspaces/" in rid.lower():
                    return rid
                try:
                    wids = await query_service.find_diagnostic_settings_workspaces(rid, user_data)
                    if wids:
                        logger.info(f"Agentic LA: workspace from diagnostic settings ({rid}): {wids[0]}")
                        return wids[0]
                except Exception:
                    logger.warning("Agentic LA: diagnostic settings lookup failed", exc_info=True)
                return None

            # 1. Explicit filter context
            if resource_id:
                ws = await _from_resource(resource_id)
                if ws:
                    _resolved_workspace_id[0] = ws
                    _workspace_resolved[0] = True
                    return ws

            # 2. Mine resource IDs from ARG results already in session
            for msg in messages_snapshot:
                if msg.get("role") != "tool":
                    continue
                try:
                    content = json.loads(msg.get("content", "{}"))
                    for row in content.get("rows", []):
                        rid = row.get("id") or row.get("resourceId") or row.get("resource_id")
                        if rid and isinstance(rid, str) and "/providers/" in rid:
                            ws = await _from_resource(rid)
                            if ws:
                                _resolved_workspace_id[0] = ws
                                _workspace_resolved[0] = True
                                return ws
                except Exception:
                    continue

            # 2b. Mine ARM resource IDs from user/assistant text in the seed (prior conversation context)
            import re as _re
            _arm_pattern = _re.compile(
                r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[^/\s|]+/providers/[^/\s|]+/[^/\s|]+/[^\s|`\"]+",
                _re.IGNORECASE,
            )
            for msg in messages_snapshot:
                if msg.get("role") not in ("user", "assistant"):
                    continue
                text = msg.get("content") or ""
                for match in _arm_pattern.finditer(text):
                    rid = match.group(0).rstrip(".,;)")
                    if "/microsoft.operationalinsights/workspaces/" in rid.lower():
                        _resolved_workspace_id[0] = rid
                        _workspace_resolved[0] = True
                        return rid
                    ws = await _from_resource(rid)
                    if ws:
                        _resolved_workspace_id[0] = ws
                        _workspace_resolved[0] = True
                        return ws

            # 3. Shared workspace resolver (diagnostic settings → ARM listing)
            if subscription_ids:
                resolved = await query_service.resolve_log_analytics_workspace(
                    kql_query, subscription_ids, user_data
                )
                if resolved:
                    ws = resolved["workspace_id"]
                    logger.info(f"Agentic LA: workspace resolved: {ws} (mode={resolved['log_mode']})")
                    _resolved_workspace_id[0] = ws
                    _workspace_resolved[0] = True
                    return ws

            # Do NOT cache None — new ARG tool results may appear before the next LA call
            # that contain the resource `id` needed for workspace discovery.
            return None

        async def _run_query(kql: str, qtype: str, extra: dict | None = None) -> dict:
            """Validate and execute a single query, with ARG table auto-correction."""
            extra = extra or {}

            # Only validate KQL for query-types that actually send KQL
            if qtype in ("resource_graph", "log_analytics") and kql:
                is_safe, reason = query_validator.validate_kql_query(kql)
                if not is_safe:
                    return {"error": f"Query rejected: {reason}"}

            # Auto-correct ARG table name using the same in-memory mapping the single-turn path uses.
            # e.g. AI writes `resources | where type =~ 'microsoft.network/firewallpolicies/...'`
            # but the data lives in `networkresources`. Zero latency — pure dict lookup.
            if qtype == "resource_graph" and kql:
                try:
                    from utils.arg_table_mapper import get_table_for_resource_type
                    type_match = re.search(r"type\s*(?:=~|==|=)\s*['\"]([^'\"]+)['\"]", kql, re.IGNORECASE)
                    if type_match:
                        correct_table = get_table_for_resource_type(type_match.group(1)).lower()
                        current_table = kql.strip().split()[0].lower()
                        if correct_table and correct_table != current_table:
                            kql = re.sub(r"^\s*\w+", correct_table, kql.strip(), count=1, flags=re.IGNORECASE)
                            logger.info(f"Agentic ARG: corrected table {current_table} → {correct_table}")
                except Exception:
                    pass

            try:
                if qtype == "log_analytics":
                    workspace_id = await _resolve_workspace(list(messages), kql)
                    if not workspace_id:
                        return {"error": "No Log Analytics workspace found. Diagnostic settings may not be configured for this resource."}
                    # Route through azure_monitor_service so the resource ID is resolved to
                    # a GUID before hitting the SDK (query_service skips that step → PathNotFoundError)
                    try:
                        from services.azure_monitor_service import AzureMonitorService
                        azure_monitor_service = AzureMonitorService()
                    except Exception:
                        logger.error("Agentic LA: failed to import AzureMonitorService", exc_info=True)
                        return {"error": "Log Analytics service unavailable"}

                    la_token = user_data.get("la_token")
                    mgmt_token = user_data.get("management_token") or user_data.get("token")
                    logger.info(f"Agentic LA: la_token present={bool(la_token)}, mgmt_token present={bool(mgmt_token)}, workspace={workspace_id}")

                    la_result = await azure_monitor_service.execute_kql_query(
                        query=kql,
                        workspace_id=workspace_id,
                        user_info=user_data,
                        timespan=None,  # Let the KQL's own time filter (ago()) control the window
                    )
                    logger.info(f"Agentic LA: execute_kql_query status={la_result.get('status')}, error={la_result.get('error')}")
                    if la_result.get("status") == "success":
                        rows = []
                        for tbl in la_result.get("tables", []):
                            rows.extend(tbl.get("rows", []))
                        return {"rows": rows, "count": len(rows)}
                    else:
                        err = la_result.get("error") or la_result.get("message", "Log Analytics query failed")
                        # On schema error: discover the actual columns of the queried table
                        # so the AI can rewrite the query with correct column names immediately.
                        is_schema_err = "schema error" in err.lower() or "SEM0100" in err or "Failed to resolve" in err
                        if is_schema_err:
                            table_name = kql.strip().split("\n")[0].strip().split("|")[0].strip()
                            if table_name and " " not in table_name:
                                try:
                                    schema_result = await azure_monitor_service.execute_kql_query(
                                        query=f"{table_name} | getschema | project ColumnName, DataType | take 60",
                                        workspace_id=workspace_id,
                                        user_info=user_data,
                                        timespan=None,
                                    )
                                    if schema_result.get("status") == "success":
                                        cols = [
                                            f"{r.get('ColumnName')}:{r.get('DataType', '')}"
                                            for tbl in schema_result.get("tables", [])
                                            for r in tbl.get("rows", [])
                                            if r.get("ColumnName")
                                        ]
                                        if cols:
                                            return {
                                                "error": f"Schema error — column name wrong. Actual columns in {table_name}: {', '.join(cols)}. Rewrite the query using only these column names."
                                            }
                                except Exception:
                                    pass
                        return {"error": err}

                elif qtype == "metrics":
                    resource_id = extra.get("resource_id") or getattr(self, "_current_resource_id", None)
                    if not resource_id:
                        return {"error": "metrics requires a resource_id"}
                    time_range = extra.get("time_range", "PT24H")
                    metric_names = extra.get("metric_names") or ["Percentage CPU"]
                    aggregation = extra.get("aggregation", "Average")
                    interval = self._calculate_metrics_interval(time_range)
                    structured = {
                        "type": "azure_metrics",
                        "multi_resource": False,
                        "resource_id": resource_id,
                        "metric_names": metric_names,
                        "aggregation": aggregation,
                        "time_range": time_range,
                        "interval": interval,
                    }
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    data = result.get("data", result)
                    return {"metrics": data, "success": result.get("success", True)}

                elif qtype == "change_analysis":
                    from services.change_analysis_service import ChangeAnalysisService
                    change_svc = ChangeAnalysisService()
                    hours = int(extra.get("hours") or 24)
                    changes = await change_svc.get_resource_changes(subscription_ids, hours, user_data)
                    all_changes = changes.get("changes", [])
                    # Scope to specific resource if AI provided one
                    rid_filter = extra.get("resource_id")
                    if rid_filter:
                        rid_lower = rid_filter.lower()
                        all_changes = [
                            c for c in all_changes
                            if (c.get("targetResourceId") or "").lower() == rid_lower
                            or (c.get("targetResourceName") or "").lower() in rid_lower
                        ]
                    return {"changes": all_changes, "count": len(all_changes), "summary": changes.get("summary", {})}

                elif qtype == "resource_health":
                    resource_id = extra.get("resource_id") or getattr(self, "_current_resource_id", None)
                    resource_group = getattr(self, "_current_resource_group", None)
                    structured = {"type": "resource_health", "resource_id": resource_id, "resource_group": resource_group}
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    return {"health": result.get("data", result), "success": result.get("success", True)}

                elif qtype == "security_score":
                    structured = {"type": "security_score"}
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    return {"security_score": result.get("data", result), "success": result.get("success", True)}

                elif qtype == "advisor":
                    # Advisor recommendations live in the ARG `advisorresources` table — pure KQL
                    advisor_kql = kql or (
                        "advisorresources "
                        "| where type =~ 'microsoft.advisor/recommendations' "
                        "| extend category=tostring(properties.category), impact=tostring(properties.impact), "
                        "shortDesc=tostring(properties.shortDescription.solution), "
                        "resourceId=tostring(properties.resourceMetadata.resourceId) "
                        "| project id, category, impact, shortDesc, resourceId, resourceGroup, subscriptionId "
                        "| order by impact asc"
                    )
                    structured = {"query": advisor_kql, "type": "resource_graph"}
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    rows = result.get("results", [])
                    return {"rows": rows, "count": len(rows), "success": result.get("success", True)}

                elif qtype == "cost":
                    time_range = extra.get("time_range", "BillingMonth")
                    structured = {
                        "type": "cost_analysis",
                        "time_range": time_range,
                        "granularity": extra.get("granularity", "Monthly"),
                    }
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    return {"cost": result.get("data", result), "success": result.get("success", True)}

                elif qtype == "diagnostic_settings":
                    from services.diagnostic_settings_service import DiagnosticSettingsService
                    ds_svc = DiagnosticSettingsService()
                    rid = extra.get("resource_id") or resource_id
                    if not rid:
                        return {"error": "diagnostic_settings requires a resource_id"}
                    result = await ds_svc.get_log_mode_for_resource(rid, user_data)
                    # Enrich with actual table names so AI can reason which tables to query
                    mode = (result.get("primary") or {}).get("mode", "")
                    if mode.lower() == "dedicated":
                        try:
                            from utils.log_analytics_table_mapper import log_analytics_table_mapper
                            resource_type = result.get("resource_type") or ""
                            table_names = await log_analytics_table_mapper.get_tables_for_resource_type(resource_type)
                            result["dedicated_tables"] = table_names
                        except Exception:
                            pass
                    return {"diagnostic_settings": result, "success": True}

                elif qtype == "network_topology":
                    if not kql:
                        return {"error": "network_topology requires a kql query — describe what network resources you want to inspect"}
                    structured = {"query": kql, "type": "resource_graph", "max_pages": 3}
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    rows = result.get("results", [])
                    return {"rows": rows, "count": len(rows), "success": result.get("success", True)}

                elif qtype == "policy_compliance":
                    structured = {"type": "policy_compliance"}
                    result = await query_service.execute_query(structured, subscription_ids, user_data)
                    return {"policy_compliance": result.get("data", result), "success": result.get("success", True)}

                else:
                    structured = {"query": kql, "type": qtype, "max_pages": 3}
                result = await query_service.execute_query(structured, subscription_ids, user_data)
            except Exception as exc:
                err_msg = str(exc)
                # Strip tenacity wrapper to expose the real error
                if "RetryError" in err_msg and exc.__cause__:
                    err_msg = str(exc.__cause__)
                # Add actionable hints for common ARG KQL errors so the AI can self-correct
                if "Failed to resolve scalar expression named" in err_msg:
                    import re as _re_err
                    col_match = _re_err.search(r"named '(\w+)'", err_msg)
                    col = col_match.group(1) if col_match else "column"
                    err_msg += (
                        f"\nFix: '{col}' is not available after 'summarize' unless listed in the 'by' clause. "
                        f"Either add '{col}' to the 'by' clause (e.g. '| summarize count() by type, {col}') "
                        f"or remove '{col}' from 'project'."
                    )
                elif "ParserFailure" in err_msg:
                    err_msg += (
                        "\nFix: KQL syntax error. Check for unclosed parentheses, invalid operator usage, "
                        "incorrect string quoting, or using a column alias before it is defined. "
                        "Rewrite the query with simpler, valid KQL."
                    )
                logger.error(f"Agentic query execution failed: {err_msg}")
                return {"error": err_msg}
            if not result.get("success"):
                err = result.get("message") or result.get("error") or "Query failed"
                return {"error": err, "results": []}
            rows = result.get("results", [])
            return {"rows": rows, "count": len(rows)}

        # One-shot RAG doc lookup: fetch relevant KQL examples from Azure docs for this goal.
        # This gives the agentic loop the same documentation quality the single-turn RAG path has,
        # without paying the cost per tool call. Runs once, result injected into system message.
        rag_examples = ""
        try:
            if settings.USE_AZURE_SEARCH:
                from services.azure_search_service import get_azure_search_service
                _search_svc = get_azure_search_service()
                _results = await _search_svc.search_resource_graph_examples(goal, top=settings.AI_SEARCH_EXAMPLES_TOP)
                if _results:
                    rag_examples = "\n".join(
                        f"# {r['title']}\n{r['content'][:400]}" for r in _results
                    )
        except Exception:
            pass  # Degraded gracefully — agentic loop still works without docs

        if rag_examples:
            system_message += f"\n\nRelevant Azure documentation for writing KQL in this session:\n{rag_examples}"

        # Seed conversation with prior context (so AI knows what resource was being discussed)
        # followed by the user's current goal.
        messages = [{"role": "system", "content": system_message}]
        messages.extend(prior_context)
        messages.append({"role": "user", "content": goal})

        tool_calls_made = 0
        max_tool_calls = settings.AI_MAX_TOOL_CALLS
        query_service_inst = QueryService()
        final_content = None  # Set via break when AI finishes; None means loop exhausted

        while tool_calls_made < max_tool_calls:
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_completion_tokens=4000,
            )

            choice = response.choices[0]
            tool_calls = choice.message.tool_calls or []
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": choice.message.content}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_msg)

        # No tool calls → AI is done.
            # Run a lightweight reflection pass only for complex analyses (3+ tool calls
            # or large output) where data consistency errors are more likely.
            if not tool_calls:
                draft = choice.message.content or ""
                needs_reflection = tool_calls_made >= 3 or len(draft) > 2000
                if draft and needs_reflection:
                    reflection_prompt = (
                        f"You just produced this analysis:\n\n{draft}\n\n"
                        "Self-check before returning:\n"
                        "1. Is every finding directly backed by query results — no inferred facts not present in the data?\n"
                        "2. For any overlap, duplicate, or conflict detection: did you use ALL dimensions that define uniqueness? "
                        "(e.g. if comparing firewall rules, did you check protocol AND port AND IP range — not just one? "
                        "Two entries that share an IP range but differ by port are NOT duplicates.)\n"
                        "3. Is every technical claim consistent with your knowledge of the Azure resource type?\n"
                        "4. Does the analysis answer what the user actually asked, without drifting into generic observations?\n"
                        "5. Are markdown tables properly formatted — header row, then | --- | separator, then each data row on its own line? "
                        "No 'same as above' in cells. No trailing offer sentences like 'I can also produce...'.\n\n"
                        "If everything checks out, return the analysis unchanged. "
                        "If you spot errors or unsupported claims, return a corrected version. No meta-commentary."
                    )
                    # Cap at draft length + 20% headroom so reflection never truncates the output
                    draft_tokens_estimate = max(len(draft) // 3, 500)
                    reflection_token_cap = min(int(draft_tokens_estimate * 1.2) + 200, 4000)
                    reflection_response = await self.openai_client.chat.completions.create(
                        model=self.settings.OPENAI_DEPLOYMENT_NAME,
                        messages=[
                            messages[0],   # system
                            {"role": "user", "content": goal},  # original user goal (not messages[1] which may be prior context)
                            {"role": "user", "content": reflection_prompt},
                        ],
                        max_completion_tokens=reflection_token_cap,
                    )
                    final_content = reflection_response.choices[0].message.content or draft
                    logger.info(f"Agentic: reflection pass complete (cap={reflection_token_cap} tokens)")
                else:
                    final_content = draft
                break

            # Execute each tool call
            for tc in tool_calls:
                tool_calls_made += 1
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                kql = args.get("kql", "")
                qtype = args.get("type", "resource_graph")
                extra = {k: v for k, v in args.items() if k not in ("kql", "type")}
                logger.info(f"Agentic tool call {tool_calls_made}: {qtype} | {kql[:150]}")

                query_result = await _run_query(kql, qtype, extra)

                # Enforce a hard token budget on tool results.
                # Raw ARG rows can contain deeply nested properties (rules, routes, etc.)
                # that explode to hundreds of KB per row. Strategy:
                # 1. Cap row/change count to 100
                # 2. If serialized size still > 40K chars, strip properties values down
                #    to a field-name schema so AI knows what to project in the next query
                # 3. Hard-truncate the JSON string at 60K chars as last resort
                rows = query_result.get("rows", [])
                total = query_result.get("count", len(rows))
                if len(rows) > 100:
                    rows = rows[:100]
                    query_result = {"rows": rows, "count": total, "truncated_at": 100}

                # Flatten change_analysis results so the AI can read property diffs
                if "changes" in query_result and isinstance(query_result["changes"], list):
                    flat_changes = []
                    for c in query_result["changes"]:
                        flat = {
                            "time": c.get("changeTime"),
                            "type": c.get("changeType"),
                            "resource": c.get("targetResourceName"),
                            "resourceType": c.get("targetResourceType"),
                            "resourceGroup": c.get("resourceGroup"),
                            "initiatedBy": c.get("initiatedBy"),
                            "clientType": c.get("clientType"),
                        }
                        raw_changes = c.get("changes")
                        if isinstance(raw_changes, dict):
                            flat["properties"] = [
                                {
                                    "path": prop,
                                    "from": v.get("previousValue"),
                                    "to": v.get("newValue"),
                                }
                                for prop, v in raw_changes.items()
                                if isinstance(v, dict)
                            ]
                        flat_changes.append(flat)
                    query_result = {
                        "changes": flat_changes,
                        "count": query_result.get("count", len(flat_changes)),
                    }

                result_str = json.dumps(query_result, default=str)
                if len(result_str) > 40_000:
                    def _schema_only(row) -> dict:
                        """Replace every value with its type/shape so AI sees the schema without the data."""
                        if isinstance(row, list):
                            # SDK returned list rows (e.g. 500+ col AzureDiagnostics)
                            return {f"col_{i}": type(v).__name__ for i, v in enumerate(row[:50])}
                        out = {}
                        for k, v in row.items():
                            if isinstance(v, dict):
                                out[k] = {sk: type(sv).__name__ for sk, sv in v.items()}
                            elif isinstance(v, list):
                                out[k] = f"array[{len(v)}]"
                            else:
                                out[k] = v
                        return out

                    schema_rows = [_schema_only(r) for r in rows[:20]]
                    query_result = {
                        "rows": schema_rows,
                        "count": total,
                        "note": (
                            f"Result contained {total} rows but was too large to return in full. "
                            "Shown above is the field schema of the first 20 rows (array lengths shown, not values). "
                            "Use 'project' to select only the fields you need, or use mv-expand + summarize "
                            "to aggregate inside the query instead of returning raw nested data."
                        ),
                    }
                    result_str = json.dumps(query_result, default=str)

                # Absolute hard cap
                if len(result_str) > 60_000:
                    result_str = result_str[:60_000] + '... [truncated]"}'

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # If the loop exhausted its budget without the AI returning a final text response
        # (i.e., the last response still had tool_calls), make one synthesis call without tools.
        if final_content is None:
            try:
                synth_response = await self.openai_client.chat.completions.create(
                    model=self.settings.OPENAI_DEPLOYMENT_NAME,
                    messages=messages,
                    max_completion_tokens=4000,
                )
                final_content = synth_response.choices[0].message.content or "Analysis complete."
                logger.info("Agentic: synthesis after loop exhaustion complete")
            except Exception:
                final_content = "Analysis complete."
        logger.info(f"Agentic analysis complete: {tool_calls_made} tool calls")

        return {
            "success": True,
            "results": [],
            "total_records": 0,
            "message": final_content,
            "query_type": "agentic_analysis",
            "suggestions": [],
        }

    async def _execute_resource_graph_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute resource graph query from AI decision"""
        try:
            from .query_service import QueryService
            query_service = QueryService()
            
            # Get user query for cache key
            original_user_query = user_data.get("query", "") if user_data else ""
            
            # Get initial KQL query from AI decision (may be simple/incomplete)
            kql_query_from_ai = ai_decision.get("query")
            
            # Use subscription_ids from the context (stored in instance variable)
            # instead of relying on AI's scope which may contain placeholder text
            subscription_ids = getattr(self, '_current_subscription_ids', [])
            resource_group = getattr(self, '_current_resource_group', None)
            resource_id = getattr(self, '_current_resource_id', None)
            
            # IMPROVED RESOURCE GRAPH KQL GENERATION: Use robust prompt engineering
            # Build resource context for proper KQL generation
            resource_context = {}
            if resource_id:
                resource_context["resource_id"] = resource_id
            elif resource_group:
                resource_context["resource_group"] = resource_group
            if subscription_ids:
                resource_context["subscription_ids"] = subscription_ids
            
            # SMART CACHE: Check if we have cached KQL for this query
            # Cache must include scope (resource ID, resource group, or subscription) since filters are enforced in KQL
            cache_scope = original_user_query
            if resource_id:
                cache_scope = f"{original_user_query}|resource_id:{resource_id}"
            elif resource_group:
                cache_scope = f"{original_user_query}|resource_group:{resource_group}"
            elif subscription_ids:
                sub_id = subscription_ids[0] if isinstance(subscription_ids, list) and subscription_ids else subscription_ids
                cache_scope = f"{original_user_query}|subscription:{sub_id}"
            
            logger.info(f" Resource Graph cache key: {cache_scope[:200]}...")
            cached_kql = await advanced_query_cache.get_cached_kql(original_user_query, cache_scope, False)
            
            if cached_kql:
                kql_query = cached_kql["kql_query"]
                logger.info(f" Using cached Resource Graph KQL: {kql_query[:1000]}...")
                
                # ENFORCE SCOPE FILTERS: Support all scope transitions (Resource ↔ RG ↔ Subscription)
                # Step 1: Remove all existing scope filters
                original_query = kql_query
                # Remove resource ID filters
                kql_query = re.sub(r'\|\s*where\s+id\s*=~?\s*["\'][^"\']+["\']\s*\n?', '\n', kql_query, flags=re.IGNORECASE)
                # Remove resource group filters  
                kql_query = re.sub(r'\|\s*where\s+resourceGroup\s*=~?\s*["\'][^"\']+["\']\s*\n?', '\n', kql_query, flags=re.IGNORECASE)
                
                # Step 2: Add the appropriate filter for current scope
                if resource_id:
                    # Add resource ID filter
                    match = re.match(r'^(\w+)\s*(\|.*)?$', kql_query.strip(), re.DOTALL)
                    if match:
                        table_name = match.group(1)
                        rest_of_query = match.group(2) or ''
                        rest_of_query = f"\n{rest_of_query}" if rest_of_query else ''
                        kql_query = f"{table_name}\n| where id =~ '{resource_id}'{rest_of_query}"
                        if original_query != kql_query:
                            logger.info(f" Applied resource ID filter to cached KQL query")
                elif resource_group:
                    # Add resource group filter
                    match = re.match(r'^(\w+)\s*(\|.*)?$', kql_query.strip(), re.DOTALL)
                    if match:
                        table_name = match.group(1)
                        rest_of_query = match.group(2) or ''
                        rest_of_query = f"\n{rest_of_query}" if rest_of_query else ''
                        kql_query = f"{table_name}\n| where resourceGroup =~ '{resource_group}'{rest_of_query}"
                        if original_query != kql_query:
                            logger.info(f" Applied resource group filter to cached KQL query: resourceGroup =~ '{resource_group}'")
                else:
                    # Subscription-wide: filters already removed
                    if original_query != kql_query:
                        logger.info(f" Removed scope filters from cached KQL for subscription-wide query")
            else:
                # Regenerate KQL using proper prompt engineering
                kql_query = kql_query_from_ai
                
                # ALWAYS regenerate to extract hints from user query (table names, resource types, etc.)
                from .kql_generation_service import AIService
                ai_service = AIService()
                
                # Use the AI's own reasoning as context for the KQL generator.
                # The router already encoded intent (best practices, specific resource, etc.) in reasoning.
                ai_reasoning = ai_decision.get("reasoning", kql_query_from_ai)
                
                # Use Resource Graph KQL generation with full prompt engineering
                # Pass BOTH original query and AI reasoning (same pattern as Log Analytics)
                # Also pass recent conversation context so follow-up queries scope to prior results
                recent_context = None
                if hasattr(self, '_conversation_messages') and self._conversation_messages:
                    last_assistant = next(
                        (m for m in reversed(self._conversation_messages) if m.get("role") == "assistant"),
                        None
                    )
                    if last_assistant:
                        recent_context = last_assistant.get("content", "")[:1500]
                regenerated_kql = await ai_service._generate_resource_graph_kql(
                    ai_reasoning,
                    resource_context,
                    original_user_query=original_user_query,
                    conversation_context=recent_context
                )
                
                if regenerated_kql:
                    kql_query = regenerated_kql
                    
                    # ENFORCE SCOPE FILTERS: Support all scope transitions (Resource ↔ RG ↔ Subscription)
                    # Step 1: Remove all existing scope filters
                    original_query = kql_query
                    # Remove resource ID filters
                    kql_query = re.sub(r'\|\s*where\s+id\s*=~?\s*["\'][^"\']+["\']\s*\n?', '\n', kql_query, flags=re.IGNORECASE)
                    # Remove resource group filters  
                    kql_query = re.sub(r'\|\s*where\s+resourceGroup\s*=~?\s*["\'][^"\']+["\']\s*\n?', '\n', kql_query, flags=re.IGNORECASE)
                    
                    # Step 2: Add the appropriate filter for current scope
                    if resource_id:
                        # Add resource ID filter
                        match = re.match(r'^(\w+)\s*(\|.*)?$', kql_query.strip(), re.DOTALL)
                        if match:
                            table_name = match.group(1)
                            rest_of_query = match.group(2) or ''
                            rest_of_query = f"\n{rest_of_query}" if rest_of_query else ''
                            kql_query = f"{table_name}\n| where id =~ '{resource_id}'{rest_of_query}"
                            if original_query != kql_query:
                                logger.info(f" Applied resource ID filter to generated KQL query")
                    elif resource_group:
                        # Add resource group filter
                        match = re.match(r'^(\w+)\s*(\|.*)?$', kql_query.strip(), re.DOTALL)
                        if match:
                            table_name = match.group(1)
                            rest_of_query = match.group(2) or ''
                            rest_of_query = f"\n{rest_of_query}" if rest_of_query else ''
                            kql_query = f"{table_name}\n| where resourceGroup =~ '{resource_group}'{rest_of_query}"
                            if original_query != kql_query:
                                logger.info(f" Applied resource group filter to generated KQL query: resourceGroup =~ '{resource_group}'")
                    else:
                        # Subscription-wide: filters already removed
                        if original_query != kql_query:
                            logger.info(f" Removed scope filters from generated KQL for subscription-wide query")
                    
                    # ADJUST LIMIT: For subscription-wide best practices, use modest limit
                    if not resource_id and not resource_group:
                        # Subscription-wide: limit to 50 to get true count, but sample only 10 for analysis
                        kql_query = re.sub(r'\|\s*limit\s+\d+', '| limit 50', kql_query, flags=re.IGNORECASE)
                        if '| limit' not in kql_query.lower():
                            # No limit found, add it
                            kql_query = kql_query.strip() + '\n| limit 50'
                        logger.info(f" Set limit 50 for subscription-wide scan (will sample 10 for analysis)")
                    
                    logger.info(f" Regenerated Resource Graph KQL with proper prompt engineering: {kql_query[:1000]}...")
                    # Cache will be stored after successful execution
                else:
                    # Fallback to AI decision query if regeneration fails
                    logger.warning("Resource Graph KQL regeneration failed, using AI decision query")
            
            logger.info("Executing regenerated query")
            result = await query_service.execute_query(
                {
                    "query": kql_query,
                    "type": "resource_graph",
                    "natural_language_query": original_user_query if resource_context else None  # Store for error recovery
                },
                subscription_ids,
                user_data
            )
            
            # Reflection pass: if 0 results, let the KQL model self-correct with full context
            if result.get("success") and result.get("total_records", -1) == 0 and self.openai_client:
                logger.info("Zero results — asking AI to reflect and self-correct the KQL")
                corrected_kql = await ai_service._regenerate_resource_graph_kql_with_docs(
                    natural_query=original_user_query,
                    failed_query=kql_query,
                    error_message="Query returned 0 results. Reflect on whether the resource type, property paths, or filter logic might be wrong and generate a corrected query.",
                    resource_context=resource_context
                )
                if corrected_kql and corrected_kql.strip() != kql_query.strip():
                    logger.info(f"AI self-corrected KQL: {corrected_kql[:200]}...")
                    retry = await query_service.execute_query(
                        {"query": corrected_kql, "type": "resource_graph"},
                        subscription_ids,
                        user_data
                    )
                    if retry.get("success") and retry.get("total_records", 0) > 0:
                        result = retry
                        kql_query = corrected_kql
            
            if result.get("success") and result.get("total_records", 0) > 0:
                await advanced_query_cache.store_kql_query(
                    original_user_query,
                    cache_scope,
                    kql_query,
                    {"generated_by": "ai_service", "query_type": "resource_graph"},
                    False
                )
                
                # Generate AI analysis of the resource graph results.
                # Route to WAF-style analysis if the AI's own reasoning signals a best-practices
                # intent — no user-query keyword matching needed.
                original_user_query = getattr(self, '_current_user_query', '')
                is_best_practices_query = any(
                    p in ai_reasoning.lower()
                    for p in ("best practice", "well-architected", "well architected", "configuration review")
                )

                if is_best_practices_query and result.get("results"):
                    logger.info(" Generating best practices analysis (Well-Architected Framework)")
                    resource_id = getattr(self, '_current_resource_id', None)
                    scope_desc = "subscription-wide"
                    if resource_id:
                        scope_desc = f"specific resource '{resource_id.split('/')[-1]}'"
                    elif resource_group:
                        scope_desc = f"resource group '{resource_group}'"
                    ai_analysis = await self._generate_best_practices_analysis(
                        result, kql_query, original_user_query, scope_desc
                    )
                else:
                    ai_analysis = await self._generate_resource_analysis(
                        result, kql_query, ai_decision.get("reasoning"),
                        suggested_followups=ai_decision.get("suggestions", [])
                    )
                
                # Get resource group filter from stored context
                resource_group = getattr(self, '_current_resource_group', None)
                scope_message = f"(Filtered by: {resource_group})" if resource_group else None

                return {
                    "success": True,
                    "message": ai_analysis,
                    "data": {"results": result.get("results"), "total_records": result.get("total_records")},
                    "query_type": "resource_graph",
                    "suggestions": [],
                    "scope_message": scope_message
                }
            else:
                err = result.get('message') or result.get('error') or 'no results found'
                msg = await self._ai_explain_error(err, context=f"resource_graph query for: {original_user_query}")
                return {
                    "success": False,
                    "message": msg,
                    "suggestions": []
                }

        except Exception as e:
            logger.error(f"Resource graph execution failed: {e}", exc_info=True)
            msg = await self._ai_explain_error(str(e), context=f"resource_graph query for: {getattr(self, '_current_user_query', '')}")
            return {
                "success": False,
                "message": msg,
                "suggestions": []
            }
    
    def _parse_time_range(self, time_range: str) -> Tuple[datetime, datetime]:
        """Parse time range string to start and end dates
        
        Supports:
        - Relative: today, yesterday, this_month, last_month, last_7_days, last_30_days, last_90_days, this_year
        - Specific months: january, february, march, april, may, june, july, august, september, october, november, december
        - Month with year: "october 2024", "jan 2025"
        """
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        time_range_lower = time_range.lower().strip()
        
        # Predefined relative ranges
        if time_range_lower == "today":
            start_date = today
            end_date = now
        elif time_range_lower == "yesterday":
            start_date = today - timedelta(days=1)
            end_date = today
        elif time_range_lower == "this_month":
            start_date = today.replace(day=1)
            end_date = now
        elif time_range_lower == "last_month":
            first_of_this_month = today.replace(day=1)
            # Last month: Nov 1 - Nov 30 (not Dec 1!)
            last_day_of_last_month = first_of_this_month - timedelta(days=1)
            start_date = last_day_of_last_month.replace(day=1)
            end_date = last_day_of_last_month.replace(hour=23, minute=59, second=59)
        elif time_range_lower == "last_7_days":
            start_date = today - timedelta(days=7)
            end_date = now
        elif time_range_lower == "last_30_days":
            start_date = today - timedelta(days=30)
            end_date = now
        elif time_range_lower == "last_90_days":
            start_date = today - timedelta(days=90)
            end_date = now
        elif time_range_lower.startswith("last_") and time_range_lower.endswith("_days"):
            # Handle arbitrary day ranges like "last_15_days", "last_45_days"
            try:
                num_days = int(time_range_lower.split("_")[1])
                start_date = today - timedelta(days=num_days)
                end_date = now
                logger.info(f" Custom day range: last {num_days} days")
            except (ValueError, IndexError):
                logger.warning(f" Invalid custom day range: {time_range}, defaulting to this_month")
                start_date = today.replace(day=1)
                end_date = now
        elif time_range_lower == "this_year":
            start_date = today.replace(month=1, day=1)
            end_date = now
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
                end_date = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59)
            else:
                # Default to last 30 days if can't parse
                start_date = today - timedelta(days=30)
                end_date = now
        
        return start_date, end_date
    
    async def _execute_cost_analysis_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute cost analysis from AI decision using parallel AdvancedCostService"""
        try:
            import asyncio
            import time
            from .advanced_cost_service import AdvancedCostService
            from .dual_credential_service import dual_cred_service
            
            # Get credential for cost service
            credential = dual_cred_service.get_user_credential(user_data)
            
            # Initialize advanced cost service (uses parallel execution)
            advanced_cost_service = AdvancedCostService(credential)
            
            # Use subscription_ids from context
            subscription_ids_list = getattr(self, '_current_subscription_ids', [])
            resource_group = getattr(self, '_current_resource_group', None)
            resource_id = getattr(self, '_current_resource_id', None)
            
            # Debug logging
            logger.info(f" Cost analysis context: subscription_ids_list={len(subscription_ids_list)} subscriptions")
            logger.info(f" Has _current_subscription_ids attribute: {hasattr(self, '_current_subscription_ids')}")
            if hasattr(self, '_current_subscription_ids'):
                logger.info(f" _current_subscription_ids value: {len(self._current_subscription_ids)} items")
            if resource_group:
                logger.info(f" Resource group filter: {resource_group}")
            if resource_id:
                logger.info(f" Resource ID filter: {resource_id}")
            
            scope = ai_decision.get("scope", {})
            time_range = scope.get("time_range", "this_month")  # Default to current month unless user specifies "last 30 days"
            cost_grouping_raw = ai_decision.get("cost_grouping", "service")  # AI uses shorthand
            cost_filters = ai_decision.get("cost_filters", {})  # AI-extracted filters (like KQL generation)
            include_reservations = bool(ai_decision.get("include_reservations", False))  # AI decides based on user intent

            if include_reservations:
                logger.info("Cost: using AMORTIZED cost (AI decision)")
            else:
                logger.info("Cost: using ACTUAL cost (AI decision)")
            
            # Map AI shorthand to Azure Cost Management dimension names
            grouping_map = {
                "service": "ServiceName",
                "service_family": "ServiceFamily",
                "resource_group": "ResourceGroupName",
                "resource": "ResourceId",
                "resource_type": "ResourceType",
                "location": "ResourceLocation",
                "subscription": "Subscription",
                "none": None
            }
            cost_grouping = grouping_map.get(cost_grouping_raw, "ServiceName")
            
            # Safety check: 'none' grouping only works for Management Group queries
            # For single subscription daily trends, fall back to ServiceName
            if cost_grouping is None and len(subscription_ids_list) == 1:
                logger.info(f" Converting 'none' grouping to 'ServiceName' for single subscription daily trend")
                cost_grouping = "ServiceName"
            
            if not subscription_ids_list:
                logger.error(f" No subscriptions found for cost analysis!")
                return {
                    "success": False,
                    "message": "I need at least one subscription to analyze costs.",
                    "suggestions": ["Enable global scope or select a subscription"]
                }
            
            # For multiple subscriptions (global scope), use billing account scope via SP
            if len(subscription_ids_list) > 1:
                subscriptions_to_query = subscription_ids_list
                
                logger.info(f" GLOBAL SCOPE DETECTED: {len(subscriptions_to_query)} subscriptions from scope selector")
                logger.info(f" Global scope: will use billing account scope via SP (single call, no per-subscription rate limits)")
                
                # Start timing
                start_time = time.time()
                
                # Parse time range to dates
                start_date, end_date = self._parse_time_range(time_range)
                
                # Extract resource type for Azure API filtering (native)
                resource_type_filter = cost_filters.get("resource_type") if cost_filters else None
                
                # Use AdvancedCostService — routes to billing account scope for global queries
                result = await advanced_cost_service.get_subscription_costs(
                    subscription_ids=subscriptions_to_query,
                    start_date=start_date,
                    end_date=end_date,
                    group_by=cost_grouping,
                    include_reservations=include_reservations,
                    resource_type_filter=resource_type_filter  # Azure API applies this natively
                )
                
                # Log performance
                elapsed_time = time.time() - start_time
                logger.info(f" Global cost query completed in {elapsed_time:.2f} seconds")
                
                if not result.get("success", False):
                    return {
                        "success": False,
                        "message": f"Unable to retrieve costs: {result.get('message', 'Unknown error')}",
                        "suggestions": ["Check your permissions", "Try a specific subscription", "Verify subscriptions have cost data available"]
                    }
                
                # NOTE: Management Group queries have NO limitations - single API call covers all subscriptions
                # Only add limitation note for non-MG queries where we had to query individual subscriptions
                if not result.get("is_management_group"):
                    result["limitation_note"] = f" (Queried {len(subscriptions_to_query)} subscriptions individually)"
                
            else:
                # Single subscription - check if we need resource-scoped query
                subscription_id = subscription_ids_list[0]
                
                # Parse time range to dates
                start_date, end_date = self._parse_time_range(time_range)
                
                # Extract resource type for Azure API filtering (native)
                resource_type_filter = cost_filters.get("resource_type") if cost_filters else None
                
                # Check for resource-scoped queries
                # Note: Azure Cost Management API doesn't support querying individual resources directly
                # For resource-level queries, we query the resource group and filter results
                if resource_id and resource_group:
                    # Query resource group and filter to specific resource
                    logger.info(f" Analyzing costs for specific resource: {resource_id}")
                    logger.info(f" Querying resource group {resource_group} and filtering to resource")
                    result = await advanced_cost_service.get_resource_group_costs(
                        subscription_id=subscription_id,
                        resource_groups=[resource_group],
                        start_date=start_date,
                        end_date=end_date,
                        group_by="ResourceId"  # Group by resource to get per-resource costs
                    )
                    
                    # Filter results to only the specific resource
                    if result.get("success") and result.get("breakdown"):
                        resource_id_lower = resource_id.lower()
                        filtered_breakdown = [
                            item for item in result["breakdown"]
                            if item.get("resourceid", "").lower() == resource_id_lower
                        ]
                        
                        # Update result with filtered data and scope information
                        result["breakdown"] = filtered_breakdown
                        result["total_cost"] = sum(item.get("cost", 0) for item in filtered_breakdown)
                        result["query_scope"] = "resource"
                        result["resource_name"] = resource_id.split('/')[-1] if '/' in resource_id else resource_id
                        result["resource_id"] = resource_id
                        result["resource_group_name"] = resource_group  # Add resource group name for context
                        
                        logger.info(f" Filtered to specific resource: {len(filtered_breakdown)} cost entries, total: {result['total_cost']}")
                    
                elif resource_group:
                    # Query specific resource group
                    logger.info(f" Analyzing costs for resource group: {resource_group}")
                    result = await advanced_cost_service.get_resource_group_costs(
                        subscription_id=subscription_id,
                        resource_groups=[resource_group],
                        start_date=start_date,
                        end_date=end_date,
                        group_by=cost_grouping
                    )
                else:
                    # Query entire subscription
                    logger.info(f" Analyzing costs for subscription {subscription_id}")
                    result = await advanced_cost_service.get_subscription_costs(
                        subscription_ids=[subscription_id],
                        start_date=start_date,
                        end_date=end_date,
                        group_by=cost_grouping,
                        include_reservations=include_reservations,
                        resource_type_filter=resource_type_filter  # Azure API applies this natively
                    )
            
            if result.get("success"):
                # Apply AI-extracted filters (all filter types: location, name, cost, top N, etc.)
                if cost_filters:
                    logger.info(f" APPLYING FILTERS - Original total: {result.get('total_cost', 0):.2f}, Items: {len(result.get('breakdown', []))}")
                    result = self._apply_cost_filters(result, cost_filters)
                    logger.info(f" AFTER FILTERS - Filtered total: {result.get('total_cost', 0):.2f}, Items: {len(result.get('breakdown', []))}")
                    
                    # Remove daily_trend when filters are applied (show chart only for pure date range queries)
                    if result.get("filters_applied"):
                        logger.info(" Removing daily_trend from response (filters applied - not a pure time range query)")
                        result.pop("daily_trend", None)
                else:
                    logger.info(f" NO FILTERS APPLIED - Total: {result.get('total_cost', 0):.2f}, Items: {len(result.get('breakdown', []))}")
                
                # Remove by_subscription table from /query endpoint (not needed for chat interface)
                result.pop("by_subscription", None)
                
                # Generate AI analysis of the cost data
                ai_analysis = await self._generate_cost_analysis(result, cost_grouping, ai_decision.get("reasoning"))
                
                # Cost service returns complex nested data - pass it through with AI analysis
                return {
                    "success": True,
                    "message": ai_analysis,  # Use AI-generated natural language analysis
                    "data": result,  # Pass entire result object (contains current_period, analysis, etc.)
                    "query_type": "cost",
                    "suggestions": ai_decision.get("suggestions", [])
                }
            else:
                return {
                    "success": False,
                    "message": f"Cost analysis failed: {result.get('message')}",
                    "suggestions": (ai_decision.get("suggestions") or self._get_starter_suggestions())
                }
                
        except Exception as e:
            logger.error(f"Cost analysis execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": "Cost analysis failed",
                "suggestions": (ai_decision.get("suggestions") or self._get_starter_suggestions())
            }
    
    def _apply_cost_filters(
        self,
        result: Dict[str, Any],
        filters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply AI-extracted filters to cost data
        
        Supports ALL filter types:
        - resource_type (already applied by Azure API)
        - service_name: filter by service
        - location: filter by region
        - resource_name_pattern: search in names
        - top_n: highest N costs
        - bottom_n: lowest N costs  
        - min_cost/max_cost: cost thresholds
        - sort_order: custom sorting
        """
        breakdown = result.get("breakdown", [])
        if not breakdown:
            return result
        
        original_count = len(breakdown)
        filtered = breakdown.copy()
        
        logger.info(f" Applying AI filters to {original_count} items: {filters}")
        
        # 1. Service Name Filter
        service_name = filters.get("service_name")
        if service_name:
            service_lower = service_name.lower()
            filtered = [item for item in filtered 
                       if service_lower in str(item.get("servicename", "")).lower()]
            logger.info(f" Service filter '{service_name}': {len(filtered)} items")
        
        # 2. Location Filter (normalize: remove spaces, lowercase)
        location = filters.get("location")
        if location:
            loc_normalized = location.replace(" ", "").lower()
            filtered = [item for item in filtered
                       if loc_normalized in str(item.get("resourcelocation", "")).replace(" ", "").lower()]
            logger.info(f" Location filter '{location}': {len(filtered)} items")
        
        # 3. Resource Name Pattern (search in multiple fields)
        pattern = filters.get("resource_name_pattern")
        if pattern:
            pattern_lower = pattern.lower()
            filtered = [item for item in filtered
                       if any(pattern_lower in str(field).lower() for field in [
                           item.get("resourcename", ""),
                           item.get("resourceid", ""),
                           item.get("resourcegroupname", ""),
                           item.get("servicename", "")
                       ])]
            logger.info(f" Name pattern '{pattern}': {len(filtered)} items")
        
        # 4. Cost Threshold Filters
        min_cost = filters.get("min_cost")
        max_cost = filters.get("max_cost")
        
        if min_cost is not None:
            filtered = [item for item in filtered if item.get("cost", 0) >= min_cost]
            logger.info(f" Min cost ≥ {min_cost}: {len(filtered)} items")
        
        if max_cost is not None:
            filtered = [item for item in filtered if item.get("cost", 0) <= max_cost]
            logger.info(f" Max cost ≤ {max_cost}: {len(filtered)} items")
        
        # 5. Sorting (before applying limit)
        top_n = filters.get("top_n")
        bottom_n = filters.get("bottom_n")
        sort_order = filters.get("sort_order", "highest")  # default: highest cost first
        
        # Determine sort direction
        if bottom_n:
            # Bottom N = lowest costs first
            filtered.sort(key=lambda x: x.get("cost", 0), reverse=False)
            logger.info(f" Sorted: lowest cost first (for bottom {bottom_n})")
        elif top_n or sort_order == "highest":
            # Top N or default = highest costs first
            filtered.sort(key=lambda x: x.get("cost", 0), reverse=True)
            logger.info(f" Sorted: highest cost first")
        elif sort_order == "lowest":
            filtered.sort(key=lambda x: x.get("cost", 0), reverse=False)
            logger.info(f" Sorted: lowest cost first")
        elif sort_order == "name_asc":
            filtered.sort(key=lambda x: self._get_item_name(x))
            logger.info(f" Sorted: name A-Z")
        elif sort_order == "name_desc":
            filtered.sort(key=lambda x: self._get_item_name(x), reverse=True)
            logger.info(f" Sorted: name Z-A")
        
        # 6. Apply Limit (top_n or bottom_n)
        limit = top_n or bottom_n
        if limit and limit > 0:
            filtered = filtered[:limit]
            limit_type = "top" if top_n else "bottom"
            logger.info(f" Limited to {limit_type} {limit} items")
        
        # Update result
        result["breakdown"] = filtered
        result["filtered_total_cost"] = sum(item.get("cost", 0) for item in filtered)
        result["original_total_cost"] = result.get("total_cost", 0)  # Save original
        result["total_cost"] = result["filtered_total_cost"]  # Update to filtered total
        
        # Add filter metadata
        result["filters_applied"] = True
        result["filter_count"] = {
            "original": original_count,
            "filtered": len(filtered),
            "removed": original_count - len(filtered)
        }
        result["active_filters"] = {k: v for k, v in filters.items() if v is not None}
        
        logger.info(f" Filtering complete: {len(filtered)}/{original_count} items (removed {original_count - len(filtered)})")
        
        return result
    
    def _get_item_name(self, item: Dict[str, Any]) -> str:
        """Extract displayable name from cost item"""
        # billing_account_global format uses 'name' key directly
        if item.get('name'):
            return item['name']
        if item.get('servicename'):
            return item.get('servicename', '')
        elif item.get('resourcename'):
            return item.get('resourcename', '')
        elif item.get('resourceid'):
            resource_id = item.get('resourceid', '')
            return resource_id.split('/')[-1] if '/' in resource_id else resource_id
        elif item.get('resourcegroupname'):
            return item.get('resourcegroupname', '')
        elif item.get('resourcelocation'):
            return item.get('resourcelocation', '')
        return 'Unknown'
    
    async def _execute_cost_comparison_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute cost comparison between two time periods"""
        try:
            from .advanced_cost_service import AdvancedCostService
            from .dual_credential_service import dual_cred_service
            
            # Get credential for cost service
            credential = dual_cred_service.get_user_credential(user_data)
            advanced_cost_service = AdvancedCostService(credential)
            
            # Use subscription_ids from context
            subscription_ids_list = getattr(self, '_current_subscription_ids', [])
            resource_group = getattr(self, '_current_resource_group', None)
            resource_id = getattr(self, '_current_resource_id', None)
            
            if not subscription_ids_list:
                return {
                    "success": False,
                    "message": "I need at least one subscription to compare costs.",
                    "suggestions": ["Enable global scope or select a subscription"]
                }
            
            # Get comparison periods from AI decision
            comparison_periods = ai_decision.get("comparison_periods", ["last_month", "this_month"])
            
            if len(comparison_periods) != 2:
                return {
                    "success": False,
                    "message": "I need exactly two time periods to compare.",
                    "suggestions": ["Try: 'compare last month and this month'"]
                }
            
            logger.info(f" Cost comparison: {comparison_periods[0]} vs {comparison_periods[1]}")
            if resource_id:
                logger.info(f" Resource-scoped comparison for: {resource_id}")
            
            # Query both periods
            results = []
            for period in comparison_periods:
                start_date, end_date = self._parse_time_range(period)
                
                # Determine query scope based on context
                if resource_id and resource_group:
                    # Resource-scoped: Query resource group and filter to specific resource
                    logger.info(f" Comparing costs for specific resource: {resource_id}")
                    subscription_id = subscription_ids_list[0] if subscription_ids_list else None
                    
                    cost_result = await advanced_cost_service.get_resource_group_costs(
                        subscription_id=subscription_id,
                        resource_groups=[resource_group],
                        start_date=start_date,
                        end_date=end_date,
                        group_by="ResourceId"  # Group by resource to get per-resource costs
                    )
                    
                    # Filter results to only the specific resource
                    if cost_result.get("success") and cost_result.get("breakdown"):
                        resource_id_lower = resource_id.lower()
                        filtered_breakdown = [
                            item for item in cost_result["breakdown"]
                            if item.get("resourceid", "").lower() == resource_id_lower
                        ]
                        
                        # Update result with filtered data
                        cost_result["breakdown"] = filtered_breakdown
                        cost_result["total_cost"] = sum(item.get("cost", 0) for item in filtered_breakdown)
                        logger.info(f" Filtered to specific resource: {len(filtered_breakdown)} cost entries, total: {cost_result['total_cost']}")
                        
                elif resource_group:
                    # Resource group scoped
                    logger.info(f" Comparing costs for resource group: {resource_group}")
                    subscription_id = subscription_ids_list[0] if subscription_ids_list else None
                    
                    cost_result = await advanced_cost_service.get_resource_group_costs(
                        subscription_id=subscription_id,
                        resource_groups=[resource_group],
                        start_date=start_date,
                        end_date=end_date,
                        group_by="ServiceName"
                    )
                else:
                    # Subscription or global scope
                    cost_result = await advanced_cost_service.get_subscription_costs(
                        subscription_ids=subscription_ids_list,
                        start_date=start_date,
                        end_date=end_date,
                        group_by="ServiceName",
                        include_reservations=False  # Faster for comparisons
                    )
                
                results.append({
                    "period": period,
                    "data": cost_result,
                    "start_date": start_date,
                    "end_date": end_date
                })
            
            # Compare the two periods
            period1 = results[0]
            period2 = results[1]
            
            cost1 = period1["data"].get("total_cost", 0)
            cost2 = period2["data"].get("total_cost", 0)
            currency = period1["data"].get("currency", "SEK")
            
            # Calculate difference and percentage
            difference = cost2 - cost1
            percent_change = ((cost2 - cost1) / cost1 * 100) if cost1 > 0 else 0
            
            # Format period names
            period1_name = period1["period"].replace("_", " ").title()
            period2_name = period2["period"].replace("_", " ").title()
            
            # Build comparison message
            if difference > 0:
                trend = f"📈 **Increased {abs(percent_change):.1f}%**"
            elif difference < 0:
                trend = f"📉 **Decreased {abs(percent_change):.1f}%**"
            else:
                trend = "📊 **No change**"
            
            message = f"""**📊 Cost Comparison**

**{period1_name}**: {currency} {cost1:,.2f}
**{period2_name}**: {currency} {cost2:,.2f}

{trend}
**Difference**: {currency} {abs(difference):,.2f}

💡 The cost {'increased' if difference > 0 else 'decreased' if difference < 0 else 'remained the same'} between {period1_name.lower()} and {period2_name.lower()}.
"""
            
            return {
                "success": True,
                "query_type": "cost_comparison",
                "message": message.strip(),
                "data": {
                    "comparison": {
                        period1["period"]: {
                            "total_cost": cost1,
                            "currency": currency,
                            "date_range": {
                                "start": period1["start_date"].isoformat(),
                                "end": period1["end_date"].isoformat()
                            },
                            "breakdown": period1["data"].get("breakdown", [])
                        },
                        period2["period"]: {
                            "total_cost": cost2,
                            "currency": currency,
                            "date_range": {
                                "start": period2["start_date"].isoformat(),
                                "end": period2["end_date"].isoformat()
                            },
                            "breakdown": period2["data"].get("breakdown", [])
                        }
                    },
                    "difference": difference,
                    "percent_change": percent_change
                },
                "suggestions": ai_decision.get("suggestions", [
                    "Show detailed breakdown for each period",
                    "Which services increased the most?",
                    "View daily cost trend"
                ])
            }
            
        except Exception as e:
            logger.error(f"Cost comparison execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": "Cost comparison failed",
                "suggestions": (ai_decision.get("suggestions") or self._get_starter_suggestions())
            }
    
    async def _execute_log_analytics_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute log analytics query from AI decision"""
        try:
            from services.azure_monitor_service import AzureMonitorService
            azure_monitor_service = AzureMonitorService()
            
            # Get user query for cache key
            original_user_query = user_data.get("query", "") if user_data else ""
            
            # Get initial KQL query from AI decision (may be simple/incomplete)
            kql_query_from_ai = ai_decision.get("query")
            scope = ai_decision.get("scope", {})
            workspace_id = scope.get("workspace_id")
            
            # Use subscription_ids from context
            subscription_ids = getattr(self, '_current_subscription_ids', [])
            resource_id = getattr(self, '_current_resource_id', None)
            resource_group = getattr(self, '_current_resource_group', None)
            
            # IMPROVED KQL GENERATION: Use robust prompt engineering
            # Build resource context for proper KQL generation
            resource_context = None
            if resource_id or resource_group or subscription_ids:
                resource_context = {}
                
                if resource_id:
                    # Extract resource name and type from resource_id
                    # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/{type}/{name}
                    resource_context["resource_id"] = resource_id
                    
                    parts = resource_id.split("/")
                    if len(parts) >= 9:
                        # Extract resource name (last part)
                        resource_context["resource_name"] = parts[-1]
                        
                        # Extract resource group
                        if "resourceGroups" in parts or "resourcegroups" in parts:
                            rg_index = next((i for i, p in enumerate(parts) if p.lower() == "resourcegroups"), None)
                            if rg_index and rg_index + 1 < len(parts):
                                resource_context["resource_group"] = parts[rg_index + 1]
                        
                        # Extract resource type
                        if "providers" in parts:
                            provider_index = parts.index("providers")
                            if provider_index + 2 < len(parts):
                                resource_context["resource_type"] = f"{parts[provider_index + 1]}/{parts[provider_index + 2]}"
                
                elif resource_group:
                    resource_context["resource_group"] = resource_group
                    resource_context["subscription_ids"] = subscription_ids
                
                elif subscription_ids:
                    resource_context["subscription_ids"] = subscription_ids
            
            # Store resource_context in ai_decision for auto-heal access
            if resource_context:
                ai_decision["resource_context"] = resource_context
            
            # Store original query from user_data for auto-heal
            if user_data and "query" in user_data:
                ai_decision["original_query"] = user_data["query"]
            
            # SMART CACHE: Check if we have cached KQL for this query
            # Cache by query text + diagnostic mode (Dedicated vs AzureDiagnostics)
            # This ensures cached queries match the actual log storage mode
            # RCA calls set skip_kql_cache=True — incident-specific queries must not be reused.
            cache_scope = original_user_query
            skip_cache = bool(user_data.get("skip_kql_cache")) if user_data else False

            # Get diagnostic mode from resource context (set during KQL generation)
            # This prevents using AzureDiagnostics cached query for Dedicated mode resources
            diagnostic_mode = resource_context.get("log_mode", "unknown") if resource_context else "unknown"

            logger.info(f" Cache key: {cache_scope[:200]}... (mode={diagnostic_mode})")
            cached_kql = None if skip_cache else await advanced_query_cache.get_cached_kql(original_user_query, cache_scope, False, diagnostic_mode)

            if cached_kql:
                kql_query = cached_kql["kql_query"]
                logger.info(f" Using cached KQL query: {kql_query[:100]}...")
            else:
                # Regenerate KQL using proper prompt engineering
                if resource_context:
                    from .kql_generation_service import AIService
                    ai_service = AIService()
                    
                    # Get AI reasoning for context enrichment
                    ai_reasoning = ai_decision.get("reasoning", kql_query_from_ai)
                    
                    # Use KQL generation with full prompt engineering
                    # Pass BOTH original query and AI reasoning, plus user_data for auth
                    kql_query = await ai_service._generate_kql_from_natural_language(
                        ai_reasoning,
                        resource_context,
                        user_info=user_data,
                        original_user_query=original_user_query
                    )
                    
                    if kql_query:
                        logger.info(f" Regenerated KQL with proper prompt engineering: {kql_query[:150]}...")
                        # Cache will be stored after successful execution
                    else:
                        # Fallback to AI decision query if regeneration fails
                        logger.warning("KQL regeneration failed, using AI decision query")
                        kql_query = kql_query_from_ai
                else:
                    # No resource context, use AI decision query as-is
                    kql_query = kql_query_from_ai
            
            # If no workspace_id provided or it's a placeholder, try to discover one
            # Log Analytics is restricted to resource scope only (not global)
            # Treat "<from context>" as missing workspace_id
            if not workspace_id or workspace_id == "<from context>":
                if workspace_id == "<from context>":
                    logger.info("workspace_id is '<from context>' placeholder - treating as missing")
                    workspace_id = None
                logger.info("No workspace_id provided, attempting workspace discovery (resource scope only)")
                logger.info(f" Context - resource_id: {resource_id}, resource_group: {resource_group}, subscription_ids: {subscription_ids}")
                
                # Check if this is an Activity Log query
                is_activity_log_query = "AzureActivity" in kql_query
                
                # For Log Analytics, we need a specific subscription/resource context
                # Global scope is not supported for log queries
                if len(subscription_ids) > 1:
                    return {
                        "success": False,
                        "message": "Log Analytics queries require a specific subscription or resource scope. Please disable global scope or select a specific resource.",
                        "suggestions": ["Disable global scope", "Select a specific subscription", "Select a specific resource"]
                    }
                
                # Get subscription_id for workspace discovery
                subscription_id = None
                if resource_id:
                    # Extract subscription from resource_id
                    # Format: /subscriptions/{sub}/resourceGroups/{rg}/providers/{type}/{name}
                    parts = resource_id.split("/")
                    if len(parts) > 2 and parts[1] == "subscriptions":
                        subscription_id = parts[2]
                elif subscription_ids and len(subscription_ids) > 0:
                    subscription_id = subscription_ids[0]
                else:
                    # Get user's accessible subscriptions
                    from .resource_service import resource_service
                    try:
                        user_subs_response = await resource_service.get_user_accessible_subscriptions(
                            user_data, page=1, page_size=100
                        )
                        user_subscriptions = user_subs_response.get("subscriptions", [])
                        if user_subscriptions:
                            subscription_id = user_subscriptions[0]["subscription_id"]
                            logger.info(f"Using first accessible subscription: {subscription_id}")
                        else:
                            logger.error("No accessible subscriptions found")
                    except Exception as e:
                        logger.error(f"Failed to get user subscriptions: {e}")
                
                if not subscription_id:
                    return {
                        "success": False,
                        "message": "I need a Log Analytics workspace to query logs.",
                        "suggestions": ["Check your subscription access", "Specify a workspace ID"]
                    }
                
                # Resolve workspace via shared resolver.
                # Handles both types correctly:
                #   AzureActivity → subscription-level diagnostic settings (cross-subscription)
                #   Resource logs → resource-level diagnostic settings with Dedicated/AzureDiagnostics mode
                if not workspace_id:
                    from .query_service import QueryService
                    query_svc = QueryService()
                    resolved = await query_svc.resolve_log_analytics_workspace(
                        kql_query=kql_query,
                        subscription_ids=[subscription_id],
                        user_info=user_data,
                        resource_id=resource_id,
                    )
                    if resolved:
                        workspace_id = resolved["workspace_id"]
                        log_mode = resolved["log_mode"]
                        logger.info(f"Found workspace from shared resolver: {workspace_id} (mode={log_mode})")
                        # Propagate log_mode into resource_context so KQL cache key stays correct
                        if resource_context:
                            resource_context["log_mode"] = log_mode
                    else:
                        msg = await self._ai_explain_error(
                            "No Log Analytics workspaces found",
                            context=f"resource_id={resource_id}, subscription={subscription_id}"
                        )
                        return {"success": False, "message": msg, "suggestions": ["Check diagnostic settings in Azure Portal", "Select a different resource", "Verify workspace access"]}
            
            # Create user_info with LA token if provided
            # Keep both tokens: management token and LA token
            user_info_for_query = user_data.copy()
            management_token = user_data.get('management_token')  # Management token for workspace resolution
            la_token = user_data.get('la_token')  # LA token for query execution
            
            if la_token:
                logger.info("Using LA token from user_data for KQL execution")
                user_info_for_query['la_token'] = la_token  # Set LA token for query execution
                user_info_for_query['management_token'] = management_token  # Keep management token for workspace resolution
            
            # AUTOMATIC CLOUDFLARE LIMIT: Force row limit to prevent 64MB errors
            if "Cloudflare_CL" in kql_query and "| take" not in kql_query.lower():
                logger.info(" Cloudflare query detected without limit - automatically adding | take 10000")
                kql_query = kql_query.rstrip() + " | take 10000"
            
            result = await azure_monitor_service.execute_kql_query(
                query=kql_query,
                workspace_id=workspace_id,
                user_info=user_info_for_query
            )
            
            if result.get("status") == "success":
                # Cache the successful KQL query for future use
                await advanced_query_cache.store_kql_query(
                    original_user_query,
                    cache_scope,
                    kql_query,
                    {"generated_by": "ai_service", "has_resource_context": bool(resource_context)},
                    cloudflare_mode
                )
                
                # Generate AI analysis of log results
                tables = result.get('tables', [])
                logger.info(f" Generating log analysis for {len(tables)} tables")
                
                # Check if we have data to analyze
                if not tables or len(tables) == 0:
                    logger.warning(" No tables in result, skipping AI analysis")
                    ai_analysis = "Query executed successfully but returned no data."
                else:
                    logger.info(f" First table has {len(tables[0].get('rows', []))} rows")
                    ai_analysis = await self._generate_log_analysis(
                        result,
                        kql_query,
                        ai_decision.get("reasoning")
                    )
                    logger.info(f" AI analysis generated: {len(ai_analysis)} chars")
                
                # Log Analytics returns {"status": "success", "tables": [...]}
                response = {
                    "success": True,
                    "message": ai_analysis,  # AI-generated natural language summary
                    "data": result,  # Pass through - contains "tables" array
                    "query_type": "logs",
                    "kql_query": kql_query,  # Include the generated KQL for debugging
                    "workspace_id": workspace_id,  # Include workspace for reference
                    "suggestions": ai_decision.get("suggestions", [])
                }
                logger.info(f" Returning response with {len(response['message'])} char message")
                return response
            else:
                err = result.get('message') or result.get('error') or 'no results'
                msg = await self._ai_explain_error(err, context=f"log analytics query")
                return {
                    "success": False,
                    "message": msg,
                    "suggestions": []
                }
                
        except Exception as e:
            error_str = str(e)
            logger.error(f"Log analytics execution failed: {e}", exc_info=True)

            # AUTO-HEAL: single AI self-correction pass, covering every error type
            if self.openai_client and kql_query:
                try:
                    fix_response = await self.openai_client.chat.completions.create(
                        model=self.settings.OPENAI_DEPLOYMENT_NAME,
                        messages=[
                            {"role": "system", "content": "You fix broken KQL queries for Azure Log Analytics. Return only the corrected KQL, no explanation."},
                            {"role": "user", "content": f"This KQL query failed:\n{kql_query}\n\nError: {error_str}\n\nFix the query. Keep the same intent."}
                        ],
                        max_completion_tokens=500
                    )
                    new_kql = fix_response.choices[0].message.content.strip()
                    if new_kql.startswith("```"):
                        new_kql = "\n".join(l for l in new_kql.split("\n") if not l.startswith("```")).strip()
                    if new_kql and new_kql != kql_query:
                        logger.info(f"Retrying with AI-corrected KQL: {new_kql[:100]}...")
                        retry_result = await azure_monitor_service.execute_kql_query(
                            query=new_kql,
                            workspace_id=workspace_id,
                            user_info=user_info_for_query
                        )
                        if retry_result.get("status") == "success":
                            original_query = ai_decision.get("reasoning", "")
                            ai_analysis = await self._generate_log_analysis(retry_result, new_kql, original_query)
                            return {
                                "success": True,
                                "message": ai_analysis,
                                "data": retry_result,
                                "query_type": "logs",
                                "kql_query": new_kql,
                                "workspace_id": workspace_id,
                                "auto_corrected": True,
                                "original_kql": kql_query
                            }
                except Exception as heal_error:
                    logger.error(f"LA auto-heal failed: {heal_error}")

            # Classify error for user-facing message only (no branching logic)
            from services.azure_monitor_service import SchemaError
            is_schema_error = isinstance(e, SchemaError) or "SEM0100" in error_str or "Failed to resolve" in error_str
            is_syntax_error = "SYN0002" in error_str or "Query could not be parsed" in error_str
            is_function_error = "SEM0260" in error_str or "Unknown function" in error_str
            
            # Function error: unknown/invalid function - fetch KQL function documentation
            return {
                "success": False,
                "message": "I had trouble executing your log query. Try rephrasing or simplifying your request.",
                "suggestions": (ai_decision.get("suggestions") or self._get_starter_suggestions())
            }
    
    def _calculate_metrics_interval(self, time_range: str) -> str:
        """
        Calculate appropriate metrics interval based on time range to optimize data points.
        Azure Monitor limits: max 1440 data points per metric query.
        Goal: ~200-300 data points for good chart resolution without hitting limits.
        
        Args:
            time_range: ISO 8601 duration (PT1H, PT24H, P7D, P30D, etc.)
            
        Returns:
            Appropriate interval string (PT1M, PT5M, PT15M, PT1H, PT6H, P1D)
        """
        import re
        
        # Parse time range to minutes
        total_minutes = 0
        
        # Handle PT format (hours and minutes)
        if time_range.startswith('PT'):
            hours_match = re.search(r'(\d+)H', time_range)
            minutes_match = re.search(r'(\d+)M', time_range)
            
            if hours_match:
                total_minutes += int(hours_match.group(1)) * 60
            if minutes_match:
                total_minutes += int(minutes_match.group(1))
        
        # Handle P format (days, weeks, months, years)
        elif time_range.startswith('P'):
            days_match = re.search(r'(\d+)D', time_range)
            weeks_match = re.search(r'(\d+)W', time_range)
            months_match = re.search(r'(\d+)M', time_range)
            years_match = re.search(r'(\d+)Y', time_range)
            
            if years_match:
                total_minutes += int(years_match.group(1)) * 365 * 24 * 60
            if months_match:
                total_minutes += int(months_match.group(1)) * 30 * 24 * 60
            if weeks_match:
                total_minutes += int(weeks_match.group(1)) * 7 * 24 * 60
            if days_match:
                total_minutes += int(days_match.group(1)) * 24 * 60
        
        # If parsing failed, default to 5 minutes
        if total_minutes == 0:
            return "PT5M"
        
        # Calculate interval to get ~250 data points (sweet spot)
        # Azure Monitor supports: PT1M, PT5M, PT15M, PT30M, PT1H, PT6H, PT12H, P1D
        
        if total_minutes <= 60:  # ≤ 1 hour
            return "PT1M"  # 1 minute → ~60 points
        elif total_minutes <= 360:  # ≤ 6 hours
            return "PT5M"  # 5 minutes → 72 points
        elif total_minutes <= 1440:  # ≤ 24 hours (1 day)
            return "PT5M"  # 5 minutes → 288 points
        elif total_minutes <= 10080:  # ≤ 7 days
            return "PT1H"  # 1 hour → 168 points
        elif total_minutes <= 43200:  # ≤ 30 days
            return "PT6H"  # 6 hours → 120 points
        else:  # > 30 days
            return "P1D"  # 1 day → varies
    
    async def _execute_metrics_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Azure Monitor metrics query from AI decision"""
        try:
            from .query_service import QueryService
            query_service = QueryService()
            
            metrics_spec = ai_decision.get("metrics", {})
            
            # Use subscription_ids from context
            subscription_ids = getattr(self, '_current_subscription_ids', [])
            
            # Check if multi-resource query
            if metrics_spec.get("multi_resource"):
                # Metrics support global scope - query across all accessible subscriptions
                if len(subscription_ids) > 1:
                    logger.info(f"Global scope enabled - querying metrics across {len(subscription_ids)} subscriptions")
                
                # Determine time range - use from metrics_spec if provided, otherwise default to 1 hour
                time_range_str = metrics_spec.get("time_range", "PT1H")  # Default: 1 hour
                if not isinstance(time_range_str, str):
                    time_range_str = "PT1H"  # Fallback if dict or other type
                
                # Calculate appropriate interval based on time range
                interval = self._calculate_metrics_interval(time_range_str)
                
                # Build structured query for multi-resource metrics
                structured_query = {
                    "type": "azure_metrics",
                    "multi_resource": True,
                    "resource_type": metrics_spec.get("resource_type", "microsoft.compute/virtualmachines"),
                    "metric_names": metrics_spec.get("metric_names", ["Percentage CPU"]),
                    "aggregation": metrics_spec.get("aggregation", "Average"),
                    "time_range": time_range_str,  # ISO 8601 duration (PT1H, PT6H, PT24H, etc.)
                    "interval": interval  # Auto-calculated based on time_range
                }
                
                result = await query_service.execute_query(
                    structured_query,
                    subscription_ids,
                    user_data
                )
                
                # Generate AI analysis of metrics
                ai_analysis = await self._generate_metrics_analysis(
                    result,
                    metrics_spec,
                    ai_decision.get("reasoning"),
                    is_multi_resource=True
                )
                
                return {
                    "success": True,
                    "message": ai_analysis,  # AI-generated analysis
                    "data": result,
                    "query_type": "metrics",
                    "suggestions": ai_decision.get("suggestions", [])
                }
            
            else:
                # Single resource metrics
                resource_id = metrics_spec.get("resource_id")
                
                if not resource_id:
                    return {
                        "success": False,
                        "message": "I need a specific resource to query metrics. Please select a resource or ask about 'all VMs'.",
                        "suggestions": ["Show all VMs", "Select a specific resource"]
                    }
                
                # Determine time range - use from metrics_spec if provided, otherwise default to 1 hour
                time_range_str = metrics_spec.get("time_range", "PT1H")  # Default: 1 hour
                if not isinstance(time_range_str, str):
                    time_range_str = "PT1H"  # Fallback if dict or other type
                
                # Calculate appropriate interval based on time range
                interval = self._calculate_metrics_interval(time_range_str)
                
                structured_query = {
                    "type": "azure_metrics",
                    "multi_resource": False,
                    "resource_id": resource_id,
                    "metric_names": metrics_spec.get("metric_names", ["Percentage CPU"]),
                    "aggregation": metrics_spec.get("aggregation", "Average"),
                    "time_range": time_range_str,  # ISO 8601 duration (PT1H, PT6H, PT24H, etc.)
                    "interval": interval  # Auto-calculated based on time_range
                }
                
                result = await query_service.execute_query(
                    structured_query,
                    subscription_ids,
                    user_data
                )
                
                # Generate AI analysis of metrics
                ai_analysis = await self._generate_metrics_analysis(
                    result,
                    metrics_spec,
                    ai_decision.get("reasoning"),
                    is_multi_resource=False
                )
                
                return {
                    "success": True,
                    "message": ai_analysis,  # AI-generated analysis
                    "data": result,
                    "query_type": "metrics",
                    "suggestions": ai_decision.get("suggestions", [])
                }
                
        except Exception as e:
            logger.error(f"Metrics execution failed: {e}", exc_info=True)
            
            # Extract the actual error from RetryError wrapper if present
            actual_error = e
            if hasattr(e, 'last_attempt') and hasattr(e.last_attempt, 'exception'):
                actual_error = e.last_attempt.exception()
            
            error_str = str(actual_error)
            
            # Get the originally requested metrics for better error messages
            requested_metrics = metrics_spec.get("metric_names", [])
            
            # Extract meaningful error information
            error_message = "Metrics query failed"
            contextual_suggestions = []
            
            # Check if it's a metric name error (already handled by AI matcher, but might still fail)
            if "Failed to find metric configuration" in error_str:
                # Extract metric name if possible (handles names with spaces like "Network In")
                import re
                metric_match = re.search(r'metric: ([^,]+),', error_str)
                if metric_match:
                    invalid_metric = metric_match.group(1).strip()
                    
                    # If multiple metrics were requested, provide context
                    if len(requested_metrics) > 1:
                        metrics_list = "**, **".join(requested_metrics)
                        error_message = f"One or more requested metrics (**{metrics_list}**) are not available for this resource type."
                    else:
                        error_message = f"The metric **{invalid_metric}** is not available for this resource type."
                    
                    contextual_suggestions = [
                        "Try asking 'what metrics are available for this resource?'",
                        "Ask about CPU, memory, or network metrics (e.g., RxBytes, TxBytes)",
                        "Check available metrics in Azure Portal"
                    ]
                else:
                    error_message = "The requested metric is not available for this resource type."
                    contextual_suggestions = [
                        "Try asking 'what metrics are available?'",
                        "Ask about common metrics like CPU or memory"
                    ]
            
            # Check for permission/access errors
            elif "AuthorizationFailed" in error_str or "does not have authorization" in error_str:
                error_message = "You don't have permission to access metrics for this resource."
                contextual_suggestions = [
                    "Check your Azure role assignments",
                    "Contact your admin for Monitoring Reader access",
                    "Try a different resource or subscription"
                ]
            
            # Check for resource not found
            elif "ResourceNotFound" in error_str or "not found" in error_str.lower():
                error_message = "The resource was not found or no longer exists."
                contextual_suggestions = [
                    "Verify the resource still exists in Azure",
                    "Try selecting a different resource",
                    "Check if the resource was deleted"
                ]
            
            # Check for invalid time range
            elif "time range" in error_str.lower() or "timespan" in error_str.lower():
                error_message = "The time range for the metrics query is invalid."
                contextual_suggestions = [
                    "Try a shorter time range",
                    "Ask for 'last hour' or 'last 24 hours'",
                    "Check Azure retention limits for this resource"
                ]
            
            # Generic error with partial details
            elif "BadRequest" in error_str or "Invalid" in error_str:
                error_message = f"Metrics query failed: {error_str[:200]}"
                contextual_suggestions = [
                    "Try rephrasing your question",
                    "Ask about specific metrics like 'CPU usage' or 'memory'",
                    "Check if the resource supports metrics"
                ]
            
            # If no specific error pattern matched, provide helpful context
            if not contextual_suggestions:
                contextual_suggestions = [
                    "Try asking about CPU or memory metrics",
                    "Ask 'what metrics are available for this resource?'",
                    "Select a different time range",
                    "Verify you have access to this resource"
                ]
            
            return {
                "success": False,
                "message": error_message,
                "suggestions": contextual_suggestions
            }
    
    async def _execute_health_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Azure Resource Health query from AI decision"""
        try:
            from .query_service import QueryService
            query_service = QueryService()
            
            # Use scope from context
            subscription_ids = getattr(self, '_current_subscription_ids', [])
            resource_group = getattr(self, '_current_resource_group', None)
            resource_id = getattr(self, '_current_resource_id', None)
            
            logger.info(
                f"🏥 AI executing health query - "
                f"Resource: {resource_id}, RG: {resource_group}, "
                f"Subs: {len(subscription_ids) if subscription_ids else 0}"
            )
            
            # Build structured query for resource health
            structured_query = {
                "type": "resource_health",
                "resource_id": resource_id,
                "resource_group": resource_group
            }
            
            result = await query_service.execute_query(
                structured_query,
                subscription_ids,
                user_data
            )
            
            # Get message from result (already formatted)
            message = result.get("message", ai_decision.get("message", "Resource health status retrieved"))
            
            # Get suggestions from AI decision or use defaults
            suggestions = ai_decision.get("suggestions", [])
            if not suggestions:
                if resource_id:
                    suggestions = [
                        "Show metrics for this resource",
                        "Check logs for this resource",
                        "View cost for this resource"
                    ]
                else:
                    suggestions = [
                        "Show detailed health status",
                        "View resource metrics",
                        "Check costs"
                    ]
            
            return {
                "success": result.get("success", True),
                "message": message,
                "data": result.get("data", result),
                "query_type": "resource_health",
                "suggestions": suggestions
            }
            
        except Exception as e:
            logger.error(f"Health query execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": "Failed to retrieve resource health",
                "suggestions": (ai_decision.get("suggestions") or self._get_starter_suggestions())
            }
    
    async def _execute_policy_compliance_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Azure Policy compliance query (reuses existing governance service)"""
        try:
            from .governance_service import GovernanceService
            governance_service = GovernanceService()
            
            # Use subscription_ids from context
            subscription_ids = getattr(self, '_current_subscription_ids', [])
            
            if not subscription_ids:
                return {
                    "success": False,
                    "message": "⚠️ No subscriptions selected. Please select subscriptions to check policy compliance.",
                    "suggestions": ["Select subscriptions first", "View security score", "Check resource health"]
                }
            
            logger.info(f" AI executing policy compliance check for {len(subscription_ids)} subscription(s)")
            
            # Call existing governance service (NO duplication!)
            compliance_data = await governance_service.get_policy_compliance(
                subscription_ids=subscription_ids,
                user_info=user_data
            )
            
            # Let AI explain the results
            if self.openai_client:
                explanation = await self._ai_explain_policy_compliance(compliance_data)
                message = explanation if explanation else f"Policy Compliance: {compliance_data.get('overall_compliance_percentage', 0):.1f}%"
            else:
                message = f"Policy Compliance: {compliance_data.get('overall_compliance_percentage', 0):.1f}%"
            
            # Get suggestions from AI or use defaults
            suggestions = ai_decision.get("suggestions", [])
            if not suggestions:
                if compliance_data.get("non_compliant_resources", 0) > 0:
                    suggestions = [
                        "Show non-compliant resources",
                        "Check security score",
                        "View governance recommendations"
                    ]
                else:
                    suggestions = [
                        "Check security score",
                        "View resource health",
                        "Show cost analysis"
                    ]
            
            return {
                "success": True,
                "message": message,
                "data": compliance_data,
                "query_type": "policy_compliance",
                "suggestions": suggestions
            }
            
        except Exception as e:
            logger.error(f"Policy compliance query failed: {e}", exc_info=True)
            msg = await self._ai_explain_error(str(e), context="Azure Policy compliance query")
            return {"success": False, "message": msg, "suggestions": ["Check Azure Policy permissions", "Try security score instead", "View resource health"]}

    async def _execute_security_score_from_ai(
        self,
        ai_decision: Dict[str, Any],
        user_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute Defender for Cloud security score via the governance service (same path as agentic)."""
        try:
            from .query_service import QueryService
            query_service = QueryService()

            subscription_ids = self._current_subscription_ids

            if not subscription_ids:
                return {
                    "success": False,
                    "message": "Please select a subscription to check the security score.",
                    "suggestions": ["Select a subscription first", "Check policy compliance", "View resource health"]
                }

            logger.info(f" Executing security score for {len(subscription_ids)} subscription(s)")

            result = await query_service.execute_query(
                {"type": "security_score"},
                subscription_ids,
                user_data
            )

            if not result.get("success"):
                err = result.get("message") or "Failed to retrieve security score"
                msg = await self._ai_explain_error(err, context="Microsoft Defender for Cloud security score")
                return {"success": False, "message": msg, "suggestions": ["Check Defender for Cloud permissions", "View policy compliance", "View resource health"]}

            suggestions = ai_decision.get("suggestions") or [
                "View security recommendations",
                "Check policy compliance",
                "Show unhealthy resources"
            ]

            return {
                "success": True,
                "message": result.get("message", ""),
                "data": result.get("data"),
                "query_type": "security_score",
                "suggestions": suggestions
            }

        except Exception as e:
            logger.error(f"Security score query failed: {e}", exc_info=True)
            msg = await self._ai_explain_error(str(e), context="Microsoft Defender for Cloud security score")
            return {"success": False, "message": msg, "suggestions": ["Check Defender permissions", "Try policy compliance instead", "View resource health"]}
    
    async def _ai_explain_policy_compliance(self, compliance_data: Dict[str, Any]) -> Optional[str]:
        """Let AI explain policy compliance results in natural language (NO hardcoded thresholds)"""
        if not self.openai_client:
            return None
        
        try:
            system_prompt = """You are an Azure governance expert. Explain Azure Policy compliance concisely in markdown with emoji and 1-2 actionable insights. 3-4 sentences max."""
            
            user_message = f"""Explain this Azure Policy compliance data:

Overall Compliance: {compliance_data.get('overall_compliance_percentage', 0):.1f}%
Compliant Resources: {compliance_data.get('compliant_resources', 0):,}
Non-Compliant Resources: {compliance_data.get('non_compliant_resources', 0):,}
Exempt Resources: {compliance_data.get('exempt_resources', 0):,}
Total Resources Evaluated: {compliance_data.get('total_resources', 0):,}

Provide a contextual explanation with appropriate emoji and actionable insights."""
            
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
            logger.warning(f"AI explanation failed: {e}")
            return None
    
    async def _generate_cost_analysis(
        self,
        cost_data: Dict[str, Any],
        grouping: str,
        user_intent: str
    ) -> str:
        """
        Use AI to generate natural language analysis of cost data
        
        Args:
            cost_data: The cost analysis result from cost service (AdvancedCostService format)
            grouping: The grouping dimension (ServiceName, ResourceGroupName, etc.)
            user_intent: The original AI reasoning for context
        """
        if not self.openai_client:
            return "Here are your cost results:"
        
        try:
            # Extract key information from cost data
            # AdvancedCostService returns flat structure with total_cost, currency, breakdown
            current_total = cost_data.get("total_cost", 0)
            currency = cost_data.get("currency", "USD")
            breakdown = cost_data.get("breakdown", [])
            date_range = cost_data.get("date_range", {})
            scopes_queried = cost_data.get("scopes_queried", 0)
            limitation_note = cost_data.get("limitation_note", "")
            
            # Query scope information (resource, resource_group, subscription, management_group)
            query_scope = cost_data.get("query_scope", "subscription")
            resource_name = cost_data.get("resource_name")
            resource_id = cost_data.get("resource_id")
            resource_group_name = cost_data.get("resource_group_name")
            
            # Management Group information (if queried via Management Group)
            is_management_group = cost_data.get("is_management_group", False)
            management_group_id = cost_data.get("management_group_id", None)
            subscription_count = cost_data.get("subscription_count", 0)
            metric_type = cost_data.get("metric_type", "ActualCost")
            query_method = cost_data.get("query_method", "individual_subscriptions")
            
            # No previous period data from AdvancedCostService (single period query)
            previous_total = 0
            trend = "current period"
            change_percent = 0
            insights = []
            
            # If we have old format (from original cost_service), use it
            if "current_period" in cost_data:
                current_total = cost_data.get("current_period", {}).get("total_cost", 0)
                previous_total = cost_data.get("previous_period", {}).get("total_cost", 0)
                currency = cost_data.get("current_period", {}).get("currency", "USD")
                analysis = cost_data.get("analysis", {})
                trend = analysis.get("trend", "stable")
                change_percent = analysis.get("change_percent", 0)
                insights = analysis.get("insights", [])
                
                # Get breakdown data from old format
                if grouping == "resource_group":
                    breakdown = cost_data.get("resource_group_breakdown", [])
                elif grouping == "resource":
                    breakdown = cost_data.get("resource_breakdown", [])
                else:  # service or none
                    breakdown = cost_data.get("service_breakdown", [])
            
            # Build context for AI
            breakdown_text = ""
            if breakdown:
                top_items = breakdown[:5]  # Top 5 items
                formatted_items = []
                for item in top_items:
                    # Use the helper method to get clean item name
                    label = self._get_item_name(item)
                    formatted_items.append(f"- {label}: {item.get('cost', 0):.2f} {currency}")
                
                breakdown_text = "\n".join(formatted_items)
            
            # Include daily trend for spike detection — pass raw data, let AI reason about the shape
            daily_trend = cost_data.get("daily_trend", [])
            daily_trend_text = ""
            if daily_trend and len(daily_trend) >= 3:
                daily_trend_text = "\n\nDAILY COSTS:\n"
                daily_trend_text += "\n".join(
                    f"- {entry.get('date', '')}: {entry.get('cost', 0):.2f} {currency}"
                    for entry in daily_trend
                )

            # Format date range if available
            date_info = ""
            if date_range:
                start = date_range.get("start", "")
                end = date_range.get("end", "")
                if start and end:
                    date_info = f"\n- Date Range: {start[:10]} to {end[:10]}"
            
            # Format scope info
            scope_info = ""
            
            # Check if filters were applied (AI-driven filtering)
            filters_applied = cost_data.get("filters_applied", False)
            filter_count = cost_data.get("filter_count", {})
            active_filters = cost_data.get("active_filters", {})
            
            # Just pass raw filter data to AI - let AI describe it naturally
            filter_info = ""
            if filters_applied and active_filters:
                filter_info = f"\n- Filters Applied: {active_filters}"
                filter_info += f"\n- Result Count: {filter_count.get('filtered', 0)} of {filter_count.get('original', 0)} total items"
            
            # Add query scope information (resource vs resource_group vs subscription)
            if query_scope == "resource" and resource_name:
                scope_info = f"\n- Query Scope: SINGLE RESOURCE ({resource_name})"
                if resource_group_name:
                    scope_info += f"\n- Resource Group: {resource_group_name}"
                scope_info += f"\n- IMPORTANT: This shows costs for ONE specific resource only, not the entire resource group"
            elif query_scope == "resource_group":
                scope_info = f"\n- Query Scope: Resource Group"
            elif is_management_group and management_group_id:
                scope_info = f"\n- Query Scope: Management Group"
                scope_info += f"\n- Query Method: Management Group (single API call)"
                scope_info += f"\n- Management Group: {management_group_id}"
                scope_info += f"\n- Subscriptions: {subscription_count} enabled subscriptions"
                scope_info += f"\n- Metric Type: {metric_type}"
            elif query_method == "billing_account_global":
                ba_count = cost_data.get("billing_accounts_queried", 1)
                scope_info = f"\n- Query Scope: Global (all accessible subscriptions)"
                scope_info += f"\n- Query Method: Billing Account (MCA-correct, {ba_count} billing account{'s' if ba_count != 1 else ''})"
                scope_info += f"\n- Subscriptions covered: {subscription_count} subscriptions"
                scope_info += f"\n- IMPORTANT: Tell the user costs shown cover exactly {subscription_count} of their accessible subscriptions"
            elif scopes_queried:
                scope_info = f"\n- Query Scope: Subscription{'s' if scopes_queried > 1 else ''}"
                scope_info += f"\n- Query Method: {query_method.replace('_', ' ').title()}"
                scope_info += f"\n- Subscriptions Queried: {scopes_queried}"
                scope_info += f"\n- Metric Type: {metric_type}"
                
                # Only add limitation note for non-MG queries
                if limitation_note and not is_management_group:
                    scope_info += f"\n- Note: {limitation_note.strip()}"
            
            context = f"""COST ANALYSIS REQUEST:
Total: {current_total:.2f} {currency}{date_info}{filter_info}{scope_info}
{"Previous: " + str(previous_total) + " " + currency + " | Change: " + str(change_percent) + "%" if previous_total > 0 else "Note: Single period query - no comparison data"}

TOP SERVICES:
{breakdown_text if breakdown_text else 'No breakdown available'}
{daily_trend_text}
USER INTENT: {user_intent}

Provide a concise cost analysis (max 150 words). No backticks for names or values."""
            
            system_message = "You are an Azure cost optimization expert. Provide detailed, actionable cost analysis in well-formatted markdown with proper sections (## Summary, ## Key Findings, ## Recommendations). DO NOT use backticks for service names, resource names, or values - use plain text or bold."
            
            # Add special instructions based on query context
            if previous_total == 0:
                system_message += " CRITICAL: This is a single-period query with NO previous period data. DO NOT mention 'no change', 'previous period', 'previous month', or any comparison. Focus only on the current period analysis."
            
            if query_scope == "resource" and resource_name:
                rg_context = f" in resource group {resource_group_name}" if resource_group_name else ""
                system_message += f" SCOPE: This query is for a SINGLE RESOURCE ({resource_name}{rg_context}). In Summary, say 'Resource: {resource_name} in resource group {resource_group_name}' with the actual resource group name. In Key Findings, list ONLY the resource name and cost amount - NO percentages, NO '100%', NO 'of total', NO 'of scoped cost'. Just: '{resource_name}: 154.63 SEK'. Avoid phrases like 'single resource' or 'scoped'."
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": context}
                ],
                max_completion_tokens=800  # Increased for comprehensive analysis
            )
            
            analysis_text = response.choices[0].message.content.strip()
            return analysis_text
            
        except Exception as e:
            logger.error(f"AI cost analysis generation failed: {e}", exc_info=True)
            # Fallback to simple message
            return f"Your costs total {current_total:.2f} {currency} for the current period, {trend} compared to the previous period."
    
    async def _generate_resource_analysis(
        self,
        result: Dict[str, Any],
        kql_query: str,
        user_intent: str,
        suggested_followups: list = None
    ) -> str:
        """Generate natural language analysis of Resource Graph results"""
        if not self.openai_client:
            total = result.get("total_records", 0)
            return f"Found {total} resource{'s' if total != 1 else ''}."
        
        try:
            results = result.get("results", [])
            total_records = result.get("total_records", len(results))
            
            if not results:
                return f"No results found."

            logger.info(f" Analyzing Resource Graph results: {total_records} total, {len(results)} in array")
            
            # Detect if this is an aggregated query (summarize count(), sum(), etc.)
            is_aggregated_query = False
            aggregated_result_values = {}
            if len(results) == 1 and results[0]:
                # Check for common aggregation column names
                aggregation_columns = [col for col in results[0].keys() 
                                     if col.endswith('_') and col.startswith(('count', 'sum', 'avg', 'min', 'max', 'dcount'))]
                if aggregation_columns:
                    is_aggregated_query = True
                    aggregated_result_values = {col: results[0][col] for col in aggregation_columns}
                    logger.info(f" Detected aggregated query result: {aggregated_result_values}")
            
            # UNIFIED ANALYSIS - Build aggregations like log analysis
            sample_results, relevant_fields = get_intelligent_sample(results, max_samples=ARG_MAX_SAMPLE_SIZE)
            logger.info(f" Got {len(sample_results)} samples with {len(relevant_fields)} relevant fields")
            
            # Build aggregations for each field
            aggregations = {}
            for field in relevant_fields:
                values = [r.get(field) for r in results if r.get(field)]
                if not values:
                    continue
                
                # Skip fields where values are consistently long (like IDs)
                avg_length = sum(len(str(v)) for v in values[:10]) / min(10, len(values))
                if avg_length > ARG_MAX_FIELD_LENGTH_FOR_AGGREGATION:
                    logger.info(f" Skipping aggregation for '{field}' (avg length: {avg_length:.0f} chars)")
                    continue
                
                # Count occurrences of each unique value
                value_counts = Counter(str(v) for v in values)
                
                # Keep top 5 most common values
                aggregations[field] = dict(value_counts.most_common(5))
            
            logger.info(f" Aggregations: {len(aggregations)} fields")
            
            # Build context for AI
            # For small result sets, pass full raw rows so AI can reason deeply
            # (priority, action, address ranges, definitive verdict).
            # For large results, aggregations are enough for pattern analysis.
            if total_records <= 20:
                context_data = {
                    "total_rows": total_records,
                    "rows": results[:total_records]
                }
            else:
                context_data = {
                    "total_rows": total_records,
                    "sampled_rows": len(sample_results),
                    "aggregations": aggregations
                }
            
            # Add aggregated result information if detected
            if is_aggregated_query:
                # For aggregated queries, ONLY send the final answer, not metadata
                context_data = {
                    "aggregated_result": aggregated_result_values
                }
            
            # Build sampling note only if data is sampled (skip for aggregated queries)
            sampling_note = ""
            if not is_aggregated_query and len(sample_results) < total_records:
                sampling_note = f"\n\n⚠️ MANDATORY: Start with: 📊 Analyzed **{len(sample_results)} sampled rows** from **{total_records} total entries**"
            
            # Add note for aggregated queries
            aggregation_note = ""
            if is_aggregated_query:
                aggregation_note = "\n\n🚨 AGGREGATION RESULT: Just state the number in Summary. Key Findings should have ONLY 1 bullet with the total (e.g., 'Total VMs: 1256'). No invented analysis."
            
            # Helper function to build context string
            def build_context(include_finding_3: bool = True) -> str:
                # For aggregated queries, use simple format
                if is_aggregated_query:
                    # Extract the actual number from aggregated_result_values
                    count_value = list(aggregated_result_values.values())[0] if aggregated_result_values else "unknown"
                    
                    return f"""User asked: "{user_intent}"
Count: {count_value}

Write exactly:
## Result
You have **{count_value}** [what the user asked about] 🚀"""
                
                # Regular format for non-aggregated queries
                findings = """- Finding 1 with **bold** numbers from aggregations
- Finding 2 with **bold** metrics"""
                if include_finding_3:
                    findings += "\n- Finding 3 if relevant"

                followup_hint = ""
                if suggested_followups:
                    followup_hint = "\n\nIf relevant, naturally weave 1-2 of these follow-up ideas into the closing of your response (do not list them as bullets, just mention them conversationally):\n" + "\n".join(f"- {s}" for s in suggested_followups)

                deep_reasoning_hint = ""
                if context_data.get("rows"):
                    deep_reasoning_hint = "\n\nYou have the full raw rows. Write a natural, expert response — no fixed format required. Reason over the actual data and give a direct, specific answer."

                if context_data.get("rows"):
                    return f"""Query: {user_intent}

DATA:
{json.dumps(context_data, separators=(',', ':'), default=serialize_datetime)}

{deep_reasoning_hint}{followup_hint}"""

                return f"""Query: {user_intent}

DATA:
{json.dumps(context_data, separators=(',', ':'), default=serialize_datetime)}{sampling_note}

Format your response:
## Summary
Brief overview with **bold** total count and emoji

## Key Findings
{findings}

Keep it concise (3-5 lines total).{followup_hint}"""
            
            # Build initial context
            context = build_context(include_finding_3=True)
            context_length = len(context)
            logger.info(f" Context size: {context_length} chars, {len(aggregations)} aggregations")
            
            # Reduce aggregations if context is too large
            if context_length > ARG_MAX_CONTEXT_LENGTH:
                logger.warning(f" Large context: {context_length} chars - reducing aggregations")
                # Keep only top N fields
                aggregations = dict(list(aggregations.items())[:ARG_REDUCED_AGGREGATION_COUNT])
                context_data["aggregations"] = aggregations
                context = build_context(include_finding_3=False)
                logger.info(f" Reduced context size: {len(context)} chars, {len(aggregations)} aggregations")
            
            logger.info(f" Calling OpenAI for unified Resource Graph analysis")
            
            # Use strict system message for aggregated queries
            system_message = "You are an Azure analyst. Follow the user's format instructions EXACTLY. Do not add extra analysis or bullet points. DO NOT use backticks for resource names or field values." if is_aggregated_query else "You are an Azure analyst. Provide structured analysis with markdown formatting: ## headings, **bold** numbers, bullet points. Use emojis for visual appeal. DO NOT use backticks for resource names, field names, or values - use plain text or bold."
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": context}
                ],
                max_completion_tokens=ARG_MAX_COMPLETION_TOKENS
            )
            
            analysis_result = response.choices[0].message.content if response.choices else None
            
            if not analysis_result or not analysis_result.strip():
                # Log only non-sensitive summary to avoid CodeQL issues
                model_name = getattr(response, "model", "unknown")
                finish_reason = response.choices[0].finish_reason if response.choices else "no_choices"
                logger.error(f" OpenAI returned empty content. Model: {model_name}, finish_reason: {finish_reason}")
                logger.error(f"Context length: {len(context)} chars (content not logged for privacy)")
                
                # Build a basic summary from aggregations instead of generic fallback
                summary_parts = [f"Found **{total_records}** resources matching your query."]
                if aggregations:
                    summary_parts.append("\n\n## Key Findings")
                    for field, values in list(aggregations.items())[:3]:  # Top 3 fields
                        top_value = list(values.items())[0] if values else None
                        if top_value:
                            summary_parts.append(f"- **{field}**: {top_value[0]} ({top_value[1]} occurrences)")
                
                return "\n".join(summary_parts)
            
            return analysis_result.strip()
            
        except Exception as e:
            logger.error(f"AI resource analysis failed: {e}", exc_info=True)
            total = result.get("total_records", 0)
            return f"Found {total} resource{'s' if total != 1 else ''}."
    
    async def _generate_best_practices_analysis(
        self,
        result: Dict[str, Any],
        kql_query: str,
        user_intent: str,
        scope_description: str = "subscription-wide"
    ) -> str:
        """
        Generate intelligent best practices analysis against Azure Well-Architected Framework
        Analyzes: Security, Cost Optimization, Reliability, Performance, Operational Excellence
        """
        if not self.openai_client:
            total = result.get("total_records", 0)
            return f"Found {total} resource{'s' if total != 1 else ''}."
        
        try:
            results = result.get("results", [])
            total_records = result.get("total_records", len(results))
            
            if not results:
                return "No resources found to analyze."
            
            logger.info(f" Analyzing {total_records} resources for best practices (scope: {scope_description})")
            
            # SMART SAMPLING: Limit subscription-wide scans to avoid overwhelming context
            is_subscription_wide = scope_description == "subscription-wide"
            max_samples = 10 if is_subscription_wide else 20
            
            # Recommendation: If subscription-wide with many resources, strongly suggest filtering
            filtering_recommendation = ""
            if is_subscription_wide and total_records > max_samples:
                filtering_recommendation = (
                    "\n\n⚠️ **RECOMMENDATION**: You have **{} resources** in this subscription. "
                    "For comprehensive analysis, **please filter by Resource Group or select a specific resource** using the filter panel. "
                    "This analysis covered only **{} sample resources**.".format(total_records, max_samples)
                )
            elif total_records > 100 and not is_subscription_wide:
                filtering_recommendation = (
                    "\n\n⚠️ **NOTE**: This scope has many resources. "
                    "For more detailed analysis, consider narrowing the filter further."
                )
            
            # Sample resources intelligently (focus on different types)
            sample_results, _ = get_intelligent_sample(results, max_samples=max_samples)
            
            if is_subscription_wide and total_records > max_samples:
                logger.info(f" Subscription-wide scan limited to {max_samples} samples (total: {total_records}). User should filter by RG or resource.")
            
            # Build detailed context with full properties for thorough analysis
            context = f"""You are an Azure Well-Architected Framework expert. Analyze the following Azure resources for best practices violations and provide actionable recommendations.

USER REQUEST: {user_intent}

TOTAL RESOURCES: {total_records}
SAMPLE SIZE: {len(sample_results)} {'(limited sample - recommend filtering by Resource Group)' if is_subscription_wide and total_records > max_samples else '(representative sample)'}

RESOURCES TO ANALYZE:
{json.dumps(sample_results, indent=2, default=serialize_datetime)}

ANALYSIS FRAMEWORK:
Evaluate against these pillars:

1. 🔒 **Security**:
   - Public network access enabled (should be disabled/use private endpoints)
   - Weak TLS versions (< 1.3)
   - Missing encryption settings
   - Access keys not disabled where Entra ID is available
   - Network security gaps

2. 💰 **Cost Optimization**:
   - Oversized SKUs (Basic, Premium when Standard may suffice)
   - Unused/unattached resources
   - Missing autoscaling
   - Development SKUs in production environments

3. 🛡️ **Reliability**:
   - No high availability (Basic SKUs have no SLA)
   - Missing backups or redundancy
   - Single availability zone
   - No disaster recovery plan

4. ⚡ **Performance**:
   - Old/deprecated versions
   - Autoscaling disabled
   - Resource limits too low
   - Suboptimal configurations

5. 📋 **Operational Excellence**:
   - Missing or inconsistent tags
   - Poor naming conventions
   - No monitoring/diagnostics enabled

SCOPE: {len(sample_results)} of {total_records} resources ({scope_description}).{filtering_recommendation}

Format as structured markdown organized by WAF pillar. For each finding use the actual resource name (never a placeholder), describe the issue, and give a concrete action step. Use **bold** for resource names, *italic* for property names and values. No backticks, no code blocks.

Start analyzing now:"""
            
            context_size = len(context)
            logger.info(f" Calling OpenAI for Well-Architected Framework analysis (context: {context_size} chars, ~{context_size/4} tokens)")
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": "You are an Azure architect specialized in the Well-Architected Framework. Provide detailed, actionable analysis with specific resource names and recommendations."},
                    {"role": "user", "content": context}
                ],
                max_completion_tokens=2000  # Increased for detailed analysis
            )
            
            # Log response details for debugging
            if response.choices:
                finish_reason = response.choices[0].finish_reason
                logger.info(f"OpenAI response finish_reason: {finish_reason}")
                if finish_reason == "length":
                    logger.warning(" OpenAI response was truncated due to token limit")
                elif finish_reason == "content_filter":
                    logger.error(" OpenAI response blocked by content filter")
            
            analysis_result = response.choices[0].message.content if response.choices else None
            
            if not analysis_result or not analysis_result.strip():
                logger.error(f"Empty response from OpenAI for best practices analysis. Response object: {response}")
                return f"Analyzed {total_records} resources ({scope_description}). No issues detected or analysis failed.{filtering_recommendation}"
            
            return analysis_result.strip()
            
        except Exception as e:
            logger.error(f"Best practices analysis failed: {e}", exc_info=True)
            total = result.get("total_records", 0)
            return f"Scanned {total} resources ({scope_description}) but detailed analysis failed. Please check logs."
    
    async def _generate_security_score_analysis(
        self,
        results: List[Dict[str, Any]],
        total_records: int,
        user_intent: str
    ) -> str:
        """Generate specialized analysis for security score queries and assessments"""
        try:
            # Detect if this is assessments (recommendations) vs scores by checking first result
            if results and ('severity' in results[0] or 'displayName' in results[0]):
                # This is security assessments/recommendations
                by_severity = {'High': 0, 'Medium': 0, 'Low': 0}
                by_status = {'Unhealthy': 0, 'Healthy': 0, 'NotApplicable': 0}
                
                for r in results:
                    severity = r.get('severity', 'Low')
                    status = r.get('status') or r.get('statusCode', 'Unknown')
                    if severity in by_severity:
                        by_severity[severity] += 1
                    if status in by_status:
                        by_status[status] += 1
                
                context = f"""
SECURITY RECOMMENDATIONS ANALYSIS:
User Query: {user_intent}

RESULTS:
- Total Recommendations: {total_records}
- By Severity: High={by_severity['High']}, Medium={by_severity['Medium']}, Low={by_severity['Low']}
- By Status: Unhealthy={by_status['Unhealthy']}, Healthy={by_status['Healthy']}, NotApplicable={by_status['NotApplicable']}

Generate a clear security recommendations summary with:
1. Total count with emoji (🛡️ for security)
2. Severity and status breakdown
3. Key insight about what needs attention
4. Actionable recommendation on what to prioritize
5. **DO NOT use backticks** for resource names, recommendation names, or values - use plain text or bold
6. Keep it informative (5-7 lines)

Use markdown formatting with **bold** for numbers."""
            else:
                # This is security scores with percentages
                total_current = sum(float(r.get('currentScore', 0)) for r in results)
                total_max = sum(float(r.get('maxScore', 0)) for r in results)
                percentages = [float(r.get('percentage', 0)) for r in results if r.get('percentage')]
                
                avg_percentage = sum(percentages) / len(percentages) if percentages else 0
                min_percentage = min(percentages) if percentages else 0
                max_percentage = max(percentages) if percentages else 0
                
                context = f"""
SECURITY SCORE ANALYSIS:
User Query: {user_intent}

RESULTS:
- Total Subscriptions: {total_records}
- Average Security Score: {avg_percentage:.1f}%
- Lowest Score: {min_percentage:.1f}%
- Highest Score: {max_percentage:.1f}%
- Aggregated: {total_current:.1f}/{total_max:.1f} points

DISTRIBUTION:
{self._format_security_distribution(results)}

Generate a clear security posture assessment with:
1. Overall emoji and score summary (use 🛡️ 🔐 ⚠️ ❌ based on average)
2. Key insight about the security posture
3. Actionable recommendation
4. **DO NOT use backticks** for subscription names or values - use plain text or bold
5. Keep it informative (5-7 lines)

Use markdown formatting with **bold** for numbers."""

            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": "You are a Microsoft Defender for Cloud security expert. Analyze security data and provide actionable insights. DO NOT use backticks for resource names, subscription names, or values."},
                    {"role": "user", "content": context}
                ],
                max_completion_tokens=300
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"Security score analysis failed: {type(e).__name__}")
            return f"Found {total_records} subscription{'s' if total_records != 1 else ''} matching your security criteria."
    
    async def _generate_log_analysis(
        self,
        result: Dict[str, Any],
        kql_query: str,
        user_intent: str
    ) -> str:
        """Generate natural language analysis of Log Analytics results with intelligent pre-aggregation"""
        logger.info(f" _generate_log_analysis called with user_intent: {user_intent[:100]}...")
        
        if not self.openai_client:
            logger.warning(" OpenAI client not available, returning default message")
            return "Here are your log query results."
        
        try:
            from services.log_analysis_service import log_analysis_service
            
            tables = result.get("tables", [])
            total_rows = sum(len(table.get("rows", [])) for table in tables)
            logger.info(f" Processing {len(tables)} tables with {total_rows} total rows")
            
            # Convert table format to list of dicts for analysis
            results_list = []
            if tables:
                first_table = tables[0]
                columns = [col.get('name', '') for col in first_table.get("columns", [])]
                rows = first_table.get("rows", [])
                logger.info(f" First table: {len(columns)} columns, {len(rows)} rows")
                
                # Convert rows to dicts
                for row in rows:
                    results_list.append(dict(zip(columns, row)))
            
            # Use intelligent pre-aggregation
            if results_list:
                logger.info(f" Preparing {len(results_list)} results for AI analysis")
                analysis_data = log_analysis_service.prepare_for_ai_analysis(
                    results_list, 
                    kql_query,
                    max_sample_rows=20
                )
                logger.info(f" Analysis data prepared: {len(str(analysis_data))} chars")
            else:
                logger.warning(" No results_list, using empty analysis data")
                analysis_data = {"total_rows": 0, "message": "No results"}
            
            # Check if sampled for disclosure
            is_sampled = analysis_data.get("is_sampled", False)
            sampled_rows = analysis_data.get("sampled_rows", 0)
            total_rows = analysis_data.get("total_rows", 0)

            sampling_note = ""
            if is_sampled:
                sampling_note = f"\n\nNOTE: is_sampled=true, sampled_rows={sampled_rows}, total_rows={total_rows}"

            data_json = json.dumps(analysis_data, separators=(',', ':'), default=serialize_datetime)
            # Hard cap: keep context well within model limits so there's room for output
            if len(data_json) > 15000:
                # Trim sample_entries to reduce size while keeping aggregations intact
                trimmed = analysis_data.copy()
                trimmed["sample_entries"] = analysis_data.get("sample_entries", [])[:5]
                data_json = json.dumps(trimmed, separators=(',', ':'), default=serialize_datetime)
                # Last resort: truncate raw JSON if still too large
                if len(data_json) > 15000:
                    data_json = data_json[:15000] + "...truncated"

            context = f"""USER INTENT: {user_intent}
QUERY: {kql_query}{sampling_note}

DATA:
{data_json}
"""
            
            from prompts.kql_generation_prompts import RESULTS_EXPLANATION_SYSTEM_PROMPT
            
            logger.info(f" Calling OpenAI with model: {self.settings.OPENAI_DEPLOYMENT_NAME}")
            logger.info(f" Context length: {len(context)} chars")
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": RESULTS_EXPLANATION_SYSTEM_PROMPT},
                    {"role": "user", "content": context}
                ],
                max_completion_tokens=1500
            )
            
            ai_response = response.choices[0].message.content
            if ai_response:
                ai_response = ai_response.strip()
            else:
                # Log only non-sensitive summary to avoid CodeQL issues
                model_name = getattr(response, "model", "unknown")
                finish_reason = response.choices[0].finish_reason if response.choices else "no_choices"
                logger.error(f" OpenAI returned None/empty content. Model: {model_name}, finish_reason: {finish_reason}")
                ai_response = ""
            
            logger.info(f" OpenAI response received: {len(ai_response)} chars")
            return ai_response
            
        except Exception as e:
            logger.error(f"AI log analysis failed: {e}", exc_info=True)
            return "Here are your log query results."
    
    async def _generate_metrics_analysis(
        self,
        result: Dict[str, Any],
        metrics_spec: Dict[str, Any],
        user_intent: str,
        is_multi_resource: bool = False
    ) -> str:
        """Generate natural language analysis of Azure Monitor metrics"""
        if not self.openai_client:
            return "Here are the metrics results."
        
        try:
            metric_names = metrics_spec.get("metric_names", [])
            aggregation = metrics_spec.get("aggregation", "Average")
            
            # Try to extract metric values for analysis
            metric_summary = ""
            if isinstance(result, dict):
                if "results" in result:
                    results_list = result.get("results", [])
                    
                    # Check if this is multi-resource format (list of resource summaries)
                    # or single-resource format (list of metrics with timeseries)
                    if results_list and isinstance(results_list, list):
                        first_item = results_list[0]
                        
                        # Single resource format: list of metrics with timeseries
                        if "timeseries" in first_item:
                            # Extract data from the first metric
                            first_metric = first_item
                            timeseries = first_metric.get("timeseries", [])
                            if timeseries:
                                data_points = timeseries[0].get("data", [])
                                if data_points:
                                    # Get recent values
                                    recent_values = [dp.get(aggregation.lower(), 0) for dp in data_points[-5:] if dp.get(aggregation.lower()) is not None]
                                    if recent_values:
                                        avg_value = sum(recent_values) / len(recent_values)
                                        max_value = max(recent_values)
                                        min_value = min(recent_values)
                                        metric_summary = f"Recent {aggregation}: avg={avg_value:.2f}, max={max_value:.2f}, min={min_value:.2f}"
                        else:
                            # Multi-resource format
                            metric_summary = f"Results from {len(results_list)} resources"
                elif "value" in result:
                    # Azure Monitor API direct response format
                    values = result.get("value", [])
                    if values:
                        first_metric = values[0]
                        timeseries = first_metric.get("timeseries", [])
                        if timeseries:
                            data_points = timeseries[0].get("data", [])
                            if data_points:
                                # Get recent values
                                recent_values = [dp.get(aggregation.lower(), 0) for dp in data_points[-5:] if dp.get(aggregation.lower()) is not None]
                                if recent_values:
                                    avg_value = sum(recent_values) / len(recent_values)
                                    max_value = max(recent_values)
                                    min_value = min(recent_values)
                                    metric_summary = f"Recent {aggregation}: avg={avg_value:.2f}, max={max_value:.2f}, min={min_value:.2f}"
            
            resource_info = "multiple resources" if is_multi_resource else "the selected resource"
            
            # Extract time range for context
            time_range = metrics_spec.get("time_range", "PT1H")
            time_context = {
                "PT1H": "over the last hour",
                "PT6H": "over the last 6 hours", 
                "PT12H": "over the last 12 hours",
                "PT24H": "over the last 24 hours",
                "P1D": "over the last day",
                "P7D": "over the last 7 days"
            }.get(time_range, "during the selected time range")
            
            context = f"""
USER INTENT: {user_intent}
RESOURCE: {resource_info}
METRICS: {', '.join(metric_names)}
AGGREGATION: {aggregation}
TIME RANGE: {time_context}

RESULTS SUMMARY:
{metric_summary or 'Metric data retrieved'}

Generate a formatted markdown summary following these rules:

STRUCTURE:
1. Opening: Metric name + resource + time range
2. Stats: Average, Peak, Minimum with **bold** values
3. Pattern: Describe the trend/pattern you observe (stable, increasing, spike at X, gradual decline)
4. Insight: ONE actionable observation (avoid subjective terms like "high/low" unless you have baseline context)

CRITICAL RULES:
- NEVER say "high" or "low" without numerical context (e.g., % of max capacity)
- ALWAYS calculate and show variance: (max - min) / average * 100
- ALWAYS mention specific patterns in the data (spikes, trends, time of occurrence)
- **DO NOT use backticks** for metric names, resource names, or values - use plain text or bold
- Keep total response to 3-4 lines maximum
- Use emojis: 📊 for metrics, 📈 trending up, 📉 trending down, ⚡ spikes, ✅ stable, ⚠️ attention needed

GOOD EXAMPLE:
📊 **Active Connections** for PostgreSQL server {time_context}:
- Average: **225** | Peak: **238** (+5.8%) | Low: **212** (-5.8%) | Variance: **±11.7%**
- Pattern: Gradual decline (224→211) then spike to 238 at 09:39, now stabilizing
- ✅ Stable workload with normal fluctuation; monitor if spikes become frequent

BAD EXAMPLE (don't do this):
⚠️ Connection count is consistently high but stable
🎯 Overall looks normal, but nearing upper ranges
"""
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": "You are an Azure monitoring expert. Provide formatted markdown summaries of metrics with interpretation. DO NOT use backticks for metric names, resource names, or values."},
                    {"role": "user", "content": context}
                ],
                max_completion_tokens=200
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.error(f"AI metrics analysis failed: {e}", exc_info=True)
            return "Here are the metrics results."
    
    def _extract_table_name(self, kql_query: str) -> Optional[str]:
        """Extract the primary table name from a KQL query"""
        try:
            import re
            # Match common patterns: TableName | ..., TableName\n|, search in (TableName), etc.
            patterns = [
                r'^(\w+)\s*\|',  # TableName |
                r'search\s+in\s*\(\s*(\w+)\s*\)',  # search in (TableName)
                r'union\s+(\w+)',  # union TableName
                r'^(\w+)\s*$',  # Just table name
                r'^(\w+)\s+\|',  # TableName | with space
            ]
            
            for pattern in patterns:
                match = re.search(pattern, kql_query.strip(), re.IGNORECASE | re.MULTILINE)
                if match:
                    table_name = match.group(1)
                    logger.info(f"Extracted table name: {table_name}")
                    return table_name
            
            logger.warning(f"Could not extract table name from KQL: {kql_query[:100]}")
            return None
        except Exception as e:
            logger.error(f"Error extracting table name: {e}")
            return None

    async def _ai_explain_error(self, error: str, context: str = "", user_query: str = "") -> str:
        """Ask the AI to explain an error and suggest next steps. Falls back to the raw error if AI is unavailable."""
        if not self.openai_client:
            return error
        try:
            prompt = (
                f"An error occurred while processing the user's Azure query.\n\n"
                f"User query: {user_query or getattr(self, '_current_user_query', 'unknown')}\n"
                f"Context: {context}\n"
                f"Error: {error}\n\n"
                "In 2-4 sentences, explain what went wrong in plain language and suggest what the user can try next. "
                "Do not repeat the raw error message. Be specific and actionable."
            )
            resp = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=200,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return error

    async def _handle_general_request(self, user_query: str, user_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Handle any general request that's not Azure-specific"""
        # This is explicitly a general request (code gen, help), so pass ai_intent to bypass keyword checks
        is_safe, reason = query_validator.validate_natural_language_query(user_query, ai_intent='general_response')
        if not is_safe:
            return {
                "success": False,
                "message": f"⚠️ {reason}"
            }
        
        conversation_history = (user_data or {}).get('conversation_history', [])
        return await self._generate_code_response(user_query, {}, conversation_history)
    

    
    async def _generate_code_response(self, user_query: str, ai_decision: Dict[str, Any], conversation_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generate any type of code or provide helpful responses"""
        if not self.openai_client:
            return {"success": False, "message": "AI service not available"}
        
        try:
            # Check if this is an identity question
            query_lower = user_query.lower()
            is_identity_question = any(phrase in query_lower for phrase in [
                'who are you', 'what are you', 'what can you do', 'what do you do',
                'introduce yourself', 'your name', 'tell me about yourself',
                'your capabilities', 'what are your capabilities', 'help me understand you'
            ])
            
            # Build system message with CIRRUS identity
            system_message = """You are CIRRUS, an AI assistant for Azure infrastructure, created by Mohammad Zeyaul Mustafa (GitHub: @mustafazeya). Read-only — cannot create, modify, or delete resources.

Capabilities: Azure resource management, cost analysis, log analytics, metrics, policy compliance, security posture, Well-Architected Framework reviews, agentic investigative analysis, and general DevOps help (Terraform, GitHub Actions, Docker, coding).

Format code in markdown code blocks with language tags. Use **bold** for emphasis, never backticks for non-code text."""
            
            # Build messages with conversation context
            messages = [{"role": "system", "content": system_message}]
            
            # Add compressed conversation context only if needed
            if conversation_history and self._needs_context(user_query) and not is_identity_question:
                context_summary = self._compress_conversation_context(conversation_history)
                if context_summary:
                    messages.append({"role": "system", "content": f"Context: {context_summary}"})
            
            messages.append({"role": "user", "content": user_query})
            
            # Detect if this is a code generation request that needs more tokens
            is_code_generation = any(keyword in query_lower for keyword in [
                'terraform', 'bicep', 'github action', 'workflow', 'infrastructure', 
                'iac', 'pipeline', 'deployment', 'dockerfile', 'kubernetes', 'helm'
            ])
            
            # Allocate more tokens for code generation (infrastructure code can be long)
            max_tokens = 4000 if is_code_generation else 1500
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=messages,
                max_completion_tokens=max_tokens
            )
            
            # Reuse suggestions produced by the classification call (call #1) — they are
            # already contextual and AI-generated.  Only fall back to a dedicated suggestions
            # call when the classification call produced none (shouldn't normally happen).
            if ai_decision.get("suggestions"):
                suggestions = ai_decision["suggestions"]
            elif is_identity_question:
                suggestions = self._get_starter_suggestions()
            else:
                suggestions = await self._generate_contextual_suggestions(conversation_history, user_query)
            
            return {
                "success": True,
                "message": response.choices[0].message.content.strip(),
                "query_type": "general_response",
                "suggestions": suggestions
            }
            
        except Exception as e:
            logger.error(f"Code generation failed: {e}")
            return {"success": False, "message": "Failed to generate response"}
    
    async def _generate_contextual_suggestions(self, conversation_history: List[Dict[str, Any]], current_query: str) -> List[str]:
        """Generate contextual suggestions using AI based on conversation history"""
        if not self.openai_client:
            return ["Show Azure resources", "Analyze costs", "Query logs", "List virtual machines"]
        
        try:
            # Build context from current query and any conversation history
            context_parts = [f"Current query: {current_query}"]
            
            if conversation_history:
                recent_context = self._compress_conversation_context(conversation_history[-2:])
                if recent_context:
                    context_parts.append(f"Previous context: {recent_context}")
            
            # Detect what type of request this is
            query_lower = current_query.lower()
            if any(word in query_lower for word in ['terraform', 'github', 'workflow', 'function app', 'code']):
                # This is a code generation request
                context_parts.append("Type: Code generation request")
            
            full_context = "\n".join(context_parts)
            
            prompt = f"""Generate 4 follow-up suggestions based on this context (one per line, under 6 words each, use verbs: show/list/view/analyze/review — not create/delete/modify):

{full_context}

Return only the 4 suggestions, one per line."""
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=100
            )
            
            suggestions = [s.strip() for s in response.choices[0].message.content.strip().split('\n') if s.strip()]
            return suggestions[:4] if suggestions else ["Generate Python function code", "Build Docker configuration", "Enhance this solution", "Show Azure resources"]
            
        except Exception as e:
            logger.error(f"Failed to generate contextual suggestions: {e}")
            return ["Generate Python function code", "Build Docker configuration", "Enhance this solution", "Show Azure resources"]
    
    def _needs_context(self, user_query: str) -> bool:
        """Determine if query needs conversation context"""
        context_keywords = ['this', 'that', 'it', 'enhance', 'improve', 'explain', 'modify', 'above', 'previous']
        return any(keyword in user_query.lower() for keyword in context_keywords)
    
    def _compress_conversation_context(self, messages: List[Dict[str, Any]]) -> str:
        """Compress conversation history to essential context only"""
        if not messages:
            return ""
        
        # For suggestions, we need more context about what was actually discussed
        context_parts = []
        for msg in messages[-2:]:  # Only last 2 messages
            content = msg.get("content", "")
            role = msg.get("role", "user")
            
            if role == "user":
                # Keep user queries short but meaningful
                context_parts.append(f"User: {content[:100]}")
            elif role == "assistant":
                # Extract what type of response was given
                if "```terraform" in content:
                    context_parts.append("AI: Generated Terraform module")
                elif "```yaml" in content or "workflow" in content.lower():
                    context_parts.append("AI: Generated GitHub workflow")
                elif "```python" in content:
                    context_parts.append("AI: Generated Python code")
                elif "```" in content:
                    context_parts.append("AI: Generated code")
                else:
                    # Keep first line of AI response for context
                    first_line = content.split('\n')[0][:80]
                    context_parts.append(f"AI: {first_line}")
        
        return "\n".join(context_parts) if context_parts else ""
    
    def _is_empty_result(self, action: str, data: Dict[str, Any]) -> bool:
        """Detect whether an action returned meaningfully empty data worth retrying."""
        if action in ("resource_graph", "log_analytics"):
            return not data.get("results") and not data.get("total_records")
        if action == "cost_analysis":
            return not data.get("breakdown") and data.get("total_cost", 0) == 0
        if action == "azure_metrics":
            metrics = data.get("metrics") or data.get("data") or {}
            if isinstance(metrics, dict):
                return not metrics.get("timeseries") and not metrics.get("value")
            return False
        if action == "resource_health":
            resources = data.get("resources") or data.get("health_data", [])
            return isinstance(resources, list) and len(resources) == 0
        return False

    def _build_data_digest(self, query_result: Optional[Dict[str, Any]]) -> str:
        """Build a compact inline summary of a previous query result for conversation context."""
        if not query_result:
            return ""
        lines = []

        # Resource Graph / Log Analytics
        results = query_result.get("results", [])
        total = query_result.get("total_records", len(results) if results else 0)
        if results:
            lines.append(f"\n\n📋 [Previous data: {total} records]")
            for r in results[:10]:
                name = r.get("name") or r.get("resourceGroup") or r.get("type", "")
                loc = r.get("location", "")
                rg = r.get("resourceGroup", "")
                parts = [p for p in [name, loc, rg] if p]
                if parts:
                    lines.append(f"  - {' / '.join(parts)}")
            if total > 10:
                lines.append(f"  ... and {total - 10} more")

        # Cost analysis breakdown
        breakdown = query_result.get("breakdown", [])
        total_cost = query_result.get("total_cost")
        currency = query_result.get("currency", "")
        if breakdown and total_cost is not None:
            lines.append(f"\n\n💰 [Previous cost: {total_cost:.2f} {currency}]")
            for item in breakdown[:6]:
                label = (
                    item.get("name") or item.get("servicename")
                    or item.get("resourcegroup", "")
                )
                cost = item.get("cost", 0)
                lines.append(f"  - {label}: {cost:.2f} {currency}")
            if len(breakdown) > 6:
                lines.append(f"  ... and {len(breakdown) - 6} more")

        # Cost comparison
        comparison = query_result.get("comparison")
        if comparison and isinstance(comparison, dict):
            diff = query_result.get("difference", 0)
            pct = query_result.get("percent_change", 0)
            direction = "increase" if diff > 0 else "decrease" if diff < 0 else "no change"
            lines.append(f"\n\n📊 [Previous comparison: {abs(pct):.1f}% {direction}]")
            for period, pdata in list(comparison.items())[:2]:
                pc = pdata.get("total_cost", 0)
                cur = pdata.get("currency", "")
                lines.append(f"  - {period}: {pc:.2f} {cur}")

        # Azure Metrics
        metrics_data = query_result.get("metrics") or query_result.get("data") or {}
        if isinstance(metrics_data, dict):
            metric_values = metrics_data.get("value") or metrics_data.get("timeseries", [])
            if metric_values and isinstance(metric_values, list):
                lines.append(f"\n\n📈 [Previous metrics: {len(metric_values)} series]")
                for mv in metric_values[:3]:
                    mname = mv.get("name", {}).get("value", "") if isinstance(mv.get("name"), dict) else mv.get("name", "")
                    if mname:
                        lines.append(f"  - {mname}")

        # Resource health
        health_resources = query_result.get("resources") or query_result.get("health_data", [])
        if health_resources and isinstance(health_resources, list):
            lines.append(f"\n\n🏥 [Previous health: {len(health_resources)} resources]")
            for h in health_resources[:6]:
                hname = h.get("name", "")
                hstatus = h.get("availability_state") or h.get("status", "")
                if hname:
                    lines.append(f"  - {hname}: {hstatus}")
            if len(health_resources) > 6:
                lines.append(f"  ... and {len(health_resources) - 6} more")

        digest = "".join(lines)
        return digest[:700]  # Hard cap to stay within token budget

    def _get_starter_suggestions(self) -> List[str]:
        """Get generic starter suggestions"""
        return [
            "Show all my virtual machines",
            "What are my costs this month?",
            "Show recent errors in logs",
            "List all storage accounts"
        ]

    async def _get_user_workspaces(self, user_data: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Get workspaces accessible to the user
        TODO: Implement actual workspace discovery
        """
        return [
            {"id": "workspace-1", "name": "Production-Workspace", "subscription": "prod-sub"},
            {"id": "workspace-2", "name": "Dev-Workspace", "subscription": "dev-sub"}
        ]
    
    async def _check_workspace_access(self, user_data: Dict[str, Any], workspace_id: str) -> bool:
        """
        Check if user has access to the workspace
        TODO: Implement actual permission checking
        """
        return True

    async def analyze_query_results(self, results_data: Dict[str, Any], original_query: str = "", query_type: str = "kql") -> Dict[str, Any]:
        """
        Analyze query results using AI to provide intelligent insights
        
        Args:
            results_data: The actual query results data
            original_query: The original query that generated these results
            query_type: Type of query (kql, natural, etc.)
            
        Returns:
            Dictionary containing AI analysis and insights
        """
        try:
            if not self.openai_client:
                logger.warning("OpenAI client not initialized, cannot analyze results")
                return {"success": False, "message": "AI analysis not available"}
                
            logger.info(f" Starting AI analysis of query results")
            
            # Prepare data for analysis - create a compact summary instead of raw data
            sample_data = results_data.get('results', [])[:3] if isinstance(results_data.get('results'), list) else []
            row_count = results_data.get('row_count', len(sample_data))
            
            # Create compact data summary
            data_summary = self._create_compact_data_summary(sample_data, row_count)
            
            analysis_prompt = f"""Query: {original_query}
Total results: {row_count}

{data_summary}

Analyze this Azure data and provide actionable insights. No backticks for names or values. Max 150 words."""

            logger.info(f" Sending analysis request to AI")
            
            response = await self.openai_client.chat.completions.create(
                model=self.settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": "You are an Azure cloud analyst. Provide actionable insights from the data."},
                    {"role": "user", "content": analysis_prompt}
                ],
                max_completion_tokens=800
            )
            
            analysis = response.choices[0].message.content.strip()
            logger.info(f" Generated AI analysis: {analysis[:100]}...")
            
            return {
                "success": True,
                "analysis": analysis,
                "data_points_analyzed": len(sample_data),
                "total_results": row_count
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze results with AI: {e}")
            return {
                "success": False, 
                "message": "AI analysis failed",
                "fallback_analysis": self._create_fallback_analysis(results_data, original_query)
            }
    
    def _create_compact_data_summary(self, sample_data: list, row_count: int) -> str:
        """Create a compact summary of the data for AI analysis"""
        if not sample_data:
            return "No sample data available"
        
        summary = f"Total Records: {row_count}\n"
        
        # Get column names
        if sample_data:
            columns = list(sample_data[0].keys())
            summary += f"Columns ({len(columns)}): {', '.join(columns[:10])}\n"
            if len(columns) > 10:
                summary += f"... and {len(columns) - 10} more columns\n"
        
        # Analyze key fields in first few rows
        summary += "\nSample Records:\n"
        for i, row in enumerate(sample_data[:3], 1):
            summary += f"Row {i}: "
            # Only include the most important fields to keep it compact
            key_fields = {}
            for key, value in row.items():
                if any(keyword in key.lower() for keyword in ['name', 'type', 'status', 'level', 'category', 'operation', 'resource']):
                    key_fields[key] = str(value)[:50] if len(str(value)) > 50 else str(value)
                if len(key_fields) >= 5:  # Limit to 5 key fields per row
                    break
            summary += ", ".join([f"{k}: {v}" for k, v in key_fields.items()])
            summary += "\n"
        
        return summary
    
    def _create_fallback_analysis(self, results_data: Dict[str, Any], original_query: str) -> str:
        """Create a basic fallback analysis when AI is not available"""
        sample_data = results_data.get('results', [])[:5]
        row_count = results_data.get('row_count', len(sample_data))
        
        analysis = f"📊 Basic Analysis (AI unavailable)\n\n"
        analysis += f"• Query: {original_query}\n"
        analysis += f"• Results: {row_count} records\n"
        
        if sample_data:
            columns = list(sample_data[0].keys()) if sample_data else []
            analysis += f"• Columns: {', '.join(columns[:5])}{'...' if len(columns) > 5 else ''}\n"
            
            # Check for common Azure log patterns
            if any('ResourceProvider' in str(row) for row in sample_data):
                analysis += f"• Type: Azure Diagnostic Logs detected\n"
            elif any('$table' in str(row) for row in sample_data):
                analysis += f"• Type: Log Analytics table summary\n"
                
        analysis += f"\n💡 Recommendation: AI analysis temporarily unavailable. Check Azure OpenAI configuration."
        
        return analysis

# Global instance
ai_query_service = AIQueryService()
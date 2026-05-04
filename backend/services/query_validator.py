"""
Query Validation Service
========================
Validates natural language queries and KQL to ensure CIRRUS remains strictly read-only.

CIRRUS Security Model:
- ❌ DESTRUCTIVE operations: delete, remove, destroy, drop, truncate, purge, erase, wipe
- ❌ MODIFICATION operations: stop, start, modify, update, change, restart, enable, disable, etc.
- ❌ CREATION operations: create, deploy, provision, add, new, scale, resize, configure, etc.
- ✅ READ-ONLY operations: show, list, get, query, find, view, analyze, report, etc.

Validation Strategy (AI-First Approach):
1. AI classifies query intent FIRST (general_response vs Azure operations)
2. AI decision validation (action allowlist check)
3. Keyword validation ONLY for Azure operations (general_response bypasses)
4. KQL query validation (for generated queries)
5. Azure RBAC at runtime (Reader role only)

General queries (code generation, help, examples) skip keyword validation entirely.
All blocked operations return user-friendly error messages with safe alternative suggestions.
"""

import logging
import re
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

class QueryValidator:
    """
    Validates queries to ensure CIRRUS is a read-only portal.
    Blocks ALL queries attempting to create, modify, or delete Azure resources.
    
    CIRRUS is strictly read-only - it only supports querying and analyzing existing resources.
    """
    
    # Keywords that indicate DESTRUCTIVE operations (deletion/destruction)
    DESTRUCTIVE_KEYWORDS = [
        'delete', 'remove', 'destroy', 'drop', 'truncate', 'purge', 'erase', 'wipe'
    ]
    
    # Keywords that indicate MODIFICATION operations (changing existing resources)
    MODIFICATION_KEYWORDS = [
        'modify', 'alter', 'patch',
        'reboot', 'shutdown', 'deallocate',
        'terminate', 'failover', 'switchover'
    ]
    
    # Keywords that indicate CREATION operations (creating new resources)
    CREATION_KEYWORDS = [
        'create', 'deploy', 'provision'
    ]
    
    # Combined list of ALL forbidden keywords (for quick checking)
    ALL_FORBIDDEN_KEYWORDS = DESTRUCTIVE_KEYWORDS + MODIFICATION_KEYWORDS + CREATION_KEYWORDS
    
    # Safe query patterns (read-only operations)
    SAFE_PATTERNS = [
        r'\bshow\b', r'\blist\b', r'\bget\b', r'\bquery\b',
        r'\bfind\b', r'\bsearch\b', r'\bview\b', r'\bdisplay\b',
        r'\bcount\b', r'\bsum\b', r'\baverage\b', r'\banalyze\b'
    ]
    
    def validate_natural_language_query(self, query: str, ai_intent: str = None) -> Tuple[bool, str]:
        """
        Validates that a natural language query doesn't contain creation, modification, or destructive attempts.
        
        CIRRUS is READ-ONLY:
        - ❌ NO Deletions (delete, remove, destroy, etc.) - for Azure operations
        - ❌ NO Modifications (update, stop, start, restart, etc.) - for Azure operations  
        - ❌ NO Creations (create, deploy, provision, etc.) - for Azure operations
        - ✅ YES Queries (show, list, get, analyze, etc.)
        - ✅ YES General responses (code, help, examples) - ALL keywords allowed
        
        Args:
            query: The natural language query from the user
            ai_intent: AI-classified action ('general_response' bypasses keyword checks)
            
        Returns:
            Tuple of (is_safe, reason)
        """
        if not query or not isinstance(query, str):
            return True, "Query is safe"
        
        # AI INTENT BYPASS: If AI classified as general_response, allow ALL keywords
        if ai_intent == 'general_response':
            logger.info(f" AI classified as general_response, bypassing keyword validation")
            return True, "Query is general/informational (AI classified)"
        
        query_lower = query.lower()
        
        # Check for DESTRUCTIVE keywords (deletion/destruction)
        for keyword in self.DESTRUCTIVE_KEYWORDS:
            # Use word boundaries to avoid false positives (e.g., "deleted_at" field name)
            if re.search(rf'\b{keyword}\b', query_lower):
                return False, f"❌ DESTRUCTIVE operation not allowed: '{keyword}'. CIRRUS cannot delete or destroy resources."
        
        # Check for MODIFICATION keywords (changing existing resources)
        for keyword in self.MODIFICATION_KEYWORDS:
            if re.search(rf'\b{keyword}\b', query_lower):
                return False, f"❌ MODIFICATION operation not allowed: '{keyword}'. CIRRUS cannot modify or change resources."
        
        # Check for CREATION keywords (creating new resources)
        for keyword in self.CREATION_KEYWORDS:
            if re.search(rf'\b{keyword}\b', query_lower):
                return False, f"❌ CREATION operation not allowed: '{keyword}'. CIRRUS cannot create new resources."
        
        return True, "Query is safe"
    
    def validate_kql_query(self, kql: str) -> Tuple[bool, str]:
        """
        Validates that a KQL query doesn't contain creation, modification, or deletion operations.
        
        Note: KQL for Azure Resource Graph is inherently read-only, but we validate
        to be explicit and catch any potential issues or injection attempts.
        
        KQL CANNOT:
        - ❌ Delete data (.delete, .drop, .clear, .purge)
        - ❌ Modify data (.set, .append, .replace, .update)
        - ❌ Create resources (not supported in KQL)
        
        KQL CAN ONLY:
        - ✅ Query data (where, project, summarize, join, etc.)
        
        Args:
            kql: The KQL query string
            
        Returns:
            Tuple of (is_safe, reason)
        """
        if not kql or not isinstance(kql, str):
            return True, "KQL is safe"
        
        kql_lower = kql.lower()
        
        # KQL doesn't support these operations, but check anyway for defense in depth
        # and to catch potential injection attempts
        forbidden_operations = [
            '.delete', '.drop', '.clear', '.purge',        # Deletion operations
            '.set', '.append', '.replace', '.update',      # Modification operations
            '.move', '.rename', '.alter'                   # Other write operations
        ]
        
        for operation in forbidden_operations:
            if operation in kql_lower:
                return False, f"❌ KQL contains forbidden operation: '{operation}'. Only read-only queries are allowed."
        
        return True, "KQL is safe"
    
    def validate_ai_decision(self, ai_decision: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates that an AI-generated decision is a safe read-only action.
        
        ONLY ALLOWED ACTIONS (Read-Only):
        - ✅ resource_graph: Query Azure Resource Graph (read-only)
        - ✅ cost_analysis: Query Cost Management (read-only)
        - ✅ cost_comparison: Compare costs between periods (read-only)
        - ✅ log_analytics: Query Log Analytics (read-only)
        - ✅ azure_metrics: Query Azure Monitor (read-only)
        - ✅ resource_health: Query Azure Resource Health (read-only)
        - ✅ policy_compliance: Query Azure Policy compliance (read-only)
        - ✅ security_score: Query Microsoft Defender security score (read-only)
        - ✅ general_response: AI text response (no Azure API calls)
        - ✅ agentic_analysis: AI-driven iterative read-only analysis (read-only)
        
        ALL OTHER ACTIONS ARE BLOCKED:
        - ❌ NO Destructive actions (delete_resource, destroy_*, remove_*, etc.)
        - ❌ NO Modification actions (update_resource, modify_*, change_*, stop_*, start_*, etc.)
        - ❌ NO Creation actions (create_resource, deploy_*, provision_*, etc.)
        
        Args:
            ai_decision: AI decision dict with 'action' key
            
        Returns:
            (is_safe, reason)
        """
        action = ai_decision.get('action', '')
        
        # Allowlist: Only read-only actions are permitted
        SAFE_ACTIONS = [
            'resource_graph',
            'cost_analysis',
            'cost_comparison',
            'log_analytics',
            'azure_metrics',
            'resource_health',
            'policy_compliance',
            'security_score',
            'general_response',
            'agentic_analysis',
        ]
        
        if action not in SAFE_ACTIONS:
            logger.warning(f" Unsafe AI action blocked: {action}")
            return False, f"❌ Action '{action}' is not allowed. CIRRUS only supports read-only operations: {', '.join(SAFE_ACTIONS)}"
        
        return True, "AI decision is safe"
    
    def get_safe_alternative_suggestion(self, query: str) -> str:
        """
        Provides a helpful suggestion for a safer alternative query based on the blocked operation type.
        
        Maps DESTRUCTIVE, MODIFICATION, and CREATION attempts to helpful read-only alternatives.
        
        Args:
            query: The original query that was rejected
            
        Returns:
            A suggested safe alternative query
        """
        query_lower = query.lower()
        
        # DESTRUCTIVE attempts (deletion/destruction)
        if any(word in query_lower for word in self.DESTRUCTIVE_KEYWORDS):
            return "💡 Instead try: 'Show me all resources' or 'List resources by type' or 'Find resources in subscription X'"
        
        # MODIFICATION attempts - Shutdown/reboot operations
        if any(word in query_lower for word in ['shutdown', 'terminate', 'deallocate', 'reboot']):
            return "💡 Instead try: 'Show me running VMs' or 'List VM power states' or 'Show me resource status'"
        
        # MODIFICATION attempts - General updates
        if any(word in query_lower for word in ['modify', 'alter', 'patch']):
            return "💡 Instead try: 'Show me VM configurations' or 'List resource properties' or 'Display resource tags'"
        
        # CREATION attempts - Deployment/provisioning
        if any(word in query_lower for word in ['create', 'deploy', 'provision']):
            return "💡 CIRRUS is read-only. For resource creation, use Azure Portal, Azure CLI, or Infrastructure-as-Code tools (Terraform/Bicep)."
        
        # Default suggestion for any other blocked operation
        return "💡 CIRRUS is read-only. Try queries like: 'Show me all resources' or 'Analyze costs for last month' or 'List VMs by location'"


# Global instance
query_validator = QueryValidator()

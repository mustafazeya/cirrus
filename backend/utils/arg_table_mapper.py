"""
Azure Resource Graph Table Mapper

Maps Azure Resource Provider namespaces to their corresponding specialized ARG tables.
This ensures queries use the most optimal table for better performance and accuracy.

Reference: https://learn.microsoft.com/en-in/azure/governance/resource-graph/reference/supported-tables-resources
"""

from typing import Optional, Dict, List, Any
import re
import logging
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Cache for ARG table documentation
_table_mapping_cache = {
    "data": None,
    "timestamp": None,
    "ttl_hours": 720  # Refresh every 30 days
}


async def refresh_table_mappings_from_docs():
    """
    Refresh ARG table mappings cache from Microsoft documentation.
    Loads from Redis cache and merges with fresh documentation extraction.
    Should be called on startup and periodically.
    """
    from services.cache_service import cache_service
    
    cache_key = "arg:table_mappings:v1"
    cache_age = None
    
    # Load existing mappings from Redis cache
    if cache_service:
        try:
            cached_data = await cache_service.get("system", cache_key)
            if cached_data:
                import json
                cache_data = json.loads(cached_data)
                cache_timestamp = datetime.fromisoformat(cache_data.get("timestamp"))
                cache_age = datetime.now() - cache_timestamp
                
                # Check if cache is still fresh (within TTL)
                if cache_age < timedelta(hours=_table_mapping_cache["ttl_hours"]):
                    logger.info(f" Loaded {len(cache_data.get('mappings', {}))} ARG table mappings from Redis cache (age: {cache_age.total_seconds()/3600:.1f}h, fresh)")
                    _table_mapping_cache["data"] = cache_data
                    _table_mapping_cache["timestamp"] = cache_timestamp
                    return  # Cache is fresh, skip fetching
                else:
                    # Cache is expired but keep it as backup
                    logger.info(f" Cache expired (age: {cache_age.total_seconds()/3600:.1f}h > {_table_mapping_cache['ttl_hours']}h) - keeping as backup while fetching fresh data")
                    _table_mapping_cache["data"] = cache_data
                    _table_mapping_cache["timestamp"] = cache_timestamp
        except Exception as e:
            logger.warning("Failed to load from Redis cache")
    
    try:
        logger.info(" Fetching latest ARG table mappings from Microsoft documentation...")
        fresh_mappings = await _fetch_arg_table_mappings_from_docs()
        
        if fresh_mappings:
            _table_mapping_cache["data"] = fresh_mappings
            _table_mapping_cache["timestamp"] = datetime.now()
            
            mappings_count = len(fresh_mappings.get("mappings", {}))
            logger.info(f" ARG table mappings refreshed: {mappings_count} mappings")
            
            # Store in Redis cache
            if cache_service:
                try:
                    import json
                    cache_data = {
                        "mappings": fresh_mappings.get("mappings", {}),
                        "timestamp": datetime.now().isoformat(),
                        "source": fresh_mappings.get("source", "unknown")
                    }
                    await cache_service.set(
                        "system",
                        cache_key, 
                        json.dumps(cache_data),
                        ttl=int(_table_mapping_cache["ttl_hours"] * 3600)
                    )
                    logger.info(f" Stored ARG table mappings in Redis cache (TTL: 30 days)")
                except Exception as e:
                    logger.warning("Failed to store in Redis cache")
        else:
            # Fetch failed - keep using expired cache if available
            if _table_mapping_cache["data"]:
                logger.warning(f" Failed to refresh from GitHub, continuing with expired cache ({len(_table_mapping_cache['data'].get('mappings', {}))} mappings)")
            else:
                logger.error(" No cache available and GitHub fetch failed - system will have limited mapping capability")
            
    except Exception as e:
        logger.error(" Error refreshing ARG table mappings")
        if _table_mapping_cache["data"]:
            logger.warning(f" Continuing with existing cache ({len(_table_mapping_cache['data'].get('mappings', {}))} mappings)")



# Mapping of Azure Resource Provider namespaces to specialized ARG tables
# Based on official Microsoft Learn documentation


def extract_resource_provider(resource_type: str) -> Optional[str]:
    """
    Extract the Resource Provider namespace from an Azure resource type.
    
    Args:
        resource_type: Azure resource type (e.g., "Microsoft.Network/virtualNetworks")
        
    Returns:
        Resource Provider namespace (e.g., "Microsoft.Network") or None if invalid
        
    Examples:
        >>> extract_resource_provider("Microsoft.Network/virtualNetworks")
        "Microsoft.Network"
        >>> extract_resource_provider("Microsoft.Compute/virtualMachines")
        "Microsoft.Compute"
        >>> extract_resource_provider("Microsoft.Network/virtualNetworks/subnets")
        "Microsoft.Network"
    """
    if not resource_type or '/' not in resource_type:
        return None
    
    parts = resource_type.split('/')
    if len(parts) < 2:
        return None
    
    # Resource provider is always the first segment (e.g., Microsoft.Network)
    return parts[0]


def is_child_resource_type(resource_type: str) -> bool:
    """
    Determine if a resource type is a child resource (3+ segments).
    
    Child resources have the pattern: Microsoft.<Provider>/<Parent>/<Child>
    Parent resources have the pattern: Microsoft.<Provider>/<Resource>
    
    Args:
        resource_type: Full Azure resource type
        
    Returns:
        True if child resource (3+ segments), False otherwise
        
    Examples:
        >>> is_child_resource_type("Microsoft.Network/virtualNetworks/subnets")
        True
        >>> is_child_resource_type("Microsoft.Network/virtualNetworks")
        False
        >>> is_child_resource_type("Microsoft.Network/firewallPolicies/ruleCollectionGroups")
        True
    """
    if not resource_type:
        return False
    
    parts = resource_type.split('/')
    # 3+ segments = child resource (RP/Parent/Child or deeper)
    return len(parts) >= 3


def get_parent_resource_type(resource_type: str) -> Optional[str]:
    """
    Extract the parent resource type from a child resource type.
    
    Args:
        resource_type: Child resource type (e.g., "Microsoft.Network/virtualNetworks/subnets")
        
    Returns:
        Parent resource type (e.g., "Microsoft.Network/virtualNetworks") or None
        
    Examples:
        >>> get_parent_resource_type("Microsoft.Network/virtualNetworks/subnets")
        "Microsoft.Network/virtualNetworks"
        >>> get_parent_resource_type("Microsoft.Network/firewallPolicies/ruleCollectionGroups")
        "Microsoft.Network/firewallPolicies"
    """
    if not is_child_resource_type(resource_type):
        return None
    
    parts = resource_type.split('/')
    # Return RP + Parent (first 2 segments)
    return '/'.join(parts[:2])


def get_table_for_resource_provider(resource_provider: str, resource_type: Optional[str] = None) -> str:
    """
    Get the PRIMARY ARG table for a given Resource Provider namespace.
    Returns the first table when multiple are available.
    
    Args:
        resource_provider: Azure Resource Provider (e.g., "Microsoft.Network")
        resource_type: Full resource type for special case handling (optional)
        
    Returns:
        ARG table name (e.g., "NetworkResources") or "Resources" as fallback
    """
    if not resource_provider:
        return "Resources"
    
    # Try documentation-based mappings from memory cache (always available after startup)
    cache = _table_mapping_cache
    if cache["data"]:
        doc_mappings = cache["data"].get("mappings", {})
        
        # PRIORITY 1: Try full resource type (most specific)
        if resource_type:
            resource_type_lower = resource_type.lower()
            if resource_type_lower in doc_mappings:
                tables = doc_mappings[resource_type_lower]
                # Backward compatibility: handle both list and single value
                if isinstance(tables, list):
                    table = tables[0]
                    table_info = f"{table} (+{len(tables)-1} alternates)" if len(tables) > 1 else table
                else:
                    table = tables
                    table_info = table
                logger.info(f" Mapping (full type): {resource_type} -> {table_info}")
                return table
            
            # PRIORITY 2: Try progressively shorter resource types (parent resources)
            parts = resource_type_lower.split('/')
            for i in range(len(parts) - 1, 1, -1):
                partial_type = '/'.join(parts[:i])
                if partial_type in doc_mappings:
                    tables = doc_mappings[partial_type]
                    # Backward compatibility
                    table = tables[0] if isinstance(tables, list) else tables
                    logger.info(f" Mapping (parent type): {partial_type} -> {table}")
                    return table
        
        # PRIORITY 3: Try provider-level mapping (least specific)
        if resource_provider.lower() in doc_mappings:
            tables = doc_mappings[resource_provider.lower()]
            # Backward compatibility
            table = tables[0] if isinstance(tables, list) else tables
            logger.info(f" Mapping (provider): {resource_provider} -> {table}")
            return table
    
    # No mappings available at all
    logger.error(f" No mapping data available for {resource_type or resource_provider} - mapping cache may not be initialized")
    raise RuntimeError(f"ARG table mappings not available. System needs initialization.")


def get_all_tables_for_resource_type(resource_type: str) -> List[str]:
    """
    Get ALL possible ARG tables for a resource type.
    Some resource types appear in multiple tables (e.g., DNS child types in both dnsresources and resources).
    
    Args:
        resource_type: Full Azure resource type
        
    Returns:
        List of table names, ordered by preference (specialized tables first)
    """
    if not resource_type:
        return ["Resources"]
    
    cache = _table_mapping_cache
    if not cache["data"]:
        return ["Resources"]
    
    doc_mappings = cache["data"].get("mappings", {})
    resource_type_lower = resource_type.lower()
    
    if resource_type_lower in doc_mappings:
        tables = doc_mappings[resource_type_lower]
        # Backward compatibility: handle both list and single value
        if isinstance(tables, list):
            return tables
        else:
            return [tables]  # Convert single value to list
    
    # Try parent types
    parts = resource_type_lower.split('/')
    for i in range(len(parts) - 1, 1, -1):
        partial_type = '/'.join(parts[:i])
        if partial_type in doc_mappings:
            tables = doc_mappings[partial_type]
            # Backward compatibility
            if isinstance(tables, list):
                return tables
            else:
                return [tables]  # Convert single value to list
    
    return ["Resources"]


def get_table_for_resource_type(resource_type: str) -> str:
    """
    Get the optimal ARG table for a complete Azure resource type.
    
    Args:
        resource_type: Full Azure resource type (e.g., "Microsoft.Network/virtualNetworks")
        
    Returns:
        ARG table name or "Resources" as fallback
        
    Examples:
        >>> get_table_for_resource_type("Microsoft.Network/virtualNetworks")
        "NetworkResources"
        >>> get_table_for_resource_type("Microsoft.Security/securescores")
        "SecurityResources"
    """
    resource_provider = extract_resource_provider(resource_type)
    return get_table_for_resource_provider(resource_provider, resource_type)


async def _fetch_arg_table_mappings_from_docs() -> Optional[Dict[str, Any]]:
    """
    Fetch ARG table mappings from Microsoft documentation using direct HTTPS.
    Returns dict mapping resource types to their ARG table names.
    """
    combined_content = None
    
    try:
        import httpx
        from config import settings
        
        # Fetch from GitHub raw markdown - much cleaner than HTML parsing
        logger.info(" Fetching ARG table mappings from GitHub markdown...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                "https://raw.githubusercontent.com/MicrosoftDocs/azure-docs/main/articles/governance/resource-graph/reference/supported-tables-resources.md"
            )
            
            if response.status_code != 200:
                logger.error(f"HTTP request failed: {response.status_code}")
                return None
            
            markdown_content = response.text
            
            # Parse markdown to extract mappings
            # Format in markdown: "- microsoft.advisor/recommendations" under "## advisorresources"
            all_mappings = {}
            current_table = None
            
            lines = markdown_content.split('\n')
            for line in lines:
                line = line.strip()
                
                # Table headers start with ##
                if line.startswith('## ') and not line.startswith('## Next') and not line.startswith('## Additional'):
                    # Extract table name (everything after "## ")
                    current_table = line[3:].strip()
                    # Skip non-table sections
                    if current_table.lower() in ['next steps', 'additional links', 'file metadata', 'navigation menu', 'history', 'latest commit']:
                        current_table = None
                    continue
                
                # Resource types are lines starting with "- microsoft." or other provider prefixes
                if current_table and line.startswith('- '):
                    resource_type = line[2:].strip()
                    
                    # Remove any markdown links or parenthetical descriptions
                    # Format: "- microsoft.advisor/recommendations (Description)" or "- microsoft.advisor/recommendations"
                    if '(' in resource_type:
                        resource_type = resource_type.split('(')[0].strip()
                    
                    # Skip lines that are just descriptions or sample queries
                    if 'sample query' in resource_type.lower():
                        continue
                    
                    # Only process valid resource types (contain "/")
                    if '/' in resource_type:
                        resource_type_lower = resource_type.lower()
                        # IMPORTANT: Store ALL tables for each type (not just first)
                        # Same resource type can appear in multiple tables
                        if resource_type_lower not in all_mappings:
                            all_mappings[resource_type_lower] = []
                        all_mappings[resource_type_lower].append(current_table)
            
            if not all_mappings:
                logger.warning("No mappings extracted from markdown")
                return None
            
            logger.info(f" Extracted {len(all_mappings)} resource types with table mappings")
            
            # Count types with multiple tables
            multi_table_types = [rt for rt, tables in all_mappings.items() if len(tables) > 1]
            if multi_table_types:
                logger.info(f" Found {len(multi_table_types)} resource types available in multiple tables")
                # Show first few examples
                for rt in multi_table_types[:3]:
                    logger.info(f"  {rt} -> {all_mappings[rt]}")
            
            # Show first 10 for debugging
            for i, (k, v) in enumerate(list(all_mappings.items())[:10]):
                tables_str = v if isinstance(v, str) else f"{v[0]} (+{len(v)-1} more)" if len(v) > 1 else v[0]
                logger.info(f"  {k} -> {tables_str}")
            if len(all_mappings) > 10:
                logger.info(f"  ... and {len(all_mappings) - 10} more")
            
            return {
                "mappings": all_mappings,
                "timestamp": datetime.now(),
                "source": "GitHub markdown (MicrosoftDocs/azure-docs)"
            }
        
    except Exception as e:
        logger.error("Failed to fetch ARG table mappings")
        return None


def detect_resource_providers_in_query(query: str) -> List[str]:
    """
    Detect Resource Provider namespaces mentioned in a natural language query.
    
    Args:
        query: Natural language query string
        
    Returns:
        List of detected Resource Provider namespaces
        
    Examples:
        >>> detect_resource_providers_in_query("Show all Microsoft.Network resources")
        ["Microsoft.Network"]
        >>> detect_resource_providers_in_query("List virtual networks and security groups")
        ["Microsoft.Network"]
    """
    detected = []
    
    # Direct RP namespace detection (e.g., "Microsoft.Network")
    # Updated pattern to handle word boundaries better
    rp_pattern = r'\bMicrosoft\.[A-Za-z]+\b'
    matches = re.findall(rp_pattern, query, re.IGNORECASE)
    
    for match in matches:
        # Normalize the case
        rp = match.split('.')[0].capitalize() + '.' + match.split('.')[1].capitalize()
        if rp not in detected:
            detected.append(rp)
    
    # Smart detection using actual resource types from GitHub mappings
    if not detected:
        cache = _table_mapping_cache
        if cache["data"] and cache["data"].get("mappings"):
            query_lower = query.lower()
            query_words = set(word for word in query_lower.split() if len(word) > 3)
            
            # Search through actual resource types for matches
            rp_scores = {}  # Track which RPs have most matches
            
            for resource_type in cache["data"]["mappings"].keys():
                # Extract meaningful words from resource type
                # E.g., "microsoft.network/dnszones/cname" → ["network", "dnszones", "cname"]
                type_parts = resource_type.lower().replace('/', ' ').replace('.', ' ').split()
                type_words = set(part for part in type_parts if len(part) > 3)
                
                # Calculate exact word matches only (no substring matching)
                matches = query_words & type_words  # Set intersection
                
                if matches:
                    # Extract RP from resource type
                    rp_match = re.match(r'(microsoft\.[a-z]+)', resource_type)
                    if rp_match:
                        rp = rp_match.group(1)
                        # Normalize case
                        rp = 'Microsoft.' + rp.split('.')[1].capitalize()
                        # Score by number of exact word matches
                        rp_scores[rp] = rp_scores.get(rp, 0) + len(matches)
            
            # Add RPs with multiple matches (higher confidence)
            for rp, score in sorted(rp_scores.items(), key=lambda x: x[1], reverse=True):
                if score >= 2 and rp not in detected:  # Need at least 2 word matches
                    detected.append(rp)
    
    return detected


def get_table_recommendation(query: str, explicit_resource_type: Optional[str] = None) -> Dict[str, Any]:
    """
    Get table recommendation for a natural language query or explicit resource type.
    
    Args:
        query: Natural language query string
        explicit_resource_type: Explicitly provided resource type (takes precedence)
        
    Returns:
        Dictionary with:
            - table: Recommended table name
            - reason: Explanation for the recommendation
            - resource_providers: List of detected RPs
            - confidence: "high", "medium", or "low"
            - is_child_resource: True if querying child resource type
            - parent_type: Parent resource type (if child resource)
            
    Examples:
        >>> get_table_recommendation("Show all network resources")
        {
            "table": "NetworkResources",
            "reason": "Detected Microsoft.Network resources",
            "resource_providers": ["Microsoft.Network"],
            "confidence": "high",
            "is_child_resource": False
        }
        >>> get_table_recommendation("", "Microsoft.Network/virtualNetworks/subnets")
        {
            "table": "NetworkResources",
            "reason": "Child resource detected, query parent: Microsoft.Network/virtualNetworks",
            "resource_providers": ["Microsoft.Network"],
            "confidence": "high",
            "is_child_resource": True,
            "parent_type": "Microsoft.Network/virtualNetworks"
        }
    """
    if explicit_resource_type:
        # Check if this is a child resource
        is_child = is_child_resource_type(explicit_resource_type)
        parent_type = get_parent_resource_type(explicit_resource_type) if is_child else None
        
        # Explicit resource type provided - high confidence
        table = get_table_for_resource_type(explicit_resource_type)
        rp = extract_resource_provider(explicit_resource_type)
        
        if is_child:
            reason = f"Child resource detected, use parent type '{parent_type}' with mv-expand in {table} table"
        else:
            reason = f"Parent resource type '{explicit_resource_type}' maps to {table}"
        
        return {
            "table": table,
            "reason": reason,
            "resource_providers": [rp] if rp else [],
            "confidence": "high",
            "is_child_resource": is_child,
            "parent_type": parent_type
        }
    
    # Detect RPs from query
    detected_rps = detect_resource_providers_in_query(query)
    
    if not detected_rps:
        # No RPs detected - use default
        return {
            "table": "Resources",
            "reason": "No specific Resource Provider detected, using default Resources table",
            "resource_providers": [],
            "confidence": "low",
            "is_child_resource": False
        }
    
    if len(detected_rps) == 1:
        # Single RP detected - ONLY use live documentation
        rp = detected_rps[0]
        
        # Get table from live documentation cache
        cache = _table_mapping_cache
        doc_table = None
        source = "not available"
        
        if cache["data"] and cache["timestamp"]:
            age = datetime.now() - cache["timestamp"]
            if age < timedelta(hours=cache["ttl_hours"]):
                doc_mappings = cache["data"].get("mappings", {})
                # Check both uppercase and lowercase keys
                doc_table = doc_mappings.get(rp) or doc_mappings.get(rp.lower())
                
                # Check special cases (e.g., DNS zones)
                if "dns" in query.lower():
                    doc_special = cache["data"].get("special_cases", {})
                    for pattern, table in doc_special.items():
                        if rp in pattern:
                            doc_table = table
                            break
                
                if doc_table:
                    source = "live documentation"
        
        if not doc_table:
            # No doc mapping available - return Resources as fallback
            return {
                "table": "Resources",
                "reason": f"Detected {rp} in query, but no table mapping found in live documentation (fallback to Resources)",
                "resource_providers": detected_rps,
                "confidence": "medium",
                "is_child_resource": False
            }
        
        return {
            "table": doc_table,
            "reason": f"Detected {rp} in query -> {doc_table} (from {source})",
            "resource_providers": detected_rps,
            "confidence": "high",
            "is_child_resource": False
        }
    
    # Multiple RPs detected - use Resources for cross-domain queries
    return {
        "table": "Resources",
        "reason": f"Multiple Resource Providers detected ({', '.join(detected_rps)}), using Resources for cross-domain query",
        "resource_providers": detected_rps,
        "confidence": "medium",
        "is_child_resource": False
    }


def is_valid_resource_type(resource_type: str) -> bool:
    """
    Check if a resource type exists in the Azure Resource Graph cache.
    This helps detect invalid/hallucinated resource types early.
    
    Args:
        resource_type: Resource type to validate (e.g., 'microsoft.network/dnszones/recordsets')
        
    Returns:
        True if the resource type exists in cache, False if unknown/invalid
    """
    if not resource_type:
        return False
    
    resource_type = resource_type.lower()
    
    # Check if we have cached mappings
    cache = _table_mapping_cache
    if not cache["data"] or not cache["timestamp"]:
        # No cache available - assume valid (can't validate)
        return True
    
    mappings = cache["data"].get("mappings", {})
    
    # Check if exact type exists
    if resource_type in mappings:
        return True
    
    # Check if any parent type exists (for child resources)
    segments = resource_type.split('/')
    if len(segments) >= 2:
        # Try parent types
        for i in range(len(segments) - 1, 1, -1):
            parent_type = '/'.join(segments[:i])
            if parent_type in mappings:
                return True
    
    # Not found in cache - likely invalid
    return False


def get_table_context_for_prompt(query: str, explicit_resource_type: Optional[str] = None) -> str:
    """
    Generate context text for AI prompt to guide table selection.
    
    Args:
        query: Natural language query string
        explicit_resource_type: Explicitly provided resource type (optional)
        
    Returns:
        Formatted context string for inclusion in AI prompt
    """
    recommendation = get_table_recommendation(query, explicit_resource_type)
    
    context_parts = []
    context_parts.append("TABLE SELECTION GUIDANCE:")
    context_parts.append("")
    context_parts.append(f"Recommended Table: {recommendation['table']}")
    context_parts.append(f"Reason: {recommendation['reason']}")
    context_parts.append(f"Confidence: {recommendation['confidence']}")
    
    if recommendation['resource_providers']:
        context_parts.append(f"Detected Resource Providers: {', '.join(recommendation['resource_providers'])}")
    
    return "\n".join(context_parts)

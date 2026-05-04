"""
Azure AI Search Service for Microsoft Learn Documentation
Self-indexed documentation from Microsoft Learn, Azure docs, and GitHub.

This service provides semantic search over a custom-indexed repository of Microsoft documentation.
"""

import logging
from typing import List, Dict, Any, Optional
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from config import settings

logger = logging.getLogger(__name__)


class AzureSearchService:
    """
    Service for searching Microsoft documentation using Azure AI Search
    """
    
    def __init__(self):
        """Initialize Azure AI Search client"""
        self.endpoint = settings.AZURE_SEARCH_ENDPOINT
        self.key = settings.AZURE_SEARCH_KEY
        self.index_name = settings.AZURE_SEARCH_INDEX_NAME
        self.client = None
        
        if self.endpoint and self.key:
            try:
                self.client = SearchClient(
                    endpoint=self.endpoint,
                    index_name=self.index_name,
                    credential=AzureKeyCredential(self.key)
                )
                logger.info(f" Azure AI Search initialized: {self.index_name}")
            except Exception as e:
                logger.warning(f"Failed to initialize Azure AI Search: {e}")
                self.client = None
        else:
            logger.warning("Azure AI Search not configured (missing endpoint or key)")
    
    async def search_microsoft_docs(
        self, 
        query: str, 
        top: int = 5,
        filter_expression: Optional[str] = None,
        use_semantic_search: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Search Microsoft documentation with Azure AI Search
        
        Args:
            query: Search query string
            top: Number of results to return
            filter_expression: OData filter expression (e.g., "source eq 'learn.microsoft.com'")
            use_semantic_search: Use semantic search (requires semantic configuration)
        
        Returns:
            List of search results with title, content, url, and score
        """
        if not self.client:
            logger.warning("Azure AI Search not available")
            return []
        
        try:
            search_params = {
                "search_text": query,
                "top": top,
                "select": ["title", "content", "url", "source", "lastUpdated", "docType", "service", "chunkIndex", "totalChunks"],
                "include_total_count": True
            }
            
            if filter_expression:
                search_params["filter"] = filter_expression
            
            # Enable semantic search if requested and available
            if use_semantic_search:
                search_params["query_type"] = "semantic"
                search_params["semantic_configuration_name"] = "default"
            
            results = self.client.search(**search_params)
            
            search_results = []
            for result in results:
                search_results.append({
                    "title": result.get("title", ""),
                    "content": result.get("content", ""),
                    "url": result.get("url", ""),
                    "source": result.get("source", ""),
                    "score": result.get("@search.score", 0.0),
                    "lastUpdated": result.get("lastUpdated", ""),
                    "docType": result.get("docType", "general"),
                    "service": result.get("service", "general"),
                    "chunkIndex": result.get("chunkIndex", 0),
                    "totalChunks": result.get("totalChunks", 1)
                })
            
            logger.info(f" Azure AI Search: Found {len(search_results)} results for '{query}'")
            return search_results
            
        except Exception as e:
            logger.warning(f"Azure AI Search query failed: {e}")
            return []
    
    async def search_kql_examples(self, resource_type: str, user_query: str = None, resource_context: dict = None, top: int = 3) -> List[Dict[str, Any]]:
        """
        Search for KQL query examples for a specific Azure resource type
        
        Args:
            resource_type: Azure resource type (e.g., "Microsoft.Network/azureFirewalls")
            user_query: User's natural language query for more specific search
            resource_context: Full resource context including kind, sku, etc.
            top: Number of results
        
        Returns:
            List of documentation pages with KQL examples
        """
        # Build search query - let the search find the right docs
        uppercase_type = resource_type.upper() if resource_type else ""
        resource_name = resource_type.split('/')[-1] if '/' in resource_type else resource_type
        
        # Extract kind from resource_context if available
        kind = None
        if resource_context:
            kind = resource_context.get('kind') or resource_context.get('sku', {}).get('name', '')
        
        # Build comprehensive search query
        query_parts = []
        
        if kind:
            query_parts.extend([kind, kind, kind])  # Repeat 3x for emphasis
            logger.info(f" AI Search with kind: {kind}")
        
        query_parts.extend([uppercase_type, resource_name])
        
        if user_query:
            query_parts.append(user_query)
        
        # Prioritize table/schema/monitoring docs over ARM templates
        # Search strategy depends on log mode
        log_mode = resource_context.get('log_mode') if resource_context else None
        if log_mode == 'Dedicated':
            # Dedicated mode: search for resource-specific tables in /reference/tables/
            # E.g., for azureFirewalls: finds AZFWNetworkRule, AZFWApplicationRule, etc.
            query_parts.extend(["/reference/tables/", resource_name, "table", "columns", "schema"])
        elif log_mode == 'AzureDiagnostics':
            # AzureDiagnostics mode: search for AzureDiagnostics table specifically
            query_parts.extend(["/reference/tables/azurediagnostics", "AzureDiagnostics", "Category", resource_name, "columns", "schema"])
        else:
            # Unknown mode: search broadly
            query_parts.extend(["table", "schema", "columns", "monitoring", "logs", "queries", "KQL", "examples"])
        
        query = " ".join(query_parts)
        logger.info(f" AI Search query: {query}")
        
        # Filter: monitoring docs from learn.microsoft.com, exclude ARM templates
        # docType='monitoring' catches /reference/tables/, /monitor/, etc.
        # This automatically excludes /templates/ URLs which are classified as 'general'
        filter_expr = "source eq 'learn.microsoft.com' and docType eq 'monitoring'"
        
        results = await self.search_microsoft_docs(
            query=query,
            top=top * 2,  # Get more results, we'll filter
            filter_expression=filter_expr,
            use_semantic_search=True
        )
        
        # Get log mode from resource_context
        log_mode = resource_context.get('log_mode') if resource_context else None
        
        # Post-filter: Exclude any ARM template URLs that slipped through
        # Also prioritize /reference/tables/ URLs (table schemas) over general monitoring docs
        # Filter based on detected log mode (Dedicated vs AzureDiagnostics)
        filtered_results = []
        table_docs = []
        other_docs = []
        azurediagnostics_docs = []
        
        for result in results:
            url = result.get('url', '').lower()
            content = result.get('content', '').lower()
            title = result.get('title', '').lower()
            
            # Skip ARM templates completely
            if '/templates/' in url or '/azure/templates/' in url:
                continue
            
            # For Dedicated mode: exclude AzureDiagnostics references
            if log_mode == 'Dedicated':
                # Exclude if:
                # 1. URL is about AzureDiagnostics table specifically
                # 2. Title mentions AzureDiagnostics queries
                # 3. Content prominently features AzureDiagnostics (in first 500 chars)
                if ('/reference/tables/azurediagnostics' in url or 
                    '/queries/azurediagnostics' in url or
                    'azurediagnostics table' in title or
                    ('azurediagnostics' in content[:500] and 'where resource' in content[:500])):
                    azurediagnostics_docs.append(result)
                    logger.debug(f" Excluded AzureDiagnostics doc: {result.get('title', '')[:60]}...")
                    continue  # Skip AzureDiagnostics examples in Dedicated mode
            
            # Prioritize table reference docs
            if '/reference/tables/' in url:
                # Always include table reference docs - they're authoritative
                table_docs.append(result)
            else:
                # For other docs, skip pagination chunks (e.g., "Monitor Azure Firewall part 37/40")
                title_lower = title.lower()
                if 'part ' in title_lower and '/' in title_lower:
                    logger.debug(f" Skipped pagination chunk: {result.get('title', '')[:60]}...")
                    continue
                    
                other_docs.append(result)
        
        # Combine based on log mode
        if log_mode == 'Dedicated':
            # Dedicated: table docs first, other docs, exclude AzureDiagnostics
            filtered_results = table_docs + other_docs
            logger.info(f" Dedicated mode: {len(table_docs)} table docs, {len(other_docs)} other docs, {len(azurediagnostics_docs)} AzureDiagnostics docs excluded")
        else:
            # Legacy/AzureDiagnostics: include all
            filtered_results = table_docs + other_docs + azurediagnostics_docs
            logger.info(f" Found {len(table_docs)} table docs, {len(other_docs)} other monitoring docs")
        
        return filtered_results[:top]
    
    async def search_resource_graph_examples(self, query_intent: str, top: int = 3) -> List[Dict[str, Any]]:
        """
        Search for Azure Resource Graph query examples
        
        Args:
            query_intent: What the user wants to query (e.g., "list VMs by region")
            top: Number of results
        
        Returns:
            List of documentation pages with Resource Graph examples
        """
        query = f"Azure Resource Graph KQL examples {query_intent}"
        # Phase 1: Filter by docType='resource-graph' for better relevance
        filter_expr = "source eq 'learn.microsoft.com' and docType eq 'resource-graph'"
        
        return await self.search_microsoft_docs(
            query=query,
            top=top,
            filter_expression=filter_expr,
            use_semantic_search=True
        )
    
    async def search_metrics_documentation(
        self, 
        resource_type: str, 
        metric_name: Optional[str] = None,
        top: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Search for Azure Monitor metrics documentation
        
        Args:
            resource_type: Azure resource type
            metric_name: Optional specific metric name
            top: Number of results
        
        Returns:
            List of documentation pages about available metrics
        """
        resource_name = resource_type.split('/')[-1] if '/' in resource_type else resource_type
        query = f"Azure Monitor metrics {resource_name}"
        if metric_name:
            query += f" {metric_name}"
        
        # Phase 1: Filter by docType='monitoring' for better relevance
        filter_expr = "source eq 'learn.microsoft.com' and docType eq 'monitoring'"
        
        return await self.search_microsoft_docs(
            query=query,
            top=top,
            filter_expression=filter_expr,
            use_semantic_search=True
        )
    
    async def search_cost_management_docs(self, top: int = 3) -> List[Dict[str, Any]]:
        """
        Search for Azure Cost Management API documentation
        
        Args:
            top: Number of results
        
        Returns:
            List of documentation pages about Cost Management API
        """
        query = "Azure Cost Management API query examples grouping aggregation"
        # Phase 1: Filter by docType='cost-management' for better relevance
        filter_expr = "source eq 'learn.microsoft.com' and docType eq 'cost-management'"
        
        return await self.search_microsoft_docs(
            query=query,
            top=top,
            filter_expression=filter_expr,
            use_semantic_search=True
        )


# Singleton instance
_azure_search_service: Optional[AzureSearchService] = None


def get_azure_search_service() -> AzureSearchService:
    """Get or create singleton Azure Search Service instance"""
    global _azure_search_service
    if _azure_search_service is None:
        _azure_search_service = AzureSearchService()
    return _azure_search_service

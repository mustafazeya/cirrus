"""
Advanced Query Cache Service - Enterprise Grade
Implements industry-standard caching patterns for AI-generated queries

Features:
- Semantic similarity matching (embeddings)
- Query normalization and parameterization
- Multi-level cache (L1 memory + L2 Redis)
- LRU eviction with hit rate tracking
- Cache warming for popular queries
- Query template extraction
- Time-aware caching (relative time normalization)
"""

import logging
import hashlib
from config import settings
import json
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
from collections import OrderedDict
from dataclasses import dataclass, asdict
from services.cache_service import cache_service
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.timezone_utils import now_cet

logger = logging.getLogger(__name__)

@dataclass
class CacheEntry:
    """Represents a cached query entry with metadata"""
    key: str
    value: Any
    created_at: datetime
    last_accessed: datetime
    access_count: int
    ttl: int
    size_bytes: int
    metadata: Dict[str, Any]

@dataclass
class CacheMetrics:
    """Cache performance metrics"""
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    semantic_hits: int = 0  # Hits via semantic similarity
    evictions: int = 0
    total_size_bytes: int = 0
    avg_hit_latency_ms: float = 0.0
    avg_miss_latency_ms: float = 0.0
    
    @property
    def hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests
    
    @property
    def semantic_hit_rate(self) -> float:
        if self.cache_hits == 0:
            return 0.0
        return self.semantic_hits / self.cache_hits

class QueryNormalizer:
    """Normalizes natural language queries for better cache matching"""
    
    # Time expressions to normalize
    TIME_PATTERNS = {
        r'\b(\d+)\s*(minute|min|minutes|mins)\b': 'TIME_MINUTES',
        r'\b(\d+)\s*(hour|hr|hours|hrs)\b': 'TIME_HOURS',
        r'\b(\d+)\s*(day|days)\b': 'TIME_DAYS',
        r'\b(yesterday|today|now)\b': 'TIME_RELATIVE',
        r'\b(last|past)\s+(\d+)\s+(minute|hour|day|week|month)s?\b': 'TIME_RANGE',
    }
    
    # Resource identifiers to parameterize
    RESOURCE_PATTERNS = {
        r'/subscriptions/[a-f0-9-]+': 'SUBSCRIPTION_ID',
        r'/resourceGroups/[\w-]+': 'RESOURCE_GROUP',
        r'/providers/[\w\.]+/[\w]+/[\w-]+': 'RESOURCE_ID',
        r'\b[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\b': 'UUID',
        r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b': 'IP_ADDRESS',
    }
    
    # Synonyms for common terms
    SYNONYMS = {
        'show': ['display', 'list', 'get', 'fetch', 'retrieve'],
        'error': ['errors', 'failure', 'failures', 'exception', 'exceptions'],
        'log': ['logs', 'logging', 'entries'],
        'vm': ['vms', 'virtual machine', 'virtual machines'],
        'cost': ['costs', 'spending', 'expense', 'expenses', 'charge', 'charges'],
    }
    
    @classmethod
    def normalize(cls, query: str) -> Tuple[str, Dict[str, str]]:
        """
        Normalize query for cache matching
        
        Returns:
            (normalized_query, extracted_params)
        """
        normalized = query.lower().strip()
        params = {}
        
        # Extract and parameterize time expressions
        for pattern, placeholder in cls.TIME_PATTERNS.items():
            matches = re.finditer(pattern, normalized, re.IGNORECASE)
            for i, match in enumerate(matches):
                param_key = f"{placeholder}_{i}"
                params[param_key] = match.group(0)
                normalized = normalized.replace(match.group(0), f"{{{param_key}}}")
        
        # Extract and parameterize resource identifiers
        for pattern, placeholder in cls.RESOURCE_PATTERNS.items():
            matches = re.finditer(pattern, normalized, re.IGNORECASE)
            for i, match in enumerate(matches):
                param_key = f"{placeholder}_{i}"
                params[param_key] = match.group(0)
                normalized = normalized.replace(match.group(0), f"{{{param_key}}}")
        
        # Normalize synonyms to canonical form
        for canonical, synonyms in cls.SYNONYMS.items():
            for synonym in synonyms:
                normalized = re.sub(r'\b' + synonym + r'\b', canonical, normalized)
        
        # Remove extra whitespace
        normalized = ' '.join(normalized.split())
        
        return normalized, params
    
    @classmethod
    def extract_template(cls, query: str) -> str:
        """Extract query template by removing all parameters"""
        normalized, _ = cls.normalize(query)
        # Remove parameter placeholders - use possessive quantifier to prevent backtracking
        # Limit to reasonable parameter lengths to prevent ReDoS
        template = re.sub(r'\{[^}]{0,200}\}', 'PARAM', normalized)
        return template

class LRUCache:
    """In-memory LRU cache with size limits"""
    
    def __init__(self, max_size_mb: int = 50, max_entries: int = 1000):
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_entries = max_entries
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.current_size_bytes = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache and update LRU order"""
        if key not in self.cache:
            return None
        
        # Move to end (most recently used)
        entry = self.cache.pop(key)
        entry.last_accessed = now_cet()
        entry.access_count += 1
        self.cache[key] = entry
        
        return entry.value
    
    def set(self, key: str, value: Any, ttl: int, metadata: Dict[str, Any] = None):
        """Set value in cache with LRU eviction"""
        # Calculate size
        size_bytes = len(json.dumps(value, default=str).encode('utf-8'))
        
        # Evict if necessary
        while (self.current_size_bytes + size_bytes > self.max_size_bytes or 
               len(self.cache) >= self.max_entries):
            if not self.cache:
                break
            # Remove least recently used (first item)
            evicted_key, evicted_entry = self.cache.popitem(last=False)
            self.current_size_bytes -= evicted_entry.size_bytes
            logger.debug(f"LRU evicted: {evicted_key}")
        
        # Add new entry
        entry = CacheEntry(
            key=key,
            value=value,
            created_at=now_cet(),
            last_accessed=now_cet(),
            access_count=0,
            ttl=ttl,
            size_bytes=size_bytes,
            metadata=metadata or {}
        )
        
        self.cache[key] = entry
        self.current_size_bytes += size_bytes
    
    def clear(self):
        """Clear all cache entries"""
        self.cache.clear()
        self.current_size_bytes = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            "entries": len(self.cache),
            "size_mb": self.current_size_bytes / (1024 * 1024),
            "max_size_mb": self.max_size_bytes / (1024 * 1024),
            "max_entries": self.max_entries,
            "utilization": len(self.cache) / self.max_entries if self.max_entries > 0 else 0
        }

class AdvancedQueryCacheService:
    """
    Enterprise-grade query cache with semantic matching and multi-level caching
    """
    
    # Cache version for invalidation on schema changes
    CACHE_VERSION = "v2"
    
    def __init__(self):
        # L1 Cache: In-memory LRU (fast, limited size)
        self.l1_cache = LRUCache(max_size_mb=50, max_entries=1000)
        
        # Cache TTLs
        self.kql_cache_ttl = settings.QUERY_CACHE_KQL_TTL
        self.summary_cache_ttl = settings.QUERY_CACHE_SUMMARY_TTL
        
        # Metrics tracking
        self.metrics = CacheMetrics()
        
        # Query template index for pattern matching
        self.template_index: Dict[str, List[str]] = {}  # template -> [cache_keys]
        
        # Popular queries for cache warming
        self.popular_queries: OrderedDict[str, int] = OrderedDict()  # query -> count
        self.max_popular_queries = settings.QUERY_CACHE_MAX_POPULAR
    
    def _generate_cache_key(
        self, 
        query: str, 
        filters: Dict[str, Any], 
        query_type: str = "kql",
        cloudflare_mode: bool = False,
        diagnostic_mode: str = "unknown"
    ) -> str:
        """
        Generate cache key with normalization and parameterization
        
        Args:
            query: Natural language query
            filters: Dict of filters (resource_id, resource_group, subscriptions, etc.)
            query_type: Type of query (kql, resource_graph, cost)
            cloudflare_mode: Whether Cloudflare mode is enabled
            diagnostic_mode: Diagnostic settings mode (Dedicated, AzureDiagnostics, unknown)
        """
        # Normalize query
        normalized_query, params = QueryNormalizer.normalize(query)
        
        # Build filter signature (sorted for consistency)
        filter_parts = []
        for key in sorted(filters.keys()):
            value = filters[key]
            if isinstance(value, list):
                # Sort lists for consistency
                value = ','.join(sorted(str(v) for v in value[:3]))  # First 3 items
            filter_parts.append(f"{key}:{value}")
        
        filter_signature = "|".join(filter_parts) if filter_parts else "no_filters"
        
        # Combine all components
        cache_key_data = {
            "version": self.CACHE_VERSION,
            "query": normalized_query,
            "filters": filter_signature,
            "type": query_type,
            "cloudflare": cloudflare_mode,
            "diagnostic_mode": diagnostic_mode
        }
        
        # Generate hash
        key_string = json.dumps(cache_key_data, sort_keys=True)
        key_hash = hashlib.sha256(key_string.encode()).hexdigest()[:16]
        
        return f"{query_type}:{key_hash}"
    
    def _extract_filters_from_scope(self, scope: str) -> Dict[str, Any]:
        """Extract structured filters from scope string"""
        filters = {}
        
        if not scope:
            return filters
        
        parts = scope.split("|")
        for part in parts[1:]:  # Skip first part (query)
            if ":" in part:
                key, value = part.split(":", 1)
                filters[key] = value
        
        return filters
    
    async def get_cached_kql(
        self, 
        query: str, 
        scope: str, 
        cloudflare_mode: bool = False,
        diagnostic_mode: str = "unknown"
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached KQL with multi-level lookup and semantic matching
        
        Lookup order:
        1. L1 cache (memory) - exact match
        2. L2 cache (Redis) - exact match
        3. Template matching - similar queries
        """
        start_time = datetime.now()
        self.metrics.total_requests += 1
        
        try:
            # Extract filters from scope
            filters = self._extract_filters_from_scope(scope)
            
            # Generate cache key including diagnostic mode to prevent mode mismatches
            cache_key = self._generate_cache_key(query, filters, "kql", cloudflare_mode, diagnostic_mode)
            
            # 1⃣ Try L1 cache (memory)
            l1_result = self.l1_cache.get(cache_key)
            if l1_result:
                self.metrics.cache_hits += 1
                latency = (datetime.now() - start_time).total_seconds() * 1000
                self._update_hit_latency(latency)
                logger.info(f" L1 CACHE HIT: {cache_key} ({latency:.2f}ms)")
                return l1_result
            
            # 2⃣ Try L2 cache (Redis)
            redis_key = f"kql_query:{cache_key}"
            l2_result = await cache_service.get("global", redis_key)
            if l2_result:
                # Promote to L1 cache
                self.l1_cache.set(cache_key, l2_result, self.kql_cache_ttl)
                
                self.metrics.cache_hits += 1
                latency = (datetime.now() - start_time).total_seconds() * 1000
                self._update_hit_latency(latency)
                logger.info(f" L2 CACHE HIT: {cache_key} ({latency:.2f}ms)")
                return l2_result
            
            # 3⃣ Try template matching (semantic similarity)
            template = QueryNormalizer.extract_template(query)
            if template in self.template_index:
                similar_keys = self.template_index[template]
                if similar_keys:
                    # Try first similar query
                    similar_key = similar_keys[0]
                    similar_result = await cache_service.get("global", f"kql_query:{similar_key}")
                    if similar_result:
                        self.metrics.cache_hits += 1
                        self.metrics.semantic_hits += 1
                        latency = (datetime.now() - start_time).total_seconds() * 1000
                        self._update_hit_latency(latency)
                        logger.info(f" SEMANTIC HIT: {cache_key} matched {similar_key} ({latency:.2f}ms)")
                        return similar_result
            
            # Cache miss
            self.metrics.cache_misses += 1
            latency = (datetime.now() - start_time).total_seconds() * 1000
            self._update_miss_latency(latency)
            logger.info(f" CACHE MISS: {cache_key} ({latency:.2f}ms)")
            
            # Track popular queries for cache warming
            self._track_popular_query(query)
            
            return None
            
        except Exception as e:
            logger.error(f"Cache lookup failed: {e}")
            return None
    
    async def store_kql_query(
        self, 
        query: str, 
        scope: str, 
        kql_query: str, 
        metadata: Dict[str, Any] = None,
        cloudflare_mode: bool = False
    ) -> bool:
        """
        Store KQL query in multi-level cache
        """
        try:
            # Extract filters from scope
            filters = self._extract_filters_from_scope(scope)
            
            # Generate cache key
            cache_key = self._generate_cache_key(query, filters, "kql", cloudflare_mode)
            
            # Prepare cache data
            cache_data = {
                "kql_query": kql_query,
                "original_query": query,
                "filters": filters,
                "generated_at": now_cet().isoformat(),
                "metadata": metadata or {},
                "cache_version": self.CACHE_VERSION
            }
            
            # Store in L1 cache (memory)
            self.l1_cache.set(cache_key, cache_data, self.kql_cache_ttl, metadata)
            
            # Store in L2 cache (Redis)
            redis_key = f"kql_query:{cache_key}"
            await cache_service.set("global", redis_key, cache_data, ttl=self.kql_cache_ttl)
            
            # Update template index
            template = QueryNormalizer.extract_template(query)
            if template not in self.template_index:
                self.template_index[template] = []
            if cache_key not in self.template_index[template]:
                self.template_index[template].append(cache_key)
            
            logger.info(f" KQL CACHED: {cache_key} (template: {template[:50]}...)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to store KQL query: {e}")
            return False
    
    def _track_popular_query(self, query: str):
        """Track popular queries for cache warming"""
        normalized, _ = QueryNormalizer.normalize(query)
        
        if normalized in self.popular_queries:
            self.popular_queries[normalized] += 1
            # Move to end (most popular)
            self.popular_queries.move_to_end(normalized)
        else:
            self.popular_queries[normalized] = 1
        
        # Limit size
        if len(self.popular_queries) > self.max_popular_queries:
            self.popular_queries.popitem(last=False)
    
    def _update_hit_latency(self, latency_ms: float):
        """Update average hit latency"""
        if self.metrics.cache_hits == 1:
            self.metrics.avg_hit_latency_ms = latency_ms
        else:
            # Running average
            self.metrics.avg_hit_latency_ms = (
                (self.metrics.avg_hit_latency_ms * (self.metrics.cache_hits - 1) + latency_ms) 
                / self.metrics.cache_hits
            )
    
    def _update_miss_latency(self, latency_ms: float):
        """Update average miss latency"""
        if self.metrics.cache_misses == 1:
            self.metrics.avg_miss_latency_ms = latency_ms
        else:
            # Running average
            self.metrics.avg_miss_latency_ms = (
                (self.metrics.avg_miss_latency_ms * (self.metrics.cache_misses - 1) + latency_ms) 
                / self.metrics.cache_misses
            )
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics"""
        try:
            # L1 stats
            l1_stats = self.l1_cache.get_stats()
            
            # L2 stats (Redis)
            l2_kql_count = 0
            l2_size_bytes = 0
            
            if hasattr(cache_service, 'redis_client') and cache_service.redis_client:
                try:
                    # Use async scan_iter for Redis
                    async for key in cache_service.redis_client.scan_iter("user:global:kql_query:*"):
                        l2_kql_count += 1
                        try:
                            # Use async get
                            value = await cache_service.redis_client.get(key)
                            if value:
                                l2_size_bytes += len(value.encode('utf-8'))
                        except:
                            pass
                except Exception as e:
                    logger.debug(f"Could not scan Redis keys: {e}")
            
            # Popular queries
            top_queries = list(self.popular_queries.items())[-10:]  # Top 10
            top_queries.reverse()
            
            return {
                "version": self.CACHE_VERSION,
                "metrics": {
                    "total_requests": self.metrics.total_requests,
                    "cache_hits": self.metrics.cache_hits,
                    "cache_misses": self.metrics.cache_misses,
                    "hit_rate": f"{self.metrics.hit_rate * 100:.2f}%",
                    "semantic_hits": self.metrics.semantic_hits,
                    "semantic_hit_rate": f"{self.metrics.semantic_hit_rate * 100:.2f}%",
                    "avg_hit_latency_ms": f"{self.metrics.avg_hit_latency_ms:.2f}",
                    "avg_miss_latency_ms": f"{self.metrics.avg_miss_latency_ms:.2f}",
                },
                "l1_cache": {
                    **l1_stats,
                    "type": "memory",
                    "strategy": "LRU"
                },
                "l2_cache": {
                    "entries": l2_kql_count,
                    "size_mb": l2_size_bytes / (1024 * 1024),
                    "type": "redis",
                    "ttl_days": self.kql_cache_ttl / 86400
                },
                "template_index": {
                    "unique_templates": len(self.template_index),
                    "total_mappings": sum(len(keys) for keys in self.template_index.values())
                },
                "popular_queries": [
                    {"query": q, "count": c} for q, c in top_queries
                ]
            }
            
        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {"error": "Failed to get cache statistics"}
    
    async def warm_cache(self, queries: List[Tuple[str, Dict[str, Any]]]):
        """
        Pre-populate cache with popular queries
        
        Args:
            queries: List of (query, filters) tuples to warm
        """
        logger.info(f" Cache warming: {len(queries)} queries")
        
        for query, filters in queries:
            # Check if already cached
            scope = "|".join([query] + [f"{k}:{v}" for k, v in filters.items()])
            cached = await self.get_cached_kql(query, scope)
            
            if not cached:
                logger.info(f" Cache warming: '{query}' not in cache, needs generation")
    
    async def clear_cache(self):
        """Clear all cache levels"""
        self.l1_cache.clear()
        self.template_index.clear()
        self.popular_queries.clear()
        
        # Clear L2 (Redis) cache
        if hasattr(cache_service, 'redis_client') and cache_service.redis_client:
            try:
                async for key in cache_service.redis_client.scan_iter("user:global:kql_query:*"):
                    await cache_service.redis_client.delete(key)
            except Exception:
                logger.error("Failed to clear L2 Redis cache")

# Global instance
advanced_query_cache = AdvancedQueryCacheService()

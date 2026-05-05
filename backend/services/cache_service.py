import json
import logging
from typing import Optional, Any, Dict, List, Tuple
from datetime import datetime, timedelta
import hashlib
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

try:
    import redis.asyncio as aioredis
    from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    aioredis = None
    RedisConnectionError = Exception
    RedisTimeoutError = Exception

from config import settings

logger = logging.getLogger(__name__)

class CacheService:
    """
    Caching service that can use Azure Cache for Redis or fall back to in-memory cache
    """
    def __init__(self):
        self.redis_client = None
        self.memory_cache: Dict[str, Dict[str, Any]] = {}
        self.use_redis = False
        
    async def initialize(self):
        """Initialize Redis connection if available and configured"""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available, using in-memory cache")
            return
            
        if not settings.REDIS_URL:
            logger.info("Redis URL not configured, using in-memory cache")
            return
            
        try:
            # Parse Azure Redis URL and handle SSL
            if settings.REDIS_URL.startswith('redis://') and '?ssl=true' in settings.REDIS_URL:
                # Convert to rediss:// for SSL
                redis_url = settings.REDIS_URL.replace('redis://', 'rediss://').replace('?ssl=true', '')
            else:
                redis_url = settings.REDIS_URL
                
            # Use async redis client with connection pool optimized for Azure Redis
            self.redis_client = await aioredis.from_url(
                redis_url,
                decode_responses=True,
                max_connections=250,
                socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_keepalive=True,
                health_check_interval=30,
                retry_on_timeout=True
            )
            
            # Test connection
            await self.redis_client.ping()
            self.use_redis = True
            logger.info(" Azure Redis connected (pool=250, timeout=30s, keepalive=enabled)")
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            logger.info("Falling back to in-memory cache")
            self.redis_client = None
            self.use_redis = False
    
    @retry(
        retry=retry_if_exception_type((RedisConnectionError, RedisTimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=False
    )
    async def _redis_operation_with_retry(self, operation, *args, **kwargs):
        """Execute Redis operation with retry logic"""
        try:
            return await operation(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Redis operation failed after retries: {e}")
            # Fallback handled by caller
            raise
    
    def _generate_cache_key(self, user_id: str, key: str) -> str:
        """Generate a cache key for user-specific data"""
        return f"user:{user_id}:{key}"
    
    def _hash_key(self, key: str) -> str:
        """Create a hash of complex keys for better cache key management"""
        return hashlib.md5(key.encode()).hexdigest()
    
    async def set_many(self, items: List[Tuple[str, str, Any, int]]) -> bool:
        """
        Batch set multiple cache items using Redis pipeline for efficiency
        
        Args:
            items: List of tuples (user_id, key, value, ttl)
        
        Returns:
            True if successful, False otherwise
        """
        if not items:
            return True
            
        if self.use_redis and self.redis_client:
            try:
                pipeline = self.redis_client.pipeline()
                
                for user_id, key, value, ttl in items:
                    cache_key = self._generate_cache_key(user_id, key)
                    serialized_data = json.dumps(value, default=str)
                    pipeline.setex(cache_key, ttl, serialized_data)
                
                await self._redis_operation_with_retry(pipeline.execute)
                logger.debug(f"Batch cached {len(items)} items in Redis using pipeline")
                return True
                
            except Exception as e:
                logger.error(f"Batch cache set error: {e}")
                # Fall through to memory cache
        
        # Fallback to memory cache
        for user_id, key, value, ttl in items:
            cache_key = self._generate_cache_key(user_id, key)
            expiry = datetime.now() + timedelta(seconds=ttl)
            self.memory_cache[cache_key] = {
                'data': value,
                'expiry': expiry.isoformat()
            }
        
        logger.debug(f"Batch cached {len(items)} items in memory")
        return True
    
    async def get(self, user_id: str, key: str) -> Optional[Any]:
        """Get cached data for a user with retry logic"""
        cache_key = self._generate_cache_key(user_id, key)
        
        try:
            if self.use_redis and self.redis_client:
                # Use retry wrapper for Redis get
                data = await self._redis_operation_with_retry(
                    self.redis_client.get,
                    cache_key
                )
                if data:
                    return json.loads(data)
                    
            else:
                # Use memory cache
                cached_item = self.memory_cache.get(cache_key)
                if cached_item:
                    expiry = cached_item.get('expiry')
                    if expiry and datetime.fromisoformat(expiry) > datetime.now():
                        return cached_item['data']
                    else:
                        # Remove expired item
                        del self.memory_cache[cache_key]
                        
        except Exception as e:
            logger.debug(f"Cache get error for key {cache_key}: {e}")
            # Silent fallback to None
            
        return None
    
    async def set(self, user_id: str, key: str, value: Any, ttl: int = None) -> bool:
        """Set cached data for a user with optional TTL and retry logic"""
        cache_key = self._generate_cache_key(user_id, key)
        ttl = ttl or settings.CACHE_TTL
        
        try:
            serialized_data = json.dumps(value, default=str)
            
            if self.use_redis and self.redis_client:
                # Use retry wrapper for Redis setex
                await self._redis_operation_with_retry(
                    self.redis_client.setex,
                    cache_key, ttl, serialized_data
                )
                logger.debug(f"Cached data in Redis for key: {cache_key}")
                
            else:
                # Use memory cache
                expiry = datetime.now() + timedelta(seconds=ttl)
                self.memory_cache[cache_key] = {
                    'data': value,
                    'expiry': expiry.isoformat()
                }
                logger.debug(f"Cached data in memory for key: {cache_key}")
                
            return True
            
        except Exception as e:
            logger.warning(f"Cache set error for key {cache_key}: {e} - using in-memory fallback")
            # Fallback to memory cache on Redis failure
            expiry = datetime.now() + timedelta(seconds=ttl)
            self.memory_cache[cache_key] = {
                'data': value,
                'expiry': expiry.isoformat()
            }
            return True
    
    async def delete(self, user_id: str, key: str) -> bool:
        """Delete cached data for a user"""
        cache_key = self._generate_cache_key(user_id, key)
        
        try:
            if self.use_redis and self.redis_client:
                await self.redis_client.delete(cache_key)
            else:
                if cache_key in self.memory_cache:
                    del self.memory_cache[cache_key]
            return True
        except Exception as e:
            logger.error(f"Cache delete error for key {cache_key}: {e}")
            return False

    async def clear_user_cache(self, user_id: str) -> bool:
        """Clear all cached data for a user"""
        try:
            if self.use_redis and self.redis_client:
                pattern = f"user:{user_id}:*"
                # Use native async Redis calls (no run_in_executor needed)
                keys = await self.redis_client.keys(pattern)
                if keys:
                    await self.redis_client.delete(*keys)
                    
            else:
                # Use memory cache
                keys_to_delete = [k for k in self.memory_cache.keys() if k.startswith(f"user:{user_id}:")]
                for key in keys_to_delete:
                    del self.memory_cache[key]
                    
            logger.info(f"Cleared cache for user: {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error clearing cache for user {user_id}: {e}")
            return False
    
    async def clear_prefetch_cache(self) -> bool:
        """Clear prefetch cache (subscriptions, resource types, etc.)"""
        try:
            if self.use_redis and self.redis_client:
                # Clear global prefetch keys using native async calls
                pattern = "global:*"
                keys = await self.redis_client.keys(pattern)
                if keys:
                    await self.redis_client.delete(*keys)
            else:
                # Clear memory cache global keys
                keys_to_delete = [k for k in self.memory_cache.keys() if k.startswith("global:")]
                for key in keys_to_delete:
                    del self.memory_cache[key]
                    
            logger.info("Cleared prefetch cache")
            return True
            
        except Exception as e:
            logger.error(f"Error clearing prefetch cache: {e}")
            return False
    
    async def health_check(self) -> Dict[str, Any]:
        """Check cache service health"""
        result = {
            "redis_available": REDIS_AVAILABLE,
            "redis_connected": self.use_redis,
            "cache_type": "redis" if self.use_redis else "memory",
            "memory_cache_size": len(self.memory_cache)
        }
        
        if self.use_redis and self.redis_client:
            try:
                # Use native async ping (no run_in_executor needed)
                await self.redis_client.ping()
                result["redis_ping"] = "ok"
            except Exception as e:
                result["redis_ping"] = f"failed: {e}"
                
        return result
    
    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            # Use native async close
            await self.redis_client.close()
            self.redis_client = None
            self.use_redis = False

# Global cache instance
cache_service = CacheService()
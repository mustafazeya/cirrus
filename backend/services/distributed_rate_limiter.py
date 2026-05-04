"""
Distributed Rate Limiter using Redis

Provides global rate limiting across multiple container instances for OpenAI API calls.
Uses sliding window algorithm to track concurrent requests and enforce limits.
"""

import logging
import asyncio
import time
from typing import Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DistributedRateLimiter:
    """
    Distributed rate limiter using Redis for cross-instance coordination.
    
    Uses a sliding window approach to track:
    - Concurrent requests (max N at a time across ALL instances)
    - Request duration tracking
    - Metrics and visibility
    """
    
    def __init__(self, redis_client, key_prefix: str, max_concurrent: int, timeout: int = 300):
        """
        Initialize distributed rate limiter.
        
        Args:
            redis_client: Async Redis client
            key_prefix: Prefix for Redis keys (e.g., 'openai_limiter')
            max_concurrent: Maximum concurrent requests across ALL instances
            timeout: Request timeout in seconds (default 5 minutes)
        """
        self.redis = redis_client
        self.key_prefix = key_prefix
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        
        # Redis keys
        self.active_key = f"{key_prefix}:active"  # Sorted set of active requests
        self.waiting_key = f"{key_prefix}:waiting"  # Counter of waiting requests
        self.stats_key = f"{key_prefix}:stats"  # Hash of statistics
    
    async def acquire(self, request_id: Optional[str] = None, wait_timeout: float = 30.0) -> bool:
        """
        Acquire a slot for making an API call.
        
        Args:
            request_id: Unique request ID (auto-generated if not provided)
            wait_timeout: Maximum time to wait for a slot (seconds)
        
        Returns:
            bool: True if acquired, False if timeout
        """
        if request_id is None:
            request_id = f"{time.time()}_{id(asyncio.current_task())}"
        
        start_time = time.time()
        
        # Increment waiting counter
        await self.redis.incr(self.waiting_key)
        
        try:
            while True:
                # Clean up expired requests (stuck requests that didn't release)
                cutoff_time = time.time() - self.timeout
                await self.redis.zremrangebyscore(self.active_key, 0, cutoff_time)
                
                # Check current active count
                active_count = await self.redis.zcard(self.active_key)
                
                if active_count < self.max_concurrent:
                    # Slot available - claim it
                    current_time = time.time()
                    await self.redis.zadd(self.active_key, {request_id: current_time})
                    
                    # Decrement waiting counter
                    await self.redis.decr(self.waiting_key)
                    
                    # Update stats
                    await self._record_acquisition(wait_time=time.time() - start_time)
                    
                    logger.info(f" Acquired OpenAI slot: {request_id} ({active_count + 1}/{self.max_concurrent} active)")
                    return True
                
                # Check if we've waited too long
                if time.time() - start_time > wait_timeout:
                    await self.redis.decr(self.waiting_key)
                    logger.warning(f" Rate limiter timeout after {wait_timeout}s: {request_id}")
                    return False
                
                # Wait a bit and retry
                await asyncio.sleep(0.1)
        
        except Exception as e:
            logger.error(f"Error acquiring rate limit slot: {e}")
            await self.redis.decr(self.waiting_key)
            return False
    
    async def release(self, request_id: str):
        """
        Release a slot after API call completes.
        
        Args:
            request_id: The request ID that was acquired
        """
        try:
            removed = await self.redis.zrem(self.active_key, request_id)
            if removed:
                active_count = await self.redis.zcard(self.active_key)
                logger.info(f" Released OpenAI slot: {request_id} ({active_count}/{self.max_concurrent} active)")
        except Exception as e:
            logger.error(f"Error releasing rate limit slot: {e}")
    
    async def __aenter__(self):
        """Context manager entry - acquire slot"""
        self.request_id = f"{time.time()}_{id(asyncio.current_task())}"
        acquired = await self.acquire(self.request_id)
        if not acquired:
            raise TimeoutError("Failed to acquire OpenAI rate limit slot within timeout")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - release slot"""
        await self.release(self.request_id)
        return False
    
    async def get_stats(self) -> dict:
        """
        Get current rate limiter statistics.
        
        Returns:
            dict: Statistics including active count, waiting count, etc.
        """
        try:
            active_count = await self.redis.zcard(self.active_key)
            waiting_count = int(await self.redis.get(self.waiting_key) or 0)
            
            # Get stats from hash
            stats_raw = await self.redis.hgetall(self.stats_key)
            total_requests = int(stats_raw.get("total_requests", 0))
            total_wait_time = float(stats_raw.get("total_wait_time", 0))
            
            avg_wait_time = total_wait_time / total_requests if total_requests > 0 else 0
            
            return {
                "active_requests": active_count,
                "waiting_requests": waiting_count,
                "max_concurrent": self.max_concurrent,
                "utilization": active_count / self.max_concurrent if self.max_concurrent > 0 else 0,
                "total_requests": total_requests,
                "average_wait_time_ms": avg_wait_time * 1000
            }
        except Exception as e:
            logger.error(f"Error getting rate limiter stats: {e}")
            return {
                "error": str(e),
                "active_requests": 0,
                "waiting_requests": 0,
                "max_concurrent": self.max_concurrent
            }
    
    async def _record_acquisition(self, wait_time: float):
        """Record statistics about slot acquisition"""
        try:
            await self.redis.hincrby(self.stats_key, "total_requests", 1)
            await self.redis.hincrbyfloat(self.stats_key, "total_wait_time", wait_time)
        except Exception as e:
            logger.error(f"Error recording stats: {e}")
    
    async def reset_stats(self):
        """Reset statistics counters"""
        try:
            await self.redis.delete(self.stats_key)
            logger.info(" Rate limiter statistics reset")
        except Exception as e:
            logger.error(f"Error resetting stats: {e}")

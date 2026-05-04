"""
Metrics Service - Track API usage, performance, and system metrics
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict
import time
import asyncio
import json
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


class MetricsService:
    """Service for tracking and reporting system metrics"""
    
    def __init__(self):
        self.redis_client = None
        self.use_redis = False
        # Fallback in-memory storage
        self.api_requests = []
        self.active_users = {}
        self.error_logs = []
        self.endpoint_stats = defaultdict(lambda: {
            'count': 0,
            'total_time': 0,
            'errors': 0
        })
        
    # Fire-and-forget wrappers for backwards compatibility
    def track_request(self, *args, **kwargs):
        """Synchronous wrapper - creates background task"""
        asyncio.create_task(self._track_request(*args, **kwargs))
    
    def track_user_activity(self, *args, **kwargs):
        """Synchronous wrapper - creates background task"""
        asyncio.create_task(self._track_user_activity(*args, **kwargs))
        
    async def initialize(self):
        """Initialize Redis connection if available"""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available for metrics, using in-memory storage")
            return
            
        if not settings.REDIS_URL:
            logger.info("Redis URL not configured for metrics, using in-memory storage")
            return
            
        try:
            # Parse Azure Redis URL and handle SSL
            if settings.REDIS_URL.startswith('redis://') and '?ssl=true' in settings.REDIS_URL:
                redis_url = settings.REDIS_URL.replace('redis://', 'rediss://').replace('?ssl=true', '')
            else:
                redis_url = settings.REDIS_URL
                
            self.redis_client = await aioredis.from_url(
                redis_url,
                decode_responses=True,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
                socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
                socket_keepalive=True,
                health_check_interval=settings.REDIS_HEALTH_CHECK_INTERVAL,
                retry_on_timeout=True
            )
            
            # Test connection
            await self.redis_client.ping()
            self.use_redis = True
            logger.info(" Metrics service connected to Redis (pool=250, timeout=30s, keepalive=enabled)")
            
        except Exception as e:
            logger.error(f"Failed to connect metrics to Redis: {e}")
            logger.info("Falling back to in-memory metrics storage")
        
    async def _track_request(self, 
                     endpoint: str, 
                     method: str, 
                     status_code: int, 
                     duration_ms: float,
                     user_email: Optional[str] = None):
        """Track an API request (internal async method)"""
        try:
            # Skip tracking admin endpoints to avoid skewing metrics
            if endpoint.startswith('/api/admin'):
                return
            
            request_log = {
                'timestamp': datetime.utcnow().isoformat(),
                'endpoint': endpoint,
                'method': method,
                'status_code': status_code,
                'duration_ms': duration_ms,
                'user_email': user_email
            }
            
            if self.use_redis and self.redis_client:
                # Use pipeline for batch writes with retry
                await self._write_request_metrics_with_retry(request_log, endpoint, method, status_code, duration_ms)
            else:
                # Fallback to in-memory
                self.api_requests.append(request_log)
                
        except Exception as e:
            logger.error(f"Failed to track request: {e}")
    
    @retry(
        retry=retry_if_exception_type((RedisConnectionError, RedisTimeoutError, ConnectionError)),
        stop=stop_after_attempt(settings.REDIS_WRITE_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=settings.REDIS_WRITE_RETRY_MIN_WAIT, max=settings.REDIS_WRITE_RETRY_MAX_WAIT),
        reraise=False
    )
    async def _write_request_metrics_with_retry(self, request_log, endpoint, method, status_code, duration_ms):
        """Write request metrics using pipeline with retry logic"""
        try:
            date_key = datetime.utcnow().strftime('%Y-%m-%d')
            
            # Use pipeline to batch all metric writes
            pipeline = self.redis_client.pipeline()
            
            # Store request log
            redis_key = f"metrics:requests:{date_key}"
            pipeline.rpush(redis_key, json.dumps(request_log))
            pipeline.expire(redis_key, 24 * 3600)
            
            # Update endpoint stats
            endpoint_key = f"metrics:endpoint:{method}:{endpoint}"
            pipeline.hincrby(endpoint_key, 'count', 1)
            pipeline.hincrbyfloat(endpoint_key, 'total_time', duration_ms)
            if status_code >= 400:
                pipeline.hincrby(endpoint_key, 'errors', 1)
            pipeline.expire(endpoint_key, 24 * 3600)
            
            # Store errors separately
            if status_code >= 400:
                error_key = f"metrics:errors:{date_key}"
                pipeline.rpush(error_key, json.dumps(request_log))
                pipeline.expire(error_key, 24 * 3600)
            
            # Execute all in one batch
            await pipeline.execute()
            
        except Exception as e:
            logger.warning(f"Failed to write metrics after retries: {e}")
            # Silent failure - metrics are non-critical
    
    async def _track_user_activity(self, user_email: str):
        """Track user activity (internal async method)"""
        try:
            timestamp = datetime.utcnow().isoformat()
            if self.use_redis and self.redis_client:
                # Store in Redis with TTL (using async)
                await self.redis_client.setex(
                    f"metrics:user:active:{user_email}",
                    3600,  # 1 hour TTL
                    timestamp
                )
            else:
                self.active_users[user_email] = datetime.utcnow()
        except Exception as e:
            logger.error(f"Failed to track user activity: {e}")
    
    async def get_api_metrics(self, hours: int = 24) -> Dict[str, Any]:
        """Get API metrics for the last N hours"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)
            recent_requests = []
            
            if self.use_redis and self.redis_client:
                # Fetch from Redis for the date range (using async)
                days_to_check = (hours // 24) + 2  # Check a few extra days
                for i in range(days_to_check):
                    date = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
                    redis_key = f"metrics:requests:{date}"
                    try:
                        requests = await self.redis_client.lrange(redis_key, 0, -1)
                        for req_str in requests:
                            req = json.loads(req_str)
                            # Skip admin endpoints
                            if req['endpoint'].startswith('/api/admin'):
                                continue
                            if datetime.fromisoformat(req['timestamp']) > cutoff_time:
                                recent_requests.append(req)
                    except:
                        pass
            else:
                # Fallback to in-memory
                recent_requests = [
                    req for req in self.api_requests
                    if datetime.fromisoformat(req['timestamp']) > cutoff_time
                    and not req['endpoint'].startswith('/api/admin')
                ]
            
            if not recent_requests:
                return {
                    'total_requests': 0,
                    'avg_response_time_ms': 0,
                    'error_rate': 0,
                    'requests_per_hour': 0,
                    'status_codes': {},
                    'top_endpoints': [],
                    'time_window_hours': hours
                }
            
            # Calculate metrics
            total_requests = len(recent_requests)
            total_time = sum(req['duration_ms'] for req in recent_requests)
            avg_response_time = total_time / total_requests if total_requests > 0 else 0
            
            error_count = sum(1 for req in recent_requests if req['status_code'] >= 400)
            error_rate = (error_count / total_requests * 100) if total_requests > 0 else 0
            
            # Status code distribution
            status_codes = defaultdict(int)
            for req in recent_requests:
                status_codes[str(req['status_code'])] += 1
            
            # Top endpoints
            endpoint_counts = defaultdict(int)
            endpoint_times = defaultdict(list)
            for req in recent_requests:
                key = f"{req['method']} {req['endpoint']}"
                endpoint_counts[key] += 1
                endpoint_times[key].append(req['duration_ms'])
            
            top_endpoints = sorted(
                [
                    {
                        'endpoint': endpoint,
                        'count': count,
                        'avg_time_ms': sum(endpoint_times[endpoint]) / len(endpoint_times[endpoint])
                    }
                    for endpoint, count in endpoint_counts.items()
                ],
                key=lambda x: x['count'],
                reverse=True
            )[:10]
            
            return {
                'total_requests': total_requests,
                'avg_response_time_ms': round(avg_response_time, 2),
                'error_rate': round(error_rate, 2),
                'requests_per_hour': round(total_requests / hours, 2),
                'status_codes': dict(status_codes),
                'top_endpoints': top_endpoints,
                'time_window_hours': hours
            }
            
        except Exception as e:
            logger.error(f"Failed to get API metrics: {e}")
            # Return safe defaults without exposing internal exception details
            return {
                'total_requests': 0,
                'avg_response_time_ms': 0,
                'error_rate': 0,
                'requests_per_hour': 0,
                'status_codes': {},
                'top_endpoints': [],
                'time_window_hours': hours,
                'error': 'Failed to get API metrics'
            }
    
    async def get_active_users(self, minutes: int = 30) -> Dict[str, Any]:
        """Get active users in the last N minutes"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
            cutoff_24h = datetime.utcnow() - timedelta(hours=24)
            cutoff_7d = datetime.utcnow() - timedelta(days=7)
            
            active_now = []
            users_24h = set()
            users_7d = set()
            
            if self.use_redis and self.redis_client:
                # Get users from request logs (more reliable) - using async
                days_to_check = settings.METRICS_USER_DAYS_LOOKBACK  # sourced from config.py
                user_activity = {}
                
                for i in range(days_to_check):
                    date = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
                    redis_key = f"metrics:requests:{date}"
                    try:
                        requests = await self.redis_client.lrange(redis_key, 0, -1)
                        for req_str in requests:
                            req = json.loads(req_str)
                            if req.get('user_email'):
                                email = req['user_email']
                                timestamp = datetime.fromisoformat(req['timestamp'])
                                
                                # Track most recent activity per user
                                if email not in user_activity or timestamp > user_activity[email]:
                                    user_activity[email] = timestamp
                    except:
                        pass
                
                # Categorize users by activity time
                for email, timestamp in user_activity.items():
                    if timestamp > cutoff_time:
                        active_now.append({'email': email, 'last_seen': timestamp.isoformat()})
                    if timestamp > cutoff_24h:
                        users_24h.add(email)
                    if timestamp > cutoff_7d:
                        users_7d.add(email)
            else:
                # Fallback to in-memory - check both active_users dict and request logs
                user_activity = {}
                
                # Get from active_users dict
                for email, timestamp in self.active_users.items():
                    user_activity[email] = timestamp
                
                # Get from request logs
                for req in self.api_requests:
                    if req.get('user_email'):
                        email = req['user_email']
                        timestamp = datetime.fromisoformat(req['timestamp'])
                        if email not in user_activity or timestamp > user_activity[email]:
                            user_activity[email] = timestamp
                
                # Categorize users
                for email, timestamp in user_activity.items():
                    if timestamp > cutoff_time:
                        active_now.append({'email': email, 'last_seen': timestamp.isoformat()})
                    if timestamp > cutoff_24h:
                        users_24h.add(email)
                    if timestamp > cutoff_7d:
                        users_7d.add(email)
            
            return {
                'active_now_count': len(active_now),
                'active_now': sorted(active_now, key=lambda x: x['last_seen'], reverse=True)[:20],
                'users_last_24h': len(users_24h),
                'users_last_7d': len(users_7d),
                'total_unique_users': len(users_24h | users_7d),
                'time_window_minutes': minutes
            }
            
        except Exception as e:
            logger.error(f"Failed to get active users: {e}")
            # Return safe defaults without exposing internal exception details
            return {
                'active_now_count': 0,
                'active_now': [],
                'users_last_24h': 0,
                'users_last_7d': 0,
                'total_unique_users': 0,
                'time_window_minutes': minutes,
                'error': 'Failed to get active users'
            }
    
    def get_error_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent error logs"""
        try:
            return sorted(
                self.error_logs[-limit:],
                key=lambda x: x['timestamp'],
                reverse=True
            )
        except Exception as e:
            logger.error(f"Failed to get error logs: {e}")
            return []
    
    async def get_performance_stats(self) -> Dict[str, Any]:
        """Get overall performance statistics"""
        try:
            all_times = []
            
            if self.use_redis and self.redis_client:
                # Get from Redis (last 24 hours) - using async
                for i in range(2):
                    date = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
                    redis_key = f"metrics:requests:{date}"
                    try:
                        requests = await self.redis_client.lrange(redis_key, 0, -1)
                        for req_str in requests:
                            req = json.loads(req_str)
                            # Skip admin endpoints
                            if not req['endpoint'].startswith('/api/admin'):
                                all_times.append(req['duration_ms'])
                    except:
                        pass
            else:
                # Fallback to in-memory
                all_times = [
                    req['duration_ms'] for req in self.api_requests
                    if not req['endpoint'].startswith('/api/admin')
                ]
            
            if not all_times:
                return {
                    'total_requests': 0,
                    'endpoints_tracked': 0,
                    'response_times': {
                        'p50': 0,
                        'p95': 0,
                        'p99': 0
                    }
                }
            
            # Calculate percentiles
            all_times.sort()
            
            def percentile(data, p):
                if not data:
                    return 0
                k = (len(data) - 1) * p / 100
                f = int(k)
                c = f + 1 if f < len(data) - 1 else f
                return data[f] + (k - f) * (data[c] - data[f])
            
            return {
                'total_requests': len(all_times),
                'endpoints_tracked': len(self.endpoint_stats),
                'response_times': {
                    'p50': round(percentile(all_times, 50), 2),
                    'p95': round(percentile(all_times, 95), 2),
                    'p99': round(percentile(all_times, 99), 2),
                    'max': round(max(all_times), 2) if all_times else 0,
                    'min': round(min(all_times), 2) if all_times else 0
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get performance stats: {e}")
            # Return safe defaults without exposing internal exception details
            return {
                'total_requests': 0,
                'endpoints_tracked': 0,
                'response_times': {
                    'p50': 0,
                    'p95': 0,
                    'p99': 0,
                    'max': 0,
                    'min': 0
                },
                'error': 'Failed to get performance stats'
            }
    
    def clear_old_data(self, days: int = 7):
        """Clear data older than N days"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(days=days)
            
            # Clear old requests
            self.api_requests = [
                req for req in self.api_requests
                if datetime.fromisoformat(req['timestamp']) > cutoff_time
            ]
            
            # Clear old errors
            self.error_logs = [
                err for err in self.error_logs
                if datetime.fromisoformat(err['timestamp']) > cutoff_time
            ]
            
            # Clear inactive users
            self.active_users = {
                email: timestamp
                for email, timestamp in self.active_users.items()
                if timestamp > cutoff_time
            }
            
            logger.info(f"Cleared metrics data older than {days} days")
            
        except Exception as e:
            logger.error(f"Failed to clear old data: {e}")


# Global metrics service instance
metrics_service = MetricsService()

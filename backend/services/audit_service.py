"""
Audit Service - Log admin actions and security events
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from enum import Enum
import json
import asyncio
from utils.timezone_utils import now_cet
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


class AuditActionType(str, Enum):
    """Types of actions to audit"""
    ADMIN_LOGIN = "admin_login"
    ADMIN_ACTION = "admin_action"
    CACHE_CLEAR = "cache_clear"
    PREFETCH_TRIGGER = "prefetch_trigger"
    CONFIG_CHANGE = "config_change"
    USER_LOGIN = "user_login"
    FAILED_AUTH = "failed_auth"
    PERMISSION_DENIED = "permission_denied"
    DATA_EXPORT = "data_export"
    QUERY_EXECUTION = "query_execution"
    GOVERNANCE_VIEW = "governance_view"


class AuditService:
    """Service for logging and retrieving audit events"""
    
    def __init__(self):
        self.redis_client = None
        self.use_redis = False
        # Fallback in-memory storage
        self.audit_logs = []
        
    # Fire-and-forget wrappers for backwards compatibility
    def log_event(self, *args, **kwargs):
        """Synchronous wrapper - creates background task"""
        asyncio.create_task(self._log_event(*args, **kwargs))
    
    def log_admin_action(self, *args, **kwargs):
        """Synchronous wrapper - creates background task"""
        asyncio.create_task(self._log_admin_action(*args, **kwargs))
    
    def log_failed_auth(self, *args, **kwargs):
        """Synchronous wrapper - creates background task"""
        asyncio.create_task(self._log_failed_auth(*args, **kwargs))
    
    def log_permission_denied(self, *args, **kwargs):
        """Synchronous wrapper - creates background task"""
        asyncio.create_task(self._log_permission_denied(*args, **kwargs))
        
    async def initialize(self):
        """Initialize Redis connection if available"""
        if not REDIS_AVAILABLE:
            logger.warning("Redis not available for audit logs, using in-memory storage")
            return
            
        if not settings.REDIS_URL:
            logger.info("Redis URL not configured for audit logs, using in-memory storage")
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
            logger.info(" Audit service connected to Redis (pool=250, timeout=30s, keepalive=enabled)")
            
        except Exception as e:
            logger.error(f"Failed to connect audit to Redis: {e}")
            logger.info("Falling back to in-memory audit storage")
        
    async def _log_event(self,
                  action: AuditActionType,
                  user_email: str,
                  details: Optional[Dict[str, Any]] = None,
                  resource: Optional[str] = None,
                  status: str = "success",
                  ip_address: Optional[str] = None):
        """Log an audit event (internal async method)"""
        try:
            event = {
                'timestamp': now_cet().isoformat(),
                'action': action.value if isinstance(action, AuditActionType) else action,
                'user_email': user_email,
                'resource': resource,
                'status': status,
                'details': details or {},
                'ip_address': ip_address
            }
            
            if self.use_redis and self.redis_client:
                # Store in Redis with retry logic
                await self._write_audit_log_with_retry(event)
            else:
                # Fallback to in-memory
                self.audit_logs.append(event)
                
                # Keep only last 10000 events (memory management)
                if len(self.audit_logs) > 10000:
                    self.audit_logs = self.audit_logs[-10000:]
            
            # Log to file as well (avoid logging PII)
            logger.debug(f"AUDIT: {action} - {status}")
                
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
    
    @retry(
        retry=retry_if_exception_type((RedisConnectionError, RedisTimeoutError, ConnectionError)),
        stop=stop_after_attempt(settings.REDIS_WRITE_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=settings.REDIS_WRITE_RETRY_MIN_WAIT, max=settings.REDIS_WRITE_RETRY_MAX_WAIT),
        reraise=False
    )
    async def _write_audit_log_with_retry(self, event):
        """Write audit log with retry logic"""
        try:
            date_key = now_cet().strftime('%Y-%m-%d')
            redis_key = f"audit:logs:{date_key}"
            
            # Use pipeline for batch operations
            pipeline = self.redis_client.pipeline()
            pipeline.rpush(redis_key, json.dumps(event))
            pipeline.expire(redis_key, 24 * 3600)
            await pipeline.execute()
            
        except Exception as e:
            logger.warning(f"Failed to write audit log after retries: {e}")
            # Silent failure - audit logs are important but not critical for app functionality
    
    async def _log_admin_action(self,
                        user_email: str,
                        action: str,
                        details: Optional[Dict[str, Any]] = None):
        """Log an admin action (internal async method)"""
        await self._log_event(
            action=AuditActionType.ADMIN_ACTION,
            user_email=user_email,
            details={'action': action, **(details or {})},
            resource=action
        )
    
    async def log_failed_auth(self,
                       user_email: str,
                       reason: str,
                       ip_address: Optional[str] = None):
        """Log a failed authentication attempt (internal async method)"""
        await self._log_event(
            action=AuditActionType.FAILED_AUTH,
            user_email=user_email,
            details={'reason': reason},
            status='failed',
            ip_address=ip_address
        )
    
    async def log_permission_denied(self,
                            user_email: str,
                            resource: str,
                            action: str):
        """Log a permission denied event (internal async method)"""
        await self._log_event(
            action=AuditActionType.PERMISSION_DENIED,
            user_email=user_email,
            resource=resource,
            details={'attempted_action': action},
            status='denied'
        )
    
    async def get_audit_logs(self,
                      hours: Optional[int] = 24,
                      action_type: Optional[str] = None,
                      user_email: Optional[str] = None,
                      limit: int = settings.AUDIT_LOG_DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        """Get audit logs with filters"""
        try:
            logs = []
            
            if self.use_redis and self.redis_client:
                # Fetch from Redis for the date range (only check last 2 days for 24h window)
                days_to_check = settings.AUDIT_LOG_DAYS_LOOKBACK
                cutoff_time = now_cet() - timedelta(hours=hours) if hours else None
                
                for i in range(days_to_check):
                    date = (now_cet() - timedelta(days=i)).strftime('%Y-%m-%d')
                    redis_key = f"audit:logs:{date}"
                    try:
                        log_entries = await self.redis_client.lrange(redis_key, 0, -1)
                        for log_str in log_entries:
                            log = json.loads(log_str)
                            log_time = datetime.fromisoformat(log['timestamp']).replace(tzinfo=None)
                            if cutoff_time and log_time <= cutoff_time.replace(tzinfo=None):
                                continue
                            logs.append(log)
                    except Exception as e:
                        logger.debug(f"Error parsing log entry: {e}")
                        pass
            else:
                # Fallback to in-memory
                logs = self.audit_logs.copy()
                
                # Filter by time
                if hours:
                    cutoff_time = now_cet() - timedelta(hours=hours)
                    filtered_logs = []
                    for log in logs:
                        try:
                            log_time = datetime.fromisoformat(log['timestamp'])
                            # Remove timezone info for comparison
                            if log_time.replace(tzinfo=None) > cutoff_time.replace(tzinfo=None):
                                filtered_logs.append(log)
                        except:
                            # Include logs with invalid timestamps
                            filtered_logs.append(log)
                    logs = filtered_logs
            
            # Filter by action type
            if action_type:
                logs = [log for log in logs if log['action'] == action_type]
            
            # Filter by user
            if user_email:
                logs = [log for log in logs if log['user_email'] == user_email]
            
            # Sort by timestamp (newest first) and limit
            logs = sorted(logs, key=lambda x: x['timestamp'], reverse=True)
            return logs[:limit]
            
        except Exception as e:
            logger.error(f"Failed to get audit logs: {e}")
            return []
    
    async def get_audit_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get summary of audit events"""
        try:
            cutoff_time = now_cet() - timedelta(hours=hours)
            recent_logs = []
            
            if self.use_redis and self.redis_client:
                # Fetch from Redis (last 2 days for 24h window)
                for i in range(2):
                    date = (now_cet() - timedelta(days=i)).strftime('%Y-%m-%d')
                    redis_key = f"audit:logs:{date}"
                    try:
                        log_entries = await self.redis_client.lrange(redis_key, 0, -1)
                        for log_str in log_entries:
                            log = json.loads(log_str)
                            log_time = datetime.fromisoformat(log['timestamp']).replace(tzinfo=None)
                            if log_time > cutoff_time.replace(tzinfo=None):
                                recent_logs.append(log)
                    except Exception as e:
                        logger.debug(f"Error parsing log entry in summary: {e}")
                        pass
            else:
                # Fallback to in-memory
                recent_logs = [
                    log for log in self.audit_logs
                    if datetime.fromisoformat(log['timestamp']) > cutoff_time
                ]
            
            if not recent_logs:
                return {
                    'total_events': 0,
                    'failed_auth_count': 0,
                    'permission_denial_count': 0,
                    'time_window_hours': hours
                }
            
            # Count by action type
            action_counts = {}
            for log in recent_logs:
                action = log['action']
                action_counts[action] = action_counts.get(action, 0) + 1
            
            # Count by status
            status_counts = {}
            for log in recent_logs:
                status = log['status']
                status_counts[status] = status_counts.get(status, 0) + 1
            
            # Get unique users
            unique_users = set(log['user_email'] for log in recent_logs)
            
            # Get failed auth attempts
            failed_auths = [
                log for log in recent_logs
                if log['action'] == AuditActionType.FAILED_AUTH.value
            ]
            
            # Get permission denials
            permission_denials = [
                log for log in recent_logs
                if log['action'] == AuditActionType.PERMISSION_DENIED.value
            ]
            
            return {
                'total_events': len(recent_logs),
                'unique_users': len(unique_users),
                'action_counts': action_counts,
                'status_counts': status_counts,
                'failed_auth_count': len(failed_auths),
                'permission_denial_count': len(permission_denials),
                'time_window_hours': hours,
                'recent_failed_auths': failed_auths[:10],
                'recent_permission_denials': permission_denials[:10]
            }
            
        except Exception as e:
            logger.error(f"Failed to get audit summary: {e}")
            # Return safe defaults without exposing internal exception details
            return {
                'total_events': 0,
                'unique_users': 0,
                'action_counts': {},
                'status_counts': {},
                'failed_auth_count': 0,
                'permission_denial_count': 0,
                'time_window_hours': hours,
                'recent_failed_auths': [],
                'recent_permission_denials': [],
                'error': 'Failed to get audit summary'
            }
    
    def get_user_activity(self, user_email: str, hours: int = 24) -> Dict[str, Any]:
        """Get activity for a specific user"""
        try:
            cutoff_time = now_cet() - timedelta(hours=hours)
            user_logs = [
                log for log in self.audit_logs
                if log['user_email'] == user_email
                and datetime.fromisoformat(log['timestamp']) > cutoff_time
            ]
            
            # Sort by timestamp (newest first)
            user_logs = sorted(user_logs, key=lambda x: x['timestamp'], reverse=True)
            
            # Count actions
            action_counts = {}
            for log in user_logs:
                action = log['action']
                action_counts[action] = action_counts.get(action, 0) + 1
            
            return {
                'user_email': user_email,
                'total_actions': len(user_logs),
                'action_counts': action_counts,
                'recent_actions': user_logs[:50],
                'time_window_hours': hours
            }
            
        except Exception as e:
            logger.error(f"Failed to get user activity: {e}")
            return {
                'total_actions': 0,
                'actions': [],
                'action_types': {},
                'time_window_hours': hours,
                'has_error': True
            }
    
    def clear_old_logs(self, days: int = settings.AUDIT_LOG_RETENTION_DAYS):
        """Clear logs older than N days"""
        try:
            cutoff_time = now_cet() - timedelta(days=days)
            self.audit_logs = [
                log for log in self.audit_logs
                if datetime.fromisoformat(log['timestamp']) > cutoff_time
            ]
            logger.info(f"Cleared audit logs older than {days} days")
        except Exception as e:
            logger.error(f"Failed to clear old logs: {e}")


# Global audit service instance
audit_service = AuditService()

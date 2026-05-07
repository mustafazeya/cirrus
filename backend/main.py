from fastapi import FastAPI, Depends, HTTPException, status, Request, Query, Response, BackgroundTasks
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, validator
import logging
from typing import List, Dict, Any, Optional
import asyncio
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time
import httpx
import traceback
# import aioredis  # Temporarily disabled for Python 3.13 compatibility
from tenacity import retry, stop_after_attempt, wait_exponential

# Load environment variables from .env file
load_dotenv('.env')

# Application Insights setup
try:
    from azure.monitor.opentelemetry import configure_azure_monitor
    APPINSIGHTS_AVAILABLE = True
except ImportError:
    APPINSIGHTS_AVAILABLE = False

from config import settings
from auth import AzureAuthHandler
auth_handler = AzureAuthHandler()
from services.resource_service import ResourceService
from services.kql_generation_service import AIService
from services.cache_service import cache_service
from services.ai_query_service import ai_query_service
from services.dual_credential_service import dual_cred_service
from services.scheduler_service import scheduler_service
from services.advanced_query_cache_service import advanced_query_cache
from services.conversation_service import conversation_service
from services.advanced_cost_service import AdvancedCostService
from services.metrics_service import metrics_service
from services.audit_service import audit_service, AuditActionType
from services.governance_service import GovernanceService
from services.change_analysis_service import change_analysis_service
from services.root_cause_analysis_service import root_cause_analysis_service
from services.query_template_service import query_template_service
from services.user_access_service import user_access_service
from services.saved_view_service import SavedViewService
from models.conversation import AIResponse, MessageType
from models.query_template import (
    QueryTemplate,
    QueryTemplateCreate,
    QueryTemplateUpdate,
    QueryTemplateExecute,
    TemplateSearchFilters,
    TemplateCategory
)
from models.saved_view import (
    SavedView,
    CreateSavedViewRequest,
    UpdateSavedViewRequest,
    SavedViewListResponse
)

# ============================================================================
# Logging Configuration
# ============================================================================
# All logging configuration is centralized in logging_config.py
# To adjust log levels (INFO/DEBUG/WARNING/ERROR), edit the constants at the
# top of logging_config.py (APP_LOG_LEVEL, UVICORN_LOG_LEVEL, etc.)
from logging_config import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    try:
        await cache_service.initialize()
        logger.info(" Cache service initialized")
        
        # Initialize metrics and audit services
        await metrics_service.initialize()
        logger.info(" Metrics service initialized")
        
        await audit_service.initialize()
        logger.info(" Audit service initialized")
        
        # Link audit service to auth handler for SSO failure tracking
        auth_handler.audit_service = audit_service
        logger.info(" Auth handler linked to audit service")
        
        # Initialize global AI service with distributed rate limiter
        global ai_service
        from services.kql_generation_service import AIService
        ai_service = AIService()
        if ai_service.is_available():
            ai_service.set_rate_limiter(cache_service.redis_client)
            logger.info(" Distributed OpenAI rate limiter initialized (max 50 concurrent globally)")
        else:
            logger.warning(" AI service not available (OpenAI not configured)")
        
        # Azure Cost Management API has STRICT rate limits
        logger.info(" Azure Cost Management API: 12 QPU/10s, 60 QPU/1min, 600 QPU/1hr")
        logger.info(" QPU budget tracking is ACTIVE to prevent 429 rate limit errors")
        
        # Start background scheduler for prefetching
        scheduler_service.start_scheduler()
        logger.info(" Background scheduler started successfully")
        
        # Load templates from Redis
        await query_template_service.load_all_from_redis()
        
        # Start pub/sub listener for real-time template sync across workers
        await query_template_service.start_pubsub()
        logger.info(" Template pub/sub listener started for multi-worker sync")
        
        # Note: Initial prefetch is handled by scheduler (runs 30 seconds after startup)
        
    except Exception as e:
        logger.error(f" Startup initialization failed: {type(e).__name__}. Continuing with minimal setup.")
    
    # Documentation examples now handled by Azure OpenAI + Azure AI Search
    logger.info(" Using Azure OpenAI with Azure AI Search for documentation examples")
    
    # Refresh ARG table mappings from Microsoft documentation (non-blocking)
    try:
        from utils.arg_table_mapper import refresh_table_mappings_from_docs
        import asyncio
        # Run in background, don't block startup
        asyncio.create_task(refresh_table_mappings_from_docs())
        logger.info(" ARG table mappings refresh started in background")
    except Exception as e:
        logger.warning(f" Failed to start ARG table mappings refresh: {e}. Will use fallback.")
    
    # Refresh Log Analytics table schemas from Microsoft Learn (non-blocking)
    try:
        from utils.log_analytics_table_mapper import log_analytics_table_mapper
        # Helper to ensure cache_service is ready before refresh
        async def delayed_la_refresh():
            await asyncio.sleep(2)  # Wait for cache_service to fully initialize
            await log_analytics_table_mapper.refresh_cache()
        asyncio.create_task(delayed_la_refresh())
        logger.info(" Log Analytics table schemas refresh started in background")
    except Exception as e:
        logger.warning(f" Failed to start Log Analytics table schemas refresh: {e}")
    
    yield
    
    # Shutdown
    
    try:
        # Stop pub/sub listener
        await query_template_service.stop_pubsub()
        logger.info("Template pub/sub listener stopped")
        
        # Stop scheduler
        scheduler_service.stop_scheduler()
        logger.info("Background scheduler stopped")
        
        await cache_service.close()
        logger.info("Cache service closed")
    except Exception as e:
        logger.error(f"Error closing services: {e}")

# Configure Application Insights BEFORE app = FastAPI() so that
# azure-monitor-opentelemetry can auto-instrument FastAPI HTTP requests.
# Must run before the ASGI app is created — moving it after loses request tracking.
if APPINSIGHTS_AVAILABLE and settings.APPINSIGHTS_CONNECTION_STRING:
    try:
        configure_azure_monitor(
            connection_string=settings.APPINSIGHTS_CONNECTION_STRING,
            service_name=f"{settings.APPINSIGHTS_SERVICE_NAME}-backend-{settings.ENVIRONMENT}",
            service_namespace=settings.APPINSIGHTS_SERVICE_NAME,
            service_instance_id=f"{settings.APPINSIGHTS_SERVICE_NAME}-backend-{settings.ENVIRONMENT}",
        )
        logger.info(f"Application Insights enabled (cloud_RoleName={settings.APPINSIGHTS_SERVICE_NAME}-backend-{settings.ENVIRONMENT})")
    except Exception as e:
        logger.warning(f"Failed to initialize Application Insights: {e}")
else:
    logger.info("Application Insights connection string not configured — skipping")

# Initialize FastAPI app with lifespan
app = FastAPI(
    title="CIRRUS API",
    version=settings.API_VERSION,
    docs_url=None,  # Disable Swagger UI in browser
    redoc_url=None,  # Disable ReDoc in browser
    openapi_url="/api/openapi.json",  # Keep OpenAPI schema for tools (curl, Postman, etc.)
    lifespan=lifespan,
    servers=[
        {"url": settings.API_BASE_URL},
    ]
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Add exception handlers to ensure CORS headers on error responses
from azure.core.exceptions import ClientAuthenticationError as AzureClientAuthenticationError
from fastapi.responses import JSONResponse

@app.exception_handler(AzureClientAuthenticationError)
async def azure_auth_exception_handler(request: Request, exc: AzureClientAuthenticationError):
    """Handle Azure authentication errors with proper CORS headers"""
    logger.error(f"Azure authentication error: {exc}")
    
    # Extract user email for audit logging if possible
    user_email = 'unknown'
    try:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            import jwt
            decoded = jwt.decode(token, options={"verify_signature": False})
            user_email = decoded.get('preferred_username') or decoded.get('email') or decoded.get('upn') or 'unknown'
    except Exception:
        pass
    
    # Log authentication failure
    audit_service.log_failed_auth(
        user_email=user_email,
        reason=str(exc)
    )
    
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "Authentication failed. Please refresh your browser to get a new token."},
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Credentials": "true",
            "WWW-Authenticate": "Bearer"
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions with proper CORS headers"""
    origin = request.headers.get("origin", "")
    
    # Check if origin is allowed
    allowed_origins = settings.get_cors_origins()
    
    # Build response headers
    response_headers = {
        "Access-Control-Allow-Origin": origin or (allowed_origins[0] if allowed_origins else "*"),
        "Access-Control-Allow-Credentials": "true"
    }
    
    # Add exception headers if present
    if exc.headers:
        response_headers.update(exc.headers)
    
    if origin in allowed_origins or "*" in allowed_origins:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=response_headers
        )
    
    # Default response without CORS if origin not allowed
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers if exc.headers else {}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions with proper CORS headers and logging"""
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}", exc_info=True)
    
    origin = request.headers.get("origin", "")
    allowed_origins = settings.get_cors_origins()
    
    response_headers = {}
    if origin in allowed_origins or "*" in allowed_origins:
        response_headers = {
            "Access-Control-Allow-Origin": origin or allowed_origins[0] if allowed_origins else "*",
            "Access-Control-Allow-Credentials": "true"
        }
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
        headers=response_headers
    )

# Add metrics tracking middleware
@app.middleware("http")
async def track_requests(request: Request, call_next):
    """Track API requests for metrics"""
    start_time = time.time()
    
    # Get user email if available (decode without validation for metrics)
    user_email = None
    try:
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            # Decode token without validation (faster, for metrics only)
            import jwt
            decoded = jwt.decode(token, options={"verify_signature": False})
            user_email = decoded.get('preferred_username') or decoded.get('email') or decoded.get('upn')
            
            # Track user activity
            if user_email:
                metrics_service.track_user_activity(user_email)
                logger.debug(" Tracking activity for authenticated user")
    except Exception as e:
        logger.debug("Could not extract user from token for metrics")
    
    # Process request
    response = await call_next(request)
    
    # Calculate duration
    duration_ms = (time.time() - start_time) * 1000
    
    # Track request metrics
    metrics_service.track_request(
        endpoint=request.url.path,
        method=request.method,
        status_code=response.status_code,
        duration_ms=duration_ms,
        user_email=user_email
    )
    
    # Disable response body logging for large responses
    if request.url.path == "/api/ai/query":
        logger.debug(f"Completed {request.method} {request.url.path} - {response.status_code} ({duration_ms:.0f}ms)")
    
    return response

# Security schemes
from fastapi.security import OAuth2AuthorizationCodeBearer

security = HTTPBearer()
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    tokenUrl="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    scopes={"openid": "OpenID", "profile": "Profile", "email": "Email"},
)

# Global services
# auth_handler is already initialized above with audit_service reference
resource_service = ResourceService()
ai_service = None  # Initialized during startup with rate limiter
governance_service = GovernanceService()

# The lifespan manager above handles startup and shutdown events

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Validate Azure AD token and extract user information"""
    from azure.core.exceptions import ClientAuthenticationError as AzureClientAuthenticationError
    
    try:
        token = credentials.credentials
        
        # Decode token to validate audience
        try:
            import jwt
            decoded = jwt.decode(token, options={"verify_signature": False})
            audience = decoded.get('aud', 'unknown')
            
            # Strict audience validation for Azure Management API
            valid_audiences = {
                'https://management.azure.com',
                'https://management.core.windows.net',
                'https://management.azure.com/',
                'https://management.core.windows.net/'
            }
            
            is_valid = False
            if isinstance(audience, list):
                is_valid = any(aud in valid_audiences for aud in audience)
            else:
                is_valid = audience in valid_audiences or audience.startswith('https://management.azure.com/') or audience.startswith('https://management.core.windows.net/')
            
            if not is_valid:
                logger.error(" Invalid token audience. Expected Azure Management API audience.")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token audience. This endpoint requires an Azure Management API token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
        except HTTPException:
            raise
        except Exception as decode_err:
            logger.debug("Could not decode token for audience check")
        
        user_info = await auth_handler.validate_token(token)
        # Add the token to user_info for downstream services
        user_info['token'] = token
        
        # Log successful SSO authentication
        user_email = user_info.get('preferred_username') or user_info.get('email') or user_info.get('upn')
        if user_email:
            audit_service.log_event(
                action=AuditActionType.USER_LOGIN,
                user_email=user_email,
                details={'auth_method': 'Azure AD SSO', 'token_audience': audience},
                status='success'
            )
        
        return user_info
    except HTTPException:
        raise
    except AzureClientAuthenticationError as e:
        # Azure authentication errors are handled by the global exception handler
        # Just re-raise them to be caught there with proper CORS headers
        logger.error(f"Azure authentication error: {e}")
        raise
    except Exception as e:
        logger.error(f"Token validation failed: {type(e).__name__}: {e}")
        
        # Log failed authentication attempt
        try:
            import jwt
            decoded = jwt.decode(credentials.credentials, options={"verify_signature": False})
            user_email = decoded.get('preferred_username') or decoded.get('email') or decoded.get('upn') or 'unknown'
            audit_service.log_failed_auth(
                user_email=user_email,
                reason=f"{type(e).__name__}: {str(e)}"
            )
        except:
            audit_service.log_failed_auth(
                user_email='unknown',
                reason=f"{type(e).__name__}: {str(e)}"
            )
        
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_user_with_dual_tokens(
    request: Request,
    current_user: dict = Depends(get_current_user)
) -> dict:
    """
    Extract dual tokens for Log Analytics operations.
    
    This is needed because Log Analytics operations require TWO tokens:
    - Management token (from Authorization header) for workspace discovery, diagnostic settings, resource resolution
    - LA token (from X-LA-Token header) for KQL query execution
    
    Returns user dict with:
    - management_token: Management token from Authorization header
    - la_token: LA token if X-LA-Token header present, otherwise None
    
    Note: 'token' key is removed to avoid confusion - use explicit token names
    """
    # LA token can arrive as a header (legacy) or inside the JSON body.
    # Body delivery avoids Envoy/Cloudflare dropping requests where two large
    # JWTs (users with many AD group memberships) exceed HTTP header size limits.
    la_token = request.headers.get("x-la-token")
    if not la_token:
        try:
            body = await request.json()
            la_token = body.get("la_token")
        except Exception:
            pass

    user_with_tokens = current_user.copy()

    # Rename 'token' to 'management_token' for clarity
    user_with_tokens['management_token'] = user_with_tokens.pop('token')
    user_with_tokens['la_token'] = la_token if la_token else None

    if la_token:
        logger.debug(" Dual tokens mode: Management + LA token")
    else:
        logger.debug(" Single token mode: Management token only")
    
    return user_with_tokens

async def get_admin_user(current_user: dict = Depends(get_current_user), request: Request = None):
    """Validate that the current user is authorized for admin access
    
    Checks for Admin role in Azure AD App Roles (from token claims)
    """
    try:
        user_email = current_user.get('preferred_username') or current_user.get('email') or current_user.get('upn')
        
        # Try to get roles from ID token first (X-ID-Token header) if available
        id_token_str = request.headers.get("x-id-token") if request else None
        user_roles = []
        
        if id_token_str:
            # Decode ID token to get roles
            try:
                import jwt
                id_token_decoded = jwt.decode(id_token_str, options={"verify_signature": False})
                user_roles = id_token_decoded.get('roles', [])
                logger.debug(" Roles extracted from ID token")
            except Exception as e:
                logger.warning("Failed to decode ID token, falling back to current_user")
                user_roles = current_user.get('roles', [])
        else:
            # Fallback to roles in current_user token
            user_roles = current_user.get('roles', [])
        
        if settings.ADMIN_ROLE_NAME in user_roles:
            audit_service.log_event(
                action=AuditActionType.ADMIN_LOGIN,
                user_email=user_email or "unknown",
                details={"access_granted": True, "role": settings.ADMIN_ROLE_NAME}
            )
            return current_user
        
        logger.warning(f"Admin access denied - required role '{settings.ADMIN_ROLE_NAME}' not found")
        audit_service.log_permission_denied(
            user_email=user_email or "unknown",
            resource="admin",
            action="access_admin_panel"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. '{settings.ADMIN_ROLE_NAME}' role required."
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin authentication failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin authentication error"
        )

async def get_network_user(current_user: dict = Depends(get_current_user), request: Request = None):
    """Validate that the current user has Network or Admin role (for firewall endpoints)."""
    try:
        id_token_str = request.headers.get("x-id-token") if request else None
        user_roles = []
        if id_token_str:
            try:
                import jwt
                id_token_decoded = jwt.decode(id_token_str, options={"verify_signature": False})
                user_roles = id_token_decoded.get("roles", [])
            except Exception:
                user_roles = current_user.get("roles", [])
        else:
            user_roles = current_user.get("roles", [])

        allowed = {settings.NETWORK_ROLE_NAME, settings.ADVANCE_ROLE_NAME, settings.ADMIN_ROLE_NAME}
        if allowed & set(user_roles):
            return current_user

        user_email = current_user.get("preferred_username") or current_user.get("email") or "unknown"
        logger.warning("Firewall access denied for %s — Network, Advance or Admin role required", user_email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. '{settings.NETWORK_ROLE_NAME}', '{settings.ADVANCE_ROLE_NAME}' or '{settings.ADMIN_ROLE_NAME}' role required.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Network role check failed: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Authorization error")

@retry(stop=stop_after_attempt(settings.SUBSCRIPTION_FETCH_MAX_ATTEMPTS), wait=wait_exponential(multiplier=1, min=settings.SUBSCRIPTION_FETCH_RETRY_MIN_WAIT, max=settings.SUBSCRIPTION_FETCH_RETRY_MAX_WAIT))
async def get_user_subscriptions(user_info: dict) -> List[dict]:
    """Get all subscriptions the user has access to (backward compatibility)"""
    try:
        # Use the resource service to get user accessible subscriptions without pagination
        result = await resource_service.get_user_accessible_subscriptions(
            user_info, 
            page=1, 
            page_size=1000  # Large page size for backward compatibility
        )
        return result["subscriptions"]
    except Exception as e:
        logger.error(f"Failed to get user subscriptions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve subscriptions"
        )

# Override OpenAPI schema to include Azure AD OAuth2 security scheme
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    from fastapi.openapi.utils import get_openapi
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,  # Include servers from FastAPI app
    )
    
    # Add Azure AD OAuth2 security scheme
    openapi_schema["components"]["securitySchemes"] = {
        "AzureAD": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                    "tokenUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                    "scopes": {
                        "openid": "OpenID Connect",
                        "profile": "User profile",
                        "email": "Email address",
                    }
                }
            },
            "description": "Azure AD OAuth2 authentication"
        },
        "Bearer": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Azure AD JWT Bearer token"
        }
    }
    
    # Apply security globally to all operations
    openapi_schema["security"] = [{"AzureAD": []}, {"Bearer": []}]
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# Health check endpoint
@app.get("/api/health", tags=["System"])
async def health_check():
    """Health check endpoint for monitoring service availability"""
    redis_connected = False
    try:
        if cache_service.redis_client:
            await cache_service.redis_client.ping()
            redis_connected = True
    except Exception:
        pass
    
    return {
        "status": "healthy",
        "version": settings.API_VERSION,
        "timestamp": datetime.utcnow().isoformat(),
        "redis_connected": redis_connected
    }

# Catch-all OPTIONS handler for CORS preflight requests
@app.options("/{full_path:path}")
async def options_handler(request: Request):
    """Handle CORS preflight requests for all endpoints"""
    origin = request.headers.get("origin", "")
    allowed_origins = settings.get_cors_origins()
    
    # Check if origin is allowed
    if origin in allowed_origins or "*" in allowed_origins:
        return Response(
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Max-Age": "3600"
            }
        )
    
    # Return 403 if origin not allowed
    return Response(status_code=403)

# Authentication endpoints
@app.get("/api/auth/me", tags=["Authentication"])
async def get_current_user_info(current_user: dict = Depends(get_current_user)):
    """Get current user information"""
    # Log successful user login
    user_email = current_user.get("preferred_username") or current_user.get("email")
    if user_email:
        audit_service.log_event(
            action=AuditActionType.USER_LOGIN,
            user_email=user_email,
            details={
                "name": current_user.get("name"),
                "tenant_id": current_user.get("tid")
            },
            status="success"
        )
    
    return {
        "user_id": current_user.get("oid"),
        "name": current_user.get("name"),
        "email": current_user.get("preferred_username"),
        "tenant_id": current_user.get("tid")
    }

@app.post("/api/user/access", tags=["Authentication"])
async def get_user_access(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """
    Get user's access information based on current scope.
    Shows what permissions the user has and the scope they're viewing.
    
    Request body should include:
    - global_scope: bool (optional)
    - subscription_id: str (optional)
    - resource_group: str (optional)
    - resource_id: str (optional)
    """
    try:
        # Parse request body
        body = await request.json()
        scope_context = {
            "global_scope": body.get("global_scope", False),
            "subscription_id": body.get("subscription_id"),
            "resource_group": body.get("resource_group"),
            "resource_id": body.get("resource_id")
        }
        
        logger.info(f" Getting access info with scope: {scope_context}")
        
        # Get access information
        result = await user_access_service.get_user_access_info(
            current_user,
            scope_context
        )
        
        if result.get("success"):
            return result["access_info"]
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve access information"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get user access info: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve access information")

@app.get("/api/subscriptions", tags=["Resources"])
async def get_subscriptions(
    page: int = 1,
    page_size: int = 100,  # Increased default to reduce API roundtrips
    current_user: dict = Depends(get_current_user)
):
    """Get subscriptions accessible to the current user with pagination support"""
    try:
        # Validate pagination parameters
        if page < 1:
            page = 1
        if page_size < 1 or page_size > 1000:  # Allow larger page size for enterprise use
            page_size = 1000
            
        result = await resource_service.get_user_accessible_subscriptions(
            current_user, 
            page=page, 
            page_size=page_size
        )
        return {
            "data": result["subscriptions"],
            "meta": {
                "total_count": result["total_count"],
                "page": result["page"],
                "page_size": result["page_size"],
                "total_pages": result["total_pages"]
            }
        }
    except Exception as e:
        logger.error(f"Error fetching subscriptions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch subscriptions"
        )

# Resource endpoints
@app.get("/api/subscriptions/{subscription_id}/resource-groups", tags=["Resources"])
async def get_resource_groups(
    subscription_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get all resource groups in a subscription"""
    return await resource_service.get_resource_groups(subscription_id, current_user)

@app.post("/api/resource-groups/batch")
async def get_resource_groups_batch(
    subscription_ids: List[str],
    current_user: dict = Depends(get_current_user)
):
    """Get resource groups for multiple subscriptions in one call"""
    try:
        results = {}
        for subscription_id in subscription_ids[:10]:  # Limit to 10 subscriptions per batch
            try:
                result = await resource_service.get_resource_groups(subscription_id, current_user)
                results[subscription_id] = result
            except Exception as e:
                logger.warning(f"Failed to get resource groups for subscription {subscription_id}: {e}")
                results[subscription_id] = {
                    "error": "Failed to retrieve resource groups",
                    "subscription_id": subscription_id,
                    "resource_groups": [],
                    "count": 0
                }
        
        return {
            "batch_results": results,
            "processed_count": len(results),
            "requested_count": len(subscription_ids)
        }
    except Exception as e:
        logger.error(f"Batch resource groups request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process batch request"
        )

@app.get("/api/subscriptions/{subscription_id}/resources", tags=["Resources"])
async def get_resources(
    subscription_id: str,
    resource_group: str = None,
    resource_type: str = None,
    current_user: dict = Depends(get_current_user)
):
    """Get resources with optional filtering"""
    return await resource_service.get_resources(
        subscription_id, resource_group, resource_type, current_user
    )

# Query endpoints
# Cost management endpoints
@app.get("/api/subscriptions/{subscription_id}/costs", tags=["Cost Management"])
async def get_cost_analysis(
    subscription_id: str,
    time_period: str = "ThisMonth",
    current_user: dict = Depends(get_current_user)
):
    """Get cost analysis for a subscription"""
    try:
        costs = await resource_service.get_cost_analysis(
            subscription_id, time_period, current_user
        )
        return {"costs": costs}
    except Exception as e:
        logger.error(f"Cost analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get cost analysis"
        )

# Governance endpoints
@app.get("/api/governance/policy-compliance", tags=["Governance"])
async def get_policy_compliance(
    subscription_ids: str = Query(..., description="Comma-separated list of subscription IDs"),
    current_user: dict = Depends(get_current_user)
):
    """Get Azure Policy compliance status across subscriptions"""
    try:
        # Split subscription IDs
        sub_ids = [s.strip() for s in subscription_ids.split(",") if s.strip()]
        
        if not sub_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one subscription ID is required"
            )
        
        logger.debug(f"Getting policy compliance for {len(sub_ids)} subscription(s)")
        
        # Get policy compliance data
        compliance_data = await governance_service.get_policy_compliance(
            subscription_ids=sub_ids,
            user_info=current_user
        )
        
        # Log audit event
        audit_service.log_event(
            action=AuditActionType.GOVERNANCE_VIEW,
            user_email=current_user.get('preferred_username') or current_user.get('email'),
            details={
                "resource_type": "policy_compliance",
                "subscription_count": len(sub_ids)
            }
        )
        
        return compliance_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get policy compliance: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get policy compliance"
        )

@app.get("/api/governance/security-score", tags=["Governance"])
async def get_security_score(
    subscription_ids: str = Query(..., description="Comma-separated list of subscription IDs"),
    current_user: dict = Depends(get_current_user)
):
    """Get Microsoft Defender for Cloud security score across subscriptions"""
    try:
        # Split subscription IDs
        sub_ids = [s.strip() for s in subscription_ids.split(",") if s.strip()]
        
        if not sub_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one subscription ID is required"
            )
        
        logger.debug(f"Getting security score for {len(sub_ids)} subscription(s)")
        
        # Get security score data
        security_data = await governance_service.get_security_score(
            subscription_ids=sub_ids,
            user_info=current_user
        )
        
        # Log audit event
        audit_service.log_event(
            action=AuditActionType.GOVERNANCE_VIEW,
            user_email=current_user.get('preferred_username') or current_user.get('email'),
            details={
                "resource_type": "security_score",
                "subscription_count": len(sub_ids)
            }
        )
        
        return security_data
        
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        logger.warning(f"Security score timed out for {len(sub_ids)} subscription(s)")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Security score request timed out — Defender API is slow for large subscriptions"
        )
    except Exception as e:
        logger.error(f"Failed to get security score: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get security score"
        )

@app.get("/api/governance/resource-health", tags=["Governance"])
async def get_resource_health(
    subscription_ids: str = Query(..., description="Comma-separated list of subscription IDs"),
    current_user: dict = Depends(get_current_user)
):
    """Get Azure Resource Health status across subscriptions"""
    try:
        # Split subscription IDs
        sub_ids = [s.strip() for s in subscription_ids.split(",") if s.strip()]
        
        if not sub_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one subscription ID is required"
            )
        
        logger.info(f"Getting resource health for {len(sub_ids)} subscription(s)")
        
        # Import service
        from services.resource_health_service import resource_health_service
        
        # Get resource health data
        health_data = await resource_health_service.get_resource_health_summary(
            subscription_ids=sub_ids,
            user_info=current_user
        )
        
        # Log audit event
        audit_service.log_event(
            action=AuditActionType.GOVERNANCE_VIEW,
            user_email=current_user.get('preferred_username') or current_user.get('email'),
            details={
                "resource_type": "resource_health",
                "subscription_count": len(sub_ids)
            }
        )
        
        return health_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get resource health: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get resource health"
        )

@app.get("/api/governance/change-analysis", tags=["Governance"])
async def get_change_analysis(
    subscription_ids: str = Query(..., description="Comma-separated list of subscription IDs"),
    hours: int = Query(24, description="Number of hours to look back for changes"),
    current_user: dict = Depends(get_current_user)
):
    """Get Azure Resource changes from the last N hours"""
    try:
        # Split subscription IDs
        sub_ids = [s.strip() for s in subscription_ids.split(",") if s.strip()]
        
        if not sub_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one subscription ID is required"
            )
        
        # Validate hours parameter
        if hours < 1 or hours > 168:  # Max 7 days
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Hours must be between 1 and 168 (7 days)"
            )
        
        logger.debug(f"Getting resource changes for {len(sub_ids)} subscription(s) in last {hours} hours")
        
        # Get change analysis data
        changes_data = await change_analysis_service.get_resource_changes(
            subscription_ids=sub_ids,
            hours=hours,
            user_info=current_user
        )
        
        # Log audit event
        audit_service.log_event(
            action=AuditActionType.GOVERNANCE_VIEW,
            user_email=current_user.get('preferred_username') or current_user.get('email'),
            details={
                "resource_type": "change_analysis",
                "subscription_count": len(sub_ids),
                "hours": hours,
                "changes_found": changes_data.get('total_changes', 0)
            }
        )
        
        return changes_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get change analysis: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get change analysis"
        )

# AI Query Endpoints
@app.post("/api/ai/root-cause", tags=["AI Assistant"])
async def analyze_root_cause(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_user_with_dual_tokens)
):
    """Run AI-assisted root cause analysis with transparent evidence and confidence."""
    try:
        incident_summary = (request_body.get("incident_summary") or "").strip()
        if not incident_summary:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="incident_summary is required"
            )

        subscription_ids = request_body.get("subscription_ids") or []
        subscription_id = request_body.get("subscription_id")
        if subscription_id and subscription_id not in subscription_ids:
            subscription_ids.append(subscription_id)

        if not subscription_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one subscription is required"
            )

        hours = int(request_body.get("hours", 24))
        if hours < 1 or hours > 24:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="hours must be between 1 and 24"
            )

        custom_start = request_body.get("custom_start")
        custom_end = request_body.get("custom_end")
        custom_start_dt = None
        custom_end_dt = None

        if custom_start or custom_end:
            if not custom_start or not custom_end:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Both custom_start and custom_end are required for custom time window"
                )

            try:
                custom_start_dt = datetime.fromisoformat(custom_start.replace("Z", "+00:00"))
                custom_end_dt = datetime.fromisoformat(custom_end.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="custom_start/custom_end must be valid ISO datetime strings"
                )

            if custom_end_dt <= custom_start_dt:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="custom_end must be later than custom_start"
                )

            now_utc = datetime.now(custom_end_dt.tzinfo or custom_start_dt.tzinfo)
            max_lookback_start = now_utc - timedelta(days=92)

            if custom_start_dt < max_lookback_start:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Custom start time cannot be older than 3 months from current time"
                )

            if custom_end_dt > now_utc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Custom end time cannot be in the future"
                )

            # Enforce max 24-hour analysis window regardless of range picked.
            # If user picks a wider span the window is clamped to start + 24h so
            # evidence density stays high and the AI prompt stays within budget.
            MAX_WINDOW_HOURS = settings.RCA_MAX_WINDOW_HOURS
            if (custom_end_dt - custom_start_dt).total_seconds() > MAX_WINDOW_HOURS * 3600:
                custom_end_dt = custom_start_dt + timedelta(hours=MAX_WINDOW_HOURS)
                logger.info(f"RCA window clamped to 24h: {custom_start_dt} → {custom_end_dt}")

            window_hours = int((custom_end_dt - custom_start_dt).total_seconds() / 3600)
            if window_hours < 1:
                window_hours = 1

            hours = window_hours

        result = await root_cause_analysis_service.analyze_incident(
            incident_summary=incident_summary,
            subscription_ids=subscription_ids,
            user_info=current_user,
            hours=hours,
            resource_id=request_body.get("resource_id"),
            resource_group=request_body.get("resource_group"),
            start_time=custom_start_dt,
            end_time=custom_end_dt,
            include_external_status=bool(request_body.get("include_external_status", True))
        )

        audit_service.log_event(
            action=AuditActionType.QUERY_EXECUTION,
            user_email=current_user.get('preferred_username') or current_user.get('email') or 'unknown',
            details={
                "query_type": "root_cause_analysis",
                "subscription_count": len(subscription_ids),
                "resource_id": request_body.get("resource_id"),
                "resource_group": request_body.get("resource_group"),
                "hours": hours,
                "confidence": result.get("analysis", {}).get("confidence")
            }
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Root cause analysis failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to run root cause analysis"
        )

@app.post("/api/ai/query")
async def process_ai_query(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_user_with_dual_tokens)
):
    """
    🤖 CONVERSATIONAL AI QUERY ENDPOINT
    
    Process natural language queries with conversation context
    
    Expected request format:
    {
        "query": "Show me costs for last month",
        "conversation_id": "optional-uuid",  // Omit to start new conversation
        "context": {
            "workspace": "optional-workspace-name",
            "subscription": "optional-subscription-filter"
        }
    }
    
    Returns:
    {
        "conversation_id": "uuid",
        "message_id": "uuid",
        "content": "AI response text",
        "message_type": "ai" | "error",
        "query_result": {...},  // Actual data if available
        "suggestions": ["alternative query 1", "..."],
        "actions": [{"label": "Show details", "query": "..."}],
        "context": {...}  // Updated conversation context
    }
    """
    try:
        # current_user already has both tokens from get_user_with_dual_tokens dependency
        query = request_body.get("query", "").strip()
        if not query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Query is required"
            )
        
        # Get or create conversation
        conversation_id = request_body.get("conversation_id")
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        conversation = await conversation_service.get_or_create_conversation(
            user_id=user_id,
            conversation_id=conversation_id
        )
        
        # Add user message to conversation
        user_message = await conversation_service.add_message(
            conversation_id=conversation.id,
            role="user",
            content=query,
            message_type="user",
            user_id=user_id
        )
        
        # Get conversation context for better understanding
        context = await conversation_service.get_context(conversation.id, user_id)
        # Retrieve last 20 messages (AI will use sliding window to select relevant ones)
        # This ensures we have enough context for the AI to understand conversation flow
        recent_messages = await conversation_service.get_recent_messages(conversation.id, count=20, user_id=user_id)
        
        # Pre-compute feedback signals once before building conversation history.
        #
        # Negative corrections: only the most recent one is injected — older corrections
        # are stale context that can confuse the model about the current query.
        # Positive signals: collapsed into one system note if any of the last 3 turns (6
        # messages) were rated helpful — prevents noisy per-message reinforcement.
        _last_neg_correction_id: str | None = None
        for _m in recent_messages:
            if _m.role == "assistant" and _m.feedback == "negative" and _m.correction:
                _last_neg_correction_id = _m.id  # overwrite each time — last one wins

        _has_recent_positive = any(
            m.role == "assistant" and m.feedback == "positive"
            for m in recent_messages[-6:]
        )

        # Format conversation history for AI
        conversation_history = []
        for msg in recent_messages:
            msg_data = {
                "role": msg.role,
                "content": msg.content,
                "query_type": msg.metadata.get("query_type") if msg.metadata else None
            }
            # Add resource_type if available (helps maintain context for follow-ups)
            if msg.metadata and msg.metadata.get("resource_type"):
                msg_data["resource_type"] = msg.metadata.get("resource_type")

            # CRITICAL: Include suggestions so AI remembers what it recommended
            # This allows AI to understand follow-up queries like "Quick scan" after suggesting it
            if msg.suggestions:
                msg_data["suggestions"] = msg.suggestions
                logger.debug(f" Including {len(msg.suggestions)} suggestions in context: {msg.suggestions}")

            # Include actual query result so AI can reference prior data in follow-up questions
            # e.g. "which of those VMs was in North Europe?" requires knowing what was returned
            if msg.query_result:
                msg_data["query_result"] = msg.query_result

            conversation_history.append(msg_data)

            # Inject the most recent negative correction inline (positioned right after the
            # problematic response for context). All older corrections are skipped.
            if (
                msg.role == "assistant"
                and msg.feedback == "negative"
                and msg.correction
                and msg.id == _last_neg_correction_id
            ):
                conversation_history.append({
                    "role": "system",
                    "content": (
                        f"User marked the previous response as incorrect and provided a correction: "
                        f'"{msg.correction}". Treat this as ground truth and adjust your next answer accordingly.'
                    ),
                })

        # Positive reinforcement — injected once at the end so it influences the next response
        # without inflating the context window.
        if _has_recent_positive:
            conversation_history.append({
                "role": "system",
                "content": "A recent response was marked as helpful by the user. Maintain the same depth, format, and approach.",
            })
        
        # Extract filters from request context
        request_context = request_body.get("context", {})
        subscription_id = request_context.get("subscription")
        resource_group = request_context.get("resource_group")
        resource_id = request_context.get("resource_id")
        global_scope = request_context.get("globalScope", False)
        
        # Handle global scope: if enabled, get all user subscriptions
        if global_scope:
            logger.info("Global scope enabled - querying across all subscriptions")
            # Get all user subscriptions
            resource_service = ResourceService()
            try:
                user_subscriptions_response = await resource_service.get_user_accessible_subscriptions(
                    user_info=current_user,
                    page=1,
                    page_size=1000,  # Get all subscriptions
                    use_cache=True
                )
                # Extract subscription IDs from the response
                subscriptions_list = user_subscriptions_response.get("subscriptions", [])
                subscription_ids = [sub["subscription_id"] for sub in subscriptions_list]
                logger.debug(f"Using {len(subscription_ids)} subscriptions for global scope query")
            except Exception as e:
                logger.error(f"Failed to get user subscriptions for global scope: {e}")
                subscription_ids = []
        else:
            # Convert single subscription to list format
            subscription_ids = [subscription_id] if subscription_id else []
        
        # current_user already has both tokens from get_user_with_dual_tokens dependency
        # Add query and context to user_data for Cloudflare mode detection
        current_user['query'] = query
        current_user['context'] = request_context
        
        # Process the natural language query with context and filters
        result = await ai_query_service.process_natural_query(
            user_query=query,
            user_data=current_user,
            subscription_ids=subscription_ids,
            resource_group=resource_group,
            resource_id=resource_id,
            conversation_history=conversation_history
        )
        
        # ── Suggestion normalisation ──────────────────────────────────────────
        # Suggestions are ALWAYS inlined as plain text at the bottom of the
        # message and never sent as a separate array to the frontend.
        # This is the single enforcement point — no other code path can bypass it.
        _raw_suggestions = result.get("suggestions") or []
        if _raw_suggestions:
            _msg = result.get("message", "") or ""
            _lines = "\n".join(_raw_suggestions)
            result["message"] = f"{_msg.rstrip()}\n\n{_lines}" if _msg else _lines
        result["suggestions"] = []
        # ─────────────────────────────────────────────────────────────────────

        # Determine message type based on result
        if not result.get("success", False):
            message_type: MessageType = "error"
            content = result.get("message", "I encountered an error processing your query.")
        else:
            message_type = "ai"
            content = result.get("message", "")
        
        # Add AI response to conversation with query_type in metadata
        ai_message = await conversation_service.add_message(
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            message_type=message_type,
            user_id=user_id,
            query_result=result.get("data"),
            suggestions=result.get("suggestions"),
            actions=result.get("actions"),
            metadata={
                "query_type": result.get("query_type"),
                "resource_type": result.get("resource_type")
            }
        )
        
        # Update conversation context
        if result.get("success"):
            request_context = request_body.get("context", {})
            await conversation_service.update_context(
                conversation_id=conversation.id,
                user_id=user_id,
                last_query_type=result.get("query_type"),
                last_subscription=request_context.get("subscription"),
                last_result=result.get("data")
            )
        
        # When rca_trigger is set the agentic was skipped — content is already the handoff
        # message. Log the trigger decision for observability.
        rca_triggered = bool(result.get("rca_trigger"))
        logger.info(
            "ai_query_completed conversation_id=%s query_type=%s success=%s rca_trigger=%s "
            "subscription_count=%d global_scope=%s",
            conversation.id,
            result.get("query_type", "unknown"),
            result.get("success", False),
            rca_triggered,
            len(subscription_ids),
            global_scope,
        )

        # Build response
        response = AIResponse(
            message_id=ai_message.id,
            conversation_id=conversation.id,
            content=content,
            message_type=message_type,
            query_result=result.get("data"),
            suggestions=result.get("suggestions"),
            actions=result.get("actions"),
            context={
                "last_query_type": context.last_query_type if context else None,
                "last_subscription": context.last_subscription if context else None
            },
            kql_query=result.get("kql_query"),  # Include generated KQL for debugging
            workspace_id=result.get("workspace_id"),  # Include workspace used
            rca_trigger=rca_triggered,
            rca_hours=result.get("rca_hours"),
            rca_start=result.get("rca_start"),
            rca_end=result.get("rca_end")
        )

        return response.dict()
        
    except Exception as e:
        logger.error(f"AI query processing failed: {e}")
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process AI query"
        )

@app.get("/api/ai/conversations", tags=["AI Assistant"])
async def get_user_conversations(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all conversations for the current user
    
    Returns recent conversations sorted by last activity.
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        # Load conversations from Redis (handles multi-worker sync)
        conversations = await conversation_service.get_user_conversations(
            user_id, 
            active_only=True
        )
        
        return {
            "conversations": [
                {
                    "id": conv.id,
                    "created_at": conv.created_at.isoformat(),
                    "updated_at": conv.updated_at.isoformat(),
                    "message_count": len(conv.messages),
                    "preview": conv.messages[0].content[:100] if conv.messages else "",
                    "last_message": {
                        "role": conv.messages[-1].role,
                        "content": conv.messages[-1].content[:200],
                        "timestamp": conv.messages[-1].timestamp.isoformat()
                    } if conv.messages else None
                }
                for conv in conversations  # Already limited to 5 in service
            ]
        }
    except Exception as e:
        logger.error(f"Failed to get conversations: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get conversations"
        )

@app.get("/api/ai/conversations/{conversation_id}")
async def get_conversation_history(
    conversation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get full conversation history with all messages
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        conversation = await conversation_service.get_conversation(conversation_id, user_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )
        
        if conversation.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this conversation"
            )
        
        return {
            "conversation_id": conversation.id,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat(),
            "messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "timestamp": msg.timestamp.isoformat(),
                    "query_result": msg.query_result,
                    "suggestions": msg.suggestions,
                    "actions": msg.actions
                }
                for msg in conversation.messages
            ],
            "context": {
                "last_query_type": conversation.context.last_query_type,
                "last_subscription": conversation.context.last_subscription,
                "last_time_range": conversation.context.last_time_range
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get conversation history: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get conversation history"
        )

# ============================================================================
# Query Template Endpoints
# ============================================================================

@app.post("/api/ai/feedback", tags=["AI Assistant"])
async def submit_message_feedback(
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """
    Record thumbs-up / thumbs-down feedback on a specific AI message.
    An optional correction text is injected as a system signal on the next turn.

    Body:
        conversation_id: str
        message_id: str
        feedback: "positive" | "negative"
        correction: str (optional — free-text the user wants AI to act on)
    """
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id")
        message_id = body.get("message_id")
        feedback = body.get("feedback")
        correction = body.get("correction") or None

        if not conversation_id or not message_id or feedback not in ("positive", "negative"):
            raise HTTPException(status_code=400, detail="conversation_id, message_id, and feedback ('positive'|'negative') are required")

        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")

        stored = await conversation_service.store_feedback(
            conversation_id=conversation_id,
            message_id=message_id,
            user_id=user_id,
            feedback=feedback,
            correction=correction,
        )
        if not stored:
            raise HTTPException(status_code=404, detail="Conversation or message not found")

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to store feedback: {e}")
        raise HTTPException(status_code=500, detail="Failed to store feedback")

@app.get("/api/templates", tags=["Query Templates"])
async def get_user_templates(
    include_public: bool = Query(True, description="Include public templates"),
    response: Response = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get all templates accessible to the current user
    
    Returns:
    - User's private templates
    - Public templates (if include_public=true)
    - System templates
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        templates = await query_template_service.get_user_templates(user_id, include_public=include_public)
        
        # Set cache control headers to prevent stale data
        # no-cache: must revalidate with server before using cached copy
        # max-age=0: cache for 0 seconds (always revalidate)
        # This ensures Cloudflare/CDN doesn't serve stale templates after deletion
        if response:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        
        return {
            "templates": [t.to_dict_for_list() for t in templates],
            "count": len(templates)
        }
    except Exception as e:
        logger.error(f"Failed to get templates: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get templates"
        )

@app.post("/api/templates")
async def create_template(
    template_data: QueryTemplateCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new query template
    
    Request body:
    {
        "name": "My Favorite Query",
        "description": "Shows all VMs in production",
        "query_type": "natural_language",
        "query": "list all virtual machines in production",
        "parameters": {},
        "category": "resources",
        "tags": ["vm", "production"],
        "is_public": false
    }
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        user_name = current_user.get("name") or current_user.get("preferred_username") or user_id
        
        logger.info(f" Creating template: name='{template_data.name}', is_public={template_data.is_public}")
        
        template = await query_template_service.create_template(
            user_id=user_id,
            template_data=template_data,
            user_name=user_name
        )
        
        logger.info(f" Created template {template.id} for user {user_id}: {template.name} (public={template.is_public})")
        
        return template.dict()
    except Exception as e:
        logger.error(f"Failed to create template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create template"
        )

# ============================================================================
# AI-Powered Template Features (Must come before generic /{template_id} route)
# ============================================================================

@app.get("/api/templates/suggestions")
async def get_template_suggestions(
    query: str = Query(..., description="User's query to find relevant templates"),
    limit: int = Query(3, ge=1, le=10, description="Number of suggestions to return"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get AI-powered template suggestions based on user's query
    Uses AI to understand intent and semantic similarity
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        suggestions = await query_template_service.suggest_templates(
            user_query=query,
            user_id=user_id,
            limit=limit
        )
        
        return {
            "suggestions": [t.to_dict_for_list() for t in suggestions],
            "count": len(suggestions),
            "query": query
        }
    except Exception as e:
        logger.error(f"Failed to get template suggestions: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get template suggestions"
        )

@app.get("/api/templates/recent")
async def get_recent_templates(
    limit: int = Query(5, ge=1, le=20, description="Number of recent templates"),
    current_user: dict = Depends(get_current_user)
):
    """Get user's recently used templates"""
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        recent = query_template_service.get_recent_templates(user_id, limit)
        
        return {
            "templates": [t.to_dict_for_list() for t in recent],
            "count": len(recent)
        }
    except Exception as e:
        logger.error(f"Failed to get recent templates: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get recent templates"
        )

@app.get("/api/templates/storage/info")
async def get_template_storage_info(
    current_user: dict = Depends(get_current_user)
):
    """
    Get template storage information
    Shows counts, TTL settings, and future migration plans
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        info = query_template_service.get_storage_info(user_id)
        return info
    except Exception as e:
        logger.error(f"Failed to get storage info: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get storage info"
        )

@app.get("/api/templates/{template_id}")
async def get_template(
    template_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific template by ID"""
    try:
        template = await query_template_service.get_template(template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )
        
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        # Check access: user owns it, or it's public, or it's a system template
        if template.user_id != user_id and not template.is_public and not template.is_system:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view this template"
            )
        
        return template.dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get template"
        )

@app.put("/api/templates/{template_id}")
async def update_template(
    template_id: str,
    update_data: QueryTemplateUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update an existing template"""
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        template = await query_template_service.update_template(
            template_id=template_id,
            user_id=user_id,
            update_data=update_data
        )
        
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found or not authorized"
            )
        
        logger.info(f" Updated template {template_id}")
        return template.dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update template"
        )

@app.delete("/api/templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user)
):
    """Delete a template
    
    - Template owner can always delete their own templates
    - Admins can delete any public shared template
    - System templates cannot be deleted by anyone
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        # Check if user is admin
        is_admin = False
        try:
            # Try to get roles from ID token first (X-ID-Token header) if available
            id_token_str = request.headers.get("x-id-token") if request else None
            user_roles = []
            
            if id_token_str:
                import jwt
                id_token_decoded = jwt.decode(id_token_str, options={"verify_signature": False})
                user_roles = id_token_decoded.get('roles', [])
            else:
                user_roles = current_user.get('roles', [])
            
            is_admin = settings.ADMIN_ROLE_NAME in user_roles
        except Exception as e:
            logger.warning(f"Failed to check admin role: {e}")
        
        success = await query_template_service.delete_template(template_id, user_id, is_admin=is_admin)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found or not authorized"
            )
        
        logger.info(f" Deleted template {template_id} (admin={is_admin})")
        return {"success": True, "message": "Template deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete template"
        )

@app.post("/api/templates/search")
async def search_templates(
    filters: TemplateSearchFilters,
    current_user: dict = Depends(get_current_user)
):
    """
    Search templates with filters
    
    Request body:
    {
        "query": "cost",  // Search in name/description
        "query_type": "natural_language",  // Filter by query type
        "category": "cost",  // Filter by category
        "tags": ["monthly", "breakdown"],  // Filter by tags (ANY match)
        "is_public": true  // Filter by visibility
    }
    """
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        templates = await query_template_service.search_templates(user_id, filters)
        
        return {
            "templates": [t.to_dict_for_list() for t in templates],
            "count": len(templates)
        }
    except Exception as e:
        logger.error(f"Failed to search templates: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search templates"
        )

@app.post("/api/templates/{template_id}/execute")
async def execute_template(
    template_id: str,
    execution_request: QueryTemplateExecute,
    current_user: dict = Depends(get_user_with_dual_tokens)
):
    """
    Execute a template with parameter substitution
    
    This is similar to /api/ai/query but uses a saved template
    
    Request body:
    {
        "template_id": "uuid",
        "parameter_values": {
            "time_range": "7d",
            "subscription": "sub-123"
        },
        "context": {
            "subscription": "sub-123",
            "resource_group": "rg-prod"
        }
    }
    """
    try:
        # Get the template
        template = await query_template_service.get_template(template_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )
        
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        # Check access
        if template.user_id != user_id and not template.is_public and not template.is_system:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to use this template"
            )
        
        # Track usage
        await query_template_service.increment_template_usage(template_id)
        
        # Substitute parameters in the query
        query = template.query
        for param_name, param_value in execution_request.parameter_values.items():
            placeholder = f"{{{param_name}}}"
            if placeholder in query:
                query = query.replace(placeholder, str(param_value))
        
        # Extract context from request
        context = execution_request.context
        subscription_id = context.get("subscription")
        resource_group = context.get("resource_group")
        resource_id = context.get("resource_id")
        
        # Convert subscription to list format
        subscription_ids = [subscription_id] if subscription_id else []
        
        # Execute based on query type
        if template.query_type == "natural_language":
            # Use AI query service
            result = await ai_query_service.process_natural_query(
                user_query=query,
                user_data=current_user,
                subscription_ids=subscription_ids,
                resource_group=resource_group,
                resource_id=resource_id,
                conversation_history=None
            )
        elif template.query_type == "kql":
            # Execute KQL directly (would need workspace_id from context)
            workspace_id = context.get("workspace_id")
            if not workspace_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="workspace_id required for KQL templates"
                )
            
            # Execute KQL using Azure Monitor service
            from services.azure_monitor_service import AzureMonitorService
            monitor_service = AzureMonitorService()
            result = await monitor_service.execute_kql(
                workspace_id=workspace_id,
                query=query,
                user_info=current_user
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported query type: {template.query_type}"
            )
        
        return {
            "template": template.to_dict_for_list(),
            "result": result,
            "query_executed": query
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to execute template: {e}")
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute template"
        )

# Admin endpoint to make templates public
@app.post("/api/admin/templates/{template_id}/make-public")
async def make_template_public(
    template_id: str,
    current_user: dict = Depends(get_admin_user)
):
    """Make a user's template public (admin only)"""
    try:
        admin_user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        template = await query_template_service.make_template_public(template_id, admin_user_id)
        if not template:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found"
            )
        
        logger.info(f" Admin {admin_user_id} made template {template_id} public")
        return template.dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to make template public: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to make template public"
        )

# ============================================================================
# Template Marketplace
# ============================================================================

@app.post("/api/templates/{template_id}/rate")
async def rate_template(
    template_id: str,
    rating: int = Query(..., ge=1, le=5, description="Rating from 1-5 stars"),
    current_user: dict = Depends(get_current_user)
):
    """Rate a template (1-5 stars)"""
    try:
        user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
        
        success = query_template_service.rate_template(template_id, user_id, rating)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Template not found or invalid rating"
            )
        
        logger.info(f" User {user_id} rated template {template_id}: {rating} stars")
        return {"success": True, "rating": rating}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to rate template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rate template"
        )

@app.post("/api/templates/storage/cleanup")
async def cleanup_expired_templates(
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger cleanup of expired templates
    Admin/maintenance endpoint
    """
    try:
        # Optional: Add admin check here
        cleaned_count = await query_template_service.cleanup_expired_templates()
        return {
            "success": True,
            "cleaned_count": cleaned_count,
            "message": f"Cleaned up {cleaned_count} expired templates"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export template: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export template"
        )

@app.get("/api/ai/workspaces")
async def get_user_workspaces(
    current_user: dict = Depends(get_current_user)
):
    """Get Log Analytics workspaces accessible to the user"""
    try:
        from services.query_service import QueryService
        query_svc = QueryService()
        subscription_ids = current_user.get("subscription_ids", [])
        workspaces = await query_svc.discover_workspaces_for_query(
            subscription_ids=subscription_ids,
            user_info=current_user
        )
        return {"workspaces": workspaces}
        
    except Exception as e:
        logger.error(f"Failed to get workspaces: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get workspaces"
        )

# Admin endpoints for monitoring prefetch status (requires admin authentication)
@app.get("/api/admin/info")
async def get_admin_info(admin_user: dict = Depends(get_admin_user)):
    """Get admin configuration info - useful for testing admin access"""
    user_email = admin_user.get('preferred_username') or admin_user.get('email') or admin_user.get('upn')
    user_roles = admin_user.get('roles', [])
    return {
        "admin_access": True,
        "user_email": user_email,
        "roles": user_roles,
        "admin_role": settings.ADMIN_ROLE_NAME,
        "message": f"Admin access confirmed for {user_email}"
    }

@app.get("/api/admin/prefetch/status")
async def get_prefetch_status(admin_user: dict = Depends(get_admin_user)):
    """Get status of background prefetching jobs"""
    try:
        job_status = scheduler_service.get_job_status()
        
        # Get last prefetch timestamp
        last_prefetch = await cache_service.get("global", "last_prefetch_timestamp")
        
        # Get count of cached data
        cached_subscriptions = await cache_service.get("global", "all_subscriptions") or []
        cached_resource_types = await cache_service.get("global", "common_resource_types") or []
        
        return {
            "prefetch_enabled": True,
            "scheduler_status": job_status,
            "last_prefetch_time": last_prefetch,
            "cached_data": {
                "subscriptions_count": len(cached_subscriptions),
                "resource_types_count": len(cached_resource_types)
            }
        }
    except Exception as e:
        logger.error(f"Failed to get prefetch status: {e}")
        return {
            "prefetch_enabled": False,
            "error": "Failed to get prefetch status"
        }

@app.post("/api/admin/prefetch/trigger")
async def trigger_prefetch(admin_user: dict = Depends(get_admin_user)):
    """Manually trigger an immediate prefetch"""
    try:
        result = await scheduler_service.trigger_immediate_prefetch()
        return result
    except Exception as e:
        logger.error(f"Failed to trigger prefetch: {e}")
        return {
            "success": False,
            "message": "Failed to trigger prefetch"
        }

@app.get("/api/admin/prefetch/data")
async def get_prefetch_data(admin_user: dict = Depends(get_admin_user)):
    """Get summary of prefetched data for debugging"""
    try:
        cached_subscriptions = await cache_service.get("global", "all_subscriptions") or []
        cached_resource_types = await cache_service.get("global", "common_resource_types") or []
        
        return {
            "subscriptions": {
                "count": len(cached_subscriptions),
                "sample": cached_subscriptions[:3] if cached_subscriptions else []
            },
            "resource_types": {
                "count": len(cached_resource_types),
                "sample": cached_resource_types[:10] if cached_resource_types else []
            }
        }
    except Exception as e:
        logger.error(f"Failed to get prefetch data: {e}")
        return {
            "error": "Failed to get prefetch data"
        }

@app.get("/api/admin/management-groups")
async def list_management_groups(user_data: dict = Depends(get_current_user)):
    """List all Management Groups user has access to"""
    try:
        from services.dual_credential_service import DualCredentialService
        from services.advanced_cost_service import AdvancedCostService
        
        dual_cred_service = DualCredentialService()
        credential = dual_cred_service.get_user_credential(user_data, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        # List all management groups
        mg_list = list(cost_service.mg_client.management_groups.list())
        
        mg_details = []
        for mg in mg_list:
            try:
                details = cost_service.mg_client.management_groups.get(mg.name, expand="children", recurse=False)
                
                # CORRECT PATH: details.parent (not properties.parent)
                has_parent = (hasattr(details, 'details') and 
                             details.details and 
                             hasattr(details.details, 'parent') and 
                             details.details.parent)
                parent_id = details.details.parent.id if has_parent else None
                
                # Try to count subscriptions
                try:
                    sub_count = cost_service._count_enabled_subscriptions_in_mg(mg.name)
                except:
                    sub_count = 0
                
                mg_details.append({
                    "id": mg.name,
                    "display_name": details.display_name or mg.name,
                    "has_parent": has_parent,
                    "parent_id": parent_id,
                    "subscription_count": sub_count
                })
            except Exception as e:
                logger.warning(f"Failed to get details for MG {mg.name}: {e}")
                mg_details.append({
                    "id": mg.name,
                    "display_name": mg.name,
                    "has_parent": None,
                    "parent_id": None,
                    "subscription_count": 0,
                    "error": "Failed to get management group details"
                })
        
        # Sort by subscription count (descending) then by name length (ascending)
        mg_details.sort(key=lambda x: (-x.get('subscription_count', 0), len(x['id'])))
        
        # Get currently selected MG
        current_mg = cost_service.get_top_management_group()
        
        return {
            "success": True,
            "management_groups": mg_details,
            "current_selection": current_mg,
            "total_count": len(mg_details)
        }
    except Exception as e:
        logger.error(f"Failed to list management groups: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Failed to list management groups"
        }

@app.post("/api/admin/management-groups/set")
async def set_management_group(request: dict, user_data: dict = Depends(get_current_user)):
    """Manually set which Management Group to use for cost queries"""
    try:
        from services.dual_credential_service import DualCredentialService
        from services.advanced_cost_service import AdvancedCostService
        
        mg_id = request.get("management_group_id")
        if not mg_id:
            return {
                "success": False,
                "error": "management_group_id is required"
            }
        
        dual_cred_service = DualCredentialService()
        credential = dual_cred_service.get_user_credential(user_data, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        # Verify MG exists
        try:
            mg_details = cost_service.mg_client.management_groups.get(mg_id)
            display_name = mg_details.display_name or mg_id
        except Exception as e:
            logger.error(f"Management Group lookup failed for '{mg_id}': {e}")
            return {
                "success": False,
                "error": f"Management Group '{mg_id}' not found or access denied"
            }
        
        # Set the MG
        cost_service.set_management_group(mg_id)
        
        # Count subscriptions
        try:
            sub_count = cost_service._count_enabled_subscriptions_in_mg(mg_id)
        except:
            sub_count = 0
        
        return {
            "success": True,
            "management_group_id": mg_id,
            "display_name": display_name,
            "subscription_count": sub_count,
            "message": f"Management Group set to '{display_name}' ({sub_count} subscriptions)"
        }
    except Exception as e:
        logger.error(f"Failed to set management group: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Failed to set management group"
        }

# New Admin Endpoints for Enhanced Monitoring

@app.get("/api/admin/metrics/api")
async def get_api_metrics(
    hours: int = 24,
    admin_user: dict = Depends(get_admin_user)
):
    """Get API usage metrics (admin only)"""
    try:
        audit_service.log_admin_action(
            user_email=admin_user.get('preferred_username') or admin_user.get('email'),
            action="view_api_metrics"
        )
        
        metrics = await metrics_service.get_api_metrics(hours=hours)
        performance = await metrics_service.get_performance_stats()
        
        return {
            "success": True,
            "metrics": metrics,
            "performance": performance,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get API metrics: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get API metrics"
        )

@app.get("/api/admin/users/active")
async def get_active_users_stats(
    minutes: int = 30,
    admin_user: dict = Depends(get_admin_user)
):
    """Get active users statistics (admin only)"""
    try:
        audit_service.log_admin_action(
            user_email=admin_user.get('preferred_username') or admin_user.get('email'),
            action="view_active_users"
        )
        
        users = await metrics_service.get_active_users(minutes=minutes)
        
        return {
            "success": True,
            "users": users,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get active users: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get active users"
        )

@app.get("/api/admin/costs/summary")
async def get_cost_summary(
    admin_user: dict = Depends(get_admin_user)
):
    """Get cost summary across all subscriptions (admin only)"""
    try:
        audit_service.log_admin_action(
            user_email=admin_user.get('preferred_username') or admin_user.get('email'),
            action="view_cost_summary"
        )
        
        # Use user credential (RBAC enforced) even for admin operations
        credential = dual_cred_service.get_user_credential(admin_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        # Get top management group
        top_mg = cost_service.get_top_management_group()
        
        if not top_mg:
            return {
                "success": False,
                "message": "No management group configured"
            }
        
        # Get current month costs
        from_date = datetime.utcnow().replace(day=1).strftime("%Y-%m-%d")
        to_date = datetime.utcnow().strftime("%Y-%m-%d")
        
        cost_data = await cost_service.get_costs_management_group(
            management_group_id=top_mg,
            from_date=from_date,
            to_date=to_date,
            granularity="Daily",
            group_by=["SubscriptionName"]
        )
        
        # Calculate summary
        total_cost = sum(item.get('cost', 0) for item in cost_data.get('results', []))
        
        # Get top subscriptions
        sub_costs = {}
        for item in cost_data.get('results', []):
            sub_name = item.get('SubscriptionName', 'Unknown')
            cost = item.get('cost', 0)
            sub_costs[sub_name] = sub_costs.get(sub_name, 0) + cost
        
        top_subscriptions = sorted(
            [{'name': k, 'cost': v} for k, v in sub_costs.items()],
            key=lambda x: x['cost'],
            reverse=True
        )[:10]
        
        return {
            "success": True,
            "summary": {
                "total_cost_mtd": round(total_cost, 2),
                "currency": cost_data.get('currency', 'USD'),
                "from_date": from_date,
                "to_date": to_date,
                "top_subscriptions": top_subscriptions,
                "management_group": top_mg
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Failed to get cost summary: {e}")
        return {
            "success": False,
            "error": "Failed to get cost summary",
            "message": "Failed to get cost summary"
        }

@app.get("/api/admin/audit/logs")
async def get_audit_logs_endpoint(
    hours: int = 24,
    action_type: str = None,
    user_email: str = None,
    limit: int = 1000,
    admin_user: dict = Depends(get_admin_user)
):
    """Get audit logs (admin only)"""
    try:
        audit_service.log_admin_action(
            user_email=admin_user.get('preferred_username') or admin_user.get('email'),
            action="view_audit_logs"
        )
        
        logs = await audit_service.get_audit_logs(
            hours=hours,
            action_type=action_type,
            user_email=user_email,
            limit=limit
        )
        
        summary = await audit_service.get_audit_summary(hours=hours)
        
        return {
            "success": True,
            "logs": logs,
            "summary": summary,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get audit logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get audit logs"
        )

@app.get("/api/admin/errors/recent")
async def get_recent_errors(
    limit: int = 50,
    admin_user: dict = Depends(get_admin_user)
):
    """Get recent error logs (admin only)"""
    try:
        errors = metrics_service.get_error_logs(limit=limit)
        
        return {
            "success": True,
            "errors": errors,
            "count": len(errors),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get error logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get error logs"
        )

@app.get("/api/admin/system/overview")
async def get_system_overview(
    admin_user: dict = Depends(get_admin_user)
):
    """Get comprehensive system overview (admin only)"""
    try:
        audit_service.log_admin_action(
            user_email=admin_user.get('preferred_username') or admin_user.get('email'),
            action="view_system_overview"
        )
        
        # Gather all stats
        api_metrics = await metrics_service.get_api_metrics(hours=24)
        active_users = await metrics_service.get_active_users(minutes=30)
        performance = await metrics_service.get_performance_stats()
        audit_summary = await audit_service.get_audit_summary(hours=24)
        cache_health = await cache_service.health_check()
        
        # Scheduler status
        scheduler_status = scheduler_service.get_job_status()
        
        return {
            "success": True,
            "overview": {
                "api_metrics": {
                    "requests_24h": api_metrics.get('total_requests', 0),
                    "avg_response_time_ms": api_metrics.get('avg_response_time_ms', 0),
                    "error_rate": api_metrics.get('error_rate', 0)
                },
                "users": {
                    "active_now": active_users.get('active_now_count', 0),
                    "last_24h": active_users.get('users_last_24h', 0),
                    "last_7d": active_users.get('users_last_7d', 0)
                },
                "performance": performance,
                "security": {
                    "failed_auth_count": audit_summary.get('failed_auth_count', 0),
                    "permission_denials": audit_summary.get('permission_denial_count', 0)
                },
                "system_health": {
                    "cache": cache_health.get('status', 'unknown'),
                    "scheduler": scheduler_status.get('scheduler_running', False)
                }
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get system overview: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get system overview"
        )

@app.get("/api/admin/cache/stats")
async def get_admin_cache_stats(
    admin_user: dict = Depends(get_admin_user),
    response: Response = None
):
    """Get comprehensive cache statistics (admin only)"""
    try:
        # Set no-cache headers to prevent CDN/browser caching of stats
        if response:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        
        # Get advanced query cache stats
        advanced_cache_stats = await advanced_query_cache.get_cache_stats()
        
        # Calculate total entries and size from L1 + L2
        l1_entries = advanced_cache_stats.get("l1_cache", {}).get("entries", 0)
        l2_entries = advanced_cache_stats.get("l2_cache", {}).get("entries", 0)
        l1_size_mb = advanced_cache_stats.get("l1_cache", {}).get("size_mb", 0)
        l2_size_mb = advanced_cache_stats.get("l2_cache", {}).get("size_mb", 0)
        
        # Parse hit rate from string "X.XX%" to float
        hit_rate_str = advanced_cache_stats.get("metrics", {}).get("hit_rate", "0%")
        try:
            hit_rate = float(hit_rate_str.rstrip('%')) / 100
        except (ValueError, AttributeError):
            hit_rate = 0.0
        
        # Get prefetch cache stats
        job_status = scheduler_service.get_job_status()
        last_prefetch = await cache_service.get("global", "last_prefetch_timestamp")
        cached_subscriptions = await cache_service.get("global", "all_subscriptions") or []
        cached_resource_types = await cache_service.get("global", "common_resource_types") or []
        
        # Get metrics cache stats from actual data (Redis or in-memory)
        api_metrics = await metrics_service.get_api_metrics(hours=24)
        active_users_data = await metrics_service.get_active_users(minutes=30)
        template_stats = query_template_service.get_storage_info()
        
        metrics_stats = {
            "total_requests": api_metrics.get('total_requests', 0),
            "total_errors": sum(1 for req in metrics_service.error_logs if datetime.fromisoformat(req['timestamp']) > datetime.utcnow() - timedelta(hours=24)) if not metrics_service.use_redis else 0,
            "active_users": active_users_data.get('active_now_count', 0),
            "endpoints_tracked": len(metrics_service.endpoint_stats),
            "using_redis": metrics_service.use_redis
        }
        
        # Get template cache stats
        template_cache_stats = {
            "total_templates": template_stats.get("total_templates", 0),
            "user_templates": template_stats.get("user_templates", 0),
            "public_templates": template_stats.get("public_templates", 0),
            "system_templates": template_stats.get("system_templates", 0)
        }
        
        # Get ARG table mappings cache stats
        from utils.arg_table_mapper import _table_mapping_cache
        arg_cache_stats = {
            "mappings_count": 0,
            "age_hours": None,
            "ttl_hours": _table_mapping_cache.get("ttl_hours", 168),
            "status": "empty"
        }
        
        if _table_mapping_cache.get("data") and _table_mapping_cache.get("timestamp"):
            mappings_count = len(_table_mapping_cache["data"].get("mappings", {}))
            cache_age = datetime.now() - _table_mapping_cache["timestamp"]
            age_hours = cache_age.total_seconds() / 3600
            
            arg_cache_stats = {
                "mappings_count": mappings_count,
                "age_hours": round(age_hours, 1),
                "ttl_hours": _table_mapping_cache.get("ttl_hours", 168),
                "status": "fresh" if age_hours < _table_mapping_cache.get("ttl_hours", 168) else "stale",
                "source": _table_mapping_cache["data"].get("source", "unknown"),
                "last_updated": _table_mapping_cache["timestamp"].isoformat()
            }
        
        # Get Log Analytics table schema cache stats
        from utils.log_analytics_table_mapper import log_analytics_table_mapper, CACHE_METADATA_KEY
        la_table_cache_stats = {
            "table_count": 0,
            "age_days": None,
            "ttl_days": 30,
            "status": "empty",
            "storage": "Redis"
        }
        
        try:
            # Load metadata directly from Redis (bypass memory cache for stats)
            redis_client = cache_service.redis_client
            if redis_client:
                metadata_str = await redis_client.get(CACHE_METADATA_KEY)
                if metadata_str:
                    import json
                    metadata = json.loads(metadata_str)
                    if metadata.get('last_refresh'):
                        last_refresh = datetime.fromisoformat(metadata['last_refresh'])
                        cache_age = datetime.now() - last_refresh
                        age_days = cache_age.days
                        
                        la_table_cache_stats = {
                            "table_count": metadata.get('table_count', 0),
                            "age_days": age_days,
                            "ttl_days": 30,
                            "status": "fresh" if age_days < 30 else "stale",
                            "last_updated": last_refresh.isoformat(),
                            "storage": "Redis"
                        }
        except Exception as e:
            logger.error(f"Failed to get LA table cache stats: {e}")
        
        # Get cost cache stats (Azure Cost Management API responses)
        # Note: Cache stats are stored at class level, so we can access from any instance
        cost_cache_stats = {
            "total_entries": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "hit_rate": 0.0,
            "storage": "Redis"
        }
        
        # Access class-level cache tracking from AdvancedCostService
        if hasattr(AdvancedCostService, '_cache_hits'):
            total_requests = AdvancedCostService._cache_hits + AdvancedCostService._cache_misses
            hit_rate = (AdvancedCostService._cache_hits / total_requests * 100) if total_requests > 0 else 0.0
            
            cost_cache_stats["cache_hits"] = AdvancedCostService._cache_hits
            cost_cache_stats["cache_misses"] = AdvancedCostService._cache_misses
            cost_cache_stats["hit_rate"] = round(hit_rate, 2)
        
        # Count cache entries in Redis
        if cache_service.redis_client:
            try:
                async for _ in cache_service.redis_client.scan_iter("cost:*"):
                    cost_cache_stats["total_entries"] += 1
            except Exception as e:
                logger.error(f"Failed to count cost cache entries: {e}")
        
        return {
            "success": True,
            "stats": {
                "advanced_query_cache": {
                    "total_entries": l1_entries + l2_entries,
                    "hit_rate": hit_rate,
                    "total_size_mb": l1_size_mb + l2_size_mb,
                    **advanced_cache_stats
                },
                "prefetch_cache": {
                    "subscriptions_count": len(cached_subscriptions),
                    "resource_types_count": len(cached_resource_types),
                    "last_updated": last_prefetch,
                    "scheduler_running": job_status.get("scheduler_running", False)
                },
                "metrics_cache": metrics_stats,
                "template_cache": template_cache_stats,
                "cost_cache": cost_cache_stats,
                "arg_table_mappings_cache": arg_cache_stats,
                "la_table_schemas_cache": la_table_cache_stats
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get cache statistics"
        )

@app.get("/api/admin/cache/entries")
async def get_cache_entries(
    cache_type: str = "all",
    admin_user: dict = Depends(get_admin_user)
):
    """List cache entries (admin only)"""
    try:
        entries = []
        
        if cache_type in ["all", "query"]:
            # Get advanced query cache entries
            advanced_stats = await advanced_query_cache.get_cache_stats()
            l1_entries = advanced_stats.get("l1_cache", {}).get("entries", 0)
            l2_entries = advanced_stats.get("l2_cache", {}).get("entries", 0)
            hit_rate_str = advanced_stats.get("metrics", {}).get("hit_rate", "0%")
            hit_rate = float(hit_rate_str.rstrip('%')) / 100 if hit_rate_str != "0%" else 0.0
            entries.append({
                "cache_name": "Advanced Query Cache",
                "type": "query",
                "entry_count": l1_entries + l2_entries,
                "hit_rate": hit_rate,
                "details": advanced_stats
            })
        
        if cache_type in ["all", "prefetch"]:
            # Get prefetch cache entries
            cached_subscriptions = await cache_service.get("global", "all_subscriptions") or []
            cached_resource_types = await cache_service.get("global", "common_resource_types") or []
            
            entries.append({
                "cache_name": "Prefetch Cache",
                "type": "prefetch",
                "entry_count": len(cached_subscriptions) + len(cached_resource_types),
                "subscriptions": len(cached_subscriptions),
                "resource_types": len(cached_resource_types),
                "details": {
                    "subscriptions_sample": cached_subscriptions[:5] if cached_subscriptions else [],
                    "resource_types_sample": cached_resource_types[:10] if cached_resource_types else []
                }
            })
        
        return {
            "success": True,
            "entries": entries,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get cache entries: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get cache entries"
        )

@app.post("/api/admin/cache/clear")
async def clear_cache_admin(
    request: Request,
    admin_user: dict = Depends(get_admin_user)
):
    """
    Clear cache (admin only)
    
    Request format:
    {
        "cache_type": "all|query|prefetch|metrics|templates|cost|arg_mappings" (optional, default: "all")
    }
    """
    try:
        body = await request.json()
        cache_type = body.get("cache_type", "all")
        
        admin_email = admin_user.get('preferred_username') or admin_user.get('email')
        audit_service.log_admin_action(
            user_email=admin_email,
            action="clear_cache",
            details={"cache_type": cache_type}
        )
        
        cleared = []
        
        if cache_type in ["all", "query"]:
            # Clear advanced query cache
            await advanced_query_cache.clear_cache()
            cleared.append("advanced_query_cache")
        
        if cache_type in ["all", "prefetch"]:
            # Clear prefetch cache
            await cache_service.clear_prefetch_cache()
            cleared.append("prefetch_cache")
        
        if cache_type in ["all", "templates"]:
            # Clear template cache from Redis
            try:
                cleared_count = await query_template_service.clear_all_redis_templates()
                cleared.append(f"template_cache ({cleared_count} templates)")
            except Exception as e:
                logger.error(f"Failed to clear template cache: {e}")
        
        if cache_type in ["all", "metrics"]:
            # Clear metrics cache (Redis only)
            if metrics_service.use_redis and metrics_service.redis_client:
                try:
                    # Clear request logs
                    for i in range(7):  # Clear last 7 days
                        date = (datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d')
                        await metrics_service.redis_client.delete(f"metrics:requests:{date}")
                        await metrics_service.redis_client.delete(f"metrics:errors:{date}")
                    # Clear endpoint stats
                    async for key in metrics_service.redis_client.scan_iter("metrics:endpoint:*"):
                        await metrics_service.redis_client.delete(key)
                    # Clear user activity
                    async for key in metrics_service.redis_client.scan_iter("metrics:user:active:*"):
                        await metrics_service.redis_client.delete(key)
                    cleared.append("metrics_cache")
                except Exception as e:
                    logger.error(f"Failed to clear metrics from Redis: {e}")
            else:
                # Clear in-memory metrics
                metrics_service.api_requests = []
                metrics_service.error_logs = []
                metrics_service.active_users = {}
                metrics_service.endpoint_stats.clear()
                cleared.append("metrics_cache")
        
        if cache_type in ["all", "arg_mappings"]:
            # Clear ARG table mappings cache
            try:
                await cache_service.delete("system", "arg:table_mappings:v1")
                from utils.arg_table_mapper import _table_mapping_cache
                _table_mapping_cache["data"] = None
                _table_mapping_cache["timestamp"] = None
                cleared.append("arg_table_mappings_cache")
            except Exception as e:
                logger.error(f"Failed to clear ARG mappings cache: {e}")
        
        if cache_type in ["all", "la_tables"]:
            # Clear Log Analytics table schemas cache
            try:
                from utils.log_analytics_table_mapper import log_analytics_table_mapper, CACHE_KEY_PREFIX, CACHE_METADATA_KEY
                
                # Clear all table schemas from Redis
                redis_client = cache_service.redis_client
                if redis_client:
                    pattern = f"{CACHE_KEY_PREFIX}:*"
                    keys_deleted = 0
                    async for key in redis_client.scan_iter(pattern):
                        await redis_client.delete(key)
                        keys_deleted += 1
                    
                    # Clear metadata
                    await redis_client.delete(CACHE_METADATA_KEY)
                    
                    cleared.append(f"la_table_schemas_cache ({keys_deleted} tables)")
                    logger.info(f" Cleared {keys_deleted} Log Analytics table schemas from Redis")
                else:
                    cleared.append("la_table_schemas_cache (Redis not available)")
            except Exception as e:
                logger.error(f"Failed to clear LA table schemas cache: {e}")
        
        if cache_type in ["all", "cost"]:
            # Clear cost analysis cache (Azure Cost Management API results)
            if cache_service.redis_client:
                try:
                    cost_cleared = 0
                    async for key in cache_service.redis_client.scan_iter("cost:*"):
                        await cache_service.redis_client.delete(key)
                        cost_cleared += 1
                    cleared.append(f"cost_cache ({cost_cleared} entries)")
                    logger.info(f" Cleared {cost_cleared} cost cache entries")
                except Exception as e:
                    logger.error(f"Failed to clear cost cache: {e}")
            else:
                cleared.append("cost_cache (Redis not available)")
        
        return {
            "success": True,
            "message": f"Cache cleared: {', '.join(cleared)}",
            "cleared_caches": cleared,
            "timestamp": datetime.utcnow().isoformat()
        }
            
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear cache"
        )

@app.post("/api/ai/analyze-results")
async def analyze_query_results(
    request: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    Analyze query results using AI to provide intelligent insights
    
    Expected request format:
    {
        "results_data": {
            "results": [...],
            "row_count": 123,
            "query_type": "kql"
        },
        "original_query": "AzureDiagnostics | where TimeGenerated >= ago(1m)",
        "query_type": "kql"
    }
    """
    try:
        results_data = request.get("results_data", {})
        original_query = request.get("original_query", "")
        query_type = request.get("query_type", "kql")
        
        if not results_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Results data is required"
            )
        
        logger.info(f" Analyzing query results")
        logger.info(f"Query: {original_query}")
        logger.info(f"Results count: {results_data.get('row_count', 0)}")
        
        # Analyze results using AI
        analysis_result = await ai_query_service.analyze_query_results(
            results_data, original_query, query_type
        )
        
        return analysis_result
        
    except Exception as e:
        logger.error(f"Query results analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze query results"
        )


# ============================================================================
# ADVANCED COST MANAGEMENT ENDPOINTS
# ============================================================================

@app.post("/api/costs/subscriptions")
async def get_subscription_costs(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    🚀 PARALLEL cost analysis across multiple subscriptions
    
    Request body:
    {
        "subscription_ids": ["sub-1", "sub-2", ...],
        "resource_groups": {"sub-id-1": ["rg-1", "rg-2"], "sub-id-2": ["rg-3"]},  // Optional: filter by resource groups per subscription
        "start_date": "2025-10-01",
        "end_date": "2025-10-31",
        "group_by": "ServiceName",  // ServiceName, ResourceType, ResourceGroupName, Location
        "include_reservations": true,  // Use amortized cost (DEPRECATED - use return_both_metrics)
        "return_both_metrics": true  // NEW: Returns both actual and amortized in one call
    }
    
    Returns:
    {
        "success": true,
        "total_cost": 12345.67,  // ActualCost
        "amortized_cost": 12000.00,  // AmortizedCost (if return_both_metrics=true)
        "currency": "USD",
        "breakdown": [...],
        "scopes_queried": 10
    }
    """
    try:
        subscription_ids = request_body.get("subscription_ids", [])
        if not subscription_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription_ids is required"
            )
        
        # Parse dates
        start_date_str = request_body.get("start_date")
        end_date_str = request_body.get("end_date")
        
        if not start_date_str or not end_date_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date and end_date are required"
            )
        
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
        
        # Handle group_by parameter:
        # - If not in request body at all: default to "ServiceName" (backward compatibility)
        # - If explicitly null/None in request body: use None for daily time series
        # - If empty string: also use None for daily time series
        if "group_by" not in request_body:
            group_by = "ServiceName"  # Default for backward compatibility
            logger.debug(" group_by not in request, using default: ServiceName")
        else:
            group_by = request_body.get("group_by")
            logger.debug(f" group_by from request: {repr(group_by)}")
            if group_by == "" or group_by is None:
                group_by = None  # No grouping = daily time series with UsageDate
                logger.debug(" group_by is empty/None, setting to None for daily time series")
        
        return_both_metrics = request_body.get("return_both_metrics", False)
        include_reservations = request_body.get("include_reservations", True)
        resource_groups = request_body.get("resource_groups", {})  # Dict mapping sub_id to list of RG names
        
        # Get user credential for user-scoped cost queries (management token)
        credential = dual_cred_service.get_user_credential(current_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        logger.debug(f" Getting costs for {len(subscription_ids)} subscriptions, {start_date.date()} to {end_date.date()}")
        if resource_groups:
            logger.debug(f" Filtering by resource groups: {resource_groups}")

        logger.info(
            "cost_analysis_requested subscription_count=%d start_date=%s end_date=%s "
            "group_by=%s return_both_metrics=%s has_resource_group_filter=%s",
            len(subscription_ids),
            start_date.date(),
            end_date.date(),
            group_by,
            return_both_metrics,
            bool(resource_groups),
        )
        
        # NEW: If return_both_metrics is true, use optimized dual-metric query
        if return_both_metrics:
            logger.info(" Using optimized dual-metric query (ActualCost + AmortizedCost in parallel)")
            
            # Map Location to ResourceLocation
            group_by_list = [group_by]
            if group_by == "Location":
                group_by_list = ["ResourceLocation"]
            
            # Build scopes - if resource groups specified, use RG scopes instead of subscription scopes
            if resource_groups:
                scopes = []
                for sub_id in subscription_ids:
                    rg_list = resource_groups.get(sub_id, [])
                    if rg_list:
                        # Add resource group scopes for this subscription
                        for rg_name in rg_list:
                            scopes.append(f"/subscriptions/{sub_id}/resourceGroups/{rg_name}")
                    else:
                        # No RGs specified for this sub, query entire subscription
                        scopes.append(f"/subscriptions/{sub_id}")
            else:
                # No resource group filtering, query entire subscriptions
                scopes = [f"/subscriptions/{sub_id}" for sub_id in subscription_ids]
            
            logger.info(f" Querying {len(scopes)} scopes")
            
            result = await cost_service.query_multiple_scopes_with_both_metrics(
                scopes=scopes,
                start_date=start_date,
                end_date=end_date,
                granularity="daily",
                group_by=group_by_list,
                filters=None
            )
            
            return result
        
        # LEGACY: Single metric query
        result = await cost_service.get_subscription_costs(
            subscription_ids=subscription_ids,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by,
            include_reservations=include_reservations,
            resource_groups=resource_groups
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscription costs query failed: {e}")
        import traceback
        logger.error(f"Stack trace: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get subscription costs"
        )


@app.post("/api/costs/resource-groups")
async def get_resource_group_costs(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    🚀 PARALLEL cost analysis for specific resource groups
    
    Request body:
    {
        "subscription_id": "sub-id",
        "resource_groups": ["rg-1", "rg-2", ...],
        "start_date": "2025-10-01",
        "end_date": "2025-10-31",
        "group_by": "ResourceType"
    }
    """
    try:
        subscription_id = request_body.get("subscription_id")
        resource_groups = request_body.get("resource_groups", [])
        
        if not subscription_id or not resource_groups:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription_id and resource_groups are required"
            )
        
        start_date = datetime.fromisoformat(request_body["start_date"].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(request_body["end_date"].replace('Z', '+00:00'))
        group_by = request_body.get("group_by", "ResourceType")
        
        credential = dual_cred_service.get_user_credential(current_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        logger.info(f" Getting costs for {len(resource_groups)} resource groups")
        
        result = await cost_service.get_resource_group_costs(
            subscription_id=subscription_id,
            resource_groups=resource_groups,
            start_date=start_date,
            end_date=end_date,
            group_by=group_by
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resource group costs query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get resource group costs"
        )


@app.post("/api/costs/by-tag")
async def get_costs_by_tag(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    🏷️ Cost analysis by tags (CostCenter, Environment, Project, etc.)
    
    Request body:
    {
        "subscription_ids": ["sub-1", "sub-2"],
        "tag_key": "CostCenter",  // Environment, Project, Department, etc.
        "tag_values": ["CC-001", "CC-002"],  // Optional filter
        "start_date": "2025-10-01",
        "end_date": "2025-10-31"
    }
    
    Returns costs grouped by tag values
    """
    try:
        subscription_ids = request_body.get("subscription_ids", [])
        tag_key = request_body.get("tag_key")
        tag_values = request_body.get("tag_values")  # Optional
        
        if not subscription_ids or not tag_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription_ids and tag_key are required"
            )
        
        start_date = datetime.fromisoformat(request_body["start_date"].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(request_body["end_date"].replace('Z', '+00:00'))
        
        credential = dual_cred_service.get_user_credential(current_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        logger.info(f" Getting costs by tag '{tag_key}' for {len(subscription_ids)} subscriptions")
        
        result = await cost_service.get_costs_by_tag(
            subscription_ids=subscription_ids,
            tag_key=tag_key,
            tag_values=tag_values,
            start_date=start_date,
            end_date=end_date
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Tag-based costs query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get costs by tag"
        )


@app.post("/api/costs/reservations")
async def get_reservation_analysis(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    💎 Reservation and Savings Plan analysis
    
    Compares AmortizedCost (with reservations) vs ActualCost (pay-as-you-go)
    Shows savings from reservations and savings plans
    
    Request body:
    {
        "subscription_ids": ["sub-1", "sub-2"],
        "start_date": "2025-10-01",
        "end_date": "2025-10-31"
    }
    
    Returns:
    {
        "success": true,
        "amortized_cost": 10000.00,
        "actual_cost": 12000.00,
        "reservation_savings": 2000.00,
        "savings_percent": 16.7,
        "currency": "USD"
    }
    """
    try:
        subscription_ids = request_body.get("subscription_ids", [])
        
        if not subscription_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription_ids is required"
            )
        
        start_date = datetime.fromisoformat(request_body["start_date"].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(request_body["end_date"].replace('Z', '+00:00'))
        
        credential = dual_cred_service.get_user_credential(current_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        logger.info(f" Analyzing reservation utilization for {len(subscription_ids)} subscriptions")
        
        result = await cost_service.get_reservation_utilization(
            subscription_ids=subscription_ids,
            start_date=start_date,
            end_date=end_date
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Reservation analysis failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze reservations"
        )


@app.post("/api/costs/budgets")
async def get_budgets(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    💰 Get budget information for subscriptions
    
    Request body:
    {
        "subscription_ids": ["sub-1", "sub-2", ...]
    }
    
    Returns:
    {
        "success": true,
        "total_budget": 50000.00,
        "total_current_spend": 35000.00,
        "total_forecasted_spend": 48000.00,
        "total_utilization_percent": 70.0,
        "budgets": [
            {
                "subscription_id": "sub-1",
                "budget_name": "Monthly Budget",
                "amount": 25000.00,
                "current_spend": 18000.00,
                "forecasted_spend": 24000.00,
                "utilization_percent": 72.0,
                "time_grain": "Monthly",
                "time_period_start": "2025-11-01",
                "time_period_end": "2025-11-30"
            }
        ]
    }
    """
    try:
        subscription_ids = request_body.get("subscription_ids", [])
        
        if not subscription_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subscription_ids is required"
            )
        
        credential = dual_cred_service.get_user_credential(current_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        logger.info(f" Getting budgets for {len(subscription_ids)} subscriptions")
        
        result = await cost_service.get_budgets(subscription_ids=subscription_ids)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Budget retrieval failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get budgets"
        )


@app.post("/api/costs/resources")
async def get_resource_costs(
    request_body: Dict[str, Any],
    current_user: dict = Depends(get_current_user)
):
    """
    🎯 Cost analysis for specific resources
    
    Request body:
    {
        "resource_ids": [
            "/subscriptions/.../resourceGroups/.../providers/.../vm1",
            "/subscriptions/.../resourceGroups/.../providers/.../vm2"
        ],
        "start_date": "2025-10-01",
        "end_date": "2025-10-31"
    }
    """
    try:
        resource_ids = request_body.get("resource_ids", [])
        
        if not resource_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="resource_ids is required"
            )
        
        start_date = datetime.fromisoformat(request_body["start_date"].replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(request_body["end_date"].replace('Z', '+00:00'))
        
        credential = dual_cred_service.get_user_credential(current_user, token_type='management')
        cost_service = AdvancedCostService(credential)
        
        logger.info(f" Getting costs for {len(resource_ids)} specific resources")
        
        result = await cost_service.get_resource_costs(
            resource_ids=resource_ids,
            start_date=start_date,
            end_date=end_date
        )
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resource costs query failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get resource costs"
        )


# ============================================================================
# SAVED VIEWS API
# ============================================================================

@app.post("/api/saved-views", response_model=SavedView)
async def create_saved_view(
    request: CreateSavedViewRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new saved view
    
    Saves current filter configuration as a reusable view
    """
    try:
        user_id = current_user.get('oid') or current_user.get('sub')
        saved_view_service = SavedViewService(cache_service.redis_client)
        
        view = await saved_view_service.create_view(user_id, request)
        
        return view
        
    except Exception as e:
        logger.error(f"Failed to create saved view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create saved view"
        )


@app.get("/api/saved-views", response_model=SavedViewListResponse)
async def list_saved_views(
    current_user: dict = Depends(get_current_user)
):
    """
    List all saved views for the current user
    """
    try:
        user_id = current_user.get('oid') or current_user.get('sub')
        saved_view_service = SavedViewService(cache_service.redis_client)
        
        return await saved_view_service.list_views(user_id)
        
    except Exception as e:
        logger.error(f"Failed to list saved views: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list saved views"
        )


@app.get("/api/saved-views/default", response_model=Optional[SavedView])
async def get_default_view(
    current_user: dict = Depends(get_current_user)
):
    """
    Get user's default saved view
    """
    try:
        user_id = current_user.get('oid') or current_user.get('sub')
        saved_view_service = SavedViewService(cache_service.redis_client)
        
        return await saved_view_service.get_default_view(user_id)
        
    except Exception as e:
        logger.error(f"Failed to get default view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get default view"
        )


@app.get("/api/saved-views/{view_id}", response_model=SavedView)
async def get_saved_view(
    view_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific saved view by ID
    """
    try:
        user_id = current_user.get('oid') or current_user.get('sub')
        saved_view_service = SavedViewService(cache_service.redis_client)
        
        view = await saved_view_service.get_view(user_id, view_id)
        if not view:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved view not found"
            )
        
        return view
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get saved view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get saved view"
        )


@app.put("/api/saved-views/{view_id}", response_model=SavedView)
async def update_saved_view(
    view_id: str,
    request: UpdateSavedViewRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing saved view
    """
    try:
        user_id = current_user.get('oid') or current_user.get('sub')
        saved_view_service = SavedViewService(cache_service.redis_client)
        
        view = await saved_view_service.update_view(user_id, view_id, request)
        if not view:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved view not found"
            )
        
        return view
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update saved view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update saved view"
        )


@app.delete("/api/saved-views/{view_id}")
async def delete_saved_view(
    view_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a saved view
    """
    try:
        user_id = current_user.get('oid') or current_user.get('sub')
        saved_view_service = SavedViewService(cache_service.redis_client)
        
        success = await saved_view_service.delete_view(user_id, view_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Saved view not found"
            )
        
        return {"success": True, "message": "Saved view deleted"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete saved view: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete saved view"
        )

# =============================================================================
# Azure Firewall Analysis Endpoints (service-principal backed)
# =============================================================================

from services.firewall_service import firewall_service, clear_flatten_cache as _fw_clear_cache
from services.packet_walk_service import PacketWalkService
from services.firewall_kql_template_service import firewall_kql_template_service

_packet_walk_service = PacketWalkService(firewall_service)

# Clear any stale flatten cache on startup (ensures field-name fixes take effect immediately)
_fw_clear_cache()


@app.post("/api/firewall/cache/clear", tags=["Firewall"])
async def fw_clear_cache(_: dict = Depends(get_network_user)):
    """Evict the rule flatten cache for all policies. Forces a fresh fetch on next request."""
    _fw_clear_cache()
    return {"message": "Flatten cache cleared"}


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/diagnostic-info", tags=["Firewall"])
async def fw_get_diagnostic_info(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    _: dict = Depends(get_network_user),
):
    """
    Detect the Azure Firewall linked to this policy and its Log Analytics workspace
    via diagnostic settings. Returns preferred workspace using Dedicated > AzureDiagnostics priority.
    """
    try:
        # Use the service principal token for this discovery call
        sp_token = await asyncio.to_thread(firewall_service._get_sp_token)
        result = await firewall_service.get_policy_diagnostic_info(
            subscription_id=subscription_id,
            resource_group=resource_group,
            policy_name=policy_name,
            management_token=sp_token,
        )
        return result
    except Exception as e:
        logger.error(f"Firewall – diagnostic info failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get firewall diagnostic info")


@app.get("/api/firewall/subscriptions", tags=["Firewall"])
async def fw_list_subscriptions(
    has_policies: bool = Query(False, description="When true, only return subscriptions that have at least one firewall policy"),
    _: dict = Depends(get_network_user),
):
    """List all subscriptions visible to the service principal."""
    try:
        subs = await firewall_service.list_subscriptions(has_policies=has_policies)
        return {"subscriptions": subs}
    except Exception as e:
        logger.error(f"Firewall – list subscriptions failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list subscriptions")


@app.get("/api/firewall/subscriptions/{subscription_id}/firewalls", tags=["Firewall"])
async def fw_list_firewalls(
    subscription_id: str,
    _: dict = Depends(get_network_user),
):
    """List Azure Firewalls in a subscription."""
    try:
        firewalls = await firewall_service.list_firewalls(subscription_id)
        return {"firewalls": firewalls}
    except Exception as e:
        logger.error(f"Firewall – list firewalls failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list firewalls")


@app.get("/api/firewall/subscriptions/{subscription_id}/policies", tags=["Firewall"])
async def fw_list_policies(
    subscription_id: str,
    _: dict = Depends(get_network_user),
):
    """List Firewall Policies in a subscription."""
    try:
        policies = await firewall_service.list_firewall_policies(subscription_id)
        return {"policies": policies}
    except Exception as e:
        logger.error(f"Firewall – list policies failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list firewall policies")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/summary", tags=["Firewall"])
async def fw_get_summary(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    _: dict = Depends(get_network_user),
):
    """KPI summary for a firewall policy."""
    try:
        summary = await firewall_service.get_policy_summary(subscription_id, resource_group, policy_name)
        return summary
    except Exception as e:
        logger.error(f"Firewall – summary failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get firewall policy summary")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/rules", tags=["Firewall"])
async def fw_get_all_rules(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    rule_types: Optional[str] = Query(None, description="Comma-separated: NetworkRule,ApplicationRule,NatRule"),
    _: dict = Depends(get_network_user),
):
    """Return all rules for a policy, optionally filtered by type."""
    try:
        rt_list = [r.strip() for r in rule_types.split(",")] if rule_types else None
        result = await firewall_service.get_all_rules(subscription_id, resource_group, policy_name, rt_list)
        return result
    except Exception as e:
        logger.error(f"Firewall – get rules failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get firewall rules")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/search", tags=["Firewall"])

class ConditionItem(BaseModel):
    field: str
    operator: str
    value: str

class ConditionGroup(BaseModel):
    conditions: List[ConditionItem]
    intra_connector: str = "AND"  # operator between conditions within this group
    inter_connector: str = "AND"  # operator from this group to the next

class AdvancedSearchRequest(BaseModel):
    groups: List[ConditionGroup]


@app.post("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/search/advanced", tags=["Firewall"])
async def fw_search_rules_advanced(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    body: AdvancedSearchRequest,
    _: dict = Depends(get_network_user),
):
    """
    Advanced rule search. Accepts a list of conditions with field/operator/value
    and an AND|OR combinator.
    """
    try:
        result = await firewall_service.search_rules_advanced(
            subscription_id=subscription_id,
            resource_group=resource_group,
            policy_name=policy_name,
            groups=[g.dict() for g in body.groups],
        )
        return result
    except Exception as e:
        logger.error("Firewall – advanced search failed: %s", e)
        raise HTTPException(status_code=500, detail="Advanced search failed")

async def fw_search_rules(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    source: Optional[str] = Query(None, description="Source IP or CIDR to match"),
    destination: Optional[str] = Query(None, description="Destination IP, CIDR or FQDN to match"),
    port: Optional[str] = Query(None, description="Destination port"),
    protocol: Optional[str] = Query(None, description="Protocol (TCP, UDP, ICMP, Any)"),
    rule_types: Optional[str] = Query(None, description="Comma-separated: NetworkRule,ApplicationRule,NatRule"),
    action: Optional[str] = Query(None, description="Allow or Deny"),
    name_filter: Optional[str] = Query(None, description="Substring match on rule name"),
    _: dict = Depends(get_network_user),
):
    """
    Flexible rule search.  All parameters are optional and combinable.
    IP/CIDR matching uses containment: a rule matches if the supplied IP is
    *inside* any of the rule's source/destination ranges.
    """
    try:
        rt_list = [r.strip() for r in rule_types.split(",")] if rule_types else None
        result = await firewall_service.search_rules(
            subscription_id=subscription_id,
            resource_group=resource_group,
            policy_name=policy_name,
            source=source,
            destination=destination,
            port=port,
            protocol=protocol,
            rule_types=rt_list,
            action=action,
            name_filter=name_filter,
        )
        return result
    except Exception as e:
        logger.error(f"Firewall – search rules failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to search firewall rules")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/overlaps", tags=["Firewall"])
async def fw_detect_overlaps(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    rule_types: Optional[str] = Query(None, description="Comma-separated rule type filter"),
    _: dict = Depends(get_network_user),
):
    """
    Detect overlapping or duplicate rules across all rule collections.
    Returns pairs with severity: exact_duplicate | same_action | conflict.
    """
    try:
        import time as _time
        rt_list = [r.strip() for r in rule_types.split(",")] if rule_types else None
        _t0 = _time.monotonic()
        result = await firewall_service.detect_overlaps(subscription_id, resource_group, policy_name, rt_list)
        logger.info(
            "Firewall overlap detection completed in %.1fs — %d rules, %d overlaps, mode: %s",
            _time.monotonic() - _t0,
            result.get("total_rules_scanned", 0),
            result.get("overlaps_found", 0),
            result.get("analysis_mode", "?")[:120],
        )
        return result
    except Exception as e:
        logger.error(f"Firewall – overlap detection failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to detect overlapping rules")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/orphans", tags=["Firewall"])
async def fw_detect_orphans(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    workspace_id: str = Query(..., description="Log Analytics workspace ID or resource path"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    rule_types: Optional[str] = Query(None, description="Comma-separated rule type filter"),
    _: dict = Depends(get_network_user),
):
    """
    Cross-correlate all rules with Azure Firewall logs.
    Rules with zero hits in the given window are flagged as orphans.
    """
    try:
        rt_list = [r.strip() for r in rule_types.split(",")] if rule_types else None
        result = await firewall_service.detect_orphan_rules(
            subscription_id=subscription_id,
            resource_group=resource_group,
            policy_name=policy_name,
            workspace_id=workspace_id,
            days=days,
            rule_types=rt_list,
        )
        return result
    except Exception as e:
        logger.error(f"Firewall – orphan detection failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to detect orphan rules")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/traffic/intelligence", tags=["Firewall"])
async def fw_traffic_intelligence(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    workspace_id: str = Query(..., description="Log Analytics workspace ID or resource path"),
    days: int = Query(7, ge=1, le=90, description="Look-back window in days"),
    rule_types: Optional[str] = Query(None, description="Comma-separated rule type filter"),
    start_time: Optional[str] = Query(None, description="Custom range start (ISO8601)"),
    end_time: Optional[str] = Query(None, description="Custom range end (ISO8601)"),
    _: dict = Depends(get_network_user),
):
    """Traffic intelligence: denied traffic, top rules, top talkers, E/W ratio, threat intel, IDPS, protocol anomalies."""
    try:
        rt_list = [r.strip() for r in rule_types.split(",")] if rule_types else None
        result = await firewall_service.get_traffic_intelligence(
            workspace_id=workspace_id,
            days=days,
            rule_types=rt_list,
            start_time=start_time,
            end_time=end_time,
        )
        return result
    except Exception as e:
        logger.error("Firewall – traffic intelligence failed: %s", e)
        raise HTTPException(status_code=500, detail="Traffic intelligence query failed")


class LogSearchRequest(BaseModel):
    workspace_id: str
    groups: List[ConditionGroup]
    start_time: str
    end_time: str
    limit: int = 1000

    @validator('limit')
    def clamp_limit(cls, v: int) -> int:
        return max(1, min(v, 100_000))


@app.post("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/logs/search", tags=["Firewall"])
async def fw_log_search(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    body: LogSearchRequest,
    _: dict = Depends(get_network_user),
):
    """Condition-based log search with custom time range (down to minute precision)."""
    try:
        result = await firewall_service.search_logs(
            workspace_id=body.workspace_id,
            groups=[g.dict() for g in body.groups],
            start_time=body.start_time,
            end_time=body.end_time,
            limit=body.limit,
        )
        return result
    except Exception as e:
        logger.error("Firewall – log search failed: %s", e)
        raise HTTPException(status_code=500, detail="Log search failed")


class KqlExecuteRequest(BaseModel):
    workspace_id: str
    kql: str
    start_time: str
    end_time: str
    limit: int = 5000

    @validator('limit')
    def clamp_limit(cls, v: int) -> int:
        return max(1, min(v, 100_000))


_KQL_JOB_TTL = settings.KQL_JOB_TTL  # sourced from config.py


@app.post("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/logs/kql", tags=["Firewall"])
async def fw_execute_kql(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    body: KqlExecuteRequest,
    background_tasks: BackgroundTasks,
    _: dict = Depends(get_network_user),
):
    """Submit a KQL query as a background job. Returns job_id immediately — poll /kql-jobs/{id}."""
    import uuid as _uuid
    job_id = str(_uuid.uuid4())
    redis = cache_service.redis_client

    async def _store(payload: dict, ttl: int = _KQL_JOB_TTL) -> None:
        if redis:
            await redis.setex(f"fw_kql_job:{job_id}", ttl, json.dumps(payload, default=str))

    await _store({"status": "running", "result": None})

    async def _run_job():
        try:
            result = await firewall_service.execute_kql(
                workspace_id=body.workspace_id,
                kql=body.kql,
                start_time=body.start_time,
                end_time=body.end_time,
                limit=body.limit,
            )
            await _store({"status": "done", "result": result})
        except Exception as e:
            logger.error("Firewall – KQL job %s failed: %s", job_id, e)
            await _store({"status": "error", "result": {
                "error": str(e) or "Query failed", "columns": [], "rows": [], "total": 0, "kql_executed": body.kql,
            }})

    background_tasks.add_task(_run_job)
    return {"job_id": job_id, "status": "running"}


@app.get("/api/firewall/kql-jobs/{job_id}", tags=["Firewall"])
async def fw_kql_job_status(
    job_id: str,
    _: dict = Depends(get_network_user),
):
    """Poll status of a submitted KQL job."""
    redis = cache_service.redis_client
    if not redis:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    raw = await redis.get(f"fw_kql_job:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    job = json.loads(raw)
    return {"job_id": job_id, "status": job["status"], "result": job.get("result")}


@app.post("/api/firewall/{subscription_id}/{resource_group}/{policy_name}/logs/kql/stream", tags=["Firewall"])
async def fw_execute_kql_stream(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    body: KqlExecuteRequest,
    _: dict = Depends(get_network_user),
):
    """
    Execute KQL and stream the result as NDJSON.
    Sends a bare newline heartbeat every 5 s while the query runs, then
    one final JSON line: {"status":"done","result":{...}}  or  {"status":"error","error":"..."}.
    The client reads with fetch + ReadableStream — no polling loop needed.
    """
    async def generate():
        task = asyncio.create_task(firewall_service.execute_kql(
            workspace_id=body.workspace_id,
            kql=body.kql,
            start_time=body.start_time,
            end_time=body.end_time,
            limit=body.limit,
        ))
        try:
            while not task.done():
                yield b"\n"                       # heartbeat — keeps the connection alive
                await asyncio.sleep(5)
            exc = task.exception()
            if exc:
                raise exc
            result = task.result()
            yield json.dumps({"status": "done", "result": result}).encode() + b"\n"
        except Exception as exc:
            logger.error("Firewall – KQL stream failed: %s", exc)
            yield json.dumps({
                "status": "error",
                "result": {"error": str(exc) or "Query failed", "columns": [], "rows": [], "total": 0, "kql_executed": body.kql},
            }).encode() + b"\n"
        finally:
            if not task.done():
                task.cancel()

    return FastAPIStreamingResponse(generate(), media_type="application/x-ndjson")


class NlToKqlRequest(BaseModel):
    natural_query: str


@app.post("/api/firewall/logs/nl-to-kql", tags=["Firewall"])
async def fw_nl_to_kql(
    body: NlToKqlRequest,
    _: dict = Depends(get_network_user),
):
    """Convert natural language to Azure Firewall KQL using AI."""
    if not ai_service or not ai_service.is_available():
        raise HTTPException(status_code=503, detail="AI service unavailable")
    try:
        kql = await ai_service.nl_to_firewall_kql(body.natural_query)
        return {"kql": kql}
    except Exception as e:
        logger.error("Firewall – NL-to-KQL failed: %s", e)
        raise HTTPException(status_code=500, detail="NL-to-KQL conversion failed")


# ── Firewall KQL saved templates (permanent, team-shared) ─────────────────────

class FwKqlTemplateSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    kql: str = Field(..., min_length=1)


@app.get("/api/firewall/kql-templates", tags=["Firewall"])
async def fw_kql_templates_list(_: dict = Depends(get_network_user)):
    """List all team-shared KQL templates."""
    try:
        templates = await firewall_kql_template_service.list_all()
        return {"templates": templates}
    except RuntimeError as e:
        logger.warning("Firewall KQL templates – Redis unavailable: %s", e)
        return {"templates": []}
    except Exception as e:
        logger.error("Firewall KQL templates list failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list KQL templates")


@app.post("/api/firewall/kql-templates", tags=["Firewall"])
async def fw_kql_templates_save(
    body: FwKqlTemplateSaveRequest,
    current_user: dict = Depends(get_current_user),
):
    """Save a KQL query shared with the whole team."""
    user_name = current_user.get("name") or current_user.get("preferred_username") or "Unknown"
    try:
        template = await firewall_kql_template_service.save(
            user_name=user_name,
            name=body.name,
            description=body.description,
            kql=body.kql,
        )
        return template
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("Firewall KQL template save failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save KQL template")


@app.delete("/api/firewall/kql-templates/{template_id}", tags=["Firewall"])
async def fw_kql_templates_delete(
    template_id: str,
    _: dict = Depends(get_network_user),
):
    """Delete a KQL template. Any user with firewall access can delete."""
    try:
        success = await firewall_kql_template_service.delete(template_id)
        if not success:
            raise HTTPException(status_code=404, detail="Template not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Firewall KQL template delete failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete KQL template")


@app.get("/api/firewall/kql-templates/favourites", tags=["Firewall"])
async def fw_kql_favourites_list(current_user: dict = Depends(get_current_user)):
    """Return the list of favourite template IDs for the current user."""
    user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
    try:
        ids = await firewall_kql_template_service.get_favourites(user_id)
        return {"favourite_ids": ids}
    except RuntimeError:
        return {"favourite_ids": []}
    except Exception as e:
        logger.error("Firewall KQL favourites list failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get favourites")


class FwKqlFavouriteToggleRequest(BaseModel):
    template_id: str


@app.post("/api/firewall/kql-templates/favourites/toggle", tags=["Firewall"])
async def fw_kql_favourites_toggle(
    body: FwKqlFavouriteToggleRequest,
    current_user: dict = Depends(get_current_user),
):
    """Toggle a template as favourite. Returns whether it was added (True) or removed (False)."""
    user_id = current_user.get("sub") or current_user.get("oid") or current_user.get("email", "unknown")
    try:
        added = await firewall_kql_template_service.toggle_favourite(user_id, body.template_id)
        return {"added": added, "template_id": body.template_id}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("Firewall KQL favourite toggle failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to toggle favourite")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{firewall_name}/azure-metrics", tags=["Firewall"])
async def fw_azure_metrics(
    subscription_id: str,
    resource_group: str,
    firewall_name: str,
    metric: str = Query(..., description="Metric name e.g. NetworkRuleHit"),
    aggregation: str = Query("Total", description="Aggregation: Average|Minimum|Maximum|Total"),
    interval: str = Query("PT1H", description="ISO 8601 interval e.g. PT1M PT5M PT1H P1D"),
    timespan_start: str = Query(..., description="ISO 8601 UTC start e.g. 2026-04-12T00:00:00Z"),
    timespan_end: str = Query(..., description="ISO 8601 UTC end"),
    split_by: str = Query("", description="Dimension to split by e.g. Protocol or empty string"),
    _: dict = Depends(get_network_user),
):
    """
    Query Azure Monitor Metrics for a specific Azure Firewall resource.
    Supports all 8 metrics exposed by Microsoft.Network/azureFirewalls,
    optional dimension splitting, and any ISO-8601 interval/timespan.
    """
    try:
        result = await firewall_service.get_firewall_metrics(
            subscription_id=subscription_id,
            resource_group=resource_group,
            firewall_name=firewall_name,
            metric_name=metric,
            aggregation=aggregation,
            interval=interval,
            timespan_start=timespan_start,
            timespan_end=timespan_end,
            split_by=split_by,
        )
        return result
    except Exception as e:
        logger.error("Firewall – azure metrics failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Metrics query failed: {e}")


@app.get("/api/firewall/rule-log-detail", tags=["Firewall"])
async def fw_rule_log_detail(
    workspace_id: str = Query(..., description="Log Analytics workspace ID"),
    rule_name: str = Query(..., description="Exact rule name to look up"),
    days: int = Query(7, ge=1, le=90, description="Look-back window in days"),
    _: dict = Depends(get_network_user),
):
    """Fetch detailed traffic log entries for a specific rule name."""
    try:
        result = await firewall_service.get_rule_log_detail(workspace_id, rule_name, days)
        return result
    except Exception as e:
        logger.error(f"Firewall – rule log detail failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get rule log detail")


@app.get("/api/firewall/{subscription_id}/{resource_group}/{firewall_name}/configuration", tags=["Firewall"])
async def fw_get_configuration(
    subscription_id: str,
    resource_group: str,
    firewall_name: str,
    _: dict = Depends(get_network_user),
):
    """
    Return a full configuration snapshot of the Azure Firewall resource:
    SKU, availability zones, public IPs (SNAT), forced-tunnelling management NIC,
    threat intelligence settings, DNS proxy, IDPS/TLS (Premium), SNAT private ranges,
    scaling characteristics, and operational state.
    """
    try:
        result = await firewall_service.get_firewall_configuration(
            subscription_id=subscription_id,
            resource_group=resource_group,
            firewall_name=firewall_name,
        )
        return result
    except Exception as e:
        logger.error(f"Firewall – configuration fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch firewall configuration")


# ---------------------------------------------------------------------------
# Packet Walk
# ---------------------------------------------------------------------------

class PacketWalkRequest(BaseModel):
    source: str                # IP, CIDR, or on-prem IP
    destination: str           # IP, CIDR, or FQDN
    port: str                  # e.g. "443"
    protocol: str = "TCP"      # TCP | UDP | ICMP | HTTPS | HTTP
    subscription_ids: List[str] = []  # scope for Resource Graph queries


@app.post(
    "/api/firewall/{subscription_id}/{resource_group}/{policy_name}/packet-walk",
    tags=["Firewall"],
)
async def fw_packet_walk(
    subscription_id: str,
    resource_group: str,
    policy_name: str,
    body: PacketWalkRequest,
    _: dict = Depends(get_network_user),
):
    """
    Full packet walk: source resolution (NIC / subnet / gateway + BGP) →
    route evaluation (UDR + BGP longest-prefix-match) → NSG outbound →
    Azure Firewall rule evaluation with priority ordering → AI verdict.
    """
    try:
        scope = body.subscription_ids or [subscription_id]
        result = await _packet_walk_service.analyze(
            subscription_id=subscription_id,
            resource_group=resource_group,
            policy_name=policy_name,
            source=body.source,
            destination=body.destination,
            port=body.port,
            protocol=body.protocol,
            subscription_ids=scope,
            ai_service=ai_service,
        )
        return result
    except Exception as e:
        logger.error("Packet walk failed: %s", e)
        raise HTTPException(status_code=500, detail="Packet walk analysis failed")


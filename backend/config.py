import os
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    # Azure AD Configuration (User Authentication)
    AZURE_AUTHORITY: str = "https://login.microsoftonline.com"
    AZURE_TENANT_ID: str = "common"  # Default to multi-tenant
    
    # Note: Client ID and Tenant ID extracted from user's JWT token
    
    # Service Principal Configuration (AI & Background Services)
    AZURE_SERVICE_PRINCIPAL_CLIENT_ID: str = ""
    AZURE_SERVICE_PRINCIPAL_CLIENT_SECRET: str = ""
    AZURE_SERVICE_PRINCIPAL_TENANT_ID: str = ""
    
    # Azure Resources Configuration
    RESOURCE_GROUP_NAME: str = ""
    KEY_VAULT_NAME: str = ""
    KEY_VAULT_URI: str = ""
    
    # Azure OpenAI Configuration (Foundry Models)
    OPENAI_API_BASE: str = ""  # Azure OpenAI base endpoint (without /openai/responses)
    OPENAI_API_KEY: str = ""  # Azure OpenAI API key
    OPENAI_API_VERSION: str = "2024-12-01-preview"  # Foundry API version
    OPENAI_DEPLOYMENT_NAME: str = ""  # Model deployment name - e.g., gpt-5.4-pro
    
    # Azure AI Search Configuration (for Microsoft Learn documentation index)
    AZURE_SEARCH_ENDPOINT: str = ""  # Azure AI Search endpoint (e.g., https://your-search.search.windows.net)
    AZURE_SEARCH_KEY: str = ""  # Azure AI Search admin/query key
    AZURE_SEARCH_INDEX_NAME: str = "microsoft-docs"  # Index name for Microsoft Learn docs
    USE_AZURE_SEARCH: bool = True  # Enable Azure AI Search for documentation for documentation examples
    
    # Application Configuration
    API_VERSION: str = "v1"
    LOG_LEVEL: str = "INFO"  # Logging level: DEBUG, INFO, WARNING, ERROR
    API_BASE_URL: str = ""  # Base URL for API server (used in Swagger)
    
    # Admin Configuration - Azure AD App Roles
    ADMIN_ROLE_NAME: str = "Admin"    # Full access: all routes including /admin
    ADVANCE_ROLE_NAME: str = "Advance"  # Elevated access: /firewall + future routes, not /admin
    NETWORK_ROLE_NAME: str = "Network"  # Network access: /firewall only
    
    # CORS Configuration - can be comma-separated string or JSON array
    CORS_ORIGINS: str = ""  # Set via environment variable
    
    def get_cors_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string and return as list"""
        if not self.CORS_ORIGINS:
            return []
        # Handle JSON array format or comma-separated string
        if self.CORS_ORIGINS.startswith('['):
            import json
            return json.loads(self.CORS_ORIGINS)
        return [origin.strip() for origin in self.CORS_ORIGINS.split(',') if origin.strip()]
    
    # Environment name — used to tag Application Insights telemetry (local / test / production)
    ENVIRONMENT: str = "local"

    # Application Insights
    APPINSIGHTS_CONNECTION_STRING: str = ""
    APPINSIGHTS_SERVICE_NAME: str = "cloudex"
    
    # Redis Configuration (for caching) - Optional
    REDIS_URL: str = ""  # Empty means no Redis caching (format: redis://[:password]@host:port?ssl=true)
    
    # Cache Configuration
    CACHE_TTL: int = 0  # Set via environment variable
    
    # Background Prefetch Configuration
    PREFETCH_INTERVAL_MINUTES: int = 210  # How often to prefetch Azure data (3.5 hours - refreshes before 4h cache expires)
    PREFETCH_CACHE_TTL_SECONDS: int = 14400  # How long to cache prefetched data (4 hours = 240 minutes)

    # Security (Note: JWT validation uses Azure AD public keys, not SECRET_KEY)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Redis / cache connection ─────────────────────────────────────────────
    REDIS_SOCKET_CONNECT_TIMEOUT: int = 10   # seconds to establish TCP connection
    REDIS_SOCKET_TIMEOUT: int = 30           # seconds for read/write operations

    # ── Cost service — Azure Cost Management API throttling ─────────────────
    COST_MAX_PARALLEL_QUERIES: int = 3       # concurrent Cost Management requests
    COST_BATCH_DELAY_SECONDS: int = 5        # pause between batches (rate-limit avoidance)
    COST_MAX_RETRIES: int = 5                # max attempts per scope on 429/5xx
    COST_INITIAL_RETRY_DELAY: int = 15       # first back-off interval (seconds)
    COST_MAX_RETRY_DELAY: int = 120          # back-off cap (seconds)

    # ── AI / OpenAI client ───────────────────────────────────────────────────
    OPENAI_CONNECT_TIMEOUT: float = 10.0     # TCP connect timeout (seconds)
    OPENAI_READ_TIMEOUT: float = 120.0       # response stream timeout (seconds) — 120s for agentic loop
    OPENAI_WRITE_TIMEOUT: float = 10.0       # request send timeout (seconds)
    OPENAI_POOL_TIMEOUT: float = 10.0        # connection-pool checkout timeout (seconds)
    OPENAI_MAX_CONCURRENT: int = 50          # distributed rate-limiter concurrency cap
    OPENAI_RATE_LIMIT_WAIT: int = 120        # max seconds to wait for a rate-limit slot

    # ── AI query construction ────────────────────────────────────────────────
    AI_MAX_TOOL_CALLS: int = 15              # agentic loop iteration cap
    AI_KQL_ROW_LIMIT_DEFAULT: int = 100      # default KQL result row limit
    AI_KQL_ROW_LIMIT_BROAD: int = 50         # row limit for broad/unfiltered queries
    AI_SEARCH_EXAMPLES_TOP: int = 3          # Azure AI Search examples to inject per query

    # ── ARG / graph helpers ──────────────────────────────────────────────────
    ARG_MAX_SAMPLE_SIZE: int = 20            # rows sampled for schema analysis
    ARG_MAX_CONTEXT_LENGTH: int = 10000      # context chars before truncating aggregations
    ARG_REDUCED_AGGREGATION_COUNT: int = 3   # aggregations kept after truncation
    ARG_MAX_FIELD_LENGTH: int = 80           # max avg field length before skipping field
    ARG_MAX_COMPLETION_TOKENS: int = 1200    # OpenAI max_tokens for ARG completions
    ARG_SUB_BATCH: int = 200                 # subscriptions per ARG batch request

    # ── Graph / AAD service ──────────────────────────────────────────────────
    GRAPH_BATCH_SIZE: int = 20               # MS Graph $batch request size limit
    GRAPH_CACHE_TTL: int = 14400             # seconds to cache group/SP lookups (4 h)

    # ── In-process cache TTLs ────────────────────────────────────────────────
    DIAGNOSTIC_SETTINGS_CACHE_TTL: int = 300   # seconds (5 min)
    FIREWALL_FLATTEN_CACHE_TTL: int = 600       # seconds (10 min)

    # ── Conversation / session management ───────────────────────────────────
    CONVERSATION_TTL_SECONDS: int = 86400 * 30  # 30 days
    MAX_CONVERSATIONS_PER_USER: int = 5

    # ── Query cache ──────────────────────────────────────────────────────────
    QUERY_CACHE_MAX_POPULAR: int = 100       # top-N popular queries tracked in memory
    QUERY_CACHE_KQL_TTL: int = 86400 * 7     # seconds to cache KQL results (7 days)
    QUERY_CACHE_SUMMARY_TTL: int = 86400 * 3 # seconds to cache summary results (3 days)

    # ── User / RBAC permission cache ─────────────────────────────────────────
    USER_PERMISSIONS_CACHE_TTL: int = 1800   # seconds (30 min) — permissions rarely change

    # ── Root cause analysis ───────────────────────────────────────────────────
    RCA_MAX_ITERATIONS: int = 4              # agentic refinement loop cap
    RCA_MAX_WINDOW_HOURS: int = 24           # max analysis time window (hours)

    # ── Authentication ────────────────────────────────────────────────────────
    JWKS_CACHE_TTL: int = 3600               # seconds to cache Azure AD public keys (1 h)

    # ── KQL async job queue ───────────────────────────────────────────────────
    KQL_JOB_TTL: int = 300                   # seconds to keep completed KQL job in Redis

    # ── Log Analytics table schema cache ─────────────────────────────────────
    LA_SCHEMA_CACHE_TTL_DAYS: int = 30       # days before refreshing table schema cache

    # ── Subscription fetch retry ──────────────────────────────────────────────
    SUBSCRIPTION_FETCH_MAX_ATTEMPTS: int = 3       # tenacity stop_after_attempt
    SUBSCRIPTION_FETCH_RETRY_MIN_WAIT: int = 4     # tenacity wait_exponential min (seconds)
    SUBSCRIPTION_FETCH_RETRY_MAX_WAIT: int = 10    # tenacity wait_exponential max (seconds)

    # ── Query / KQL service retry ─────────────────────────────────────────────
    QUERY_RETRY_MAX_ATTEMPTS: int = 3        # tenacity stop_after_attempt for ARG/KQL queries
    QUERY_RETRY_MIN_WAIT: int = 4            # tenacity wait_exponential min (seconds)
    QUERY_RETRY_MAX_WAIT: int = 10           # tenacity wait_exponential max (seconds)

    # ── Redis write-retry (audit + metrics) ───────────────────────────────────
    REDIS_WRITE_RETRY_ATTEMPTS: int = 2      # retries for audit/metrics Redis writes
    REDIS_WRITE_RETRY_MIN_WAIT: int = 1      # wait_exponential min (seconds)
    REDIS_WRITE_RETRY_MAX_WAIT: int = 5      # wait_exponential max (seconds)

    # ── Redis connection pool (shared across audit, metrics, cache services) ──
    REDIS_MAX_CONNECTIONS: int = 250         # aioredis connection pool size
    REDIS_HEALTH_CHECK_INTERVAL: int = 30    # seconds between health pings

    # ── Template / query cache TTLs ───────────────────────────────────────────
    TEMPLATE_TTL_SECONDS: int = 86400 * 30   # query template Redis TTL (30 days)
    RESOURCE_GROUP_CACHE_TTL: int = 600      # live resource-group fetch cache (10 min)
    RESOURCE_CACHE_TTL_FILTERED: int = 1800  # resource cache with RG/type filter (30 min)
    RESOURCE_CACHE_TTL_ALL: int = 900        # resource cache without filter (15 min)

    # ── RCA OpenAI client ─────────────────────────────────────────────────────
    RCA_OPENAI_TIMEOUT: float = 180.0        # RCA service OpenAI request timeout (seconds)
    RCA_OPENAI_MAX_RETRIES: int = 1          # RCA OpenAI client max_retries

    # ── Resource Graph / change analysis HTTP ─────────────────────────────────
    CHANGE_ANALYSIS_TIMEOUT: float = 120.0   # httpx timeout for resource-change ARG query

    # ── Audit service ─────────────────────────────────────────────────────────
    AUDIT_LOG_DAYS_LOOKBACK: int = 2         # Redis date keys to scan for recent logs
    AUDIT_LOG_DEFAULT_LIMIT: int = 100       # default max entries returned by get_logs
    AUDIT_LOG_RETENTION_DAYS: int = 30       # clear_old_logs default retention window

    # ── Dev server ────────────────────────────────────────────────────────────
    SERVER_PORT: int = 8000

    # ── Metrics service ───────────────────────────────────────────────────────
    METRICS_USER_DAYS_LOOKBACK: int = 8      # Redis date keys scanned for 7-day user metric

    # ── Log Analytics schema fetch ────────────────────────────────────────────
    LA_SCHEMA_FETCH_BATCH_SIZE: int = 10     # tables fetched per batch to avoid rate limits

    # ── RCA payload budget ────────────────────────────────────────────────────
    RCA_MAX_PAYLOAD_CHARS: int = 180_000     # ~60K tokens; trim evidence above this

    class Config:
        env_file = ".env.local"
        case_sensitive = True
        extra = "ignore"  # Allow extra fields in .env for deployment scripts

settings = Settings()
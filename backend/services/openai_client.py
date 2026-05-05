"""Shared Azure OpenAI client singleton.

Both AIService (kql_generation_service) and AIQueryService share this single
AsyncAzureOpenAI instance to maintain a consistent connection pool and uniform
client configuration (timeouts, retries, API version).

Retry logic is intentionally disabled (max_retries=0). AIService gates
concurrent calls through the distributed rate limiter; AIQueryService runs
user-initiated agentic loops that are naturally single-concurrency per session.
"""

import httpx
import openai

from config import settings

_client: openai.AsyncAzureOpenAI | None = None


def get_openai_client() -> openai.AsyncAzureOpenAI | None:
    """Return the shared AsyncAzureOpenAI singleton, or None if not configured."""
    global _client

    if _client is not None:
        return _client

    if not settings.OPENAI_API_KEY or not settings.OPENAI_API_BASE:
        return None

    _client = openai.AsyncAzureOpenAI(
        api_key=settings.OPENAI_API_KEY,
        azure_endpoint=settings.OPENAI_API_BASE,
        api_version=settings.OPENAI_API_VERSION,
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.OPENAI_CONNECT_TIMEOUT,
                read=settings.OPENAI_READ_TIMEOUT,
                write=settings.OPENAI_WRITE_TIMEOUT,
                pool=settings.OPENAI_POOL_TIMEOUT,
            ),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        ),
        max_retries=0,
    )
    return _client

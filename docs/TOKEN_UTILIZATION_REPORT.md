# CloudEx Token Utilization

## Summary

**Model:** GPT-5.4 (Azure OpenAI Foundry API `2025-04-01-preview`)

This document tracks actual token allocations configured in the codebase.

---

## Token Allocation by Service

Based on analysis of `max_completion_tokens` values in the codebase:

### AI Service (`ai_service.py`)

| Operation | Max Completion Tokens |
|-----------|----------------------|
| Query Type Detection | 100 |
| Workspace Detection | 500 |
| Table Name Suggestions | 300 |
| KQL Generation (Log Analytics) | 2,000 |
| Resource Graph Query Generation | 800 |
| Metrics Query Generation | 600 |
| Query Refinement | 500 |
| Code Formatting | 800 |
| Error Explanation | 300 |

### AI Query Service (`ai_query_service.py`)

| Operation | Max Completion Tokens |
|-----------|----------------------|
| Main Query Processing | 1,000 |
| Intent Classification | 100 |
| Cost Breakdown | 200 |
| Time Range Detection | 300 |
| Resource Analysis | 800 |
| Results Analysis | 600 |
| Conversation Context | 1,500 |
| Workspace Suggestion | 500 |
| ARG Analysis (constant) | 800 |

### Query Service (`query_service.py`)

| Operation | Max Completion Tokens |
|-----------|----------------------|
| Error Explanation | 300 |

### Query Template Service (`query_template_service.py`)

| Operation | Max Completion Tokens |
|-----------|----------------------|
| Template Matching | 300 |
| Template Categorization | 150 |

### Main API (`main.py`)

| Operation | Max Completion Tokens |
|-----------|----------------------|
| Health Check Response | 50 |

**Token Range:** 50 - 2,000 tokens per AI operation

---

## Caching Configuration

Based on analysis of cache services:

### Cache TTL Settings

**Default Cache TTL** (`config.py`):
```python
CACHE_TTL: int = 0  # Set via environment variable
```

**Service-Specific TTLs:**

| Service | TTL |
|---------|-----|
| User RBAC | 1,800s (30 min) |
| User Access | 1,800s (30 min) |
| Metrics Cache | 86,400s (24 hours) |
| Metrics Short | 3,600s (1 hour) |
| Query Templates | 2,592,000s (30 days) |

**Advanced Query Cache** (`advanced_query_cache_service.py`):
```python
kql_cache_ttl = 86400 * 7        # 7 days
summary_cache_ttl = 86400 * 3    # 3 days
```

**Background Prefetch** (`config.py`):
```python
PREFETCH_INTERVAL_MINUTES: int = 210          # Run every 210 minutes (3.5 hours)
PREFETCH_CACHE_TTL_SECONDS: int = 14400       # 4 hours (240 minutes)
```

### Cache Architecture

**Multi-Level Cache:**

- **L1 (Memory)**: LRU cache, 50 MB max, 1,000 entries
- **L2 (Redis)**: Azure Cache for Redis (optional, falls back to in-memory)

**Cache Service** (`cache_service.py`):
- Backend: Azure Cache for Redis or in-memory dict
- Connection pool: 100 connections max
- Timeout: 5 seconds

---

## Conversation Management

**Message History** (`conversation.py`):
```python
def get_recent_messages(self, count: int = 10) -> List[ConversationMessage]:
    """Get recent messages for context"""
    return self.messages[-count:] if self.messages else []
```

**Cleanup Policy** (`conversation_service.py`):
```python
def cleanup_old_conversations(self, max_age_hours: int = 24):
    """Remove conversations older than specified hours"""
```

**Default:** Keep last 10 messages, cleanup after 24 hours

---

## Optimization Strategies

### 1. Search-First Architecture ✅

**Implementation:**

- Azure AI Search retrieves documentation (no OpenAI tokens)
- AI processes only search results, not full corpus
- Reduces context window size

### 2. Dynamic Token Allocation ✅

**Implementation:**

- Simple operations: 50-300 tokens
- Medium operations: 500-800 tokens  
- Complex operations: 1,000-2,000 tokens
- Configured per operation type in code

### 3. Multi-Level Caching ✅

**L1 Cache (Memory):**

- LRU eviction
- 50 MB max size
- 1,000 entries max

**L2 Cache (Redis):**

- Azure Cache for Redis
- TTL: 7 days (KQL queries)
- TTL: 3 days (summaries)

**Cache Metrics Tracked:**

- Hit/miss rates
- Semantic similarity hits
- Eviction counts
- Latency per cache level

### 4. Conversation Context Pruning ✅

**Implementation:**

- Keep last 10 messages only
- Cleanup conversations older than 24 hours
- Removes old context to reduce input tokens

### 5. Background Prefetching ✅

**Implementation:**

- Runs every 60 minutes
- Pre-fetches common queries
- Cache TTL: 70 minutes (10 min buffer)
- Warms cache before expiration

### 6. Query Normalization ✅

**Features** (`advanced_query_cache_service.py`):
- Time expression normalization
- Resource ID parameterization
- Synonym replacement
- Template extraction for semantic matching

---

## Related Documentation

- **Architecture:** [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md)
- **Security:** [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md)

# CloudEx AI Architecture

## Overview

CloudEx uses a **Retrieval-Augmented Generation (RAG)** architecture to generate accurate Azure queries from natural language. The system combines GPT-5.4 with live Microsoft Learn documentation to ensure query accuracy and adaptability.

**System Type:** RAG-enhanced AI assistant (not autonomous/agentic)

---

## Core Architecture

### 1. RAG Pattern

```
User Query
    ↓
Intent Detection → Documentation Search → Query Generation → Execution → Analysis
```

**Key Components:**

- **GPT-5.4**: Azure OpenAI Foundry API (`2025-04-01-preview`)
- **Azure AI Search**: 71,447 indexed Microsoft Learn docs
- **Dynamic Context**: Injects relevant documentation into prompts
- **Query Types**: KQL (Log Analytics), Resource Graph, Metrics, Cost Management

**Benefits:**

- Uses current official documentation
- Adapts to Azure API changes automatically
- 85-95% accuracy vs 70% without search
- No model retraining needed

### 2. Search-First Workflow

**Example:** "Show DNS queries for flysas.com"

```
1. Intent Detection (ai_query_service.py)
   → Identifies: Log Analytics query needed

2. Documentation Search (azure_search_service.py)
   → Searches: "Azure Firewall DNS query KQL AZFWDnsQuery flysas.com"
   → Returns: 10 relevant documentation snippets

3. Query Generation (ai_service.py)
   → Context: System prompt + search results
   → Generates:
   AzureDiagnostics
   | where Category == "AZFWDnsQuery"
   | where TimeGenerated > ago(1h)
   | extend QueryName = QueryName_s
   | where QueryName contains "flysas.com"
   | project TimeGenerated, SourceIP, QueryName
   | order by TimeGenerated desc

4. Security Validation
   → Validates query is read-only (see SECURITY_ARCHITECTURE.md)

5. Execution (query_service.py)
   → Executes against Log Analytics workspace
   → Applies user RBAC automatically
   → Caches results (300s default TTL)

6. Results Analysis
   → AI summarizes findings
   → Suggests related queries
```

---

## Authentication & Credentials

### Dual Credential System

**User Authentication:**

- Frontend: Azure AD MSAL tokens
- Backend: JWT validation
- Purpose: Resource queries with user's RBAC permissions

**Service Principal:**

- Background services only
- Purpose: Pre-fetching, health checks
- Permissions: Reader role only

**Security:** User sees only what their Azure RBAC allows. Service Principal cannot modify resources.

---

## Service Architecture

### Core Services

| Service | Purpose | File |
|---------|---------|------|
| **AI Service** | GPT-5.4 integration, prompt management | `ai_service.py` |
| **AI Query Service** | Query orchestration, intent detection | `ai_query_service.py` |
| **Azure Search Service** | Microsoft Learn documentation search | `azure_search_service.py` |
| **Query Service** | KQL/Resource Graph execution | `query_service.py` |
| **Cost Service** | Cost Management API queries | `advanced_cost_service.py` |
| **Cache Service** | Redis/in-memory caching | `cache_service.py` |
| **Conversation Service** | Chat history management | `conversation_service.py` |

### Query Flow

```
┌─────────────────────────────────────────────────┐
│  User Natural Language Query                   │
└────────────────┬────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────────────┐
│  AIQueryService: Intent Detection               │
│  • Cost analysis?                               │
│  • Resource query?                              │
│  • Log Analytics?                               │
│  • Metrics query?                               │
└────────────────┬────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────────────┐
│  AzureSearchService: Find Documentation         │
│  • Search 71,447 indexed docs                   │
│  • Return relevant examples                     │
└────────────────┬────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────────────┐
│  AIService: Generate Query                      │
│  • Inject doc context into prompt               │
│  • GPT-5.4 generates KQL/ARG query              │
│  • Security validation applied                  │
└────────────────┬────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────────────┐
│  QueryService: Execute                          │
│  • Execute against Azure APIs                   │
│  • User RBAC enforced                           │
│  • Cache results                                │
└────────────────┬────────────────────────────────┘
                 ↓
┌─────────────────────────────────────────────────┐
│  AIQueryService: Analyze Results                │
│  • AI summarizes findings                       │
│  • Generates insights                           │
│  • Suggests follow-ups                          │
└────────────────┬────────────────────────────────┘
                 ↓
           Return to User
```

---

## Caching Strategy

**Cache Service** (`cache_service.py`):
- **Backend**: Azure Cache for Redis (fallback: in-memory dict)
- **Connection Pool**: 100 connections max
- **Timeout**: 5 seconds

**Multi-Level Cache** (`advanced_query_cache_service.py`):
- **L1 (Memory)**: LRU cache, 50 MB max, 1,000 entries
- **L2 (Redis)**: Persistent cache with configurable TTLs

**Cache TTLs:**
- KQL queries: 7 days
- Summaries: 3 days  
- User RBAC: 30 minutes
- Query templates: 30 days
- Metrics: 24 hours

**Prefetching** (`background_prefetch_service.py`):
- Runs every 60 minutes
- Cache TTL: 70 minutes (10 min buffer)
- Pre-fetches common queries to warm cache

---

## AI Model Configuration

**Azure OpenAI Settings** (`config.py`):
```python
OPENAI_API_VERSION = "2025-04-01-preview"  # Foundry API
OPENAI_DEPLOYMENT_NAME = "gpt-5.4"     # Model deployment
```

**Token Limits:** 50 - 2,000 tokens per operation (varies by complexity)

See [TOKEN_UTILIZATION_REPORT.md](TOKEN_UTILIZATION_REPORT.md) for complete breakdown of token allocation by service and operation.

---

## Security

CloudEx implements **4-layer security validation** to ensure read-only operations:

1. **Input Validation**: Block destructive keywords
2. **Action Validation**: Whitelist safe operations
3. **Query Validation**: Block forbidden KQL operations
4. **Azure RBAC**: Service Principal limited to Reader role

**Result:** Even if AI generates destructive commands, all layers block execution.

**Full Details:** [SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md)

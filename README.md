# CloudEx

CloudEx is a read-only Azure management portal that lets you query, analyse, and troubleshoot resources across multiple subscriptions using natural language. It combines Azure APIs with a GPT-5.4 backend to translate questions into Resource Graph KQL, Log Analytics queries, cost breakdowns, and metric lookups — then surfaces the results in a conversational UI.

---

## What it does

**AI query interface** — Type a question ("which VMs have had CPU above 80% in the last week?") and the backend classifies the intent, selects the right Azure API, generates and validates the query, executes it with your credentials, and returns the results with an AI-written summary.

**Root cause analysis** — Given an incident description and time window, the system collects parallel evidence from resource health, change history, Log Analytics, Azure Monitor metrics, and Service Health, then produces a structured verdict with a confidence score, timeline, and manual checklist.

**Firewall analysis** — Browse Azure Firewall policies, inspect rules, search by IP/CIDR, detect overlaps and orphan rules, and query firewall logs in KQL or natural language. Streaming KQL jobs handle large log queries asynchronously.

**Cost management** — Per-subscription and per-resource-group cost breakdown, tag-based analysis, reservation coverage, and budget tracking — all driven by the Azure Cost Management API with user-delegated credentials.

**Governance** — Policy compliance state, Secure Score, and resource health status, with AI-generated explanations.

**Query templates** — Save, share, and rate reusable queries. Public templates are available to all users; personal templates are scoped to the creating user.

---

## Architecture

```
Browser (Next.js 14)
  └── MSAL authentication (Azure AD)
  └── Axios → FastAPI backend (Python 3.11)
                ├── JWT validation (Azure AD public keys)
                ├── AI query service (GPT-5.4 via Azure OpenAI)
                │     ├── Intent classification
                │     ├── KQL / ARG / metrics / cost dispatch
                │     └── Agentic loop (up to 15 tool calls)
                ├── Query executor (Resource Graph, Log Analytics, Cost Mgmt)
                ├── Firewall service (ARM API, direct HTTPS)
                ├── RCA service (parallel evidence collection)
                ├── Redis cache (optional)
                └── Background prefetch (APScheduler, 3.5h interval)
```

Every Azure API call runs with the authenticated user's delegated token. The service principal is used only for background operations that cannot use a user token (firewall policy reads, MS Graph group resolution). Azure enforces RBAC at the API layer — CloudEx cannot exceed the user's actual permissions.

The system is strictly read-only. A query validator checks every natural language query and AI-generated action against an allowlist before any Azure call is made.

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- An Azure subscription you can read from
- An Azure AD App Registration (for user login)
- A service principal with Reader role on the subscriptions you want to query
- Azure OpenAI with a GPT-5.4 deployment
- Azure AI Search with a `microsoft-docs` index (optional — improves KQL generation quality)
- Redis (optional — in-memory cache used if not configured)

---

## Getting started

```bash
git clone https://github.com/flysas-tech/platform-cloudex.git
cd platform-cloudex

# Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Frontend
cd frontend && npm install && cd ..
```

Copy and fill in the environment files:

```bash
cp backend/.env.example backend/.env.local
cp frontend/.env.example frontend/.env.local
```

Start both services:

```bash
./start-dev.sh
```

Frontend runs on `http://localhost:3000`, backend on `http://localhost:8000`. API documentation is available at `http://localhost:8000/api/docs`.

---

## Configuration

### Azure AD App Registration

Create an App Registration in Azure Portal for the frontend:

1. Azure Portal → App Registrations → New Registration
2. Add a redirect URI: `http://localhost:3000` (type: Single-page application)
3. Under Authentication, enable Access tokens and ID tokens
4. Copy the Application (client) ID

The application uses three token scopes:
- `https://management.azure.com/user_impersonation` — Azure Resource Manager
- `https://api.loganalytics.io/Data.Read` — Log Analytics query execution
- `User.Read` — Microsoft Graph (user profile only)

### Service principal

```bash
az ad sp create-for-rbac \
  --name "cloudex-sp" \
  --role "Reader" \
  --scopes /subscriptions/<subscription-id>
```

For firewall features the service principal also needs `Reader` on the firewall resource group. For MS Graph group name resolution it needs `Group.Read.All` (Application permission, admin consent required).

### Backend environment (`backend/.env.local`)

| Variable | Description |
|---|---|
| `AZURE_TENANT_ID` | Your Azure AD tenant ID |
| `AZURE_SERVICE_PRINCIPAL_CLIENT_ID` | SP application ID |
| `AZURE_SERVICE_PRINCIPAL_CLIENT_SECRET` | SP client secret |
| `AZURE_SERVICE_PRINCIPAL_TENANT_ID` | Usually same as `AZURE_TENANT_ID` |
| `OPENAI_API_BASE` | Azure OpenAI endpoint (e.g. `https://my-openai.openai.azure.com`) |
| `OPENAI_API_KEY` | Azure OpenAI API key |
| `OPENAI_DEPLOYMENT_NAME` | Deployment name for your GPT-5.4 model |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint (optional) |
| `AZURE_SEARCH_KEY` | Azure AI Search key (optional) |
| `CORS_ORIGINS` | JSON array of allowed origins, e.g. `["http://localhost:3000"]` |
| `REDIS_URL` | Redis connection string (optional, e.g. `redis://localhost:6379`) |

### Frontend environment (`frontend/.env.local`)

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_AZURE_CLIENT_ID` | App Registration client ID |
| `NEXT_PUBLIC_AZURE_TENANT_ID` | Azure AD tenant ID |
| `NEXT_PUBLIC_API_URL` | Backend URL, e.g. `http://localhost:8000` |

---

## Access roles

Roles are defined as Azure AD App Roles on the App Registration and assigned to users in the Enterprise Application.

| Role | Access |
|---|---|
| `Admin` | Full access including admin panel (`/api/admin/*`) |
| `Advance` | Firewall module + standard queries |
| `Network` | Firewall module only |
| _(none)_ | Standard query, cost, governance, and RCA features |

Role names are configurable via `ADMIN_ROLE_NAME`, `ADVANCE_ROLE_NAME`, and `NETWORK_ROLE_NAME` in the backend config.

---

## Project structure

```
backend/
  main.py                    # FastAPI app, all route definitions
  config.py                  # All configuration via environment variables
  auth.py                    # Token credential adapters for Azure SDK
  services/
    ai_query_service.py      # Chat orchestrator — intent classification, agentic loop
    kql_generation_service.py# NL-to-KQL conversion, doc-grounded prompt construction
    query_service.py         # Azure API executor — ARG, Log Analytics, Cost Mgmt
    advanced_cost_service.py # Parallel multi-subscription cost analysis
    cost_service.py          # Single-subscription cost executor (query pipeline)
    firewall_service.py      # Firewall policy reads, rule analysis, overlap detection
    root_cause_analysis_service.py  # Evidence collection and AI correlation
    conversation_service.py  # Multi-turn conversation state (Redis-backed)
    query_template_service.py# Template CRUD, semantic search, AI suggestions
    governance_service.py    # Policy compliance, Secure Score
    resource_health_service.py
    metrics_service.py
    change_analysis_service.py
    audit_service.py         # Query audit log (Redis)
    cache_service.py         # Redis cache wrapper
    advanced_query_cache_service.py  # Semantic KQL cache (L1 memory + L2 Redis)
    scheduler_service.py     # APScheduler — background data prefetch
    user_rbac_service.py     # RBAC scope filtering per user
    ms_graph_service.py      # Microsoft Graph — group name resolution
  prompts/
    kql_generation_prompts.py# System prompts for KQL generation
  models/
    conversation.py
    query_template.py
    saved_view.py
  utils/
    arg_table_mapper.py      # Resource type → ARG table mapping
    log_analytics_table_mapper.py
    timezone_utils.py        # CET timezone helpers
  tests/                     # pytest unit tests

frontend/
  src/
    app/                     # Next.js app router pages
      ai/                    # Conversational query interface
      costs/                 # Cost management dashboard
      firewall/              # Firewall analysis
      dashboard/             # Overview / pinned cards
      governance/            # Policy, health, security score (under platform/)
      admin/                 # Admin panel
    components/              # Shared React components
    services/                # Axios API clients
    store/                   # Zustand state stores
    types/                   # TypeScript type definitions
    config/
      authConfig.ts          # MSAL configuration and API base URL
```

---

## Running tests

```bash
cd backend
source ../.venv/bin/activate
python -m pytest tests/ -v
```

Tests cover the query validator (read-only enforcement), log analysis sampling, ARG table mapper, timezone utilities, and pure methods on the AI query service. No Azure connection or OpenAI key required.

---

## Key design decisions

**Read-only enforcement has three layers.** The query validator checks the natural language input. The AI decision validator checks the action the model wants to take against an allowlist. Azure's own RBAC rejects any write at the API level. All three must pass for a request to proceed.

**The AI does not decide the table.** KQL generation uses a cache of resource type → ARG table mappings sourced from Microsoft Learn documentation, so `Microsoft.Network/firewallPolicies` always routes to `NetworkResources` rather than relying on the model to guess correctly.

**User tokens are never stored.** Each request re-extracts the token from the Authorization header. The backend holds no session state for authentication — Redis is used only for query results, conversation history, audit logs, and cache.

**Conversation history is bounded.** Each user can have at most 5 active conversations, each kept for 30 days. History is truncated before being sent to the model to stay within context limits.

**The agentic loop is capped.** The AI query service runs a tool-call loop with a hard limit of 15 iterations (`AI_MAX_TOOL_CALLS`). The RCA service caps at 4 refinement iterations (`RCA_MAX_ITERATIONS`).

---

## License

Proprietary. All rights reserved.

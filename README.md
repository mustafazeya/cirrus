# CloudEx - AI-Powered Azure Multi-Subscription Portal

> 🚀 **Enterprise-grade Azure management platform with GPT-5.4 powered natural language queries, real-time cost analysis, and comprehensive resource monitoring across multiple subscriptions**

[![Azure](https://img.shields.io/badge/Azure-0078D4?style=flat&logo=microsoft-azure&logoColor=white)](https://azure.microsoft.com/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js_14-000000?style=flat&logo=next.js&logoColor=white)](https://nextjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Python](https://img.shields.io/badge/Python_3.11-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Key Features](#-key-features)
- [Architecture](#️-architecture)
- [Technology Stack](#️-technology-stack)
- [Development](#-development)
- [Security](#-security)
- [Documentation](#-documentation)
- [Contributing](#-contributing)
---

## 🌟 Overview

**CloudEx** is a conversational Azure management platform that combines GPT-5.4 with Azure APIs to enable natural language querying, real-time cost analysis, AI-powered root cause analysis, and intelligent resource analysis across multi-subscription environments.

### What Makes CloudEx Different?

- **🤖 AI-First Architecture**: Search-first query system using live Microsoft Learn documentation with GPT-5.4 fallback
- **🔬 AI-Powered Root Cause Analysis**: AI-powered incident investigation correlating changes, health, logs, and metrics
- **🔐 Zero-Trust Security**: Read-only operations with per-user RBAC enforcement
- **📊 Real-Time Intelligence**: Instant cost analysis, performance metrics, and resource insights
- **💬 Conversational Interface**: Natural language queries like "Analyze denied traffic in Azure Firewall logs"
- **🌐 Multi-Subscription**: Unified view across all your Azure subscriptions
- **🏥 Platform Health Awareness**: Integrated Azure Service Health for context-aware incident analysis
---

## ✨ Key Features

### 🎯 AI-Powered Query Engine

- **Natural Language Processing**: Ask questions in plain English, get accurate Azure queries
- **Search-First Architecture**: Queries powered by live Microsoft Learn documentation (71,447 indexed docs)
- **Multi-Query Support**: 
  - **KQL (Kusto Query Language)** for Log Analytics and Application Insights
  - **Azure Resource Graph** queries for resource discovery and analysis
  - **Azure Metrics** queries for performance monitoring
  - **Cost Management** queries for financial insights
- **Context-Aware**: Remembers conversation history for follow-up questions
- **Query Suggestions**: AI suggests relevant follow-up queries

### 🔬 AI-Powered Root Cause Analysis (RCA)

- **Automated Incident Investigation**: AI-assisted correlation across multiple Azure telemetry sources
- **Multi-Source Evidence Collection**: Parallel aggregation from:
  - Azure Resource Changes (Resource Graph)
  - Resource Health status transitions
  - Azure Monitor metrics and platform logs
  - Log Analytics workspace data
  - Azure Service Health platform incidents
- **Service-Specific Analysis**: Specialized log parsing for AKS, Firewall, App Service, SQL, and more
- **Confidence Scoring**: Transparent AI confidence levels (with evidence gaps analysis)
- **Timeline Visualization**: Chronological event correlation across changes, health, metrics, and logs
- **4-Card Evidence Summary**: Changes | Resource Logs | Health Signals | Metric Signals
- **Actionable Insights**: Manual checklist for human validation and next steps
- **Flexible Time Windows**: Analyze incidents from 1 hour to 3 months

### 💰 Advanced Cost Management

- **Real-Time Cost Analysis**: View spending across subscriptions, resource groups, and services
- **AI Cost Insights**: GPT-5.4-powered recommendations for cost optimization
- **Budget Tracking**: Monitor spending against budgets and forecasts
- **Cost Breakdowns**: Drill down by service, resource, tag, or time period
- **Pre-Built Dashboards**: Visualize cost trends and anomalies
- **Export & Reports**: CSV/JSON exports for offline analysis

### 🔍 Intelligent Resource Discovery

- **Multi-Subscription Search**: Find resources across all accessible subscriptions
- **Advanced Filtering**: Filter by type, location, tags, and custom properties
- **Resource Graph Integration**: Lightning-fast queries using Azure Resource Graph
- **RBAC-Aware**: Only show resources users have permission to access

### 📊 Performance & Monitoring

- **Azure Monitor Integration**: Real-time metrics for CPU, memory, disk, and network
- **Log Analytics**: Query application logs and diagnostic data.
- **Pre-Built Dashboards**: Visualize key performance indicators

---

## 🔬 Root Cause Analysis (RCA) Deep Dive

CloudEx's RCA feature is a comprehensive AI-powered incident investigation system that helps identify the root cause of Azure operational issues.

### How It Works

```
1. User describes incident → "Production AKS cluster experiencing 403 errors"
                              ↓
2. Parallel evidence collection (5 sources):
   • Resource Changes      - What changed? (ARG)
   • Resource Health       - Was Azure down? (Resource Health API)
   • Platform Metrics      - CPU/Memory spikes? (Azure Monitor)
   • Resource Logs         - Error logs? (Log Analytics)
   • Service Health        - Azure platform issues? (ARG ServiceHealthResources)
                              ↓
3. Timeline construction   - Chronological correlation
                              ↓
4. AI analysis (GPT-5)     - Pattern matching & causality inference
                              ↓
5. Confidence scoring      - How certain is the AI?
                              ↓
6. Report generation       - Root cause + alternative explanations + manual checklist
```

### Evidence Cards

RCA presents evidence in **4 intuitive cards**:

| Card | What It Shows | Data Source |
|------|---------------|-------------|
| **Changes** | Resource configuration changes, deployments, scaling | Azure Resource Graph |
| **Resource Logs** | Application logs, firewall denies, errors | Log Analytics |
| **Health Signals** | Azure platform health status transitions | Resource Health API |
| **Metric Signals** | CPU, memory, network performance anomalies | Azure Monitor |

### Service-Specific Intelligence

RCA understands Azure service-specific log schemas:

- **AKS**: Control plane logs, pod scheduling, autoscaler events
- **Azure Firewall**: Network/application rule hits, denies, threat intelligence
- **App Service**: HTTP errors, deployment logs, autoscale events
- **SQL Database**: DTU exhaustion, deadlocks, connection errors
- **Storage**: Throttling, availability degradation
- **And more...**

### Azure Service Health Integration

RCA checks for **active Azure platform incidents** that might be affecting your resources:

```kusto
ServiceHealthResources
| where type =~ 'microsoft.resourcehealth/events'
| where properties.EventType in ('ServiceIssue', 'HealthAdvisory')
| where properties.Status == 'Active'
```

This helps rule out or confirm platform-wide issues as root causes.

### AI Transparency

RCA doesn't just give answers—it shows its reasoning:

- **Confidence Score**: 45% to 95% (based on evidence quality)
- **Evidence Gaps**: What data is missing that would increase certainty
- **Alternative Explanations**: Other plausible causes the AI considered
- **Manual Checklist**: Human validation steps to confirm the AI's conclusion

### Real-World Example

**Incident**: "AKS cluster pods failing to schedule"

**RCA Findings**:
- ✅ **Changes**: Node pool scaled down 30 → 10 nodes (15 min ago)
- ✅ **Health Signals**: No Azure platform issues
- ✅ **Metrics**: Node CPU at 95% (spike after scale-down)
- ✅ **Logs**: "Insufficient CPU" pod scheduling errors
- 🎯 **Root Cause**: "Aggressive node scale-down caused resource exhaustion" (Confidence: 87%)

---

### 🔐 Enterprise Security

- **Azure AD Authentication**: Native Microsoft identity integration
- **Read-Only Operations**: Zero write/modify/delete capabilities
- **Per-User RBAC**: Respects Azure role-based access control
- **Audit Logging**: Complete audit trail of all queries and actions

---

## 🏗️ Architecture

CloudEx follows a modern microservices architecture optimized for enterprise Azure environments:

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend Layer                          │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │            Next.js 14 (React + TypeScript)               │   │
│  │  • App Router    • Server Components   • Tailwind CSS    │   │
│  │  • MSAL Auth     • Zustand State       • Real-time UI    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              ↓ HTTPS/REST
┌─────────────────────────────────────────────────────────────────┐
│                         Backend Layer                           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                 FastAPI (Python 3.11)                    │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │           Core Services                            │  │   │
│  │  │  • AI Query Service    • Auth Service              │  │   │
│  │  │  • Resource Service    • Cost Service              │  │   │
│  │  │  • Query Service       • Cache Service             │  │   │
│  │  │  • Conversation Service • RBAC Service             │  │   │
│  │  │  • Azure Monitor Service                           │  │   │
│  │  │  • Root Cause Analysis Service                     │  │   │
│  │  │  • Resource Health Service                         │  │   │
│  │  │  • Change Analysis Service                         │  │   │
│  │  │  • External Status Service (Service Health)        │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  │  ┌────────────────────────────────────────────────────┐  │   │
│  │  │      Search & AI Integration                       │  │   │
│  │  │  • Azure AI Search (71,447 docs indexed)           │  │   │
│  │  │  • OpenAI GPT-5.4 (Azure OpenAI Service)           │  │   │
│  │  │  • Search-First Query Generation                   │  │   │
│  │  └────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              ↓ Azure SDK
┌─────────────────────────────────────────────────────────────────┐
│                         Azure Services                          │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  • Azure Resource Graph    • Azure Monitor               │   │
│  │  • Cost Management API     • Log Analytics               │   │
│  │  • Azure AD / Entra ID     • Key Vault                   │   │
│  │  • Azure AI Search         • Azure OpenAI                │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Key Architectural Patterns

#### 🔍 Search-First Query System

CloudEx uses a unique **search-first architecture** for maximum accuracy:

```
User Query → AI determines intent → Search Microsoft Learn docs → Extract examples
                                                ↓ (if search fails)
                                         GPT-5.4 prompt engineering fallback
```

**Benefits:**
- Always uses **current** official Microsoft documentation
- Handles Azure service evolution automatically (e.g., new table schemas)
- 85-95% query accuracy target (vs 70% with prompts alone)
- Reduces maintenance burden as Azure changes

**Documentation Index:**
- **71,447 English Azure documentation URLs** indexed
- Source: Microsoft Learn official documentation
- Updated: Re-crawled with fixed chunking (preserves full content)
- Chunking: 700 characters per chunk, 100 character overlap

#### 🔐 Dual Authentication System

```
User Authentication (Entra ID)
    ↓
Frontend (MSAL) → Backend (JWT validation)
    ↓
User Token → Azure Management APIs (RBAC-enforced)

Background Services (Service Principal)
    ↓
Pre-fetching, Caching → Service Principal Token
```

---

## 🛠️ Technology Stack

### Backend

| Technology | Version | Purpose |
|------------|---------|---------|
| **FastAPI** | 0.115.2 | High-performance async Python web framework |
| **Python** | 3.11+ | Latest stable Python with async/await support |
| **Azure SDK** | Latest | Official Azure libraries for Python |
| **OpenAI** | 1.56.2 | Azure OpenAI GPT-5.4 integration |
| **Azure Search** | 11.4.0 | Microsoft Learn documentation search |
| **Redis** | 2.0.1 | In-memory caching for performance |
| **PyJWT** | 2.8.0 | JWT token validation |
| **Tenacity** | 8.2.3 | Retry logic for resilience |

### Frontend

| Technology | Version | Purpose |
|------------|---------|---------|
| **Next.js** | 14.0.4 | React framework with App Router |
| **React** | 18.2.0 | UI component library |
| **TypeScript** | 5.3.3 | Type-safe JavaScript |
| **Tailwind CSS** | 3.3.6 | Utility-first CSS framework |
| **MSAL React** | 2.0.9 | Microsoft Authentication Library |
| **Zustand** | 4.4.7 | Lightweight state management |
| **Axios** | 1.6.2 | HTTP client for API calls |
| **Recharts** | 2.8.0 | Data visualization |

### Infrastructure

| Technology | Purpose |
|------------|---------|
| **Azure Container Apps** | Serverless container hosting |
| **Azure Container Registry** | Private Docker registry |
| **Azure Static Web Apps** | Frontend hosting with CDN |
| **Azure OpenAI Service** | GPT-5.4 model hosting |
| **Azure AI Search** | Documentation search index |
| **Azure Key Vault** | Secure secret management |
| **Application Insights** | Monitoring and telemetry |

### Azure Services Integrated

- **Azure Resource Graph**: Fast resource queries across subscriptions, resource change tracking
- **Azure Monitor**: Metrics and log analytics
- **Log Analytics**: KQL query execution
- **Cost Management API**: Billing and cost data
- **Entra ID**: Authentication and authorization
- **Azure Advisor**: Resource recommendations
- **Azure Policy**: Compliance and governance
- **Azure Resource Health**: Resource availability and health status monitoring
- **Azure Service Health**: Platform-wide incident and advisory tracking

---

## 💻 Development

### Prerequisites

- Python 3.11+, Node.js 18+, Azure CLI
- Azure subscription with appropriate permissions

### Setup

```bash
# Clone and setup Python environment
git clone https://github.com/flysas-tech/platform-cloudex.git
cd platform-cloudex
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# Setup frontend
cd frontend && npm install && cd ..

# Configure environment
cp backend/.env.example backend/.env.local
cp frontend/.env.example frontend/.env.local
# Edit both files with your Azure credentials
```

### Run Locally

```bash
# Quick start (both services)
./start-dev.sh

# Or manually:
# Terminal 1: cd backend && uvicorn main:app --reload --port 8000
# Terminal 2: cd frontend && npm run dev
```

**Access**:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000

### Azure Configuration

**1. Create Azure AD App Registrations:**

- **Frontend Enterprise App**: 
  - Azure Portal → App Registrations → New Registration
  - Redirect URI: `http://localhost:3000` (add production URL later)
  - Enable ID tokens and Access tokens under Authentication
  - Copy Application (client) ID for frontend `.env.local`

- **Backend Service Principal**:
  ```bash
  az ad sp create-for-rbac --name "cloudex-backend-sp" \
    --role "Reader" \
    --scopes /subscriptions/<your-subscription-id>
  ```
  - Copy `appId`, `password`, and `tenant` into `backend/.env.local`

**2. Configure Environment Variables:**

**`backend/.env.local`** — Required:
- `AZURE_TENANT_ID`, `AZURE_SERVICE_PRINCIPAL_CLIENT_ID/SECRET/TENANT_ID`
- `OPENAI_API_BASE`, `OPENAI_API_KEY`, `OPENAI_DEPLOYMENT_NAME`
- `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_KEY` (optional but improves query quality)
- `CORS_ORIGINS=["http://localhost:3000"]`

**`frontend/.env.local`** — Required:
- `NEXT_PUBLIC_AZURE_CLIENT_ID`, `NEXT_PUBLIC_AZURE_TENANT_ID`
- `NEXT_PUBLIC_API_URL=http://localhost:8000`

### Project Structure

**Backend** (`backend/`):
- `main.py` - FastAPI app & endpoints
- `services/` - Business logic (AI, Azure APIs, caching)
- `models/` - Pydantic models
- `prompts/` - AI prompt templates

**Frontend** (`frontend/src/`):
- `app/` - Next.js pages (costs, query, dashboard)
- `components/` - React components
- `services/` - API client services
- `types/` - TypeScript definitions


## 🔐 Security

CloudEx follows security best practices for enterprise applications.

### Authentication & Authorization

- **Azure AD Integration**: Single sign-on with Microsoft accounts
- **JWT Token Validation**: Secure API authentication
- **RBAC Enforcement**: Per-user Azure role-based access control
- **Read-Only Operations**: Zero write/modify/delete capabilities

### Security Architecture

```
User → Azure AD Authentication → JWT Token
                                     ↓
                            Backend validates token
                                     ↓
                      Extracts user identity & permissions
                                     ↓
                        Queries Azure with user token
                                     ↓
                    Azure enforces RBAC automatically
```

### Data Protection

- **No Data Storage**: CloudEx doesn't store Azure resource data
- **No Credentials**: User tokens never stored, only validated
- **Key Vault Integration**: Secrets managed securely
- **Encrypted Transit**: HTTPS/TLS for all communications

### Audit & Compliance

- **Audit Logging**: All queries logged with user identity
- **Compliance Tracking**: Azure Policy and security score monitoring
- **Activity Tracking**: Complete audit trail in Application Insights

### Security Best Practices

✅ **What Users CAN Do**:
- Query resources across subscriptions
- Analyze costs with AI insights
- Search logs for troubleshooting
- View metrics and performance data
- Get recommendations (read-only)
- Export data for analysis
- Generate reports

❌ **What Users CANNOT Do**:
- Create, modify, or delete resources
- Change configurations
- Modify permissions or RBAC
- Access secrets or credentials
- Perform any write operations

---

## 📚 Documentation

### 📖 Technical Documentation

Comprehensive documentation is available in the `docs/` directory:

- **[AI Architecture](docs/AI_ARCHITECTURE.md)** - AI system architecture and implementation
  - AI system classification (RAG-based assistant)
  - Search-first architecture with 71,447 indexed docs
  - Token utilization summary
  - Architecture patterns

- **[Security Architecture](docs/SECURITY_ARCHITECTURE.md)** - Complete security model
  - Four-layer security validation system
  - Attack vector analysis
  - Data privacy & OpenAI data handling
  - Incident response and compliance

- **[Token Utilization Report](docs/TOKEN_UTILIZATION_REPORT.md)** - Cost analysis
  - Per-operation token allocation
  - Monthly cost estimates
  - Current optimization strategies
  - Token monitoring implementation

---

## 🤝 Contributing

Contributions are welcome! Please follow these guidelines:

### Development Process

1. **Clone** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Pull Request Guidelines

- Describe changes in detail
- Reference related issues
- Include screenshots for UI changes
- Ensure all tests pass
- Update documentation as needed

---

## 📞 Support & Contact

- **Issues**: [GitHub Issues](https://github.com/flysas-tech/platform-cloudex/issues)
- **General Questions**: [Cloud Platform Team Slack](https://sasdigitalhub.slack.com/archives/C02QCKWBJ68)

---

**Built with ❤️ with a passion for Azure and AI**

[![Azure](https://img.shields.io/badge/Powered_by-Azure-0078D4?style=for-the-badge&logo=microsoft-azure&logoColor=white)](https://azure.microsoft.com/)
[![AI](https://img.shields.io/badge/AI_Powered-GPT--5.1-00A67E?style=for-the-badge&logo=openai&logoColor=white)](https://openai.com/)

[⬆ Back to Top](#cloudex---ai-powered-azure-multi-subscription-portal)

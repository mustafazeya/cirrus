
## 🌟 Overview

**CloudEx** is a conversational Azure management platform that combines GPT-5.4 with Azure APIs to enable natural language querying, real-time cost analysis, and intelligent resource analysis across multi-subscription environments.

### What Makes CloudEx Different?

- **🤖 AI-First Architecture**: Search-first query system using live Microsoft Learn documentation with GPT-5.4 fallback
- **🔐 Zero-Trust Security**: Read-only operations with per-user RBAC enforcement
- **📊 Real-Time Intelligence**: Instant cost analysis, performance metrics, and resource insights
- **💬 Conversational Interface**: Natural language queries like "Analyze denied traffic in Azure Firewall logs"
- **🌐 Multi-Subscription**: Unified view across all your Azure subscriptions

---

## 🌐 Application Pages

CloudEx provides multiple specialized pages for different workflows:

### Home (`/`)

Landing page with:

- Quick search bar for immediate natural language queries
- Quick action buttons (costs, resources, health status)
- Navigation to main features

### Query Interface (`/query`)

AI-powered conversational interface for natural language queries:

- **Natural Language Input**: Type questions in plain English
- **Conversation History**: Full context maintained (last 10 messages)
- **Auto-Execution**: Support for URL parameters (`?q=query&execute=true`)
- **Scope Selection**: Choose specific subscriptions or use global scope
- **Template Library**: Access 60+ pre-built query templates
- **Multi-Format Results**: Supports KQL, ARG, Metrics, and Cost queries
- **Real-Time Feedback**: Live query execution with detailed results

### Dashboard (`/dashboard`)

Unified resource and compliance overview:

- **Resource Explorer**: Browse all Azure resources across subscriptions
  - Group by resource type with expandable sections
  - Filter by location, resource group, tags
  - Search across all resource properties
  - Export resource lists
- **Cost Summary**: Current month spend vs last month with trend indicators
- **Policy Compliance**: Overall compliance percentage and breakdown
- **Security Score**: Microsoft Defender for Cloud security posture
- **Resource Health**: Availability and health status monitoring
- **Quick Actions**: Deep-link to query interface with context

### Cost Management (`/costs`)

Comprehensive cost analysis and forecasting:

- **Cost Overview**:
  - Total spend for selected period
  - Comparison with previous period (trend indicators)
  - Reservation savings analysis
- **Cost Breakdown**: Visualize costs by:
  - Service name (default)
  - Resource location
  - Resource group
  - Individual resources
- **Time Periods**:
  - Today, Yesterday
  - This Week, Last Week
  - This Month, Last Month
  - Custom date range
- **Budgets**: Track spending against configured Azure budgets
  - Total budget vs current spend
  - Forecasted spend
  - Utilization percentage per subscription
- **Visualizations**:
  - Interactive charts (bar, pie, line, area)
  - Daily trend analysis
  - Top cost drivers
- **Data Export**: Download cost data as CSV or JSON

### Profile (`/profile`)

User settings and preferences:

- **User Information**: Display name, email, profile photo
- **Subscriptions**: View all accessible subscriptions with filtering
  - Filter mode: Show only selected subscriptions across the app
  - Search and select specific subscriptions
- **Display Preferences**:
  - Message layout style (standard, compact, code-focused)
  - Conversation view customization
- **Permissions**: View your Azure RBAC roles

### Admin (`/admin`)

System monitoring and administration (requires admin role):

- **Overview Tab**:
  - API metrics (requests, response times, error rates)
  - Active users (now, 24h, 7d)
  - System health indicators
- **Metrics Tab**:
  - Detailed API performance metrics
  - Endpoint-level statistics
  - Response time percentiles (p50, p95, p99)
- **Audit Tab**:
  - User query history with timestamps
  - Query type breakdown (KQL, ARG, Cost, Metrics)
  - Success/failure rates
- **Cache Tab**:
  - Cache hit rates and statistics
  - Query cache performance
  - Template cache status
  - ARG table mappings cache
- **Prefetch Tab**:
  - Background prefetch scheduler status
  - Subscriptions and resource types cached
  - Last update timestamps

---

## ✨ Key Features

### AI-Powered Query Engine

- **Natural Language Processing**: Ask questions in plain English, get accurate Azure queries
- **Search-First Architecture**: Queries powered by live Microsoft Learn documentation (71,447 indexed docs)
- **Multi-Query Support**:
  - **KQL (Kusto Query Language)** for Log Analytics and Application Insights
  - **Azure Resource Graph** queries for resource discovery and analysis
  - **Azure Metrics** queries for performance monitoring
  - **Cost Management** queries for financial insights
- **Context-Aware**: Remembers conversation history for follow-up questions
- **Query Templates**: 60+ pre-built query templates for common scenarios
- **Conversation History**: Full conversation context with last 10 messages retained

#### Example Queries

**Security & Compliance:**

- "Show denied traffic in Azure Firewall logs"
- "Find virtual machines with public IP exposure"
- "Show Front Door WAF blocked requests by rule"

**Cost Analysis:**

- "Show cost breakdown grouped by service"
- "Show cost trend over time"
- "Show top cost driving resources"

**Resource Discovery:**

- "Find unattached network interfaces"
- "Find managed disks not attached to VMs"
- "Find app services with public endpoint"

**Performance & Logs:**

- "Show virtual machines with high CPU or memory utilization"
- "Show failing pods and crashloopbackoff in AKS"
- "Show app service HTTP 4xx and 5xx errors"
- "Show Front Door HTTP 403, 404, and 5xx responses"

**Custom Queries:**

- "Who are you?" - Learn about CloudEx
- "What can you do?" - Discover capabilities
- "Give me best practices for Azure Container Apps"
- "Give me best practices for Azure Kubernetes Service"

### Cost Management

- **Real-Time Cost Analysis**: Daily, weekly, and monthly cost breakdowns
- **Cost Forecasting**: Predict future spending based on historical trends
- **Resource-Level Costs**: Drill down to individual resource costs
- **Cost Optimization**: AI-powered recommendations for cost savings
- **Budget Alerts**: Track spending against budgets

### Resource Discovery & Analysis

- **Multi-Subscription View**: See all resources across all subscriptions
- **Advanced Filtering**: Filter by resource type, location, tags, resource groups
- **Resource Health**: Monitor resource health and availability
- **Tag Management**: View and analyze resource tags
- **Change Tracking**: See what changed and when

### Monitoring & Logs

- **Log Analytics**: Query Azure Monitor logs with natural language
- **Application Insights**: Analyze application telemetry and performance
- **Azure Firewall Logs**: Security analytics and threat detection
- **Metrics Visualization**: Real-time performance metrics with charts
- **Custom Queries**: Save and share your favorite queries

### Security & Compliance

- **Read-Only Platform**: Zero risk of accidental modifications
- **Azure AD Integration**: Single sign-on with Azure Active Directory
- **Per-User RBAC**: See only what you have permission to see
- **Audit Logging**: Track all queries and actions
- **4-Layer Security**: Input, action, query, and RBAC validation

---

## 🏗️ Architecture

CloudEx uses a **Retrieval-Augmented Generation (RAG)** architecture:

```
User Query → Intent Detection → Documentation Search → Query Generation → Execution → Analysis
```

### Core Components

- **Backend**: Python FastAPI with Azure OpenAI integration
- **Frontend**: Next.js 14 with TypeScript and Tailwind CSS
- **AI Engine**: GPT-5.4 via Azure OpenAI Foundry API
- **Search**: Azure AI Search with 71,447 Microsoft Learn docs indexed
- **Cache**: Multi-level caching (L1 memory + L2 Redis)
- **Security**: 4-layer validation pipeline

For detailed technical documentation, see the Architecture section in the navigation.

---

## 🛠️ Technology Stack

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **AI**: Azure OpenAI (GPT-5.4), Azure AI Search
- **Cache**: Azure Cache for Redis
- **Security**: Azure Key Vault, Azure AD
- **Monitoring**: Azure Application Insights

### Frontend
- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript
- **UI**: Tailwind CSS, Radix UI
- **State**: Zustand
- **API**: Axios, TanStack Query

### Infrastructure
- **Hosting**: Azure Container Apps and Azure Static Web Apps
- **CI/CD**: GitHub Actions
- **IaC**: Terraform

---

**Built with ❤️ with a passion for Azure and AI**

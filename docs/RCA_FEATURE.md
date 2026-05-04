# Root Cause Analysis (RCA) Feature

## ⚠️ Experimental Feature

**Status:** Experimental - Currently in active development  
**Availability:** Enable via Profile → Preferences → "Enable Root Cause Analysis (Experimental)"  
**Scope:** Per-resource analysis only (select a resource to see RCA option)

---

## Overview

CloudEx's **Root Cause Analysis (RCA)** feature provides automated, AI-assisted incident investigation that correlates evidence from multiple Azure telemetry sources to identify the most likely root cause of operational issues.

**System Type:** Evidence-based AI correlation engine

**Key Capabilities:**

- Multi-source evidence aggregation (changes, health, logs, metrics, external status)
- AI-powered metric selection (GPT-5 selects top 6 critical metrics per resource type)
- Automated anomaly detection (spikes, trends, intermittent error patterns)
- Resource-specific log analysis using AI mode's proven KQL generation
- Confidence scoring with transparency
- Timeline correlation across signal sources
- Visibility gap detection
- Actionable manual checklist generation

---

## Architecture

### High-Level Flow

```
User Incident Description
    ↓
[Parallel Evidence Collection - 5 sources]
    ├─ Changes (Resource Graph)
    ├─ Health (Resource Health API)
    ├─ Metrics (Azure Monitor + AI selection)
    ├─ Logs (Log Analytics via AI mode)
    └─ External Status (Service Health)
    ↓
Anomaly Detection (Metrics)
    ↓
Timeline Construction
    ↓
Visibility Gap Detection
    ↓
AI Correlation (GPT-5)
    ↓
Confidence Scoring
    ↓
RCA Report
```

### Core Components

**Backend:**

- `root_cause_analysis_service.py` - Main RCA orchestration
- `ai_query_service.py` - Reused for intelligent log collection
- `change_analysis_service.py` - Resource change detection
- `resource_health_service.py` - Azure Resource Health integration
- `metrics_service.py` - Platform and resource metrics with AI selection

**Frontend:**

- `ConversationView.tsx` - RCA mode UI toggle
- `apiService.ts` - RCA API client

**API Endpoint:**
```
POST /api/ai/root-cause
```

---

## Evidence Collection

RCA collects evidence from **5 parallel sources** simultaneously.

### 1. Change Evidence

**Source:** Azure Resource Graph

**What it collects:**

- Resource configuration changes (Create, Update, Delete)
- Change timestamps and initiators
- Before/after snapshots

**Time window:** User-specified (1h to 3 months)

**Confidence boost:** +8% if changes detected

---

### 2. Resource Health Evidence

**Source:** Azure Resource Health API

**What it collects:**

- Availability state (Available, Unavailable, Degraded, Unknown)
- Historical availability events
- Reason type and chronology

**Confidence boost:** +8% if unavailability/degradation detected

---

### 3. Metrics Evidence

**Dynamic metric discovery:**

1. Queries resource's available metrics via `list_metric_definitions()`
2. **AI selects top 6 critical metrics** using GPT-5
3. Queries selected metrics from Azure Monitor

**AI Selection Prompt:**
```
For incident analysis on '{resource_type}', select top 6 metrics.

Prioritize:
1. Errors, failures, throttling
2. Resource exhaustion (CPU, memory, SNAT ports)
3. Performance degradation (latency, throughput)
4. Security issues (denies, blocks, threats)
5. Health and availability

Return JSON array: ["Percentage CPU", "Failed Requests", ...]
```

**Fallback:** Keyword-based selection if AI fails

**Anomaly Detection (4-tier system):**

1. **Spike Detection** (3x threshold): `max_value > avg_value * 3`
2. **Trend Detection**: Recent 20% vs baseline 80% comparison
3. **Latest Value Spike** (4x threshold): `latest > avg * 4`
4. **Intermittent Error Pattern**: Errors in <30% of time points

**Confidence boost:** +5% if resource metric signals detected

---

### 4. Log Evidence

**Resource-Specific Logs via AI Mode**

**Key Innovation:** Reuses `AIQueryService.process_natural_query()` for intelligent log collection

**How it works:**
```python
from services.ai_query_service import AIQueryService
ai_service = AIQueryService()

natural_query = f"Show all diagnostic logs for this resource in the {time_desc}..."

result = await ai_service.process_natural_query(
    user_query=natural_query,
    user_data=user_info,
    resource_id=resource_id,
    ...
)
# AI mode handles:
# ✅ Table discovery from schema
# ✅ Resource-specific KQL generation
# ✅ Workspace selection
# ✅ Query execution
```

**Benefits:**

- Automatic adaptation to new Azure services
- Proven KQL generation logic (same as chat mode)
- Supports all resource types AI mode supports

**Deduplication:** Events deduplicated by (timestamp, table, action, operation, category)

**Coverage modes:**

- `resource_firewall_logs` - Firewall-specific tables analyzed
- `resource_logs` - Generic diagnostic logs analyzed
- `none` - No diagnostic settings found

**Confidence impact:** No visibility gaps penalty if logs available

---

### 5. External Status Evidence

**Source:** Azure Resource Graph (`ServiceHealthResources`)

**What it checks:**

- Active service issues affecting subscriptions
- Impacted services and regions
- Incident severity levels

**Confidence boost:** +3% if all external services operational

---

## Anomaly Detection

**Metric anomalies detected via 4-tier system:**

| Tier | Threshold | Example |
|------|-----------|---------|
| Spike | max > avg × 3 | CPU 90% when avg 30% |
| Trend | recent_avg > baseline × 2 | Memory 4GB vs baseline 2GB |
| Latest Spike | latest > avg × 4 | SNAT ports suddenly max out |
| Error Pattern | errors in <30% time points | Bursty deny events |

Each detection increments `signal_count`, helping AI focus on abnormal metrics.

---

## Timeline Construction

All evidence merged into **unified chronological timeline** (newest first).

### Event Types

| Type | Source | Example |
|------|--------|---------|
| `change` | Resource Graph | "Update on app-service-prod" |
| `health` | Resource Health | "Health: Unavailable" |
| `resource_log` | Log Analytics (via AI mode) | "AZFWNetworkRule: Deny" |
| `resource_metric` | Azure Monitor | "Metric alert: SNAT 80%" |
| `metric_insight` | Calculated | "Failed request rate 3.2%" |

**Metric Labeling:**

- **"Metric alert:"** - Problem indicators → Counted in "Metric Signals"
- **"Metric:"** - Informational → Not counted, provides context

**Timeline limits:**

- Top 30 changes
- Top 20 resource logs
- Top 10 health events
- Top 3 problem metrics + 2 informational metrics
- **Total: 40 most relevant events**

---

## AI Correlation Analysis

### GPT-5 Integration

**Model:** `settings.OPENAI_DEPLOYMENT_NAME` (typically GPT-5)  
**Timeout:** 90 seconds  
**Max tokens:** 700 (output)

### System Prompt (Summary)

```
You are an SRE root-cause analysis assistant.

Rules:
- Never claim certainty beyond evidence
- If evidence is weak, explicitly say so
- Prefer concise and actionable language
- Return STRICT JSON with keys: root_cause, confidence_reasoning, 
  alternative_explanations, timeline_summary, ai_confidence
```

### Response Schema

```json
{
  "root_cause": "Clear description of identified cause",
  "confidence_reasoning": "Why this confidence level",
  "alternative_explanations": ["alt1", "alt2", "alt3"],
  "timeline_summary": "Chronological event summary",
  "ai_confidence": 0.82
}
```

### Fallback Analysis

If OpenAI unavailable, uses **heuristic-based scoring**:

- Unavailable + changes → 72% confidence
- Unavailable only → 58% confidence
- Changes only → 55% confidence
- Insufficient evidence → 42% confidence

---

## Confidence Scoring

Final confidence = AI confidence + evidence quality adjustments.

### Formula

```python
confidence = ai_confidence  # Base: 0.5

# Evidence boosts
if changes_detected: confidence += 0.08
if health_degradation: confidence += 0.08
if external_services_operational: confidence += 0.03
if resource_metric_signals > 0: confidence += 0.05

# Visibility gap penalties
confidence -= min(0.2, len(visibility_gaps) * 0.04)

# Clamp to valid range
confidence = max(0.05, min(0.95, confidence))
```

### Ranges

| Range | Interpretation |
|-------|----------------|
| 85-95% | 🟢 High confidence - Clear correlation |
| 70-84% | 🟡 Good confidence - Strong signals |
| 50-69% | 🟠 Moderate - Mixed signals |
| 30-49% | 🔴 Low - Weak evidence |
| 5-29% | ⚫ Very low - Insufficient evidence |

---

## Visibility Gap Detection

Identifies **missing or incomplete telemetry** that limits analysis.

### Common Gaps

| Gap | Impact | Resolution |
|-----|--------|------------|
| No resource_id | Analysis too broad | Select specific resource |
| No diagnostic settings | No resource logs | Enable diagnostic settings |
| No firewall logs | Can't confirm deny rules | Enable AZFWApplicationRule/NetworkRule |
| No resource metrics | Missing performance data | Verify metrics enabled |
| External status not checked | Can't rule out Azure issues | Include external status |

**Confidence penalty:** Each gap reduces confidence by 4% (max 20%)

---

## Manual Checklist Generation

**Base checklist (always included):**

1. Validate impact scope with affected teams and timestamps
2. Cross-check deployment/change windows against symptoms
3. Verify service quotas, throttling, and regional incidents
4. Confirm dependency failures from external systems
5. Document remediations and add detection guardrails

**Dynamic additions based on gaps:**

- Missing resource_id → "Narrow to specific resource and rerun"
- Missing logs → "Enable diagnostic settings for {resource_type}"
- Missing metrics → "Collect resource-level metrics"

---

## Usage Guide

### 1. Enable RCA Mode

**Via UI:** Profile → Preferences → "Enable Root Cause Analysis (Experimental)"  
**Access:** Select a resource → RCA icon appears in conversation header

### 2. Configure Scope

**Best practice:** Narrow scope for better accuracy

| Scope | Best For |
|-------|----------|
| Resource-specific | Specific service issue ✅ |
| Resource Group | Multiple resources in same RG |
| Subscription | Cross-resource correlation |
| Global | Unknown affected resource |

### 3. Set Time Window

**Presets:** 6h, 24h, 72h, 7 days  
**Custom:** Up to 3 months  
**Recommendation:** Start narrow (6-24h), expand if needed

### 4. Describe Incident

✅ **Good:** "API latency spiked from 200ms to 5s at 2pm, users reporting timeouts"  
❌ **Poor:** "Something is broken"

**Include:** Symptoms, timeline, scope, recent changes

### 5. Review Results

- **Analysis Summary:** Root cause, confidence, reasoning
- **Timeline:** Chronological events with expand/collapse
- **Visibility Gaps:** Missing telemetry
- **Manual Checklist:** Next investigation steps
- **Evidence Summary:** Signal counts (changes, logs, health, metrics)

---

## Best Practices

**Before RCA:**

- Enable diagnostic settings on critical resources
- Send logs to centralized Log Analytics workspace

**During RCA:**

- Start with resource-specific scope
- Use shorter time windows first (6-24h)
- Provide specific incident descriptions

**After RCA:**

- Don't blindly trust AI root cause
- Verify with raw evidence
- Follow manual checklist
- Address visibility gaps for future incidents

---

## Security & Compliance

### What RCA CAN Do ✅

- Query resource configurations (read-only)
- Analyze logs and metrics
- View health and change history
- Generate incident reports

### What RCA CANNOT Do ❌

- Modify resource configurations
- Delete resources or logs
- Change permissions or RBAC
- Write to Log Analytics workspaces

### Data Handling

**Evidence collected:**

- Resource metadata (no secrets)
- Availability states (not content)
- Metric values (numeric only)
- Log query results (configurable retention)

**Retention:** RCA results purged after 90 days in Logs 
**Access:** RBAC permissions inherited from user  
**Encryption:** At rest and in transit

---

## Troubleshooting

### Low Confidence (<40%)

**Causes:**

- Vague incident description
- Scope too broad
- Time window too long
- Many visibility gaps

**Solutions:**

1. Narrow to specific resource
2. Reduce time window to 6-24h
3. Add symptoms and timeline to description
4. Address visibility gaps (enable diagnostic settings)

### No Resource Logs Found

**Cause:** Missing diagnostic settings

**Solution:**

1. Azure Portal → Resource → Diagnostic Settings
2. Add diagnostic setting → Send to Log Analytics
3. Wait 5-10 minutes
4. Re-run RCA

### RCA Times Out

**Cause:** Large scope or time window

**Solutions:**

1. Reduce time window to 6-24 hours
2. Narrow to specific resource
3. Retry after a few minutes

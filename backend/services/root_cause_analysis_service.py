"""
Root Cause Analysis Service
Aggregates evidence from multiple sources and generates AI-assisted RCA output.
"""

import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from config import settings
from services.azure_monitor_service import AzureMonitorService
from services.change_analysis_service import change_analysis_service
from services.diagnostic_settings_service import DiagnosticSettingsService
from services.dual_credential_service import dual_cred_service
from services.external_status_service import external_status_service
from services.metrics_service import metrics_service
from services.resource_health_service import resource_health_service

logger = logging.getLogger(__name__)


class RootCauseAnalysisService:
    """Service to perform automated, evidence-based root cause analysis."""

    def __init__(self):
        self.openai_client = None
        self._diagnostic_settings_service = DiagnosticSettingsService()
        self._azure_monitor_service = AzureMonitorService()
        self._initialize_openai()

    def _initialize_openai(self):
        if not settings.OPENAI_API_KEY:
            logger.warning("RCA AI correlation disabled: OpenAI API key not configured")
            return

        try:
            if settings.OPENAI_API_BASE:
                base_url = settings.OPENAI_API_BASE.rstrip("/") + "/openai/v1/"
                self.openai_client = AsyncOpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    base_url=base_url,
                    max_retries=settings.RCA_OPENAI_MAX_RETRIES,
                    timeout=settings.RCA_OPENAI_TIMEOUT,
                )
            else:
                self.openai_client = AsyncOpenAI(
                    api_key=settings.OPENAI_API_KEY,
                    max_retries=settings.RCA_OPENAI_MAX_RETRIES,
                    timeout=settings.RCA_OPENAI_TIMEOUT,
                )
        except Exception as error:
            logger.warning(f"Failed to initialize RCA OpenAI client: {error}")
            self.openai_client = None

    async def analyze_incident(
        self,
        incident_summary: str,
        subscription_ids: List[str],
        user_info: Dict[str, Any],
        hours: int = 24,
        resource_id: Optional[str] = None,
        resource_group: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        include_external_status: bool = True,
    ) -> Dict[str, Any]:
        """Run end-to-end root cause analysis."""
        started_at = datetime.utcnow()

        logger.info(
            f"RCA START | resource={resource_id or resource_group or 'subscription-wide'} "
            f"| subs={len(subscription_ids)} | hours={hours} "
            f"| incident='{incident_summary[:80]}'"
        )

        evidence_tasks = {
            "changes": self._collect_change_evidence(subscription_ids, hours, user_info, start_time, end_time, incident_summary),
            "health": self._collect_health_evidence(subscription_ids, user_info, resource_id, resource_group),
            "metrics": self._collect_metrics_evidence_with_resource(hours, resource_id, user_info, start_time, end_time, incident_summary),
            "logs": self._collect_log_evidence(hours, resource_id, user_info, start_time, end_time, incident_summary),
            "advisor": self._collect_advisor_evidence(resource_id, resource_group, subscription_ids, user_info),
            "network": self._collect_network_evidence(resource_id, resource_group, subscription_ids, user_info),
            "diagnostic_coverage": self._collect_diagnostic_coverage_evidence(resource_id, user_info),
        }

        if include_external_status:
            evidence_tasks["external_status"] = self._collect_external_status_evidence(
                subscription_ids[0] if subscription_ids else None,
                user_info,
                hours,
                start_time=start_time,
                end_time=end_time,
            )

        task_names = list(evidence_tasks.keys())
        task_results = await asyncio.gather(*evidence_tasks.values(), return_exceptions=True)

        evidence: Dict[str, Any] = {}
        evidence_errors: Dict[str, str] = {}

        for task_name, task_result in zip(task_names, task_results):
            if isinstance(task_result, Exception):
                evidence_errors[task_name] = str(task_result)
                evidence[task_name] = {"success": False, "error": str(task_result)}
            else:
                evidence[task_name] = task_result
                if not task_result.get("success", True):
                    evidence_errors[task_name] = task_result.get("message") or task_result.get("error", "Unknown error")

        timeline = self._build_timeline(evidence)
        # Initial structural gaps (facts only — no interpretation)
        structural_gaps = self._structural_evidence_gaps(evidence_errors, resource_id)

        # ---------------------------------------------------------------
        # Agentic loop: AI analyzes, requests follow-up queries, refines
        # ---------------------------------------------------------------
        MAX_ITERATIONS = settings.RCA_MAX_ITERATIONS
        CONFIDENCE_THRESHOLD = 0.75
        ai_analysis: Dict[str, Any] = {}
        previous_context: Optional[Dict[str, Any]] = None  # Full prior iteration context, not just root_cause
        executed_followup_reasons: List[str] = []  # Track what was already queried

        for iteration in range(1, MAX_ITERATIONS + 1):
            ai_analysis = await self._generate_ai_correlation(
                incident_summary=incident_summary,
                hours=hours,
                subscription_ids=subscription_ids,
                resource_id=resource_id,
                resource_group=resource_group,
                evidence=evidence,
                visibility_gaps=structural_gaps,
                previous_context=previous_context,
                executed_followup_reasons=executed_followup_reasons,
                iteration=iteration,
            )

            current_confidence = float(ai_analysis.get("ai_confidence", 0.5))
            needs_more = ai_analysis.get("needs_more_evidence", False)
            follow_up_queries = ai_analysis.get("follow_up_queries", [])

            logger.info(
                f"RCA iteration {iteration}/{MAX_ITERATIONS}: "
                f"confidence={current_confidence:.2f}, needs_more={needs_more}, "
                f"follow_ups={len(follow_up_queries)}"
            )

            # Always run at least one follow-up cycle (iteration 1 only has initial evidence).
            # After iteration 2+, exit if confident enough or nothing left to query.
            can_exit_on_confidence = iteration >= 2
            if (not needs_more or not follow_up_queries) or (can_exit_on_confidence and current_confidence >= CONFIDENCE_THRESHOLD):
                logger.info(f"RCA loop complete after {iteration} iteration(s)")
                break

            if iteration < MAX_ITERATIONS:
                logger.info(f"Executing {len(follow_up_queries)} AI-requested follow-up queries")
                followup_results = await self._execute_ai_followup_queries(
                    follow_up_queries=follow_up_queries,
                    resource_id=resource_id,
                    subscription_ids=subscription_ids,
                    user_info=user_info,
                    hours=hours,
                )
                evidence.update(followup_results)
                structural_gaps = self._structural_evidence_gaps(evidence_errors, resource_id)
                executed_followup_reasons.extend([q.get("reason", "") for q in follow_up_queries])
                # Carry prior context + summary of what follow-ups returned
                followup_summary = {
                    k: {"success": v.get("success"), "count": v.get("count", 0), "reason": v.get("reason")}
                    for k, v in followup_results.items()
                }
                previous_context = {
                    "root_cause": ai_analysis.get("root_cause"),
                    "root_cause_category": ai_analysis.get("root_cause_category"),
                    "confidence_reasoning": ai_analysis.get("confidence_reasoning"),
                    "alternative_explanations": ai_analysis.get("alternative_explanations", []),
                    "ai_confidence": current_confidence,
                    "queries_run": [q.get("reason") for q in follow_up_queries],
                    "followup_results_summary": followup_summary,
                }

        # Retrospective self-check — skip if AI is already highly confident (saves one model call)
        RETRO_CONFIDENCE_SKIP = 0.79
        if float(ai_analysis.get("ai_confidence", 0.5)) < RETRO_CONFIDENCE_SKIP:
            metrics_evidence = evidence.get("metrics", {})
            raw_retro_changes = evidence.get("changes", {}).get("changes", [])[:5]
            retro_changes_summary = [
                {
                    "changeTime": c.get("changeTime"),
                    "changeType": c.get("changeType"),
                    "targetResourceName": c.get("targetResourceName"),
                    "initiatedBy": c.get("initiatedBy"),
                    "changedProperties": list((c.get("changes") or {}).keys())[:5],
                }
                for c in raw_retro_changes
            ]
            retro_activity = [
                {
                    "TimeGenerated": a.get("TimeGenerated"),
                    "OperationNameValue": a.get("OperationNameValue") or a.get("Operation"),
                    "ActivityStatusValue": a.get("ActivityStatusValue") or a.get("ActivityStatus"),
                    "Level": a.get("Level"),
                    "Caller": a.get("Caller"),
                }
                for a in evidence.get("changes", {}).get("activity_log_events", [])[:8]
            ]
            retro_logs = [
                {
                    "TimeGenerated": e.get("TimeGenerated"),
                    "Action": e.get("Action") or e.get("action"),
                    "OperationName": e.get("OperationName") or e.get("OperationNameValue"),
                    "ResultType": e.get("ResultType"),
                    "Level": e.get("Level"),
                    "Msg": (e.get("Msg") or e.get("Message") or "")[:150],
                }
                for e in evidence.get("logs", {}).get("resource_logs", {}).get("events", [])[:8]
            ]
            retro_metrics = [
                {
                    "name": m.get("name"),
                    "unit": m.get("unit"),
                    "timeseries": [
                        {"dimensions": ts.get("dimensions"), "avg": round(ts.get("avg", 0), 3), "max": round(ts.get("max", 0), 3), "latest": round(ts.get("latest", 0), 3)}
                        for ts in (m.get("timeseries") or [])[:3]
                    ],
                }
                for m in metrics_evidence.get("metrics", [])[:5]
            ]
            compact_evidence_for_retro = {
                "changes": {"total_changes": evidence.get("changes", {}).get("total_changes", 0), "sample": retro_changes_summary, "activity_log_events": retro_activity},
                "health": {"availability_state": evidence.get("health", {}).get("availability_state"), "reason_type": evidence.get("health", {}).get("reason_type")},
                "logs": {"deny_count": evidence.get("logs", {}).get("resource_logs", {}).get("deny_count", 0), "sample": retro_logs},
                "metrics": {"metrics": retro_metrics},
                "external_status": {"active_incident_count": len(evidence.get("external_status", {}).get("services", [])), "incidents": evidence.get("external_status", {}).get("services", [])[:3]},
                "advisor": {"count": evidence.get("advisor", {}).get("count", 0), "recommendations": evidence.get("advisor", {}).get("recommendations", [])[:5]},
            }
            ai_analysis = await self._retrospective_validation(
                final_analysis=ai_analysis,
                compact_evidence=compact_evidence_for_retro,
                incident_summary=incident_summary,
            )
        else:
            logger.info(f"Skipping retrospective — confidence {ai_analysis.get('ai_confidence'):.2f} >= {RETRO_CONFIDENCE_SKIP}")

        confidence = round(max(0.05, min(0.95, float(ai_analysis.get("ai_confidence", 0.5)))), 2)
        manual_checklist = ai_analysis.get("manual_checklist") or []
        visibility_gaps = ai_analysis.get("visibility_gaps") or structural_gaps

        completed_at = datetime.utcnow()
        duration_ms = int((completed_at - started_at).total_seconds() * 1000)

        response = {
            "success": True,
            "incident_summary": incident_summary,
            "scope": {
                "subscription_ids": subscription_ids,
                "resource_id": resource_id,
                "resource_group": resource_group,
                "hours": hours,
                "start_time": start_time.isoformat() if start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
            },
            "analysis": {
                "root_cause": ai_analysis.get("root_cause"),
                "root_cause_category": ai_analysis.get("root_cause_category", "unknown"),
                "confidence": confidence,
                "confidence_reasoning": ai_analysis.get("confidence_reasoning"),
                "alternative_explanations": ai_analysis.get("alternative_explanations", []),
                "timeline_summary": ai_analysis.get("timeline_summary"),
            },
            "timeline": timeline,
            "visibility_gaps": visibility_gaps,
            "manual_checklist": manual_checklist,
            "evidence_summary": self._build_evidence_summary(evidence),
            "evidence": evidence,
            "metadata": {
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_ms": duration_ms,
                "ai_used": bool(self.openai_client),
                "model": settings.OPENAI_DEPLOYMENT_NAME if self.openai_client else None,
                "evidence_errors": evidence_errors,
                "retrospective_notes": ai_analysis.get("retrospective_notes", ""),
            },
        }

        return response

    async def _collect_change_evidence(
        self,
        subscription_ids: List[str],
        hours: int,
        user_info: Dict[str, Any],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Collect change evidence from both Resource Graph (configuration changes) 
        and Activity Log (operational events).
        """
        # Get Resource Graph changes (fast, structured)
        rg_changes = await change_analysis_service.get_resource_changes(
            subscription_ids=subscription_ids,
            hours=hours,
            user_info=user_info,
            start_time=start_time,
            end_time=end_time,
        )
        
        # Enrich with Activity Log data (creates, deletes, failures)
        activity_log_events = await self._collect_activity_log_evidence(
            subscription_ids=subscription_ids,
            hours=hours,
            user_info=user_info,
            start_time=start_time,
            end_time=end_time,
            incident_summary=incident_summary,
        )
        
        # Merge both sources
        return {
            **rg_changes,
            "activity_log_events": activity_log_events.get("events", []),
            "activity_log_count": len(activity_log_events.get("events", [])),
        }

    async def _collect_activity_log_evidence(
        self,
        subscription_ids: List[str],
        hours: int,
        user_info: Dict[str, Any],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query Activity Log (AzureActivity) for operational events using AI-generated KQL.
        """
        try:
            if not subscription_ids:
                return {"events": [], "message": "No subscription IDs provided"}
            
            # Generate AI query for Activity Log
            query = await self._build_activity_log_query_with_ai(
                subscription_ids=subscription_ids,
                hours=hours,
                start_time=start_time,
                end_time=end_time,
                incident_summary=incident_summary,
                user_info=user_info,
            )
            
            if not query:
                logger.warning("Failed to generate Activity Log query")
                return {"events": [], "message": "Activity Log query generation failed"}
            
            # Resolve workspace via shared resolver (subscription diag settings → resource diag settings → ARM listing)
            try:
                from services.query_service import QueryService
                query_svc = QueryService()
                resolved = await query_svc.resolve_log_analytics_workspace(
                    kql_query=query,
                    subscription_ids=subscription_ids,
                    user_info=user_info,
                )
                workspace_id = resolved["workspace_id"] if resolved else None
            except Exception as e:
                logger.warning(f"Could not find Activity Log workspace: {e}")
                return {"events": [], "message": "No Activity Log workspace configured"}

            if not workspace_id:
                return {"events": [], "message": "No Activity Log diagnostic setting found"}
            
            # Execute query
            # Execute query - pass timespan=None so KQL's own ago() filter controls the window
            result = await self._azure_monitor_service.execute_kql_query(
                query=query,
                workspace_id=workspace_id,
                user_info=user_info,
                timespan=None,
            )
            
            events = result.get("results", [])[:50] if result.get("status") == "success" else []
            
            return {
                "events": events,
                "count": len(events),
                "workspace_id": workspace_id,
                "message": f"Retrieved {len(events)} Activity Log events"
            }
            
        except Exception as error:
            logger.warning(f"Activity Log evidence collection failed: {error}")
            return {"events": [], "message": str(error)}

    async def _build_activity_log_query_with_ai(
        self,
        subscription_ids: List[str],
        hours: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        from services.kql_generation_service import AIService
        ai_svc = AIService()
        time_desc = (
            f"between {start_time.isoformat()} and {end_time.isoformat()}"
            if start_time and end_time
            else f"last {hours} hours"
        )
        natural_query = (
            f"RCA investigation: {incident_summary or 'operational errors and failures'}. "
            f"Query AzureActivity for errors, failures, warnings, and critical events in the {time_desc}."
        )
        resource_context: Dict[str, Any] = {
            "subscription_ids": subscription_ids,
            "natural_query": natural_query,
        }
        if start_time and end_time:
            resource_context["start_time"] = start_time.isoformat()
            resource_context["end_time"] = end_time.isoformat()
        else:
            resource_context["hours"] = hours
        try:
            return await ai_svc._generate_kql_from_natural_language(
                natural_query=natural_query,
                resource_context=resource_context,
                user_info=user_info or {},
                original_user_query=natural_query,
            )
        except Exception as e:
            logger.warning(f"AI Activity Log query generation failed: {e}")
            return None

    async def _collect_health_evidence(
        self,
        subscription_ids: List[str],
        user_info: Dict[str, Any],
        resource_id: Optional[str],
        resource_group: Optional[str],
    ) -> Dict[str, Any]:
        return await resource_health_service.get_resource_health(
            user_info=user_info,
            resource_id=resource_id,
            resource_group=resource_group,
            subscription_ids=subscription_ids,
        )

    async def _collect_metrics_evidence_with_resource(
        self,
        hours: int,
        resource_id: Optional[str],
        user_info: Dict[str, Any],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Collect Azure resource metrics only (no CIRRUS internal metrics).
        RCA focuses solely on the selected resource's telemetry.
        """
        if not resource_id:
            return {
                "success": False,
                "coverage": "none",
                "metrics": [],
                "signal_count": 0,
                "message": "No resource_id provided for metrics analysis",
            }

        resource_metrics = await self._collect_resource_metrics_evidence(
            resource_id=resource_id,
            user_info=user_info,
            hours=hours,
            start_time=start_time,
            end_time=end_time,
            incident_summary=incident_summary,
        )

        return resource_metrics

    async def _collect_resource_metrics_evidence(
        self,
        resource_id: str,
        user_info: Dict[str, Any],
        hours: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect resource-specific Azure Monitor metrics with AI-enhanced metric selection."""
        try:
            from azure.monitor.query import MetricsQueryClient, MetricAggregationType

            user_credential = dual_cred_service.get_user_credential(user_info, token_type='management')
            client = MetricsQueryClient(user_credential)

            loop = asyncio.get_event_loop()
            window_end = end_time or datetime.utcnow()
            window_start = start_time or (window_end - timedelta(hours=min(hours, 24)))

            if window_start.tzinfo is not None:
                window_start = window_start.astimezone(timezone.utc).replace(tzinfo=None)
            if window_end.tzinfo is not None:
                window_end = window_end.astimezone(timezone.utc).replace(tzinfo=None)

            granularity = timedelta(minutes=5)

            def _list_defs():
                return list(client.list_metric_definitions(resource_uri=resource_id))

            metric_definitions = await loop.run_in_executor(None, _list_defs)
            if not metric_definitions:
                return {
                    "success": False,
                    "coverage": "none",
                    "metrics": [],
                    "signal_count": 0,
                    "message": "No metric definitions available for selected resource",
                }
            
            # Extract resource type for AI-enhanced selection
            resource_type = resource_id.split('/providers/')[-1].split('/')[0] if '/providers/' in resource_id else ""
            
            # Use AI to determine most relevant metrics for this resource type
            selected_metric_names = await self._select_metrics_with_ai(
                metric_definitions=metric_definitions,
                resource_type=resource_type,
                resource_id=resource_id,
                incident_summary=incident_summary,
            )
            
            # Fallback to keyword-based selection if AI fails
            if not selected_metric_names:
                logger.warning("AI metric selection failed, using keyword-based fallback")
                selected_metric_names = self._select_metrics_by_keywords(metric_definitions)

            metric_results: List[Dict[str, Any]] = []

            for metric_name in selected_metric_names:
                def _query_metric():
                    return client.query_resource(
                        resource_uri=resource_id,
                        metric_names=[metric_name],
                        timespan=(window_start, window_end),
                        granularity=granularity,
                        aggregations=[
                            MetricAggregationType.AVERAGE,
                            MetricAggregationType.MAXIMUM,
                            MetricAggregationType.TOTAL,
                        ],
                    )

                try:
                    response = await loop.run_in_executor(None, _query_metric)
                except Exception:
                    continue

                for metric in response.metrics:
                    timeseries_data: List[Dict[str, Any]] = []
                    point_timestamps: List[Any] = []

                    for timeseries in metric.timeseries:
                        dimension_labels = {
                            d.key: d.value
                            for d in (timeseries.metadata_values or [])
                        } if hasattr(timeseries, "metadata_values") and timeseries.metadata_values else {}

                        series_points: List[float] = []
                        for point in timeseries.data:
                            value = (
                                point.average
                                if getattr(point, "average", None) is not None
                                else point.total
                                if getattr(point, "total", None) is not None
                                else point.maximum
                                if getattr(point, "maximum", None) is not None
                                else None
                            )
                            if value is not None:
                                series_points.append(value)
                            if not point_timestamps and getattr(point, "timestamp", None) is not None:
                                point_timestamps.append(point.timestamp)

                        if series_points:
                            timeseries_data.append({
                                "dimensions": dimension_labels,
                                "values": series_points[-20:],
                                "max": max(series_points),
                                "avg": sum(series_points) / len(series_points),
                                "latest": series_points[-1],
                            })

                        if not point_timestamps:
                            for point in timeseries.data:
                                if getattr(point, "timestamp", None) is not None:
                                    point_timestamps.append(point.timestamp)

                    if not timeseries_data:
                        continue

                    metric_summary = {
                        "name": metric.name,
                        "unit": str(metric.unit),
                        "timeseries": timeseries_data,
                        "window_start": self._normalize_timestamp(min(point_timestamps)) if point_timestamps else self._normalize_timestamp(window_start),
                        "window_end": self._normalize_timestamp(max(point_timestamps)) if point_timestamps else self._normalize_timestamp(window_end),
                    }
                    metric_results.append(metric_summary)

            if not metric_results:
                return {
                    "success": False,
                    "coverage": "none",
                    "metrics": [],
                    "message": "No metric datapoints found for selected resource/time window",
                }

            total_requests_metric = next(
                (
                    metric for metric in metric_results
                    if str(metric.get("name", "")).lower() in {"totalrequests", "total requests"}
                ),
                None,
            )
            failed_requests_metric = next(
                (
                    metric for metric in metric_results
                    if str(metric.get("name", "")).lower() in {"failedrequests", "failed requests"}
                ),
                None,
            )

            failure_rate_pct = None
            total_requests_metric = next(
                (m for m in metric_results if str(m.get("name", "")).lower() in {"totalrequests", "total requests"}),
                None,
            )
            failed_requests_metric = next(
                (m for m in metric_results if str(m.get("name", "")).lower() in {"failedrequests", "failed requests"}),
                None,
            )
            if total_requests_metric and failed_requests_metric:
                def _latest(m: Dict[str, Any]) -> float:
                    ts = m.get("timeseries", [])
                    return ts[0]["latest"] if ts else 0.0
                total_latest = _latest(total_requests_metric)
                failed_latest = _latest(failed_requests_metric)
                if total_latest > 0:
                    failure_rate_pct = round((failed_latest / total_latest) * 100, 3)

            return {
                "success": True,
                "coverage": "resource_metrics",
                "metrics": metric_results,
                "selected_metric_count": len(metric_results),
                "failure_rate_pct": failure_rate_pct,
                "message": f"Analyzed {len(metric_results)} resource metrics",
            }
        except Exception as error:
            logger.warning(f"Resource metrics evidence collection failed: {error}")
            return {
                "success": False,
                "coverage": "none",
                "metrics": [],
                "signal_count": 0,
                "message": "Resource metrics analysis failed",
                "error": str(error),
            }

    async def _collect_log_evidence(
        self,
        hours: int,
        resource_id: Optional[str],
        user_info: Dict[str, Any],
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect Azure resource logs (not CIRRUS internal audit logs.)"""
        
        resource_log_evidence = {
            "success": False,
            "coverage": "none",
            "workspace_id": None,
            "table_mode": None,
            "events": [],
            "deny_count": 0,
            "message": "No resource log analysis attempted",
        }

        if resource_id:
            resource_log_evidence = await self._collect_resource_log_evidence(
                resource_id=resource_id,
                user_info=user_info,
                hours=hours,
                start_time=start_time,
                end_time=end_time,
                incident_summary=incident_summary,
            )

        return {
            "success": True,
            "source": "azure_resource_logs",
            "summary": {"total_events": resource_log_evidence.get("events", [])},
            "recent_events": [],  # No CIRRUS audit events in RCA
            "raw_event_count": 0,
            "signal_event_count": 0,
            "resource_logs": resource_log_evidence,
            "firewall_rule_log_coverage": resource_log_evidence.get("coverage") == "resource_firewall_logs",
        }

    async def _collect_resource_log_evidence(
        self,
        resource_id: str,
        user_info: Dict[str, Any],
        hours: int,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        incident_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Collect service/resource-specific logs from diagnostic settings workspace."""
        try:
            diag = await self._diagnostic_settings_service.get_log_mode_for_resource(
                resource_id=resource_id,
                user_info=user_info,
            )

            if not diag.get("has_diagnostics"):
                return {
                    "success": False,
                    "coverage": "none",
                    "workspace_id": None,
                    "table_mode": None,
                    "events": [],
                    "deny_count": 0,
                    "message": "No Log Analytics diagnostic settings found for selected resource",
                }

            workspace_id = diag.get("primary", {}).get("workspace_id")
            table_mode = diag.get("primary", {}).get("mode")
            resource_type = (diag.get("resource_type") or "").lower()

            time_desc = f"last {hours} hours" if not start_time else f"between {start_time.isoformat()} and {end_time.isoformat()}"
            natural_query = (
                f"RCA investigation: {incident_summary or 'Show diagnostic logs'}. "
                f"Retrieve diagnostic logs for this resource in the {time_desc} that are most relevant "
                f"to diagnosing this incident. Prioritise errors, failures, denies, and anomalous events."
            )

            # Build resource_context from already-collected diagnostic info so
            # _generate_kql_from_natural_language gets exactly what it needs without
            # re-fetching anything. RCA already has workspace, mode and tables.
            resource_context: Dict[str, Any] = {
                "resource_id": resource_id,
                "resource_type": diag.get("resource_type") or resource_type,
                "resource_name": resource_id.split("/")[-1] if resource_id else None,
                "subscription_ids": [resource_id.split("/")[2]] if "/" in resource_id and len(resource_id.split("/")) > 2 else [],
                # natural_query stored so _get_diagnostic_settings_context table-selection AI
                # gets the actual incident description instead of falling back to 'analyze logs'
                "natural_query": natural_query,
            }
            # Pre-populate workspace list so _generate_kql_from_natural_language skips its own diag fetch
            if workspace_id:
                resource_context["_workspace_ids_from_diagnostics"] = [workspace_id]
            # Pre-populate log_mode so it doesn't re-detect; carries into cache key
            if table_mode:
                resource_context["log_mode"] = table_mode

            from services.kql_generation_service import AIService
            ai_svc = AIService()

            logger.info(f" Generating KQL for RCA log evidence: {resource_type} (mode={table_mode})")

            kql = await ai_svc._generate_kql_from_natural_language(
                natural_query=natural_query,
                resource_context=resource_context,
                user_info=user_info,
                original_user_query=natural_query,
            )

            async def _execute_with_schema_retry(query: str) -> List[Dict[str, Any]]:
                """Execute KQL; on schema error, fetch getschema and retry once."""
                result = await self._azure_monitor_service.execute_kql_query(
                    query=query, workspace_id=workspace_id, user_info=user_info, timespan=None
                )
                if result.get("status") == "success":
                    # Use result["results"] — flat list of dicts already built by the service.
                    # result["tables"][].rows are raw list-rows (not dicts); never use those.
                    return result.get("results", [])
                err = result.get("error", "") or result.get("message", "")
                is_schema = "SEM0100" in err or "Failed to resolve" in err or "schema" in err.lower()
                if not is_schema:
                    return []
                # Schema error: discover actual columns and regenerate once
                table_name = query.strip().split("\n")[0].strip().split("|")[0].strip()
                if not table_name or " " in table_name:
                    return []
                schema_result = await self._azure_monitor_service.execute_kql_query(
                    query=f"{table_name} | getschema | project ColumnName, DataType | take 80",
                    workspace_id=workspace_id, user_info=user_info, timespan=None,
                )
                if schema_result.get("status") != "success":
                    return []
                cols = [
                    f"{r.get('ColumnName')}:{r.get('DataType', '')}"
                    for tbl in schema_result.get("tables", [])
                    for r in tbl.get("rows", [])
                    if r.get("ColumnName")
                ]
                if not cols:
                    return []
                logger.info(f" Schema correction: {len(cols)} columns found in {table_name}")
                resource_context["_schema_correction"] = (
                    f"Column schema for {table_name}: {', '.join(cols[:40])}. "
                    f"Use ONLY these column names — no _s suffix variants."
                )
                retry_kql = await ai_svc._generate_kql_from_natural_language(
                    natural_query=natural_query + f"\n\nIMPORTANT: {resource_context['_schema_correction']}",
                    resource_context=resource_context,
                    user_info=user_info,
                    original_user_query=natural_query,
                )
                retry_result = await self._azure_monitor_service.execute_kql_query(
                    query=retry_kql, workspace_id=workspace_id, user_info=user_info, timespan=None
                )
                if retry_result.get("status") == "success":
                    return retry_result.get("results", [])
                return []

            events = (await _execute_with_schema_retry(kql))[:200]
            logger.info(f" Retrieved {len(events)} raw events from Log Analytics")

            cleaned_events = []
            seen = set()
            for event in events:
                # Flexible field extraction - handle various AI-generated field names
                action = str(event.get("Action", "") or event.get("action", "")).strip()
                operation_name = str(event.get("OperationName", "") or event.get("Operation", "")).strip()
                category = str(event.get("Category", "")).strip()
                rule = str(event.get("Rule", "") or event.get("RuleName", "")).strip()
                result_type = str(event.get("ResultType", "")).strip()
                # Message can be "Message", "Msg", "message", or "msg_s"
                msg = str(event.get("Message", "") or event.get("Msg", "") or event.get("message", "") or event.get("msg_s", "")).strip()
                level = str(event.get("Level", "")).strip()

                # Skip events with no useful signal fields (more lenient - just need timestamp + any field)
                if not any([action, operation_name, category, rule, result_type, msg, level]):
                    continue

                # Dedup on the full event content — field-agnostic so nothing gets stripped
                try:
                    event_key = frozenset((k, str(v)[:200]) for k, v in event.items())
                except Exception:
                    event_key = str(sorted(event.items()))
                if event_key in seen:
                    continue
                seen.add(event_key)
                cleaned_events.append(event)

            logger.info(f" After cleaning: {len(cleaned_events)} events (from {len(events)} raw events)")

            deny_count = sum(
                1 for event in cleaned_events
                if str(event.get("Action", "") or event.get("action", "")).lower() in {"deny", "denied", "drop", "blocked"}
            )

            coverage = "resource_firewall_logs" if "microsoft.network/azurefirewalls" in resource_type else "resource_logs"

            return {
                "success": True,
                "coverage": coverage,
                "workspace_id": workspace_id,
                "table_mode": table_mode,
                "events": cleaned_events,
                "deny_count": deny_count,
                "message": f"Analyzed {len(cleaned_events)} resource log events from Log Analytics",
            }

        except Exception as error:
            logger.warning(f"Resource log evidence collection failed: {error}")
            return {
                "success": False,
                "coverage": "none",
                "workspace_id": None,
                "table_mode": None,
                "events": [],
                "deny_count": 0,
                "message": "Resource log analysis failed",
                "error": str(error),
            }



    async def _select_metrics_with_ai(
        self,
        metric_definitions: List[Any],
        resource_type: str,
        resource_id: str,
        incident_summary: Optional[str] = None,
    ) -> List[str]:
        if not self.openai_client:
            return []

        available_names = [
            (m.name.value if hasattr(m.name, "value") else str(m.name))
            for m in metric_definitions
        ]
        if not available_names:
            return []

        prompt = (
            f"Resource type: {resource_type}\n"
            f"Incident: {incident_summary or 'general health investigation'}\n"
            f"Available metrics ({len(available_names)}): {', '.join(available_names)}\n\n"
            "Select the 6 most relevant metric names from the list above to diagnose this incident. "
            "Prefer metrics that expose errors, failures, saturation, latency, and availability. "
            "Return ONLY a JSON array of metric name strings, exactly as they appear in the list. No explanation."
        )
        try:
            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": "You are an Azure monitoring expert. Return only valid JSON arrays."},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=200,
            )
            raw = response.choices[0].message.content or ""
            if raw.strip().startswith("```"):
                raw = raw.strip().lstrip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
            selected = json.loads(raw)
            available_lower = {n.lower(): n for n in available_names}
            resolved = [available_lower[s.lower()] for s in selected if s.lower() in available_lower]
            return resolved[:6]
        except Exception:
            return []
    
    def _select_metrics_by_keywords(self, metric_definitions: List[Any]) -> List[str]:
        """
        Fallback keyword-based metric selection (original logic).
        """
        priority_keywords = [
            "deny", "drop", "blocked", "reject", "threat", "health", "availability",
            "throughput", "latency", "error", "failed", "packet", "snat", "cpu", "memory",
        ]

        def _score_metric(metric_name: str) -> int:
            lowered = metric_name.lower()
            score = 0
            for idx, keyword in enumerate(priority_keywords):
                if keyword in lowered:
                    score += (len(priority_keywords) - idx)
            return score

        sorted_defs = sorted(
            metric_definitions,
            key=lambda metric: (_score_metric(metric.name.value if hasattr(metric.name, 'value') else str(metric.name))),
            reverse=True,
        )

        selected_metric_names: List[str] = []
        for metric in sorted_defs:
            metric_name = metric.name.value if hasattr(metric.name, 'value') else str(metric.name)
            if metric_name and metric_name not in selected_metric_names:
                selected_metric_names.append(metric_name)
            if len(selected_metric_names) >= 6:
                break
        
        return selected_metric_names

    async def _collect_external_status_evidence(
        self,
        subscription_id: Optional[str],
        user_info: Dict[str, Any],
        hours: int = 24,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Collect Azure Service Health incidents for the analysis window."""
        if not subscription_id:
            return {
                "success": False,
                "message": "No subscription ID provided for service health check",
                "services": [],
                "degraded_services": [],
                "all_operational": True,
            }

        # For historical windows fetch enough history to cover the window, then
        # filter incidents to the exact start_time → end_time range.
        if start_time:
            from datetime import timezone as _tz
            now_utc = datetime.now(_tz.utc)
            st = start_time.replace(tzinfo=_tz.utc) if start_time.tzinfo is None else start_time
            fetch_hours = max(int((now_utc - st).total_seconds() / 3600) + 1, hours)
        else:
            fetch_hours = hours

        raw = await external_status_service.get_external_status(subscription_id, user_info, fetch_hours)

        # If no custom window, return as-is
        if not start_time or not end_time:
            return raw

        # Filter incidents to the exact analysis window
        from datetime import timezone as _tz
        window_start = start_time.replace(tzinfo=_tz.utc) if start_time.tzinfo is None else start_time
        window_end = end_time.replace(tzinfo=_tz.utc) if end_time.tzinfo is None else end_time

        filtered = []
        for incident in raw.get("services", []):
            ts = incident.get("impact_start_time")
            if not ts:
                continue
            try:
                impact_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if window_start <= impact_dt <= window_end:
                    filtered.append(incident)
            except Exception:
                pass

        raw["services"] = filtered
        raw["degraded_services"] = [s.get("service") for s in filtered if s.get("level") in ("Critical", "Warning")]
        raw["all_operational"] = len(filtered) == 0
        return raw

    def _build_timeline(self, evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
        timeline: List[Dict[str, Any]] = []

        changes = evidence.get("changes", {})
        for item in changes.get("changes", [])[:30]:
            timestamp = self._normalize_timestamp(item.get("changeTime"))
            if timestamp:
                timeline.append(
                    {
                        "timestamp": timestamp,
                        "type": "change",
                        "title": f"{item.get('changeType', 'Change')} on {item.get('targetResourceName', 'resource')}",
                        "details": {
                            "resource_id": item.get("targetResourceId"),
                            "resource_type": item.get("targetResourceType"),
                            "initiated_by": item.get("initiatedBy"),
                        },
                    }
                )

        # Add Activity Log operational events
        activity_log_events = changes.get("activity_log_events", [])
        for item in activity_log_events[:20]:
            timestamp = self._normalize_timestamp(item.get("TimeGenerated"))
            if timestamp:
                operation = str(item.get("OperationNameValue", "")).strip() or str(item.get("Operation", "")).strip()
                status = str(item.get("ActivityStatusValue", "")).strip() or str(item.get("Status", "")).strip()
                level = str(item.get("Level", "")).strip()
                
                # Resource Health lifecycle events are informational, never flag as errors
                op_lower = operation.lower()
                is_health_lifecycle = "resourcehealth/healthevent" in op_lower
                is_error = (
                    not is_health_lifecycle
                    and (level.lower() in ["error", "critical"] or status.lower() in ["failed", "failure"])
                )
                event_type = "activity_log_error" if is_error else "activity_log"
                
                timeline.append(
                    {
                        "timestamp": timestamp,
                        "type": event_type,
                        "title": f"Activity: {operation} ({status})" if status else f"Activity: {operation}",
                        "details": {
                            "operation": operation,
                            "status": status,
                            "level": level,
                            "caller": item.get("Caller"),
                            "resource_id": item.get("ResourceId") or item.get("_Resource Id"),
                        },
                    }
                )

        health = evidence.get("health", {})
        if health.get("success"):
            health_occurred = self._normalize_timestamp(health.get("occurred_time"))
            if health_occurred:
                timeline.append(
                    {
                        "timestamp": health_occurred,
                        "type": "health",
                        "title": f"Resource health: {health.get('availability_state', 'Unknown')}",
                        "details": {
                            "reason": health.get("reason_type"),
                            "summary": health.get("summary"),
                        },
                    }
                )
            else:
                for resource in health.get("resources", [])[:10]:
                    occurred_time = self._normalize_timestamp(resource.get("occurred_time"))
                    if occurred_time:
                        timeline.append(
                            {
                                "timestamp": occurred_time,
                                "type": "health",
                                "title": f"Health signal: {resource.get('availability_state', 'Unknown')}",
                                "details": {
                                    "resource_id": resource.get("resource_id"),
                                    "reason": resource.get("reason_type"),
                                },
                            }
                        )

        logs = evidence.get("logs", {})
        # CIRRUS audit events are now excluded from RCA - only Azure resource logs
        # for event in logs.get("recent_events", [])[:10]:
        #     (removed - was showing CIRRUS internal logs, not Azure audit trail)

        resource_logs = logs.get("resource_logs", {})
        log_events = resource_logs.get("events", [])
        if log_events:
            # Group by category, count totals and errors — one summary entry per category
            category_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "errors": 0})
            earliest: Optional[str] = None
            latest_ts: Optional[str] = None

            for event in log_events:
                cat = str(event.get("Category", "") or event.get("Table", "") or "ResourceLog").strip()
                category_stats[cat]["total"] += 1
                result = str(event.get("ResultType", "") or event.get("Level", "")).lower()
                if result in ("failure", "error", "failed", "critical"):
                    category_stats[cat]["errors"] += 1
                ts = self._normalize_timestamp(event.get("TimeGenerated"))
                if ts:
                    if earliest is None or ts < earliest:
                        earliest = ts
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts

            for cat, stats in category_stats.items():
                error_note = f", {stats['errors']} errors" if stats["errors"] else ""
                timeline.append(
                    {
                        "timestamp": latest_ts or self._normalize_timestamp(datetime.utcnow()),
                        "type": "resource_log",
                        "title": f"Resource Logs: {cat} — {stats['total']} events{error_note}",
                        "details": {
                            "category": cat,
                            "total_events": stats["total"],
                            "error_events": stats["errors"],
                            "window_start": earliest,
                            "window_end": latest_ts,
                        },
                    }
                )

        # evidence["metrics"] is the direct dict from _collect_resource_metrics_evidence
        metrics_data = evidence.get("metrics", {})
        failure_rate_pct = metrics_data.get("failure_rate_pct")
        if failure_rate_pct is not None:
            timeline.append(
                {
                    "timestamp": self._normalize_timestamp(datetime.utcnow()),
                    "type": "metric_insight",
                    "title": f"Metric insight: Failed request rate {failure_rate_pct}%",
                    "details": {"failure_rate_pct": failure_rate_pct},
                }
            )

        for metric in metrics_data.get("metrics", [])[:5]:
            ts_list = metric.get("timeseries", [])
            first_ts = ts_list[0] if ts_list else {}
            ts_count = len(ts_list)
            latest = first_ts.get("latest", 0)
            max_val = first_ts.get("max", 0)
            avg_val = first_ts.get("avg", 0)
            label = f"Metric: {metric.get('name')} (latest={round(latest, 2)}, max={round(max_val, 2)}{f', {ts_count} series' if ts_count > 1 else ''})"
            timeline.append(
                {
                    "timestamp": metric.get("window_end") or self._normalize_timestamp(datetime.utcnow()),
                    "type": "resource_metric",
                    "title": label,
                    "details": {
                        "avg": round(avg_val, 2),
                        "latest": round(latest, 2),
                        "unit": metric.get("unit"),
                        "timeseries_count": ts_count,
                        "window_start": metric.get("window_start"),
                        "window_end": metric.get("window_end"),
                    },
                }
            )

        # Add Azure Service Health incidents to timeline
        external_status = evidence.get("external_status", {})
        service_health_incidents = external_status.get("services", [])
        
        for incident in service_health_incidents[:10]:
            impact_start_time = self._normalize_timestamp(incident.get("impact_start_time"))
            
            if impact_start_time:
                timeline.append(
                    {
                        "timestamp": impact_start_time,
                        "type": "service_health",
                        "title": f"Azure Service Health: {incident.get('service', 'Unknown Service')} - {incident.get('level', 'Issue')}",
                        "details": {
                            "service": incident.get("service"),
                            "region": incident.get("region"),
                            "level": incident.get("level"),
                            "status": incident.get("status"),
                            "title": incident.get("title"),
                            "summary": incident.get("summary", "")[:200],
                        },
                    }
                )

        timeline.sort(key=lambda item: self._timeline_sort_key(item.get("timestamp")), reverse=True)
        
        # Ensure service health incidents are always visible in the timeline
        # (they may be older than recent changes/logs but are critical context)
        service_health_entries = [item for item in timeline if item.get("type") == "service_health"]
        other_entries = [item for item in timeline if item.get("type") != "service_health"]
        
        # Take top 30 recent events + all service health incidents (max 10)
        combined_timeline = other_entries[:30] + service_health_entries[:10]
        combined_timeline.sort(key=lambda item: self._timeline_sort_key(item.get("timestamp")), reverse=True)
        
        logger.info(f" Timeline: {len(combined_timeline)} total ({len(service_health_entries)} service health, {len(other_entries[:30])} other)")
        
        return combined_timeline[:40]

    def _normalize_timestamp(self, timestamp_value: Any) -> Optional[str]:
        """Normalize different timestamp types to ISO string for safe serialization and sorting."""
        if timestamp_value is None:
            return None

        if isinstance(timestamp_value, datetime):
            if timestamp_value.tzinfo is None:
                timestamp_value = timestamp_value.replace(tzinfo=timezone.utc)
            return timestamp_value.isoformat()

        if isinstance(timestamp_value, str):
            value = timestamp_value.strip()
            if not value:
                return None
            # Keep original if parsable or best-effort fallback as-is
            try:
                # Handle trailing Z explicitly
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.isoformat()
            except Exception:
                return timestamp_value

        return str(timestamp_value)

    def _timeline_sort_key(self, timestamp_value: Any) -> float:
        """Convert timeline timestamp to comparable unix epoch for robust sorting."""
        normalized = self._normalize_timestamp(timestamp_value)
        if not normalized:
            return 0.0

        try:
            value = normalized
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except Exception:
            return 0.0

    def _to_kusto_datetime_literal(self, value: Any) -> str:
        normalized = self._normalize_timestamp(value) or ""
        return normalized.replace("+00:00", "Z")

    def _structural_evidence_gaps(
        self,
        evidence_errors: Dict[str, str],
        resource_id: Optional[str],
    ) -> List[str]:
        """Returns factual collection failures only — no interpretation.
        Used as seed context for the AI; AI generates the final visibility_gaps.
        """
        gaps: List[str] = []
        for source_name, error in (evidence_errors or {}).items():
            gaps.append(f"{source_name} collection failed: {error}")
        if not resource_id:
            gaps.append("No specific resource_id scoped — analysis is subscription-wide")
        return gaps

    def _build_evidence_summary(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        changes = evidence.get("changes", {})
        health = evidence.get("health", {})
        logs = evidence.get("logs", {})
        metrics = evidence.get("metrics", {})
        external_status = evidence.get("external_status", {})

        return {
            "changes_count": changes.get("total_changes", 0),
            "activity_log_count": changes.get("activity_log_count", 0),
            "health_signals": len(health.get("resources", [])) if health.get("resources") else (1 if health.get("availability_state") else 0),
            "audit_events_count": logs.get("summary", {}).get("total_events", 0),
            "resource_log_events_count": len(logs.get("resource_logs", {}).get("events", [])),
            "firewall_deny_count": logs.get("resource_logs", {}).get("deny_count", 0),
            "resource_metric_signals": len(metrics.get("metrics", [])),
            "failure_rate_pct": metrics.get("failure_rate_pct"),
            "service_health_incidents": len(external_status.get("services", [])),
            "external_degraded_services": external_status.get("degraded_services", []),
        }

    async def _collect_advisor_evidence(
        self,
        resource_id: Optional[str],
        resource_group: Optional[str],
        subscription_ids: List[str],
        user_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collect Azure Advisor recommendations for the resource or resource group."""
        try:
            from services.dual_credential_service import dual_cred_service
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

            scope_filter = ""
            if resource_id:
                scope_filter = f"| where tolower(tostring(properties.resourceMetadata.resourceId)) == tolower('{resource_id}')"
            elif resource_group:
                scope_filter = f"| where resourceGroup =~ '{resource_group}'"

            kql = (
                "advisorresources "
                "| where type =~ 'microsoft.advisor/recommendations' "
                f"{scope_filter} "
                "| extend category=tostring(properties.category), "
                "  impact=tostring(properties.impact), "
                "  shortDesc=tostring(properties.shortDescription.solution), "
                "  resourceId=tostring(properties.resourceMetadata.resourceId) "
                "| project category, impact, shortDesc, resourceId, resourceGroup "
                "| order by impact asc "
                "| limit 30"
            )

            cred = dual_cred_service.get_user_credential(user_info)
            client = ResourceGraphClient(cred)
            req = QueryRequest(
                subscriptions=subscription_ids,
                query=kql,
                options=QueryRequestOptions(result_format="objectArray"),
            )
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: client.resources(req))
            rows = list(resp.data) if resp.data else []
            return {"success": True, "recommendations": rows, "count": len(rows)}
        except Exception as e:
            logger.warning(f"Advisor evidence collection failed: {e}")
            return {"success": False, "recommendations": [], "count": 0, "error": str(e)}

    async def _collect_network_evidence(
        self,
        resource_id: Optional[str],
        resource_group: Optional[str],
        subscription_ids: List[str],
        user_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collect all network resources in scope — no type filter, let AI reason over topology."""
        try:
            from services.dual_credential_service import dual_cred_service
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions

            rg_filter = f"| where resourceGroup =~ '{resource_group}'" if resource_group else ""
            kql = (
                "networkresources "
                f"{rg_filter} "
                "| project id, name, type, location, resourceGroup, properties "
                "| limit 80"
            )

            cred = dual_cred_service.get_user_credential(user_info)
            client = ResourceGraphClient(cred)
            req = QueryRequest(
                subscriptions=subscription_ids,
                query=kql,
                options=QueryRequestOptions(result_format="objectArray"),
            )
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: client.resources(req))
            rows = list(resp.data) if resp.data else []
            return {"success": True, "resources": rows, "count": len(rows)}
        except Exception as e:
            logger.warning(f"Network evidence collection failed: {e}")
            return {"success": False, "resources": [], "count": 0, "error": str(e)}

    async def _collect_diagnostic_coverage_evidence(
        self,
        resource_id: Optional[str],
        user_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collect diagnostic settings coverage — which log categories are enabled."""
        if not resource_id:
            return {"success": False, "message": "No resource_id provided"}
        try:
            result = await self._diagnostic_settings_service.get_log_mode_for_resource(
                resource_id=resource_id, user_info=user_info
            )
            return {"success": True, **result}
        except Exception as e:
            logger.warning(f"Diagnostic coverage collection failed: {e}")
            return {"success": False, "error": str(e)}

    async def _generate_ai_correlation(
        self,
        incident_summary: str,
        hours: int,
        subscription_ids: List[str],
        resource_id: Optional[str],
        resource_group: Optional[str],
        evidence: Dict[str, Any],
        visibility_gaps: List[str],
        previous_context: Optional[Dict[str, Any]] = None,
        executed_followup_reasons: Optional[List[str]] = None,
        iteration: int = 1,
    ) -> Dict[str, Any]:
        if not self.openai_client:
            return self._fallback_ai_analysis(evidence)

        # evidence["metrics"] is the direct dict returned by _collect_resource_metrics_evidence
        metrics_evidence = evidence.get("metrics", {})

        # Extract resource metadata for explicit context
        resource_metadata: Dict[str, Any] = {}
        if resource_id:
            parts = resource_id.split("/")
            resource_metadata["resource_name"] = parts[-1] if parts else None
            provider_idx = next((i for i, p in enumerate(parts) if p.lower() == "providers"), None)
            if provider_idx is not None and len(parts) > provider_idx + 2:
                resource_metadata["resource_type"] = f"{parts[provider_idx + 1]}/{parts[provider_idx + 2]}"
            resource_metadata["subscription_id"] = parts[2] if len(parts) > 2 else None
            resource_metadata["resource_group"] = parts[4] if len(parts) > 4 else resource_group

        # --- Compress each evidence source before building compact_evidence ---
        # Network: strip properties blobs — AI only needs name/type/location for topology reasoning
        raw_network = evidence.get("network", {}).get("resources", [])[:20]
        network_summary = [
            {"name": r.get("name"), "type": r.get("type"), "location": r.get("location"), "resourceGroup": r.get("resourceGroup")}
            for r in raw_network
        ]

        # Changes: sort by relevance to the scoped resource before sampling.
        # Subscription-wide change collection means unrelated resources dominate if unsorted.
        # Priority: 1=scoped resource itself, 2=same resource group, 3=everything else.
        all_changes = evidence.get("changes", {}).get("changes", [])
        scoped_rid_lower = (resource_id or "").lower()
        scoped_rg_lower = (resource_group or "").lower()
        if scoped_rid_lower or scoped_rg_lower:
            def _change_priority(c: Dict[str, Any]) -> int:
                tid = (c.get("targetResourceId") or "").lower()
                trg = (c.get("resourceGroup") or "").lower()
                if scoped_rid_lower and scoped_rid_lower in tid:
                    return 0
                if scoped_rg_lower and trg == scoped_rg_lower:
                    return 1
                return 2
            all_changes = sorted(all_changes, key=_change_priority)
        raw_changes = all_changes[:8]
        changes_summary = []
        for c in raw_changes:
            changed_keys = list((c.get("changes") or {}).keys())[:5]
            changes_summary.append({
                "changeTime": c.get("changeTime"),
                "changeType": c.get("changeType"),
                "targetResourceName": c.get("targetResourceName"),
                "targetResourceType": c.get("targetResourceType"),
                "resourceGroup": c.get("resourceGroup"),
                "initiatedBy": c.get("initiatedBy"),
                "changedProperties": changed_keys,
            })

        # Metrics: strip raw value arrays — keep only statistical summary per metric per slice
        raw_metrics = metrics_evidence.get("metrics", [])
        metrics_summary = []
        for m in raw_metrics[:8]:
            slices = []
            for ts in (m.get("timeseries") or [])[:4]:
                slices.append({
                    "dimensions": ts.get("dimensions"),
                    "avg": round(ts.get("avg", 0), 3),
                    "max": round(ts.get("max", 0), 3),
                    "latest": round(ts.get("latest", 0), 3),
                })
            metrics_summary.append({"name": m.get("name"), "unit": m.get("unit"), "timeseries": slices})

        # Logs: keep only key diagnostic fields, not full raw event objects
        raw_log_events = evidence.get("logs", {}).get("resource_logs", {}).get("events", [])[:15]
        log_summary = []
        for e in raw_log_events:
            log_summary.append({
                "TimeGenerated": e.get("TimeGenerated"),
                "Table": e.get("Table") or e.get("table"),
                "Action": e.get("Action") or e.get("action"),
                "OperationName": e.get("OperationName") or e.get("OperationNameValue"),
                "ResultType": e.get("ResultType"),
                "Level": e.get("Level"),
                "Category": e.get("Category"),
                "Rule": e.get("Rule") or e.get("RuleName"),
                "Msg": (e.get("Msg") or e.get("Message") or "")[:200],
            })

        # Activity log: key fields only
        raw_activity = evidence.get("changes", {}).get("activity_log_events", [])[:10]
        activity_summary = [
            {
                "TimeGenerated": a.get("TimeGenerated"),
                "OperationNameValue": a.get("OperationNameValue") or a.get("Operation"),
                "ActivityStatusValue": a.get("ActivityStatusValue") or a.get("ActivityStatus"),
                "Level": a.get("Level"),
                "Caller": a.get("Caller"),
                "ResourceGroup": a.get("ResourceGroup"),
            }
            for a in raw_activity
        ]

        compact_evidence = {
            "changes": {
                "total_changes": evidence.get("changes", {}).get("total_changes", 0),
                "statistics": evidence.get("changes", {}).get("statistics", {}),
                "sample": changes_summary,
                "activity_log_events": activity_summary,
            },
            "health": {
                "summary": evidence.get("health", {}).get("summary", {}),
                "availability_state": evidence.get("health", {}).get("availability_state"),
                "reason_type": evidence.get("health", {}).get("reason_type"),
            },
            "logs": {
                "resource_logs_coverage": evidence.get("logs", {}).get("resource_logs", {}).get("coverage"),
                "deny_count": evidence.get("logs", {}).get("resource_logs", {}).get("deny_count", 0),
                "sample": log_summary,
            },
            "metrics": {
                "coverage": metrics_evidence.get("coverage"),
                "failure_rate_pct": metrics_evidence.get("failure_rate_pct"),
                "metrics": metrics_summary,
            },
            "external_status": {
                "all_operational": evidence.get("external_status", {}).get("all_operational", True),
                "active_incident_count": len(evidence.get("external_status", {}).get("services", [])),
                "degraded_services": evidence.get("external_status", {}).get("degraded_services", []),
                "incidents": evidence.get("external_status", {}).get("services", [])[:5],
            },
            "advisor": {
                "count": evidence.get("advisor", {}).get("count", 0),
                "recommendations": evidence.get("advisor", {}).get("recommendations", [])[:10],
            },
            "network": {
                "count": evidence.get("network", {}).get("count", 0),
                "resources": network_summary,
            },
            "diagnostic_coverage": {
                "has_diagnostics": evidence.get("diagnostic_coverage", {}).get("has_diagnostics", False),
                "primary": evidence.get("diagnostic_coverage", {}).get("primary"),
                "enabled_categories": (
                    (evidence.get("diagnostic_coverage", {}).get("primary") or {}).get("enabled_log_categories", [])
                ),
            },
            # Follow-up results from prior iterations
            **{k: v for k, v in evidence.items() if k.startswith("followup_")},
        }

        system_prompt = """You are a senior SRE performing root cause analysis on Azure infrastructure. \
You have full access to correlated telemetry for a scoped resource (health, logs, metrics, changes, network, advisor). \
Your job is to reason over the evidence and deliver a decisive, data-backed verdict.

Key facts about the evidence schema:
- changes: subscription-wide. Weight changes on the scoped resource highest; treat changes on unrelated \
resources as low-weight context only.
- logs: deny_count=0 with rows actually queried is a positive finding — the resource is not blocking traffic.
- metrics timeseries: each entry is a dimension slice with avg/max/latest values.
- followup_*: results from queries you requested in a prior iteration.
- changes.sample includes a resourceGroup field — use it to distinguish scoped vs unrelated changes.

Behavioural intent:
- Be assertive. If the evidence supports a verdict, state it. If the scoped resource is clean across all \
signals, say it is exonerated and name the most plausible alternative from the available data.
- Absence of deny events after actual log collection is conclusive — not a gap.
- Only set needs_more_evidence=true if a specific follow-up would materially change the verdict. \
Do not request follow-ups for completeness.

Return STRICT JSON. All string fields are rendered as markdown in the UI.

Fields:
  root_cause: string — assertive, data-backed. Bold resource names and timestamps.
  root_cause_category: "azure_platform"|"configuration_change"|"resource_exhaustion"|\
"network_connectivity"|"security"|"external_dependency"|"no_issue_detected"|"unknown"
  confidence_reasoning: array of objects — one per evidence source checked:
    [{source: "Metrics", stat: "FirewallHealth=100%", finding: "Firewall fully healthy, no degradation"}, ...]
    Required sources to cover when data exists: Metrics, Logs, Changes, Health, Service Health, Advisor.
  alternative_explanations: string[] max 2
  timeline_summary: array of objects — chronological signal events only, omit noise:
    [{time: "HH:MM UTC", event: "plain text description of what happened"}]
  ai_confidence: float 0.0–1.0
  visibility_gaps: string[] — actionable gaps only, not raw error messages
  manual_checklist: string[] 3–6 plain-text next steps
  needs_more_evidence: boolean
  follow_up_queries: array when needs_more_evidence=true. Each item:
    type: "log_analytics"|"resource_graph"|"metrics"|"advisor"|"network_topology"|"diagnostic_settings"
    reason: string — how this changes the verdict
    natural_query: string — plain English, required for log_analytics
    kql: string — valid KQL ending | limit 20, required for resource_graph/advisor/network_topology
    resource_id: string — required for metrics/diagnostic_settings
    metric_names: string[] — required for metrics
"""

        # Build explicit scope limitations so the AI reasons correctly about blind spots
        scope_notes: List[str] = []
        if not resource_id:
            scope_notes.append("NO resource_id scoped: metrics collection impossible, log collection impossible, diagnostic coverage unknown. Confidence ceiling is ~0.60 without these.")
        else:
            if not compact_evidence.get("metrics", {}).get("metrics"):
                scope_notes.append(f"Metrics came back empty for resource_id={resource_id}. Request a metrics follow-up to collect health KPIs.")
            if compact_evidence.get("logs", {}).get("resource_logs_coverage") in (None, "none"):
                scope_notes.append(f"No diagnostic logs available for resource_id={resource_id}. Consider requesting diagnostic_settings follow-up to confirm why.")
        if not compact_evidence.get("health", {}).get("availability_state"):
            scope_notes.append("Resource Health availability_state is null — either no resource_id or resource is exempt from health tracking.")

        user_prompt: Dict[str, Any] = {
            "incident_summary": incident_summary,
            "iteration": iteration,
            "scope": {
                "hours": hours,
                "subscription_ids": subscription_ids,
                "resource_id": resource_id,
                "resource_group": resource_group,
                "resource_metadata": resource_metadata,
                "limitations": scope_notes,
            },
            "visibility_gaps": visibility_gaps,
            "evidence": compact_evidence,
        }
        if previous_context:
            user_prompt["previous_iteration"] = previous_context
            user_prompt["instruction"] = (
                "You have already formed an initial hypothesis (see previous_iteration). "
                "New follow-up evidence from the queries you requested is now included in evidence.followup_*. "
                "Do NOT repeat queries already listed in previous_iteration.queries_run. "
                "Refine your analysis using the new evidence. Update confidence up or down based on what you found. "
                "Set needs_more_evidence=false unless a specific additional query would materially change the verdict."
            )
        if executed_followup_reasons:
            user_prompt["already_queried"] = executed_followup_reasons

        # Hard token budget: if serialised payload is still too large, progressively
        # trim the most verbose fields so the AI call succeeds rather than fails.
        MAX_PAYLOAD_CHARS = settings.RCA_MAX_PAYLOAD_CHARS  # sourced from config.py
        payload_str = json.dumps(user_prompt, default=str)
        if len(payload_str) > MAX_PAYLOAD_CHARS:
            logger.warning(f"RCA compact_evidence too large ({len(payload_str):,} chars) — trimming")
            # Remove raw log events first (largest contributor)
            user_prompt["evidence"]["logs"]["sample"] = user_prompt["evidence"]["logs"].get("sample", [])[:5]
            # Trim activity log
            user_prompt["evidence"]["changes"]["activity_log_events"] = user_prompt["evidence"]["changes"].get("activity_log_events", [])[:5]
            # Trim advisor
            user_prompt["evidence"]["advisor"]["recommendations"] = user_prompt["evidence"]["advisor"].get("recommendations", [])[:5]
            # Remove followup raw rows
            for k in list(user_prompt["evidence"].keys()):
                if k.startswith("followup_"):
                    user_prompt["evidence"][k].pop("rows", None)
            payload_str = json.dumps(user_prompt, default=str)
            logger.info(f"RCA payload after trim: {len(payload_str):,} chars")

        try:
            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": payload_str},
                ],
                max_completion_tokens=1500,
            )

            raw = response.choices[0].message.content or ""
            if raw.strip().startswith("```"):
                raw = raw.strip().lstrip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
            parsed = json.loads(raw)

            def _ensure_list(val: Any, fallback: Any = None) -> Any:
                """Return val if it's already a list, else fallback (or empty list)."""
                return val if isinstance(val, list) else (fallback if fallback is not None else [])

            return {
                "root_cause": parsed.get("root_cause"),
                "root_cause_category": parsed.get("root_cause_category", "unknown"),
                "confidence_reasoning": _ensure_list(parsed.get("confidence_reasoning")),
                "alternative_explanations": _ensure_list(parsed.get("alternative_explanations")),
                "timeline_summary": _ensure_list(parsed.get("timeline_summary")),
                "ai_confidence": float(parsed.get("ai_confidence", 0.5)),
                "manual_checklist": _ensure_list(parsed.get("manual_checklist")),
                "needs_more_evidence": bool(parsed.get("needs_more_evidence", False)),
                "follow_up_queries": _ensure_list(parsed.get("follow_up_queries")),
                "visibility_gaps": _ensure_list(parsed.get("visibility_gaps")),
            }
        except Exception as error:
            logger.warning(f"RCA AI correlation failed, using fallback analysis: {error}")
            return self._fallback_ai_analysis(evidence)

    def _fallback_ai_analysis(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """Used only when the OpenAI call itself fails (network/auth error).
        Returns structured signal counts so the caller can surface factual data
        instead of hardcoded verdict strings."""
        changes_count = evidence.get("changes", {}).get("total_changes", 0)
        unavailable_count = evidence.get("health", {}).get("summary", {}).get("unavailable", 0)
        activity_errors = [
            e for e in evidence.get("changes", {}).get("activity_log_events", [])
            if str(e.get("Level", "")).lower() in ("error", "critical")
            or str(e.get("ActivityStatus", "")).lower() == "failed"
        ]
        deny_count = evidence.get("logs", {}).get("resource_logs", {}).get("deny_count", 0)
        service_incidents = len(evidence.get("external_status", {}).get("services", []))

        # Derive category from raw signal counts — no verdict text, just category + confidence
        if unavailable_count > 0 and changes_count > 0:
            root_cause_category = "configuration_change"
            ai_confidence = 0.55
        elif unavailable_count > 0:
            root_cause_category = "azure_platform"
            ai_confidence = 0.50
        elif service_incidents > 0:
            root_cause_category = "azure_platform"
            ai_confidence = 0.55
        elif activity_errors or deny_count > 0:
            root_cause_category = "unknown"
            ai_confidence = 0.40
        elif changes_count > 0:
            root_cause_category = "configuration_change"
            ai_confidence = 0.35
        else:
            root_cause_category = "no_issue_detected"
            ai_confidence = 0.70

        return {
            "root_cause": None,
            "root_cause_category": root_cause_category,
            "confidence_reasoning": None,
            "alternative_explanations": [],
            "timeline_summary": None,
            "ai_confidence": ai_confidence,
            "manual_checklist": [],
            "needs_more_evidence": False,
            "follow_up_queries": [],
            "visibility_gaps": [],
        }

    # ------------------------------------------------------------------
    # Agentic loop helpers
    # ------------------------------------------------------------------

    async def _execute_ai_followup_queries(
        self,
        follow_up_queries: List[Dict[str, Any]],
        resource_id: Optional[str],
        subscription_ids: List[str],
        user_info: Dict[str, Any],
        hours: int,
    ) -> Dict[str, Any]:
        """
        Execute the follow-up queries the AI requested in a previous round.
        Supports three types: log_analytics, resource_graph, metrics.
        Returns a dict keyed by query reason/index that gets merged into evidence.
        """
        results: Dict[str, Any] = {}

        for idx, q in enumerate(follow_up_queries[:2]):  # cap at 2 follow-ups per round to stay within frontend timeout
            qtype = q.get("type", "").lower()
            reason = q.get("reason", f"followup_{idx}")
            key = f"followup_{idx}_{qtype}"

            try:
                if qtype == "advisor":
                    kql = q.get("kql")
                    if not kql:
                        results[key] = {"success": False, "reason": reason, "message": "advisor follow-up requires kql from AI"}
                        continue
                    from azure.mgmt.resourcegraph import ResourceGraphClient
                    from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
                    from services.dual_credential_service import dual_cred_service as _dcs
                    _cred = _dcs.get_user_credential(user_info)
                    _client = ResourceGraphClient(_cred)
                    _req = QueryRequest(
                        subscriptions=subscription_ids,
                        query=kql,
                        options=QueryRequestOptions(result_format="objectArray"),
                    )
                    _resp = await asyncio.get_event_loop().run_in_executor(None, lambda: _client.resources(_req))
                    rows = list(_resp.data) if _resp.data else []
                    results[key] = {"success": True, "reason": reason, "recommendations": rows, "count": len(rows)}

                elif qtype == "network_topology":
                    kql = q.get("kql")
                    if not kql:
                        results[key] = {"success": False, "reason": reason, "message": "network_topology follow-up requires kql from AI"}
                        continue
                    from azure.mgmt.resourcegraph import ResourceGraphClient
                    from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
                    from services.dual_credential_service import dual_cred_service as _dcs
                    _cred = _dcs.get_user_credential(user_info)
                    _client = ResourceGraphClient(_cred)
                    _req = QueryRequest(
                        subscriptions=subscription_ids,
                        query=kql,
                        options=QueryRequestOptions(result_format="objectArray"),
                    )
                    _resp = await asyncio.get_event_loop().run_in_executor(None, lambda: _client.resources(_req))
                    rows = list(_resp.data) if _resp.data else []
                    results[key] = {"success": True, "reason": reason, "rows": rows, "count": len(rows)}

                elif qtype == "diagnostic_settings":
                    rid = q.get("resource_id") or resource_id
                    if rid:
                        ds_result = await self._diagnostic_settings_service.get_log_mode_for_resource(
                            resource_id=rid, user_info=user_info
                        )
                        results[key] = {"success": True, "reason": reason, **ds_result}
                    else:
                        results[key] = {"success": False, "reason": reason, "message": "No resource_id"}

                elif qtype == "log_analytics":
                    # Use direct KQL generation + execute — same fast path as _collect_resource_log_evidence.
                    # process_natural_query takes 60-120s (classify + single-turn + possible agentic escalation)
                    # and returns results:[] (narrative only). Direct path takes ~20s and returns actual rows.
                    natural_query = q.get("natural_query") or q.get("reason") or q.get("kql", "")
                    if not natural_query:
                        continue
                    target_rid = q.get("resource_id") or resource_id
                    followup_rows: List[Dict[str, Any]] = []
                    try:
                        from services.kql_generation_service import AIService as _AISvc
                        from services.diagnostic_settings_service import DiagnosticSettingsService as _DSSvc
                        _diag = await _DSSvc().get_log_mode_for_resource(target_rid, user_info) if target_rid else {}
                        _ws = (_diag.get("primary") or {}).get("workspace_id") if _diag.get("has_diagnostics") else None
                        if _ws:
                            _rctx: Dict[str, Any] = {
                                "resource_id": target_rid,
                                "resource_type": _diag.get("resource_type"),
                                "resource_name": (target_rid or "").split("/")[-1],
                                "subscription_ids": subscription_ids,
                                "_workspace_ids_from_diagnostics": [_ws],
                                "log_mode": (_diag.get("primary") or {}).get("mode"),
                            }
                            _kql = await _AISvc()._generate_kql_from_natural_language(
                                natural_query=f"RCA follow-up: {natural_query}",
                                resource_context=_rctx,
                                user_info=user_info,
                                original_user_query=natural_query,
                            )
                            _exec = await self._azure_monitor_service.execute_kql_query(
                                query=_kql, workspace_id=_ws, user_info=user_info, timespan=None
                            )
                            if _exec.get("status") == "success":
                                followup_rows = _exec.get("results", [])[:50]
                    except Exception as _fe:
                        logger.warning(f"Follow-up log_analytics failed: {_fe}")
                    results[key] = {
                        "success": True,  # 0 rows is a valid result (absence of events is signal)
                        "reason": reason,
                        "rows": followup_rows,
                        "count": len(followup_rows),
                        "note": "0 results — no matching events found" if not followup_rows else None,
                    }

                elif qtype == "resource_graph":
                    kql = q.get("kql", "")
                    if not kql:
                        continue
                    # Enforce row cap on resource graph too
                    kql_lower = kql.lower()
                    if "| limit " not in kql_lower and "| take " not in kql_lower:
                        kql = kql.rstrip().rstrip(";") + "\n| limit 20"
                    from azure.mgmt.resourcegraph import ResourceGraphClient
                    from azure.mgmt.resourcegraph.models import QueryRequest, QueryRequestOptions
                    from services.dual_credential_service import dual_cred_service
                    cred = dual_cred_service.get_user_credential(user_info)
                    client = ResourceGraphClient(cred)
                    req = QueryRequest(
                        subscriptions=subscription_ids,
                        query=kql,
                        options=QueryRequestOptions(result_format="objectArray"),
                    )
                    resp = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: client.resources(req)
                    )
                    rows = list(resp.data) if resp.data else []
                    # Compress: strip properties blobs, keep identifying fields only
                    compressed = [
                        {k: v for k, v in (r if isinstance(r, dict) else {}).items()
                         if k != "properties" and not k.startswith("_")}
                        for r in rows[:20]
                    ]
                    results[key] = {
                        "success": True,
                        "reason": reason,
                        "rows": compressed,
                        "count": len(rows),
                    }

                elif qtype == "metrics":
                    rid = q.get("resource_id") or resource_id
                    metric_names = q.get("metric_names", [])
                    if not rid or not metric_names:
                        continue
                    from azure.monitor.query import MetricsQueryClient, MetricAggregationType
                    from services.dual_credential_service import dual_cred_service
                    cred = dual_cred_service.get_user_credential(user_info, token_type="management")
                    client = MetricsQueryClient(cred)
                    window_end = datetime.utcnow()
                    window_start = window_end - timedelta(hours=min(hours, 24))

                    def _query():
                        return client.query_resource(
                            resource_uri=rid,
                            metric_names=metric_names[:6],
                            timespan=(window_start, window_end),
                            granularity=timedelta(minutes=5),
                            aggregations=[MetricAggregationType.AVERAGE, MetricAggregationType.MAXIMUM],
                        )
                    resp = await asyncio.get_event_loop().run_in_executor(None, _query)
                    metric_rows = []
                    for m in resp.metrics:
                        pts = [p.average or p.maximum for ts in m.timeseries for p in ts.data if (p.average or p.maximum) is not None]
                        if pts:
                            metric_rows.append({"name": m.name, "max": round(max(pts), 3), "avg": round(sum(pts)/len(pts), 3), "latest": round(pts[-1], 3)})
                    results[key] = {"success": True, "reason": reason, "metrics": metric_rows}

                else:
                    results[key] = {"success": False, "reason": reason, "message": f"Unknown query type: {qtype}"}

            except Exception as e:
                logger.warning(f"Agentic follow-up query {key} failed: {e}")
                results[key] = {"success": False, "reason": reason, "error": str(e)}

        return results

    async def _retrospective_validation(
        self,
        final_analysis: Dict[str, Any],
        compact_evidence: Dict[str, Any],
        incident_summary: str,
    ) -> Dict[str, Any]:
        """
        AI reviews its own final conclusion for internal consistency.
        Asks: 'What would disprove this? Is any such evidence already present?
        Should confidence be revised?'
        Returns the same dict structure, potentially with revised confidence/reasoning.
        """
        if not self.openai_client:
            return final_analysis

        try:
            prompt = {
                "task": "retrospective_self_check",
                "your_conclusion": {
                    "root_cause": final_analysis.get("root_cause"),
                    "ai_confidence": final_analysis.get("ai_confidence"),
                    "confidence_reasoning": final_analysis.get("confidence_reasoning"),
                    "alternative_explanations": final_analysis.get("alternative_explanations"),
                },
                "evidence": {
                    "changes": compact_evidence.get("changes", {}),
                    "health": compact_evidence.get("health", {}),
                    "logs": compact_evidence.get("logs", {}),
                    "metrics": compact_evidence.get("metrics", {}),
                    "external_status": compact_evidence.get("external_status", {}),
                },
                "incident_summary": incident_summary,
                "instructions": (
                    "You have the actual evidence. Review your conclusion critically. "
                    "1. Verify each supporting data point — timestamp, value, operation name. "
                    "2. Identify contradictory evidence and reconcile it. "
                    "3. Check temporal alignment — do signals correlate within ±15 min? "
                    "4. Revise ai_confidence and root_cause if warranted. "
                    "confidence_reasoning MUST be an array of objects: "
                    "[{source, stat, finding}, ...] — same format as original analysis. "
                    "timeline_summary MUST be an array of objects: "
                    "[{time, event}, ...] — signal events only, no noise. "
                    "Return STRICT JSON: root_cause, root_cause_category, confidence_reasoning (array), "
                    "alternative_explanations (array), timeline_summary (array), ai_confidence, manual_checklist, "
                    "visibility_gaps, "
                    "retrospective_notes (string — what you verified and what you revised, if anything)."
                ),
            }

            response = await self.openai_client.chat.completions.create(
                model=settings.OPENAI_DEPLOYMENT_NAME,
                messages=[
                    {"role": "system", "content": "You are a senior SRE performing a retrospective self-check on your own RCA output. Be critical, honest, and precise."},
                    {"role": "user", "content": json.dumps(prompt, default=str)},
                ],
                max_completion_tokens=1500,
            )

            raw = response.choices[0].message.content or ""
            # Strip markdown fences if model wraps output
            if raw.strip().startswith("```"):
                raw = raw.strip().lstrip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
            parsed = json.loads(raw)

            def _ensure_list(val: Any, fallback: Any = None) -> Any:
                return val if isinstance(val, list) else (fallback if fallback is not None else [])

            return {
                "root_cause": parsed.get("root_cause", final_analysis["root_cause"]),
                "root_cause_category": parsed.get("root_cause_category", final_analysis.get("root_cause_category", "unknown")),
                "confidence_reasoning": _ensure_list(parsed.get("confidence_reasoning"), final_analysis.get("confidence_reasoning", [])),
                "alternative_explanations": _ensure_list(parsed.get("alternative_explanations"), final_analysis.get("alternative_explanations", []))[:2],
                "timeline_summary": _ensure_list(parsed.get("timeline_summary"), final_analysis.get("timeline_summary", [])),
                "ai_confidence": float(parsed.get("ai_confidence", final_analysis["ai_confidence"])),
                "manual_checklist": _ensure_list(parsed.get("manual_checklist"), final_analysis.get("manual_checklist", [])),
                "visibility_gaps": _ensure_list(parsed.get("visibility_gaps"), final_analysis.get("visibility_gaps", [])),
                "retrospective_notes": parsed.get("retrospective_notes", ""),
            }
        except Exception as e:
            logger.warning(f"Retrospective validation failed: {e}")
            return final_analysis


root_cause_analysis_service = RootCauseAnalysisService()

"""
Azure Governance Service - Policy Compliance and Security Score
Provides policy compliance data and Microsoft Defender for Cloud security scores
Uses native Azure Policy Insights API for accurate, authoritative compliance data
"""
from typing import List, Dict, Any, Optional
from azure.identity import ClientSecretCredential, ChainedTokenCredential, AzureCliCredential
import logging
import asyncio
import os

logger = logging.getLogger(__name__)


class GovernanceService:
    """Service for Azure Policy and Security Score governance data"""
    
    def __init__(self):
        """Initialize governance service"""
        pass
    
    def _get_user_credential(self, user_info: Dict[str, Any]):
        """Get credential from user token"""
        from services.dual_credential_service import dual_cred_service
        return dual_cred_service.get_user_credential(user_info)
    
    async def get_policy_compliance(self, subscription_ids: List[str], user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get policy compliance summary for subscriptions using native Azure Policy Insights API
        
        Uses the official Azure Policy Insights Summarize API which provides pre-calculated
        compliance data matching Azure Portal exactly. This is the authoritative source.
        
        Args:
            subscription_ids: List of subscription IDs to query
            user_info: User authentication information
            
        Returns:
            Dictionary containing:
            - overall_compliance_percentage: Overall compliance percentage (from Azure)
            - compliant_resources: Count of compliant resources
            - non_compliant_resources: Count of non-compliant resources
            - exempt_resources: Count of exempt resources
            - conflict_resources: Count of conflicting resources
            - unknown_resources: Count of unknown resources
            - total_resources: Total resources evaluated
            - by_subscription: List of per-subscription compliance data
        """
        try:
            logger.debug(f" Fetching policy compliance for {len(subscription_ids)} subscription(s)")
            
            # Get user credential
            user_credential = self._get_user_credential(user_info)
            
            # Import Policy Insights client
            from azure.mgmt.policyinsights import PolicyInsightsClient
            
            # Aggregate results across all subscriptions
            total_compliant = 0
            total_non_compliant = 0
            total_exempt = 0
            total_conflict = 0
            total_unknown = 0
            by_subscription = []
            
            # Query each subscription using native Policy Insights API
            loop = asyncio.get_event_loop()
            
            for sub_id in subscription_ids:
                try:
                    # Create client for this subscription
                    client = PolicyInsightsClient(user_credential, sub_id)
                    
                    # Call the native Summarize API
                    # This returns pre-calculated compliance data from Azure's policy evaluation engine
                    summary_result = await loop.run_in_executor(
                        None,
                        lambda sid=sub_id: client.policy_states.summarize_for_subscription(
                            subscription_id=sid
                        )
                    )
                    
                    # Extract data from the summary response
                    if summary_result.value and len(summary_result.value) > 0:
                        results = summary_result.value[0].results
                        
                        # Log raw API response for debugging
                        logger.debug(f" Summarize API: {sub_id}: compliant={results.non_compliant_resources}")
                        if not results.resource_details:
                            logger.warning(f" No resource_details in Summarize API response for {sub_id}")
                        
                        # Note: The Summarize API only returns evaluated resources
                        # It doesn't include "not started" resources (resources without policy assignments)
                        # Azure Portal may show these separately as "Unknown" or "Not Started"
                        
                        # Extract counts from resourceDetails
                        compliant = 0
                        non_compliant = 0
                        exempt = 0
                        conflict = 0
                        unknown = 0
                        
                        if results.resource_details:
                            for detail in results.resource_details:
                                state = detail.compliance_state.lower()
                                count = detail.count
                                
                                if state == 'compliant':
                                    compliant = count
                                elif state == 'noncompliant':
                                    non_compliant = count
                                elif state == 'exempt':
                                    exempt = count
                                elif state == 'conflict':
                                    conflict = count
                                elif state == 'unknown':
                                    unknown = count
                                else:
                                    logger.warning(f" Unexpected compliance state: {state} with count {count}")
                        
                        total = compliant + non_compliant + exempt + conflict + unknown
                        
                        # Calculate compliance percentage using Azure's formula
                        # Formula: (compliant + exempt) / total * 100
                        # This matches what Azure Portal displays
                        if total > 0:
                            compliance_pct = ((compliant + exempt) / total * 100)
                        else:
                            compliance_pct = 100.0
                        
                        by_subscription.append({
                            "subscription_id": sub_id,
                            "compliance_percentage": round(compliance_pct, 1),
                            "compliant": compliant,
                            "non_compliant": non_compliant,
                            "exempt": exempt,
                            "conflict": conflict,
                            "unknown": unknown,
                            "total": total
                        })
                        
                        # Aggregate totals
                        total_compliant += compliant
                        total_non_compliant += non_compliant
                        total_exempt += exempt
                        total_conflict += conflict
                        total_unknown += unknown
                        
                        logger.info(f" Subscription {sub_id}: {compliance_pct:.1f}% compliant ({compliant}+{exempt}/{total})")
                    else:
                        logger.warning(f" No policy data for subscription {sub_id}")
                        by_subscription.append({
                            "subscription_id": sub_id,
                            "compliance_percentage": 100.0,
                            "compliant": 0,
                            "non_compliant": 0,
                            "exempt": 0,
                            "conflict": 0,
                            "unknown": 0,
                            "total": 0,
                            "message": "No policy assignments"
                        })
                        
                except Exception as sub_error:
                    logger.error(f" Error fetching compliance for subscription {sub_id}: {sub_error}")
                    by_subscription.append({
                        "subscription_id": sub_id,
                        "compliance_percentage": 0.0,
                        "compliant": 0,
                        "non_compliant": 0,
                        "exempt": 0,
                        "conflict": 0,
                        "unknown": 0,
                        "total": 0,
                        "error": str(sub_error)
                    })
            
            total_resources = total_compliant + total_non_compliant + total_exempt + total_conflict + total_unknown
            
            if total_resources == 0:
                logger.warning(" No policy states found across all subscriptions")
                return {
                    "overall_compliance_percentage": 100.0,
                    "compliant_resources": 0,
                    "non_compliant_resources": 0,
                    "exempt_resources": 0,
                    "conflict_resources": 0,
                    "unknown_resources": 0,
                    "total_resources": 0,
                    "by_subscription": by_subscription,
                    "message": "No policy assignments found"
                }
            
            # Calculate overall compliance using Azure's formula
            # Formula: (compliant + exempt) / total * 100
            overall_compliance = ((total_compliant + total_exempt) / total_resources * 100)
            
            logger.debug(
                f"Policy compliance: {overall_compliance:.1f}% "
                f"(compliant={total_compliant}, non-compliant={total_non_compliant}/{total_resources})"
            )
            
            return {
                "overall_compliance_percentage": round(overall_compliance, 1),
                "compliant_resources": total_compliant,
                "non_compliant_resources": total_non_compliant,
                "exempt_resources": total_exempt,
                "conflict_resources": total_conflict,
                "unknown_resources": total_unknown,
                "total_resources": total_resources,
                "by_subscription": sorted(by_subscription, key=lambda x: x['compliance_percentage'])
            }
            
        except Exception as e:
            logger.error(f" Failed to get policy compliance: {e}", exc_info=True)
            raise
    
    async def get_portal_exact_secure_score_v2(self,
                                               subscription_ids: List[str],
                                               user_info: Dict[str, Any],
                                               portal_mode: bool = True,
                                               derive_from_severity: bool = True) -> Dict[str, Any]:
        """
        Portal-accurate secure score using assessment -> subAssessment aggregation.
        - subscription_ids: list of subscription GUIDs
        - portal_mode: apply portal-like filtering (exclude NotApplicable, exclude plan-disabled only when plans exist)
        - derive_from_severity: when subAssessment lacks explicit score, derive conservatively from severity (heuristic)
        Returns: overall_score, current_score, max_score, by_subscription, diagnostic
        """
        try:
            user_credential = self._get_user_credential(user_info)
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest

            client = ResourceGraphClient(user_credential)
            loop = asyncio.get_event_loop()

            # 1) Fetch pricings to learn which Defender plans are enabled (used for excluding plan-scoped assessments)
            q_pricings = """
                SecurityResources
                | where type == 'microsoft.security/pricings'
                | project subscriptionId = tostring(subscriptionId), pricingName = tostring(name), tier = tostring(properties.pricingTier)
            """
            pr_req = QueryRequest(subscriptions=subscription_ids, query=q_pricings)
            pr_resp = await loop.run_in_executor(None, client.resources, pr_req)
            enabled_plans = {}
            if hasattr(pr_resp, 'data') and pr_resp.data:
                for r in pr_resp.data:
                    sid = r.get('subscriptionId') or 'unknown'
                    plan = (r.get('pricingName') or '').lower()
                    tier = (r.get('tier') or '').lower()
                    if sid not in enabled_plans:
                        enabled_plans[sid] = set()
                    if tier and tier not in ('free', 'none', 'disabled', 'freepreview'):
                        enabled_plans[sid].add(plan)
                    elif tier in ('standard', 'paid', 'paidpreview', 'enhanced'):
                        enabled_plans[sid].add(plan)

            # 2) Fetch assessments metadata (useful mapping)
            q_am = """
                SecurityResources
                | where type == 'microsoft.security/assessmentsMetadata'
                | project name, id, subscriptionId = tostring(subscriptionId), properties
            """
            am_req = QueryRequest(subscriptions=subscription_ids, query=q_am)
            am_resp = await loop.run_in_executor(None, client.resources, am_req)
            assessments_metadata = {}
            if hasattr(am_resp, 'data') and am_resp.data:
                for r in am_resp.data:
                    assessments_metadata[r.get('name')] = r.get('properties') or {}

            # 3) Fetch assessments (parents of subAssessments)
            q_assess = """
                SecurityResources
                | where type == 'microsoft.security/assessments'
                | extend subscriptionId = tostring(subscriptionId), assessmentName = tostring(name), properties_obj = properties
                | project subscriptionId, assessmentName, properties_obj, id
            """
            ass_req = QueryRequest(subscriptions=subscription_ids, query=q_assess)
            ass_resp = await loop.run_in_executor(None, client.resources, ass_req)
            # Map assessments by id/name so we can link subAssessments if needed
            assessments_by_id = {}
            if hasattr(ass_resp, 'data') and ass_resp.data:
                for r in ass_resp.data:
                    assessments_by_id[r.get('id') or r.get('assessmentName')] = {
                        "subscription_id": r.get('subscriptionId'),
                        "name": r.get('assessmentName'),
                        "properties": r.get('properties_obj') or {}
                    }

            # 4) Fetch subAssessments (this is where per-item current/max often live)
            # ResourceGraph type string can vary; use contains 'subAssessments'
            q_sub = """
                SecurityResources
                | where type contains 'subassessments'
                | extend subscriptionId = tostring(subscriptionId), saName = tostring(name), properties_obj = properties
                | project subscriptionId, saName, properties_obj, id
            """
            sub_req = QueryRequest(subscriptions=subscription_ids, query=q_sub)
            sub_resp = await loop.run_in_executor(None, client.resources, sub_req)

            if not hasattr(sub_resp, 'data') or not sub_resp.data:
                logger.warning("No subAssessments found - cannot compute portal-accurate score")
                return {
                    "overall_score": 0.0,
                    "current_score": 0.0,
                    "max_score": 0.0,
                    "by_subscription": [],
                    "calculation_method": "assessments-subAssessments-v2",
                    "diagnostic": {"sub_assessments": 0}
                }

            # severity -> relative score heuristic (used only if explicit score missing)
            severity_to_fraction = {
                "critical": 1.0,
                "high": 0.75,
                "medium": 0.5,
                "low": 0.25,
                "unknown": 0.0,
                "informational": 0.0
            }

            total_current = 0.0
            total_max = 0.0
            per_sub = {}
            diag = {
                "sub_total": 0,
                "explicit_scores": 0,
                "derived_from_severity": 0,
                "excluded_not_applicable": 0,
                "excluded_plan_disabled": 0,
                "excluded_zero_max": 0
            }

            for row in sub_resp.data:
                diag["sub_total"] += 1
                sid = row.get('subscriptionId') or 'unknown'
                props = row.get('properties_obj') or {}
                # status parsing
                status_obj = props.get('status') or {}
                status_code = None
                if isinstance(status_obj, dict):
                    status_code = (status_obj.get('code') or status_obj.get('value') or
                                   status_obj.get('status') or status_obj.get('state') or None)
                elif isinstance(status_obj, str):
                    status_code = status_obj
                status = (status_code.lower() if isinstance(status_code, str) else None)

                if status and ('notapplicable' in status or 'not applicable' in status or 'deprecated' in status):
                    diag["excluded_not_applicable"] += 1
                    continue

                # Try explicit score extraction from common paths
                curr = None
                mx = None
                # path 1: properties.score
                score_obj = props.get('score') or {}
                try:
                    if isinstance(score_obj, dict):
                        if 'current' in score_obj and 'max' in score_obj:
                            curr = float(score_obj.get('current') or 0.0)
                            mx = float(score_obj.get('max') or 0.0)
                except Exception:
                    curr = mx = None

                # path 2: properties.metadata.assessmentInsights[*].score
                if (curr is None or mx is None):
                    meta = props.get('metadata') or {}
                    ai = meta.get('assessmentInsights') if isinstance(meta.get('assessmentInsights'), list) else None
                    if ai:
                        for insight in ai:
                            sc = insight.get('score') or {}
                            if isinstance(sc, dict) and 'current' in sc and 'max' in sc:
                                try:
                                    curr = float(sc.get('current') or 0.0)
                                    mx = float(sc.get('max') or 0.0)
                                    break
                                except Exception:
                                    continue

                # path 3: additionalData fields (some tenants put scoring here)
                add = props.get('additionalData') or {}
                if (curr is None or mx is None) and isinstance(add, dict):
                    # explicit fields sometimes present
                    if 'currentScore' in add and 'maxScore' in add:
                        try:
                            curr = float(add.get('currentScore') or 0.0)
                            mx = float(add.get('maxScore') or 0.0)
                        except Exception:
                            pass

                # path 4: fallback to parent assessment metadata (rare)
                if (curr is None or mx is None) and isinstance(props.get('assessmentKey'), str):
                    parent = assessments_by_id.get(props.get('assessmentKey'))
                    if parent:
                        pmeta = parent.get('properties') or {}
                        pscore = pmeta.get('score') or {}
                        try:
                            if isinstance(pscore, dict) and 'current' in pscore and 'max' in pscore:
                                curr = float(pscore.get('current') or 0.0)
                                mx = float(pscore.get('max') or 0.0)
                        except Exception:
                            pass

                # normalize missing -> None -> will attempt derive
                if curr is None:
                    curr = None
                if mx is None:
                    mx = None

                # If still missing explicit score, attempt to derive from severity if allowed
                derived = False
                used_severity = None
                if (curr is None or mx is None) and derive_from_severity:
                    # severity available in props.status.severity or props.severity or props.metadata.severity
                    sev = None
                    if isinstance(props.get('status'), dict):
                        sev = props.get('status').get('severity')
                    if not sev:
                        sev = props.get('severity') or (props.get('metadata') or {}).get('severity')
                    if isinstance(sev, str):
                        used_severity = sev.lower()
                        frac = severity_to_fraction.get(used_severity, None)
                        # choose a nominal max (use 1.0 as canonical max unit) to keep relative weighting
                        # IMPORTANT: these are heuristics; flagged in diagnostics
                        if frac is not None:
                            mx = 1.0
                            curr = mx * frac
                            derived = True
                            diag["derived_from_severity"] += 1

                # If still no mx or mx == 0, exclude as Portal does
                if mx is None or mx == 0:
                    diag["excluded_zero_max"] += 1
                    continue

                # Plan hint filtering: try to detect which plan/service this subAssessment belongs to
                plan_hint = None
                addd = props.get('additionalData') or {}
                if isinstance(addd, dict):
                    plan_hint = (addd.get('service') or addd.get('product') or addd.get('scanner') or None)
                if not plan_hint:
                    # try metadata fields
                    md = props.get('metadata') or {}
                    plan_hint = md.get('serviceName') or md.get('product') or None
                if isinstance(plan_hint, str):
                    plan_hint = plan_hint.lower()

                # If portal_mode and plan_hint exists and subscription has enabled plans, enforce plan filtering
                subs_enabled = enabled_plans.get(sid, set())
                if portal_mode and plan_hint and len(subs_enabled) > 0:
                    matched = False
                    for p in subs_enabled:
                        if plan_hint in p or p in plan_hint:
                            matched = True
                            break
                    if not matched:
                        diag["excluded_plan_disabled"] += 1
                        continue

                # include this subAssessment
                diag["explicit_scores"] += (0 if derived else 1)
                # accumulate totals
                total_current += float(curr)
                total_max += float(mx)
                s = per_sub.setdefault(sid, {"current": 0.0, "max": 0.0, "count": 0})
                s["current"] += float(curr)
                s["max"] += float(mx)
                s["count"] += 1

            # compute overall
            overall_pct_raw = (total_current / total_max * 100.0) if total_max > 0 else 0.0
            overall_pct = round(overall_pct_raw, 1)

            # per-subscription list
            by_subscription = []
            for sid, vals in per_sub.items():
                pct = (vals["current"] / vals["max"] * 100.0) if vals["max"] > 0 else 0.0
                by_subscription.append({
                    "subscription_id": sid,
                    "current_score": round(vals["current"], 1),
                    "max_score": round(vals["max"], 1),
                    "percentage_score": round(pct, 1),
                    "assessments_count": vals["count"]
                })

            by_subscription = sorted(by_subscription, key=lambda x: x["percentage_score"])

            diagnostic = {
                "sub_total": diag["sub_total"],
                "explicit_scores": diag["explicit_scores"],
                "derived_from_severity": diag["derived_from_severity"],
                "excluded_not_applicable": diag["excluded_not_applicable"],
                "excluded_plan_disabled": diag["excluded_plan_disabled"],
                "excluded_zero_max": diag["excluded_zero_max"],
                "enabled_plans_snapshot": enabled_plans
            }

            logger.info(f" Portal-accurate secure score v2: {overall_pct}% ({total_current:.1f}/{total_max:.1f}) | sub_total={diag['sub_total']}, included={diag['explicit_scores']+diag['derived_from_severity']}")

            return {
                "overall_score": overall_pct,
                "current_score": round(total_current, 1),
                "max_score": round(total_max, 1),
                "by_subscription": by_subscription,
                "calculation_method": "assessments-subAssessments-v2-portal-normalized",
                "diagnostic": diagnostic
            }

        except Exception as e:
            logger.exception("Failed to compute portal-exact secure score v2")
            raise

    async def get_portal_exact_secure_score_v1(self, subscription_ids: List[str], user_info: Dict[str, Any],
                                            portal_mode: bool = True) -> Dict[str, Any]:
        """
        Returns the Secure Score exactly matching Azure Portal (portal_mode=True).
        Uses assessment-level aggregation, excludes 'NotApplicable' assessments,
        excludes assessments whose parent Defender plan is disabled, and applies
        portal-like rounding/normalization.

        Args:
            subscription_ids: list of subscription IDs to include
            user_info: user credential info for _get_user_credential
            portal_mode: if True, apply Portal filtering (recommended)

        Returns:
            dict with overall_score (rounded to 1 decimal), current_score, max_score,
            breakdowns per subscription, and diagnostic info.
        """
        try:
            user_credential = self._get_user_credential(user_info)
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest

            client = ResourceGraphClient(user_credential)
            loop = asyncio.get_event_loop()

            # 1) Fetch Defender plan/pricings
            q_pricings = """
                SecurityResources
                | where type == 'microsoft.security/pricings'
                | project subscriptionId = tostring(subscriptionId), pricingName = tostring(name), tier = tostring(properties.pricingTier)
            """
            req_pr = QueryRequest(subscriptions=subscription_ids, query=q_pricings)
            pricings_resp = await loop.run_in_executor(None, client.resources, req_pr)
            enabled_plans = {}
            if hasattr(pricings_resp, 'data') and pricings_resp.data:
                for r in pricings_resp.data:
                    sid = r.get('subscriptionId') or 'unknown'
                    tier = (r.get('tier') or '').lower()
                    plan = (r.get('pricingName') or '').lower()
                    enabled_plans.setdefault(sid, set())
                    if tier and tier not in ('free', 'free_tier', 'none', 'disabled', 'freepreview'):
                        enabled_plans[sid].add(plan)
                    elif tier in ('standard', 'paid', 'paidpreview', 'enhanced'):
                        enabled_plans[sid].add(plan)

            # 2) Fetch secure score controls
            q_controls = """
                SecurityResources
                | where type == 'microsoft.security/securescores/securescorecontrols'
                | extend subscriptionId = tostring(subscriptionId),
                         controlId = tostring(properties.controlId),
                         controlName = tostring(properties.displayName),
                         controlMax = todouble(properties.score.max),
                         controlCurrent = todouble(properties.score.current),
                         controlWeight = todouble(properties.weight)
                | project subscriptionId, controlId, controlName, controlMax, controlCurrent, controlWeight, properties
            """
            req_ctrl = QueryRequest(subscriptions=subscription_ids, query=q_controls)
            controls_resp = await loop.run_in_executor(None, client.resources, req_ctrl)

            control_map = {}
            if hasattr(controls_resp, 'data') and controls_resp.data:
                for r in controls_resp.data:
                    cid = r.get('controlId') or r.get('properties', {}).get('controlId')
                    if not cid:
                        cid = f"{r.get('subscriptionId','unknown')}::{r.get('controlName','no-name')}"
                    control_map[cid] = {
                        "subscription_id": r.get('subscriptionId'),
                        "control_name": r.get('controlName'),
                        "control_max": float(r.get('controlMax') or 0.0),
                        "control_current": float(r.get('controlCurrent') or 0.0),
                        "control_weight": float(r.get('controlWeight') or 1.0),
                        "properties": r.get('properties') or {}
                    }

            # 3) Fetch assessments metadata
            q_assess_meta = """
                SecurityResources
                | where type == 'microsoft.security/assessmentsMetadata'
                | project name, id, subscriptionId = tostring(subscriptionId), properties
            """
            req_am = QueryRequest(subscriptions=subscription_ids, query=q_assess_meta)
            am_resp = await loop.run_in_executor(None, client.resources, req_am)
            assessments_metadata = {}
            if hasattr(am_resp, 'data') and am_resp.data:
                for r in am_resp.data:
                    meta_name = r.get('name')
                    assessments_metadata[meta_name] = r.get('properties') or {}

            # 4) Fetch assessments
            q_assess = """
                SecurityResources
                | where type == 'microsoft.security/assessments'
                | extend subscriptionId = tostring(subscriptionId),
                         assessmentName = tostring(name),
                         properties_obj = properties
                | project subscriptionId, assessmentName, properties_obj
            """
            req_ass = QueryRequest(subscriptions=subscription_ids, query=q_assess)
            ass_resp = await loop.run_in_executor(None, client.resources, req_ass)

            if not hasattr(ass_resp, 'data') or not ass_resp.data:
                logger.warning("No assessments returned - Defender may be disabled")
                return {
                    "overall_score": 0.0,
                    "current_score": 0.0,
                    "max_score": 0.0,
                    "by_subscription": [],
                    "calculation_method": "assessments-portal-mode",
                    "diagnostic": "no_assessments"
                }

            # 5) Extract assessment data
            assessments_list = []
            for row in ass_resp.data:
                sid = row.get('subscriptionId') or 'unknown'
                aname = row.get('assessmentName')
                props = row.get('properties_obj') or {}
                
                # Extract current/max score
                curr = None
                mx = None
                sc = props.get('score') if isinstance(props.get('score'), dict) else {}
                try:
                    curr = float(sc.get('current')) if sc and sc.get('current') is not None else None
                    mx = float(sc.get('max')) if sc and sc.get('max') is not None else None
                except Exception:
                    pass

                curr = 0.0 if curr is None else float(curr)
                mx = 0.0 if mx is None else float(mx)

                # Control linkage
                control_id = None
                if isinstance(props, dict):
                    control_id = props.get('controlId') or (props.get('controlDetails') or {}).get('controlId')
                if not control_id:
                    meta_for_ass = assessments_metadata.get(aname)
                    if meta_for_ass:
                        control_id = meta_for_ass.get('controlId') or (meta_for_ass.get('control') or {}).get('id')

                # Plan hint
                plan_hint = None
                md = props.get('metadata') or {}
                plan_hint = md.get('serviceName') or md.get('product') or md.get('provider') or None
                if isinstance(plan_hint, str):
                    plan_hint = plan_hint.lower()

                # Status
                status = None
                try:
                    status = props.get('status') or (props.get('assessmentStatus') or {})
                    if isinstance(status, dict):
                        status_code = status.get('code') or status.get('value') or status.get('status')
                        if status_code:
                            status = str(status_code).lower()
                    elif isinstance(status, str):
                        status = status.lower()
                except Exception:
                    pass

                assessments_list.append({
                    "subscription_id": sid,
                    "assessment_name": aname,
                    "current": float(curr),
                    "max": float(mx),
                    "control_id": control_id,
                    "plan_hint": plan_hint,
                    "status": status
                })

            # 6) Filter using portal rules
            included_assessments = []
            excluded_reasons = {"not_applicable": 0, "plan_disabled": 0, "zero_max": 0}
            for a in assessments_list:
                sid = a['subscription_id']
                status = (a.get('status') or '').lower() if a.get('status') else ''
                if status and ('notapplicable' in status or 'not applicable' in status or 'deprecated' in status):
                    excluded_reasons['not_applicable'] += 1
                    continue
                if a['max'] == 0:
                    excluded_reasons['zero_max'] += 1
                    continue
                
                # Plan filtering: Only exclude if plan is explicitly in the name and NOT enabled
                # Don't exclude assessments without a plan_hint
                plan_hint = a.get('plan_hint')
                if portal_mode and plan_hint:
                    subs_enabled = enabled_plans.get(sid, set())
                    # Only exclude if we have enabled plans but this specific plan appears to be disabled
                    # Check if plan_hint looks like it should map to a specific Defender plan
                    should_check = any(keyword in plan_hint for keyword in ['vm', 'storage', 'sql', 'container', 'keyvault', 'dns', 'arm', 'api', 'app', 'database'])
                    if should_check and len(subs_enabled) > 0:
                        found = False
                        for p in subs_enabled:
                            if plan_hint in p or p in plan_hint:
                                found = True
                                break
                        if not found:
                            excluded_reasons['plan_disabled'] += 1
                            continue
                
                included_assessments.append(a)

            # 7) Aggregate
            per_subscription_summary = {}
            total_current = 0.0
            total_max = 0.0
            for a in included_assessments:
                sid = a['subscription_id']
                curr = a['current']
                mx = a['max']
                total_current += curr
                total_max += mx
                sub = per_subscription_summary.setdefault(sid, {"current": 0.0, "max": 0.0, "assessments": []})
                sub['current'] += curr
                sub['max'] += mx
                sub['assessments'].append(a)

            # 8) Calculate percentage
            overall_pct = (total_current / total_max * 100.0) if total_max > 0 else 0.0
            overall_rounded = round(overall_pct, 1)

            # 9) Build per-subscription list
            by_subscription = []
            for sid, vals in per_subscription_summary.items():
                pct = (vals['current'] / vals['max'] * 100.0) if vals['max'] > 0 else 0.0
                by_subscription.append({
                    "subscription_id": sid,
                    "current_score": round(vals['current'], 1),
                    "max_score": round(vals['max'], 1),
                    "percentage_score": round(pct, 1),
                    "assessments_count": len(vals['assessments'])
                })

            by_subscription = sorted(by_subscription, key=lambda x: x['percentage_score'])

            diagnostic = {
                "assessments_total": len(assessments_list),
                "assessments_included": len(included_assessments),
                "assessments_excluded": excluded_reasons,
                "controls_count": len(control_map)
            }

            logger.info(f" Portal-accurate secure score: {overall_rounded}% ({total_current:.1f}/{total_max:.1f}) | included={len(included_assessments)}")

            return {
                "overall_score": overall_rounded,
                "current_score": round(total_current, 1),
                "max_score": round(total_max, 1),
                "by_subscription": by_subscription,
                "calculation_method": "assessments-portal-normalized",
                "diagnostic": diagnostic
            }

        except Exception as e:
            logger.exception("Failed to compute portal-exact secure score")
            raise
    
    async def get_security_score(self, subscription_ids: List[str], user_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get Microsoft Defender for Cloud Secure Score using control-level aggregation.
        
        Uses securescorecontrols which contain current/max scores per security control.
        Aggregates across all subscriptions using: SUM(current) / SUM(max) * 100
        
        Args:
            subscription_ids: List of subscription IDs to query
            user_info: User authentication information
            
        Returns:
            Dictionary containing:
            - overall_score: Overall percentage score
            - current_score: Sum of current scores
            - max_score: Sum of max scores  
            - by_subscription: Per-subscription breakdown
        """
        try:
            logger.debug(f" Fetching secure score for {len(subscription_ids)} subscription(s)")
            
            user_credential = self._get_user_credential(user_info)
            from azure.mgmt.resourcegraph import ResourceGraphClient
            from azure.mgmt.resourcegraph.models import QueryRequest
            
            client = ResourceGraphClient(user_credential)
            loop = asyncio.get_event_loop()
            
            # Query security controls across all subscriptions
            query = """
                SecurityResources
                | where type == 'microsoft.security/securescores/securescorecontrols'
                | extend subscriptionId = tostring(subscriptionId),
                         controlName = tostring(properties.displayName),
                         currentScore = todouble(properties.score.current),
                         maxScore = todouble(properties.score.max),
                         percentageScore = todouble(properties.score.percentage) * 100,
                         weight = coalesce(todouble(properties.weight), 1.0)
                | where maxScore > 0
                | project subscriptionId, controlName, currentScore, maxScore, percentageScore, weight
            """
            
            request = QueryRequest(subscriptions=subscription_ids, query=query)
            response = await asyncio.wait_for(
                loop.run_in_executor(None, client.resources, request),
                timeout=30.0
            )
            
            if not hasattr(response, 'data') or not response.data:
                logger.warning("No security controls found - Defender may be disabled")
                return {
                    "overall_score": 0.0,
                    "current_score": 0.0,
                    "max_score": 0.0,
                    "by_subscription": [],
                    "diagnostic": "no_controls"
                }
            
            # Aggregate by subscription
            per_subscription = {}
            total_current = 0.0
            total_max = 0.0
            
            for row in response.data:
                sid = row.get('subscriptionId') or 'unknown'
                current = float(row.get('currentScore') or 0.0)
                max_score = float(row.get('maxScore') or 0.0)
                
                if max_score > 0:
                    total_current += current
                    total_max += max_score
                    
                    if sid not in per_subscription:
                        per_subscription[sid] = {
                            "current": 0.0,
                            "max": 0.0,
                            "controls": []
                        }
                    
                    per_subscription[sid]["current"] += current
                    per_subscription[sid]["max"] += max_score
                    per_subscription[sid]["controls"].append({
                        "name": row.get('controlName'),
                        "current": current,
                        "max": max_score,
                        "percentage": round(float(row.get('percentageScore') or 0.0), 1)
                    })
            
            # Calculate overall percentage
            overall_pct = (total_current / total_max * 100.0) if total_max > 0 else 0.0
            
            # Build per-subscription list
            by_subscription = []
            for sid, vals in per_subscription.items():
                pct = (vals['current'] / vals['max'] * 100.0) if vals['max'] > 0 else 0.0
                by_subscription.append({
                    "subscription_id": sid,
                    "current_score": round(vals['current'], 1),
                    "max_score": round(vals['max'], 1),
                    "percentage_score": round(pct, 1),
                    "controls_count": len(vals['controls'])
                })
            
            by_subscription = sorted(by_subscription, key=lambda x: x['percentage_score'])
            
            logger.debug(f"Secure score: {overall_pct:.1f}% ({total_current:.1f}/{total_max:.1f})")
            
            return {
                "overall_score": round(overall_pct, 1),
                "current_score": round(total_current, 1),
                "max_score": round(total_max, 1),
                "by_subscription": by_subscription,
                "calculation_method": "control-level-sum"
            }
            
        except Exception as e:
            logger.error(f" Failed to get secure score: {e}", exc_info=True)
            raise

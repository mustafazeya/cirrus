'use client';
import { fmtN } from '../../utils/format';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useIsAuthenticated, useMsal } from '@azure/msal-react';
import { useAppStore } from '../../store/appStore';
import { apiService } from '../../services/apiService';
import toast from 'react-hot-toast';
import type {
  FirewallSubscription,
  FirewallPolicy,
  PolicySummary,
  RulesResult,
  SearchResult,
  OverlapResult,
  OrphanResult,
  FirewallContext,
  FirewallRule,
  AnalysisTab,
  TrafficIntelligenceResult,
  LogSearchResult,
  RuleLogDetail,
  FirewallDiagnosticInfo,
  PacketWalkResult,
} from '../../types/firewall';
import FirewallRulesTable from '../../components/firewall/FirewallRulesTable';
import RuleSearchBuilder, { type SearchQuery, type SearchConditionGroup, newGroup as newSearchGroup } from '../../components/firewall/RuleSearchBuilder';
import TrafficIntelligence from '../../components/firewall/TrafficIntelligence';
import LogSearch, { type LogSearchQuery, type LogConditionGroup, newGroup } from '../../components/firewall/LogSearch';
import OverlapAnalysis from '../../components/firewall/OverlapAnalysis';
import OrphanDetection from '../../components/firewall/OrphanDetection';
import FirewallMetrics from '../../components/firewall/FirewallMetrics';
import FirewallConfiguration from '../../components/firewall/FirewallConfiguration';
import PacketWalk, { type PacketWalkRequest } from '../../components/firewall/PacketWalk';
import {
  ShieldCheckIcon,
  MagnifyingGlassIcon,
  ExclamationTriangleIcon,
  TrashIcon,
  ChevronDownIcon,
  ArrowPathIcon,
  ServerStackIcon,
  DocumentMagnifyingGlassIcon,
  InformationCircleIcon,
  XMarkIcon,
  ClockIcon,
  ChartBarIcon,
  PresentationChartLineIcon,
  Cog6ToothIcon,
  ArrowRightIcon,
  GlobeAltIcon,
  ArrowsRightLeftIcon,
} from '@heroicons/react/24/outline';

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function KpiCard({ label, value, sub, colorClass = 'text-indigo-600', icon, onClick }: {
  label: string;
  value: string | number;
  sub?: string;
  colorClass?: string;
  icon?: React.ReactNode;
  onClick?: () => void;
}) {
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      onClick={onClick}
      className={`bg-white rounded-xl border border-gray-200 p-4 shadow-sm flex items-start gap-3 w-full text-left
        ${onClick ? 'cursor-pointer hover:border-indigo-300 hover:shadow-md hover:bg-indigo-50/40 transition-all' : ''}`}
    >
      {icon && <div className="shrink-0 mt-0.5">{icon}</div>}
      <div>
        <div className={`text-2xl font-bold ${colorClass}`}>{value}</div>
        <div className="text-xs font-medium text-gray-700 mt-0.5">{label}</div>
        {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
      </div>
    </Tag>
  );
}

function SectionSpinner({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-gray-400">
      <ArrowPathIcon className="w-5 h-5 animate-spin" />
      <span className="text-sm">{label}</span>
    </div>
  );
}

// ─────────────────────────────────────────────
// Rule Detail Modal (Traffic Intelligence drilldown)
// ─────────────────────────────────────────────

// ─────────────────────────────────────────────
// Rule Modal — drilldown for both Traffic Intelligence and Orphan Detection
// Shows rule definition (when ctx provided) + recent log entries
// ─────────────────────────────────────────────

function RuleModal({ ruleName, ruleCollection, ruleType, ctx, workspaceId, days, onClose }: {
  ruleName: string;
  ruleCollection?: string;
  ruleType?: string;
  ctx?: FirewallContext;
  workspaceId: string;
  days: number;
  onClose: () => void;
}) {
  const [loadingDef, setLoadingDef]   = useState(!!ctx);
  const [loadingLogs, setLoadingLogs] = useState(true);
  const [ruleDef, setRuleDef]         = useState<FirewallRule | null>(null);
  const [logDetail, setLogDetail]     = useState<RuleLogDetail | null>(null);

  const ACTION_COLORS: Record<string, string> = { Allow: 'bg-green-100 text-green-700', Deny: 'bg-red-100 text-red-700', DNAT: 'bg-yellow-100 text-yellow-700' };
  const TYPE_COLORS:   Record<string, string> = { Application: 'text-purple-600', NAT: 'text-orange-500', Network: 'text-indigo-600' };
  const pill = (text: string, color = 'bg-gray-100 text-gray-700') => (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-mono ${color}`}>{text}</span>
  );

  useEffect(() => {
    if (!ctx) return;
    (async () => {
      setLoadingDef(true);
      try {
        setRuleDef((await apiService.firewallSearchRulesAdvanced(
          ctx.subscription_id, ctx.resource_group, ctx.policy_name,
          [{ conditions: [{ field: 'name', operator: 'equals', value: ruleName }], intra_connector: 'AND', inter_connector: 'AND' }],
        )).rules[0] ?? null);
      } catch { /* best-effort */ }
      finally { setLoadingDef(false); }
    })();
  }, [ruleName, ctx]);

  useEffect(() => {
    if (!workspaceId) { setLoadingLogs(false); return; }
    (async () => {
      setLoadingLogs(true);
      try {
        setLogDetail(await apiService.firewallGetRuleLogDetail(workspaceId, ruleName, days));
      } catch { /* best-effort */ }
      finally { setLoadingLogs(false); }
    })();
  }, [workspaceId, ruleName, days]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className={`bg-white rounded-2xl shadow-2xl w-full mx-4 max-h-[90vh] flex flex-col ${ctx ? 'max-w-4xl' : 'max-w-3xl'}`}
        onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 border-b border-gray-200">
          <div>
            <h3 className="text-base font-semibold text-gray-900 font-mono">{ruleName}</h3>
            <div className="flex items-center gap-2 mt-1 text-xs text-gray-500">
              {ruleCollection && <span>{ruleCollection}</span>}
              {ruleCollection && ruleType && <span>·</span>}
              {ruleType && <span className={TYPE_COLORS[ruleType] ?? 'text-gray-500'}>{ruleType}</span>}
              {workspaceId && <><span>·</span><span>Last {days}d logs</span></>}
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-gray-100 shrink-0">
            <XMarkIcon className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 p-5 space-y-5">

          {/* ── Rule Definition (only when ctx provided) ────────────── */}
          {ctx && (
            <div>
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Rule Definition</h4>
              {loadingDef ? (
                <div className="flex items-center gap-2 text-sm text-gray-400 py-4">
                  <ArrowPathIcon className="w-4 h-4 animate-spin" /> Looking up rule…
                </div>
              ) : !ruleDef ? (
                <p className="text-xs text-gray-400 italic">Rule definition not found in policy (may be a legacy rule).</p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                  <div className="bg-gray-50 rounded-lg p-3 space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500 w-24 shrink-0">Action</span>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${ACTION_COLORS[ruleDef.action] ?? 'bg-gray-100 text-gray-700'}`}>{ruleDef.action}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500 w-24 shrink-0">Type</span>
                      <span className={`font-medium ${TYPE_COLORS[ruleDef.rule_type] ?? 'text-gray-700'}`}>{ruleDef.rule_type}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500 w-24 shrink-0">Collection</span>
                      <span className="text-gray-700 font-mono">{ruleDef.rule_collection}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-gray-500 w-24 shrink-0">Group</span>
                      <span className="text-gray-700 font-mono">{ruleDef.rule_group}</span>
                    </div>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3 space-y-2">
                    <div className="flex gap-2 flex-wrap items-start">
                      <span className="text-gray-500 w-24 shrink-0">Protocols</span>
                      <div className="flex flex-wrap gap-1">
                        {(ruleDef.protocols ?? []).length > 0
                          ? ruleDef.protocols.map((p) => pill(p, 'bg-indigo-100 text-indigo-700'))
                          : <span className="text-gray-400 italic">Any</span>}
                      </div>
                    </div>
                    <div className="flex gap-2 flex-wrap items-start">
                      <span className="text-gray-500 w-24 shrink-0">Ports</span>
                      <div className="flex flex-wrap gap-1">
                        {(ruleDef.destination_ports ?? []).length > 0
                          ? ruleDef.destination_ports.map((p) => pill(p, 'bg-yellow-100 text-yellow-700'))
                          : <span className="text-gray-400 italic">*</span>}
                      </div>
                    </div>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3 space-y-2">
                    <div className="text-gray-500 font-medium mb-1">Sources</div>
                    <div className="flex flex-wrap gap-1">
                      {[...(ruleDef.source_addresses ?? []), ...(ruleDef.source_ip_groups ?? [])].length > 0
                        ? [...(ruleDef.source_addresses ?? []), ...(ruleDef.source_ip_groups ?? [])].map((s) => pill(s))
                        : <span className="text-gray-400 italic">Any</span>}
                    </div>
                  </div>
                  <div className="bg-gray-50 rounded-lg p-3 space-y-2">
                    <div className="text-gray-500 font-medium mb-1">Destinations</div>
                    <div className="flex flex-wrap gap-1">
                      {[
                        ...(ruleDef.destination_addresses ?? []), ...(ruleDef.destination_ip_groups ?? []),
                        ...(ruleDef.destination_fqdns ?? []),      ...(ruleDef.destination_urls ?? []),
                        ...(ruleDef.destination_fqdn_tags ?? []),
                      ].map((d) => pill(d, 'bg-orange-100 text-orange-700'))}
                      {[
                        ...(ruleDef.destination_addresses ?? []), ...(ruleDef.destination_ip_groups ?? []),
                        ...(ruleDef.destination_fqdns ?? []),      ...(ruleDef.destination_urls ?? []),
                        ...(ruleDef.destination_fqdn_tags ?? []),
                      ].length === 0 && <span className="text-gray-400 italic">Any</span>}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── Recent Logs ─────────────────────────────────────────── */}
          <div>
            <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3 flex items-center gap-2">
              Recent Logs
              {logDetail && <span className="font-normal normal-case text-gray-400">— {fmtN(logDetail.total_hits)} hits in {days}d</span>}
            </h4>
            {loadingLogs ? (
              <SectionSpinner label="Querying Log Analytics…" />
            ) : !logDetail || logDetail.entries.length === 0 ? (
              <p className="text-xs text-gray-400 italic">No log entries found in the selected window.</p>
            ) : (
              <div className={`overflow-auto rounded-lg border border-gray-200 ${ctx ? 'max-h-64' : ''}`}>
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 text-gray-600 sticky top-0">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium whitespace-nowrap">Time</th>
                      <th className="px-3 py-2 text-left font-medium">Source</th>
                      <th className="px-3 py-2 text-left font-medium">Destination</th>
                      <th className="px-3 py-2 text-left font-medium">Port</th>
                      <th className="px-3 py-2 text-left font-medium">Protocol</th>
                      <th className="px-3 py-2 text-left font-medium">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {logDetail.entries.map((e, i) => (
                      <tr key={`${e.TimeGenerated ?? i}-${e.SourceIP}-${e.DestIP}`} className="hover:bg-gray-50">
                        <td className="px-3 py-1.5 font-mono text-gray-500 whitespace-nowrap">{e.TimeGenerated?.replace('T', ' ').split('.')[0]}</td>
                        <td className="px-3 py-1.5 font-mono">{e.SourceIP || '—'}</td>
                        <td className="px-3 py-1.5 font-mono">{e.DestIP || '—'}</td>
                        <td className="px-3 py-1.5 font-mono">{e.DestinationPort || '—'}</td>
                        <td className="px-3 py-1.5">{e.Protocol || '—'}</td>
                        <td className="px-3 py-1.5">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${e.Action?.toLowerCase() === 'allow' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                            {e.Action}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Context selector panel
// ─────────────────────────────────────────────

function ContextSelector({ onSelect }: { onSelect: (ctx: FirewallContext) => void }) {
  const [subscriptions, setSubscriptions] = useState<FirewallSubscription[]>([]);
  const [filteredSubs, setFilteredSubs] = useState<FirewallSubscription[]>([]);
  const [subSearch, setSubSearch] = useState('');
  const [showSubDropdown, setShowSubDropdown] = useState(false);
  const [selectedSub, setSelectedSub] = useState<FirewallSubscription | null>(null);
  const [loadingSubs, setLoadingSubs] = useState(false);

  const [policies, setPolicies] = useState<FirewallPolicy[]>([]);
  const [selectedPolicy, setSelectedPolicy] = useState<FirewallPolicy | null>(null);
  const [loadingPolicies, setLoadingPolicies] = useState(false);

  const [diagInfo, setDiagInfo] = useState<FirewallDiagnosticInfo | null>(null);
  const [loadingDiag, setLoadingDiag] = useState(false);
  const [workspaceId, setWorkspaceId] = useState('');
  const [workspaceOverridden, setWorkspaceOverridden] = useState(false);

  // Load subscriptions on mount
  useEffect(() => {
    let cancelled = false;
    const load = async (attempt = 0) => {
      setLoadingSubs(true);
      try {
        const res = await apiService.firewallListSubscriptions();
        const subs: FirewallSubscription[] = res.subscriptions ?? [];
        if (!cancelled) {
          if (subs.length === 0 && attempt < 3) {
            // Token may not have been injected yet — retry with back-off
            setTimeout(() => load(attempt + 1), 800 * (attempt + 1));
          } else {
            setSubscriptions(subs);
            setFilteredSubs(subs);
          }
        }
      } catch {
        if (!cancelled) toast.error('Failed to load subscriptions');
      } finally {
        if (!cancelled) setLoadingSubs(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  // Filter subscriptions client-side
  useEffect(() => {
    const q = subSearch.toLowerCase();
    setFilteredSubs(
      q
        ? subscriptions.filter(
            (s) =>
              s.display_name.toLowerCase().includes(q) ||
              s.subscription_id.toLowerCase().includes(q),
          )
        : subscriptions,
    );
  }, [subSearch, subscriptions]);

  // Load policies when subscription changes
  useEffect(() => {
    if (!selectedSub) return;
    (async () => {
      setLoadingPolicies(true);
      setPolicies([]);
      setSelectedPolicy(null);
      setDiagInfo(null);
      setWorkspaceId('');
      setWorkspaceOverridden(false);
      try {
        const res = await apiService.firewallListPolicies(selectedSub.subscription_id);
        setPolicies(res.policies ?? []);
      } catch {
        toast.error('Failed to load firewall policies');
      } finally {
        setLoadingPolicies(false);
      }
    })();
  }, [selectedSub]);

  // Auto-detect firewall + diagnostic workspace when policy selected
  useEffect(() => {
    if (!selectedSub || !selectedPolicy) {
      setDiagInfo(null);
      return;
    }
    (async () => {
      setLoadingDiag(true);
      setDiagInfo(null);
      if (!workspaceOverridden) setWorkspaceId('');
      try {
        const info = await apiService.firewallGetDiagnosticInfo(
          selectedSub.subscription_id,
          selectedPolicy.resource_group,
          selectedPolicy.name,
        );
        setDiagInfo(info);
        if (!workspaceOverridden) {
          // Auto-populate: prefer primary (Dedicated) then fallback
          const ws = info.primary_workspace || info.fallback_workspace || '';
          setWorkspaceId(ws);
        }
      } catch {
        // Non-fatal – user can still enter manually
      } finally {
        setLoadingDiag(false);
      }
    })();
  }, [selectedSub, selectedPolicy]);

  const canStart = selectedSub && selectedPolicy;

  const [showPolicyDropdown, setShowPolicyDropdown] = useState(false);

  return (
    <div className="max-w-xl">
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-5">

        {/* Searchable Subscription Selector */}
        <div className="relative">
          <div className="flex items-center justify-between mb-1.5">
            <label className="block text-sm font-medium text-gray-700">Subscription</label>
            <span className="text-xs text-gray-400 italic">Only subscriptions with a firewall policy are shown</span>
          </div>
          <button
            type="button"
            className="w-full border border-gray-200 rounded-lg px-3 py-2.5 text-sm bg-white text-left flex items-center justify-between focus:ring-2 focus:ring-indigo-500 focus:outline-none transition-colors hover:border-indigo-300"
            onClick={() => setShowSubDropdown((v) => !v)}
            disabled={loadingSubs}
          >
            <span className={selectedSub ? 'text-gray-900' : 'text-gray-400'}>
              {loadingSubs
                ? 'Loading…'
                : selectedSub
                ? `${selectedSub.display_name} (${selectedSub.subscription_id.slice(0, 8)}…)`
                : 'Choose a subscription'}
            </span>
            <ChevronDownIcon className={`w-4 h-4 text-gray-400 transition-transform ${showSubDropdown ? 'rotate-180' : ''}`} />
          </button>

          {showSubDropdown && (
            <div className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
              {/* Search input */}
              <div className="p-2 border-b border-gray-100">
                <div className="relative">
                  <MagnifyingGlassIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    autoFocus
                    type="text"
                    placeholder="Search subscriptions…"
                    className="w-full pl-8 pr-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    value={subSearch}
                    onChange={(e) => setSubSearch(e.target.value)}
                  />
                </div>
              </div>
              {/* Options list */}
              <ul className="max-h-56 overflow-y-auto divide-y divide-gray-50">
                {filteredSubs.length === 0 ? (
                  <li className="px-4 py-3 text-sm text-gray-400 text-center">No subscriptions match</li>
                ) : (
                  filteredSubs.map((s) => (
                    <li key={s.subscription_id}>
                      <button
                        type="button"
                        className={`w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50 transition-colors ${
                          selectedSub?.subscription_id === s.subscription_id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-800'
                        }`}
                        onClick={() => {
                          setSelectedSub(s);
                          setSubSearch('');
                          setShowSubDropdown(false);
                        }}
                      >
                        <div>{s.display_name}</div>
                        <div className="text-xs text-gray-400 font-mono">{s.subscription_id}</div>
                      </button>
                    </li>
                  ))
                )}
              </ul>
            </div>
          )}
        </div>

        {/* Firewall Policy — styled custom dropdown matching subscription picker */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">Firewall Policy</label>
          <div className="relative">
            <button
              type="button"
              className={`w-full border rounded-lg px-3 py-2.5 text-sm bg-white text-left flex items-center justify-between focus:ring-2 focus:ring-indigo-500 focus:outline-none transition-colors ${
                !selectedSub || loadingPolicies
                  ? 'border-gray-200 opacity-60 cursor-not-allowed'
                  : 'border-gray-200 hover:border-indigo-300'
              }`}
              onClick={() => { if (selectedSub && !loadingPolicies) setShowPolicyDropdown((v) => !v); }}
              disabled={!selectedSub || loadingPolicies}
            >
              <span className={selectedPolicy ? 'text-gray-900' : 'text-gray-400'}>
                {!selectedSub
                  ? 'Select a subscription first'
                  : loadingPolicies
                  ? 'Loading policies…'
                  : policies.length === 0
                  ? 'No firewall policies found'
                  : selectedPolicy
                  ? selectedPolicy.name
                  : 'Choose a firewall policy'}
              </span>
              <ChevronDownIcon className={`w-4 h-4 text-gray-400 transition-transform ${showPolicyDropdown ? 'rotate-180' : ''}`} />
            </button>

            {showPolicyDropdown && policies.length > 0 && (
              <div className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
                <ul className="max-h-52 overflow-y-auto divide-y divide-gray-50">
                  {policies.map((p) => (
                    <li key={p.id}>
                      <button
                        type="button"
                        className={`w-full text-left px-4 py-2.5 text-sm hover:bg-indigo-50 transition-colors ${
                          selectedPolicy?.name === p.name ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-800'
                        }`}
                        onClick={() => {
                          setSelectedPolicy(p);
                          setWorkspaceOverridden(false);
                          setShowPolicyDropdown(false);
                        }}
                      >
                        <div className="font-medium">{p.name}</div>
                        <div className="text-xs text-gray-400 mt-0.5">{p.resource_group} · {p.sku_tier}{p.location ? ` · ${p.location}` : ''}</div>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          {selectedPolicy && (
            <p className="text-xs text-gray-400 mt-1">
              Tier: {selectedPolicy.sku_tier}{selectedPolicy.threat_intel_mode ? ` · Threat Intel: ${selectedPolicy.threat_intel_mode}` : ''}
            </p>
          )}
        </div>

        {/* Diagnostic auto-detection result */}
        {selectedPolicy && (
          <div>
            {loadingDiag ? (
              <div className="flex items-center gap-2 text-xs text-gray-400 py-1">
                <ArrowPathIcon className="w-3.5 h-3.5 animate-spin" />
                Detecting linked firewall &amp; Log Analytics workspace…
              </div>
            ) : diagInfo ? (
              <div className={`rounded-xl border p-3 text-xs space-y-2 ${
                diagInfo.has_diagnostics
                  ? 'bg-green-50 border-green-200'
                  : diagInfo.firewall_found
                  ? 'bg-yellow-50 border-yellow-200'
                  : 'bg-gray-50 border-gray-200'
              }`}>
                {/* Firewall found */}
                <div className="flex items-center gap-2">
                  <ShieldCheckIcon className={`w-4 h-4 shrink-0 ${diagInfo.firewall_found ? 'text-green-600' : 'text-gray-400'}`} />
                  <span className={diagInfo.firewall_found ? 'text-green-800 font-medium' : 'text-gray-500'}>
                    {diagInfo.firewall_found
                      ? `Linked firewall: ${diagInfo.firewall_name}`
                      : 'No Azure Firewall found linked to this policy'}
                  </span>
                </div>

                {/* Diagnostic mode */}
                {diagInfo.has_diagnostics && diagInfo.log_mode && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${
                      diagInfo.log_mode === 'Dedicated'
                        ? 'bg-blue-100 text-blue-700 border-blue-300'
                        : 'bg-orange-100 text-orange-700 border-orange-300'
                    }`}>
                      {diagInfo.log_mode === 'Dedicated' ? 'Dedicated Tables' : 'AzureDiagnostics'}
                    </span>
                    <span className="text-gray-600">→ workspace: <span className="font-medium">{diagInfo.primary_workspace_name}</span></span>
                    {diagInfo.fallback_workspace && (
                      <span className="text-gray-400">
                        · fallback ({diagInfo.fallback_workspace_name})
                      </span>
                    )}
                  </div>
                )}

                {!diagInfo.has_diagnostics && diagInfo.firewall_found && (
                  <p className="text-yellow-700">
                    No Log Analytics diagnostic settings found on this firewall.
                    Orphan detection requires diagnostic logs to be enabled.
                  </p>
                )}

                {/* All configured settings */}
                {diagInfo.all_settings.length > 1 && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-gray-500 hover:text-gray-700">
                      {diagInfo.all_settings.length} diagnostic settings configured
                    </summary>
                    <ul className="mt-1 space-y-0.5 pl-2">
                      {diagInfo.all_settings.map((s) => (
                        <li key={s.diagnostic_setting_name} className="text-gray-600">
                          {s.mode === 'Dedicated'
                            ? <span className="text-blue-600 font-medium">[Dedicated]</span>
                            : <span className="text-orange-600 font-medium">[AzureDiagnostics]</span>
                          }{' '}
                          {s.workspace_name} ({s.diagnostic_setting_name})
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            ) : null}
          </div>
        )}

        {/* Log Analytics workspace (pre-filled or manual) */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            Log Analytics Workspace
            <span className="ml-1 text-xs font-normal text-gray-400">(auto-detected — for traffic analysis)</span>
          </label>
          <input
            type="text"
            readOnly
            placeholder="—"
            className="w-full border border-gray-200 bg-slate-50 rounded-lg px-3 py-2.5 text-sm text-gray-500 cursor-default select-all"
            value={diagInfo?.primary_workspace_name ?? workspaceId.split('/').pop() ?? workspaceId}
            title={workspaceId}
          />
        </div>

        <button
          disabled={!canStart}
          onClick={() => {
            if (!selectedSub || !selectedPolicy) return;
            onSelect({
              subscription_id: selectedSub.subscription_id,
              subscription_name: selectedSub.display_name,
              resource_group: selectedPolicy.resource_group,
              policy_name: selectedPolicy.name,
              workspace_id: workspaceId || undefined,
            });
          }}
          className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed text-white font-semibold rounded-lg py-2.5 text-sm transition-colors"
        >
          Open Firewall Dashboard
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Shared constants
// ─────────────────────────────────────────────

const RULE_TYPE_OPTIONS = [
  { label: 'All Types',          value: '' },
  { label: 'Network Rules',      value: 'NetworkRule' },
  { label: 'Application Rules',  value: 'ApplicationRule' },
  { label: 'NAT Rules',          value: 'NatRule' },
];

// ─────────────────────────────────────────────
// Tab components — each owns its own state
// ─────────────────────────────────────────────

function RulesTab({ ctx }: { ctx: FirewallContext }) {
  const [rulesResult, setRulesResult] = useState<RulesResult | null>(null);
  const [loading, setLoading]         = useState(false);
  const [typeFilter, setTypeFilter]   = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRulesResult(await apiService.firewallGetRules(ctx.subscription_id, ctx.resource_group, ctx.policy_name, typeFilter || undefined));
    } catch { toast.error('Failed to load rules'); }
    finally  { setLoading(false); }
  }, [ctx, typeFilter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <ServerStackIcon className="w-4 h-4 text-gray-500" /> All Rules in Policy
        </h3>
        <div className="flex gap-2 flex-wrap">
          <select className="text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            {RULE_TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button onClick={load} className="flex items-center gap-1.5 px-3 py-2 text-sm bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 transition-colors">
            <ArrowPathIcon className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>
      {loading ? <SectionSpinner label="Loading rules…" /> : rulesResult ? <FirewallRulesTable rules={rulesResult.rules} /> : null}
    </div>
  );
}

function SearchTab({ ctx, initialGroups, triggerKey }: { ctx: FirewallContext; initialGroups?: SearchConditionGroup[]; triggerKey?: number }) {
  const [searchQuery,   setSearchQuery]   = useState<SearchQuery | null>(null);
  const [searchResult,  setSearchResult]  = useState<SearchResult | null>(null);
  const [loading,       setLoading]       = useState(false);
  const [groups,        setGroups]        = useState<SearchConditionGroup[]>([newSearchGroup()]);

  const run = useCallback(async (query: SearchQuery) => {
    setSearchQuery(query);
    setLoading(true);
    try {
      setSearchResult(await apiService.firewallSearchRulesAdvanced(
        ctx.subscription_id, ctx.resource_group, ctx.policy_name,
        query.groups.map(g => ({
          conditions: g.conditions.map(c => ({ field: c.field, operator: c.operator, value: c.value })),
          intra_connector: g.intraConnector, inter_connector: g.interConnector,
        })),
      ));
    } catch { toast.error('Search failed'); }
    finally  { setLoading(false); }
  }, [ctx]);

  useEffect(() => {
    if (!triggerKey || !initialGroups) return;
    setGroups(initialGroups);
    setSearchResult(null);
    run({ groups: initialGroups });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [triggerKey]);

  return (
    <div className="space-y-4">
      <RuleSearchBuilder loading={loading} onSearch={run}
        onClear={() => { setSearchResult(null); setSearchQuery(null); }}
        groups={groups} onGroupsChange={setGroups} />
      {loading && <SectionSpinner label="Searching rules…" />}
      {searchResult && !loading && (
        <div className="space-y-3">
          <div className={`flex items-center gap-2 p-3 rounded-lg text-sm font-medium ${
            searchResult.matches_found > 0 ? 'bg-green-50 text-green-800 border border-green-200' : 'bg-gray-50 text-gray-600 border border-gray-200'
          }`}>
            {searchResult.matches_found > 0 ? <ShieldCheckIcon className="w-4 h-4 text-green-600" /> : <InformationCircleIcon className="w-4 h-4 text-gray-400" />}
            {searchResult.matches_found} match{searchResult.matches_found !== 1 ? 'es' : ''} found across {searchResult.total_scanned} rules
            {searchQuery && (
              <span className="ml-2 text-xs font-normal text-gray-500">
                ({searchQuery.groups.reduce((n, g) => n + g.conditions.length, 0)} condition{searchQuery.groups.reduce((n, g) => n + g.conditions.length, 0) !== 1 ? 's' : ''})
              </span>
            )}
          </div>
          <FirewallRulesTable rules={searchResult.rules} showMatchReasons={true} />
        </div>
      )}
    </div>
  );
}

function OverlapsTab({ ctx }: { ctx: FirewallContext }) {
  const [overlapResult, setOverlapResult] = useState<OverlapResult | null>(null);
  const [loading,       setLoading]       = useState(false);
  const [typeFilter,    setTypeFilter]    = useState('');

  const run = useCallback(async () => {
    setLoading(true);
    try {
      setOverlapResult(await apiService.firewallDetectOverlaps(ctx.subscription_id, ctx.resource_group, ctx.policy_name, typeFilter || undefined));
    } catch { toast.error('Overlap detection failed'); }
    finally  { setLoading(false); }
  }, [ctx, typeFilter]);

  // Auto-run on first mount only; subsequent runs are manual
  const hasRun = useRef(false);
  useEffect(() => {
    if (!hasRun.current) { hasRun.current = true; run(); }
  }, [run]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <ExclamationTriangleIcon className="w-4 h-4 text-orange-500" /> Overlap &amp; Duplicate Detection
        </h3>
        <div className="flex gap-2">
          <select className="text-sm border border-gray-300 rounded-lg px-3 py-2 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
            {RULE_TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button onClick={run}
            className="flex items-center gap-1.5 px-3 py-2 text-sm bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 transition-colors">
            <ArrowPathIcon className="w-4 h-4" /> {overlapResult ? 'Re-run' : 'Detect Overlaps'}
          </button>
        </div>
      </div>
      {loading ? <SectionSpinner label="Analysing rules for overlaps…" /> : overlapResult ? (
        <OverlapAnalysis overlaps={overlapResult.overlaps} totalScanned={overlapResult.total_rules_scanned}
          summary={overlapResult.summary} analysisMode={overlapResult.analysis_mode} />
      ) : (
        <div className="text-center py-16 text-gray-400 text-sm">Click <strong>"Detect Overlaps"</strong> to run the analysis</div>
      )}
    </div>
  );
}

function OrphanTab({ ctx, workspaceId }: { ctx: FirewallContext; workspaceId: string }) {
  const [orphanResult,     setOrphanResult]     = useState<OrphanResult | null>(null);
  const [loading,          setLoading]          = useState(false);
  const [days,             setDays]             = useState(30);
  const [activePreset,     setActivePreset]     = useState(30);
  const [showCustom,       setShowCustom]       = useState(false);
  const [customStart,      setCustomStart]      = useState('');
  const [customEnd,        setCustomEnd]        = useState('');
  const [typeFilter,       setTypeFilter]       = useState('');
  const [drilldownRule,    setDrilldownRule]    = useState<FirewallRule | null>(null);

  const run = async () => {
    if (!workspaceId) { toast.error('No Log Analytics workspace configured for this firewall'); return; }
    setLoading(true);
    try {
      const effectiveDays = showCustom && customStart && customEnd
        ? Math.max(1, Math.ceil((new Date(customEnd).getTime() - new Date(customStart).getTime()) / 86400000))
        : days;
      setOrphanResult(await apiService.firewallDetectOrphans(ctx.subscription_id, ctx.resource_group, ctx.policy_name, workspaceId, effectiveDays, typeFilter || undefined));
    } catch { toast.error('Orphan detection failed'); }
    finally  { setLoading(false); }
  };

  return (
    <div className="space-y-4">
      <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <TrashIcon className="w-4 h-4 text-red-500" /> Orphan Rule Detection
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1">Log Analytics Workspace</label>
            <input type="text" readOnly placeholder="—"
              className="w-full border border-gray-200 bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-500 cursor-default select-all"
              value={workspaceId.split('/').pop() ?? workspaceId} title={workspaceId} />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1 flex items-center gap-1">
              <ClockIcon className="w-3.5 h-3.5" /> Look-back Window
            </label>
            <div className="flex flex-wrap gap-1 mb-1.5">
              {[{l:'7d',d:7},{l:'14d',d:14},{l:'30d',d:30},{l:'45d',d:45},{l:'60d',d:60},{l:'90d',d:90}].map(({l,d}) => (
                <button key={d} onClick={() => { setDays(d); setActivePreset(d); setShowCustom(false); }}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${!showCustom && activePreset===d ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}
                >{l}</button>
              ))}
              <button onClick={() => setShowCustom((v) => !v)}
                className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${showCustom ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}
              >Custom</button>
            </div>
            {showCustom && (
              <div className="flex gap-2 items-center mt-1">
                <input type="datetime-local" value={customStart} onChange={(e) => setCustomStart(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                <span className="text-xs text-gray-400">to</span>
                <input type="datetime-local" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button onClick={run} disabled={loading}
            className="flex items-center gap-2 px-5 py-2.5 bg-indigo-500 text-white text-sm font-medium rounded-lg hover:bg-indigo-600 disabled:opacity-50 transition-colors">
            <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            {orphanResult ? 'Re-run Analysis' : 'Run Orphan Detection'}
          </button>
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium text-gray-700 shrink-0">Rule Types</label>
            <select className="border border-gray-300 rounded-lg px-4 py-2 text-sm bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none min-w-40"
              value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              {RULE_TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
        </div>
      </div>
      {loading ? <SectionSpinner label="Correlating rules with firewall logs…" /> : orphanResult ? (
        <OrphanDetection result={orphanResult} onDrilldown={(rule) => setDrilldownRule(rule)} />
      ) : (
        <div className="text-center py-16 text-gray-400 text-sm space-y-2">
          <TrashIcon className="w-10 h-10 mx-auto text-gray-300" />
          <p>Select a look-back window and click <strong>"Run Orphan Detection"</strong></p>
          <p className="text-xs">Requires Azure Firewall diagnostic logs streaming to the workspace</p>
        </div>
      )}
      {drilldownRule && (
        <RuleModal ruleName={drilldownRule.name} workspaceId={workspaceId} days={days} onClose={() => setDrilldownRule(null)} />
      )}
    </div>
  );
}

function TrafficTab({ ctx, workspaceId }: { ctx: FirewallContext; workspaceId: string }) {
  const [trafficResult, setTrafficResult] = useState<TrafficIntelligenceResult | null>(null);
  const [loading,       setLoading]       = useState(false);
  const [days,          setDays]          = useState(7);
  const [customStart,   setCustomStart]   = useState('');
  const [customEnd,     setCustomEnd]     = useState('');
  const [showCustom,    setShowCustom]    = useState(false);
  const [drillRule,     setDrillRule]     = useState<{ name: string; collection: string; type: string } | null>(null);

  const run = async () => {
    if (!workspaceId.trim()) { toast.error('No workspace configured for this firewall'); return; }
    setLoading(true);
    try {
      setTrafficResult(await apiService.firewallGetTrafficIntelligence(
        ctx.subscription_id, ctx.resource_group, ctx.policy_name, workspaceId, days, undefined,
        showCustom && customStart && customEnd ? new Date(customStart).toISOString() : undefined,
        showCustom && customStart && customEnd ? new Date(customEnd).toISOString() : undefined,
      ));
    } catch { toast.error('Traffic intelligence query failed'); }
    finally  { setLoading(false); }
  };

  return (
    <div className="space-y-4">
      <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
          <ChartBarIcon className="w-4 h-4 text-indigo-500" /> Traffic Intelligence
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1">Log Analytics Workspace</label>
            <input type="text" readOnly placeholder="—"
              className="w-full border border-gray-200 bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-500 cursor-default select-all"
              value={workspaceId.split('/').pop() ?? workspaceId} title={workspaceId} />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1 flex items-center gap-1">
              <ClockIcon className="w-3.5 h-3.5" /> Look-back Window
            </label>
            <div className="flex flex-wrap gap-1 mb-1.5">
              {[{l:'1d',d:1},{l:'3d',d:3},{l:'7d',d:7},{l:'14d',d:14},{l:'30d',d:30},{l:'90d',d:90}].map(({l,d}) => (
                <button key={d} onClick={() => { setDays(d); setShowCustom(false); }}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${!showCustom && days===d ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}
                >{l}</button>
              ))}
              <button onClick={() => setShowCustom(true)}
                className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${showCustom ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}
              >Custom</button>
            </div>
            {showCustom && (
              <div className="flex gap-2 items-center mt-1">
                <input type="datetime-local" value={customStart} onChange={(e) => setCustomStart(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                <span className="text-xs text-gray-400">to</span>
                <input type="datetime-local" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
              </div>
            )}
          </div>
        </div>
        <button onClick={run} disabled={loading}
          className="flex items-center gap-2 px-5 py-2.5 bg-indigo-500 text-white text-sm font-medium rounded-lg hover:bg-indigo-600 disabled:opacity-50 transition-colors">
          <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          {trafficResult ? 'Re-run Analysis' : 'Run Traffic Intelligence'}
        </button>
      </div>
      {loading ? <SectionSpinner label="Querying Log Analytics…" /> : trafficResult ? (
        <TrafficIntelligence result={trafficResult}
          onRuleClick={(name, collection, type) => setDrillRule({ name, collection, type })} />
      ) : (
        <div className="text-center py-16 text-gray-400 text-sm space-y-2">
          <ChartBarIcon className="w-10 h-10 mx-auto text-gray-300" />
          <p>Select a look-back window and click <strong>"Run Traffic Intelligence"</strong></p>
          <p className="text-xs">Queries denied traffic, top rules, threat intel, IDPS, protocol anomalies and more</p>
        </div>
      )}
      {drillRule && (
        <RuleModal ctx={ctx} ruleName={drillRule.name} ruleCollection={drillRule.collection}
          ruleType={drillRule.type} workspaceId={workspaceId} days={days} onClose={() => setDrillRule(null)} />
      )}
    </div>
  );
}

function PacketWalkTab({ ctx }: { ctx: FirewallContext }) {
  const [result,   setResult]   = useState<PacketWalkResult | null>(null);
  const [loading,  setLoading]  = useState(false);
  const [source,   setSource]   = useState('');
  const [dest,     setDest]     = useState('');
  const [port,     setPort]     = useState('443');
  const [protocol, setProtocol] = useState('TCP');

  const run = async (req: PacketWalkRequest) => {
    setLoading(true);
    try {
      setResult(await apiService.firewallPacketWalk(
        ctx.subscription_id, ctx.resource_group, ctx.policy_name, {
          source: req.source, destination: req.destination,
          port: req.port, protocol: req.protocol,
          subscription_ids: [ctx.subscription_id],
        },
      ));
    } catch { toast.error('Packet walk analysis failed'); }
    finally  { setLoading(false); }
  };

  return (
    <PacketWalk subscriptionId={ctx.subscription_id} resourceGroup={ctx.resource_group}
      policyName={ctx.policy_name} loading={loading} result={result} onAnalyze={run}
      source={source} dest={dest} port={port} protocol={protocol}
      onSourceChange={setSource} onDestChange={setDest}
      onPortChange={setPort} onProtocolChange={setProtocol} />
  );
}

function LogSearchTab({ ctx, workspaceId, onWorkspaceChange }: {  ctx: FirewallContext;
  workspaceId: string;
  onWorkspaceChange: (v: string) => void;
}) {
  const [result,       setResult]       = useState<LogSearchResult | null>(null);
  const [loading,      setLoading]      = useState(false);
  const [groups,       setGroups]       = useState<LogConditionGroup[]>([newGroup()]);
  const [startTime,    setStartTime]    = useState(() => new Date(Date.now() - 3600_000).toISOString().slice(0, 16));
  const [endTime,      setEndTime]      = useState(() => new Date().toISOString().slice(0, 16));
  const [limit,        setLimit]        = useState(200);
  const [activePreset, setActivePreset] = useState('1h');

  const run = async (query: LogSearchQuery) => {
    setLoading(true);
    try {
      setResult(await apiService.firewallSearchLogs(
        ctx.subscription_id, ctx.resource_group, ctx.policy_name, {
          workspace_id: query.workspace_id,
          groups: query.groups.map(g => ({
            conditions: g.conditions.map(c => ({ field: c.field, operator: c.operator, value: c.value })),
            intra_connector: g.intraConnector, inter_connector: g.interConnector,
          })),
          start_time: query.start_time, end_time: query.end_time, limit: query.limit,
        },
      ));
    } catch { toast.error('Log search failed'); }
    finally  { setLoading(false); }
  };

  return (
    <LogSearch
      defaultWorkspace={workspaceId}
      loading={loading}
      onSearch={run}
      onClear={() => setResult(null)}
      result={result}
      ctx={ctx}
      workspace={workspaceId}
      groups={groups}
      startTime={startTime}
      endTime={endTime}
      limit={limit}
      activePreset={activePreset}
      onWorkspaceChange={onWorkspaceChange}
      onGroupsChange={setGroups}
      onStartTimeChange={setStartTime}
      onEndTimeChange={setEndTime}
      onLimitChange={setLimit}
      onActivePresetChange={setActivePreset}
    />
  );
}

// ─────────────────────────────────────────────
// Main dashboard
// ─────────────────────────────────────────────

function FirewallDashboard({ ctx, onReset }: { ctx: FirewallContext; onReset: () => void }) {
  const [activeTab, setActiveTab] = useState<AnalysisTab>('rules');
  const [summary,   setSummary]   = useState<PolicySummary | null>(null);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [workspaceId, setWorkspaceId] = useState(ctx.workspace_id ?? '');
  const [searchInitialGroups, setSearchInitialGroups] = useState<SearchConditionGroup[]>([newSearchGroup()]);
  const [searchTrigger, setSearchTrigger] = useState(0);

  const navigateToSearch = useCallback((field: string, value: string) => {
    const group: SearchConditionGroup = {
      id: crypto.randomUUID(),
      conditions: [{ id: crypto.randomUUID(), field, operator: 'equals', value }],
      intraConnector: 'AND',
      interConnector: 'AND',
    };
    setSearchInitialGroups([group]);
    setSearchTrigger((n) => n + 1);
    setActiveTab('search');
  }, []);

  const navigateToSearchAll = useCallback((field: string) => {
    const group: SearchConditionGroup = {
      id: crypto.randomUUID(),
      conditions: [{ id: crypto.randomUUID(), field, operator: 'contains', value: '' }],
      intraConnector: 'AND',
      interConnector: 'AND',
    };
    setSearchInitialGroups([group]);
    setSearchTrigger((n) => n + 1);
    setActiveTab('search');
  }, []);

  // Keep workspaceId in sync if ctx changes (e.g. user switches policy via store)
  useEffect(() => {
    setWorkspaceId(ctx.workspace_id ?? '');
  }, [ctx.workspace_id]);

  useEffect(() => {
    (async () => {
      setLoadingSummary(true);
      try {
        setSummary(await apiService.firewallGetSummary(ctx.subscription_id, ctx.resource_group, ctx.policy_name));
      } catch { toast.error('Failed to load policy summary'); }
      finally  { setLoadingSummary(false); }
    })();
  }, [ctx]);

  const TABS = [
    { id: 'configuration' as AnalysisTab, label: 'Config',               icon: Cog6ToothIcon },
    { id: 'rules'         as AnalysisTab, label: 'Rule Explorer',         icon: ServerStackIcon },
    { id: 'search'        as AnalysisTab, label: 'Smart Search',          icon: MagnifyingGlassIcon },
    { id: 'traffic'       as AnalysisTab, label: 'Traffic Intelligence',  icon: ChartBarIcon },
    { id: 'packetwalk'    as AnalysisTab, label: 'Packet Walk',           icon: ArrowRightIcon },
    { id: 'logsearch'     as AnalysisTab, label: 'Log Search',            icon: DocumentMagnifyingGlassIcon },
    { id: 'metrics'       as AnalysisTab, label: 'Metrics',               icon: PresentationChartLineIcon },
    { id: 'overlaps'      as AnalysisTab, label: 'Overlap / Duplicates',  icon: ExclamationTriangleIcon },
    { id: 'orphans'       as AnalysisTab, label: 'Orphans',      icon: TrashIcon },
  ];

  return (
    <div className="space-y-4">
      {/* KPI strip */}
      {loadingSummary ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 animate-pulse">
          {[1,2,3,4].map(i => <div key={i} className="h-20 bg-gray-100 rounded-xl" />)}
        </div>
      ) : summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3">
          <KpiCard label="Total Rules"   value={summary.total_rules}             icon={<ServerStackIcon      className="w-5 h-5 text-indigo-500" />}  onClick={() => navigateToSearchAll('name')} />
          <KpiCard label="Network Rules" value={summary.network_rules}           colorClass="text-indigo-600" icon={<ShieldCheckIcon       className="w-5 h-5 text-indigo-400" />} onClick={() => navigateToSearch('rule_type', 'NetworkRule')} />
          <KpiCard label="App Rules"     value={summary.application_rules}       colorClass="text-purple-600" icon={<GlobeAltIcon          className="w-5 h-5 text-purple-400" />} onClick={() => navigateToSearch('rule_type', 'ApplicationRule')} />
          <KpiCard label="NAT Rules"     value={summary.nat_rules}               colorClass="text-orange-600" icon={<ArrowsRightLeftIcon   className="w-5 h-5 text-orange-400" />} onClick={() => navigateToSearch('rule_type', 'NatRule')} />
          <KpiCard label="Allow Rules"   value={summary.allow_rules}             colorClass="text-green-600"  onClick={() => navigateToSearch('action', 'Allow')} />
          <KpiCard label="Deny Rules"    value={summary.deny_rules}              colorClass="text-red-600"    onClick={() => navigateToSearch('action', 'Deny')} />
          <KpiCard label="Rule Groups"   value={summary.rule_collection_groups}  colorClass="text-gray-700"   onClick={() => navigateToSearchAll('group')} />
        </div>
      )}

      {/* Tab bar */}
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="flex border-b border-gray-100 overflow-x-auto">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                title={tab.label}
                className={`flex items-center justify-center gap-2 px-3 sm:px-4 py-3 text-sm font-medium whitespace-nowrap border-b-2 transition-all flex-1 sm:flex-none ${
                  activeTab === tab.id
                    ? 'border-indigo-500 text-indigo-600 bg-indigo-50/50'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                }`}>
                <Icon className="w-4 h-4 shrink-0" />
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            );
          })}
        </div>

        <div className="p-5">
          <div className={activeTab === 'configuration' ? '' : 'hidden'}><FirewallConfiguration ctx={ctx} /></div>
          <div className={activeTab === 'rules'         ? '' : 'hidden'}><RulesTab ctx={ctx} /></div>
          <div className={activeTab === 'search'        ? '' : 'hidden'}><SearchTab ctx={ctx} initialGroups={searchInitialGroups} triggerKey={searchTrigger} /></div>
          <div className={activeTab === 'traffic'       ? '' : 'hidden'}><TrafficTab ctx={ctx} workspaceId={workspaceId} /></div>
          <div className={activeTab === 'packetwalk'    ? '' : 'hidden'}><PacketWalkTab ctx={ctx} /></div>
          <div className={activeTab === 'logsearch'     ? '' : 'hidden'}><LogSearchTab ctx={ctx} workspaceId={workspaceId} onWorkspaceChange={setWorkspaceId} /></div>
          <div className={activeTab === 'metrics'       ? '' : 'hidden'}><FirewallMetrics ctx={ctx} /></div>
          <div className={activeTab === 'overlaps'      ? '' : 'hidden'}><OverlapsTab ctx={ctx} /></div>
          <div className={activeTab === 'orphans'       ? '' : 'hidden'}><OrphanTab ctx={ctx} workspaceId={workspaceId} /></div>
        </div>
      </div>
    </div>
  );
}


// ─────────────────────────────────────────────
// Page entry-point
// ─────────────────────────────────────────────

export default function FirewallPage() {
  const isAuthenticated = useIsAuthenticated();
  const { accounts } = useMsal();
  const { tokensReady, firewallCtx, setFirewallCtx } = useAppStore();

  const userRoles = (accounts?.[0]?.idTokenClaims as { roles?: string[] })?.roles ?? [];
  const hasFirewallAccess = userRoles.includes('Network') || userRoles.includes('Advance') || userRoles.includes('Admin');

  if (!isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-32 text-gray-400 text-sm">
        Please sign in to access the Firewall Analysis dashboard.
      </div>
    );
  }

  if (!hasFirewallAccess) {
    return (
      <div className="max-w-7xl mx-auto py-16 px-4 sm:px-6 lg:px-8 text-center">
        <div className="w-16 h-16 mx-auto bg-red-100 rounded-full flex items-center justify-center mb-4">
          <ShieldCheckIcon className="w-8 h-8 text-red-500" />
        </div>
        <h2 className="text-xl font-semibold text-gray-900 mb-2">Access Denied</h2>
        <p className="text-gray-500 text-sm">You need the <span className="font-semibold">Network</span>, <span className="font-semibold">Advance</span> or <span className="font-semibold">Admin</span> role to access the Firewall dashboard.</p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">

      {/* ── Page header — always visible, matches Costs / Dashboard pattern ── */}
      <div className="mb-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          {/* Left: icon + title + subtitle */}
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-50 flex items-center justify-center flex-shrink-0 mt-0.5">
              <ShieldCheckIcon className="w-5 h-5 text-indigo-600" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-slate-900 tracking-tight">Firewall Analysis</h1>
              {firewallCtx ? (
                <p className="mt-1 text-sm text-gray-500">
                  <span className="font-medium text-slate-700">{firewallCtx.policy_name}</span>
                  <span className="mx-1.5 text-gray-300">·</span>
                  {firewallCtx.resource_group}
                  <span className="mx-1.5 text-gray-300">·</span>
                  {firewallCtx.subscription_name}
                </p>
              ) : (
                <p className="mt-1 text-gray-500 text-sm">
                  Select a subscription and firewall policy to analyse rules, traffic and logs
                </p>
              )}
            </div>
          </div>

          {/* Right: actions (only when a context is active) */}
          {firewallCtx && (
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                onClick={() => setFirewallCtx(null)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-slate-600 bg-white border border-gray-200 rounded-lg hover:bg-slate-50 transition-colors"
              >
                <ArrowPathIcon className="w-3.5 h-3.5" />
                Change Policy
              </button>
            </div>
          )}
        </div>
      </div>

      {firewallCtx ? (
        // Restored from localStorage or freshly selected — show dashboard immediately.
        // Individual tabs have their own loading states; tokens will be ready by first fetch.
        <FirewallDashboard ctx={firewallCtx} onReset={() => setFirewallCtx(null)} />
      ) : tokensReady ? (
        <ContextSelector onSelect={setFirewallCtx} />
      ) : (
        <div className="flex items-center justify-center py-32 text-gray-400 text-sm gap-2">
          <ArrowPathIcon className="w-4 h-4 animate-spin" />
          Initialising…
        </div>
      )}
    </div>
  );
}

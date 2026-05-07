'use client';

import { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useMsal } from '@azure/msal-react';
import {
  CodeBracketIcon,
  PlayIcon,
  SparklesIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClipboardDocumentIcon,
  ArrowDownTrayIcon,
  XMarkIcon,
  FunnelIcon,
  BookmarkIcon,
  TrashIcon,
  StarIcon,
  ClockIcon,
} from '@heroicons/react/24/outline';
import { StarIcon as StarIconSolid } from '@heroicons/react/24/solid';
import toast from 'react-hot-toast';
import { apiService } from '../../services/apiService';
import type { FirewallContext, SavedKqlTemplate, KqlResult } from '../../types/firewall';
import KqlEditor from './KqlEditor';

// ─── KQL Template Library ─────────────────────────────────────────────────────
export interface KqlTemplate {
  id: string;
  category: string;
  title: string;
  description: string;
  kql: string;
}

export const KQL_TEMPLATES: KqlTemplate[] = [
  {
    id: 'denied-network',
    category: 'Network Rules',
    title: 'Top denied network connections',
    description: 'Top 50 denied network connections grouped by source/destination',
    kql: `AZFWNetworkRule
| where Action == "Deny"
| summarize Count=count() by SourceIp, DestinationIp, DestinationPort, Protocol
| order by Count desc
| take 50`,
  },
  {
    id: 'allowed-network',
    category: 'Network Rules',
    title: 'Allowed network traffic by rule',
    description: 'Allowed traffic grouped by rule name',
    kql: `AZFWNetworkRule
| where Action == "Allow"
| summarize Count=count() by Rule, RuleCollection, Protocol
| order by Count desc
| take 50`,
  },
  {
    id: 'top-sources',
    category: 'Network Rules',
    title: 'Top source IPs by traffic volume',
    description: 'Source IPs generating the most firewall hits',
    kql: `AZFWNetworkRule
| summarize Count=count() by SourceIp
| order by Count desc
| take 20`,
  },
  {
    id: 'top-destinations',
    category: 'Network Rules',
    title: 'Top destination IPs',
    description: 'Most frequently contacted destination IPs',
    kql: `AZFWNetworkRule
| summarize Count=count() by DestinationIp, DestinationPort
| order by Count desc
| take 20`,
  },
  {
    id: 'port-scan',
    category: 'Security',
    title: 'Potential port scan detection',
    description: 'Source IPs hitting many unique ports (possible scan)',
    kql: `AZFWNetworkRule
| where Action == "Deny"
| summarize UniquePorts=dcount(DestinationPort), Count=count() by SourceIp
| where UniquePorts > 10
| order by UniquePorts desc`,
  },
  {
    id: 'threat-intel',
    category: 'Security',
    title: 'Threat intelligence hits',
    description: 'All connections blocked by threat intelligence',
    kql: `AZFWThreatIntel
| project TimeGenerated, Action, SourceIp, DestinationIp, DestinationPort, Protocol, ThreatDescription, Fqdn
| order by TimeGenerated desc`,
  },
  {
    id: 'app-denied',
    category: 'Application Rules',
    title: 'Denied application traffic',
    description: 'Top denied FQDNs via application rules',
    kql: `AZFWApplicationRule
| where Action == "Deny"
| summarize Count=count() by coalesce(Fqdn, TargetUrl, ""), SourceIp
| order by Count desc
| take 50`,
  },
  {
    id: 'app-allowed',
    category: 'Application Rules',
    title: 'Top allowed web destinations',
    description: 'Most accessed FQDNs via application rules',
    kql: `AZFWApplicationRule
| where Action == "Allow"
| summarize Count=count() by coalesce(Fqdn, TargetUrl, "")
| order by Count desc
| take 30`,
  },
  {
    id: 'app-by-protocol',
    category: 'Application Rules',
    title: 'Application traffic by protocol',
    description: 'Application rule hits broken down by protocol',
    kql: `AZFWApplicationRule
| summarize Count=count() by Protocol, Action
| order by Count desc`,
  },
  {
    id: 'nat-rules',
    category: 'NAT Rules',
    title: 'NAT (DNAT) translations',
    description: 'All DNAT translations with source and translated destination',
    kql: `AZFWNatRule
| project TimeGenerated, SourceIp, SourcePort, DestinationIp, DestinationPort, TranslatedIp, TranslatedPort, Protocol, Rule
| order by TimeGenerated desc`,
  },
  {
    id: 'nat-top-sources',
    category: 'NAT Rules',
    title: 'Top NAT sources',
    description: 'Top source IPs using DNAT rules',
    kql: `AZFWNatRule
| summarize Count=count() by SourceIp, Rule
| order by Count desc
| take 20`,
  },
  {
    id: 'traffic-over-time',
    category: 'Trends',
    title: 'Traffic volume over time',
    description: 'Firewall hits per hour across all rule types',
    kql: `union AZFWNetworkRule, AZFWApplicationRule, AZFWNatRule, AZFWThreatIntel
| summarize Count=count() by bin(TimeGenerated, 1h), Action
| order by TimeGenerated asc`,
  },
  {
    id: 'deny-rate-over-time',
    category: 'Trends',
    title: 'Deny rate over time',
    description: 'Denied traffic per 30-minute window',
    kql: `union AZFWNetworkRule, AZFWApplicationRule, AZFWThreatIntel
| where Action == "Deny"
| summarize Denied=count() by bin(TimeGenerated, 30m)
| order by TimeGenerated asc`,
  },
  {
    id: 'rule-hit-count',
    category: 'Rules',
    title: 'Rule hit counts',
    description: 'Which rules are hit most frequently',
    kql: `union AZFWNetworkRule, AZFWApplicationRule, AZFWNatRule
| summarize Count=count() by Rule, RuleCollection, RuleCollectionGroup
| order by Count desc
| take 30`,
  },
  {
    id: 'unused-rule-hint',
    category: 'Rules',
    title: 'Rules with zero hits (hint)',
    description: 'Policy rules not seen in logs — possible unused rules',
    kql: `union AZFWNetworkRule, AZFWApplicationRule
| summarize LastSeen=max(TimeGenerated), Count=count() by Rule, RuleCollection
| where Count < 5
| order by Count asc`,
  },
  {
    id: 'protocol-breakdown',
    category: 'Trends',
    title: 'Protocol breakdown',
    description: 'Traffic volume by protocol across all firewall tables',
    kql: `union AZFWNetworkRule, AZFWApplicationRule, AZFWNatRule
| summarize Count=count() by Protocol
| order by Count desc`,
  },
  {
    id: 'east-west',
    category: 'Security',
    title: 'East-west (internal) traffic',
    description: 'Traffic between RFC-1918 private subnets',
    kql: `AZFWNetworkRule
| where SourceIp matches regex @"^(10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)"
  and DestinationIp matches regex @"^(10\\.|172\\.(1[6-9]|2[0-9]|3[01])\\.|192\\.168\\.)"
| summarize Count=count() by SourceIp, DestinationIp, Action
| order by Count desc
| take 30`,
  },
  {
    id: 'high-port-traffic',
    category: 'Security',
    title: 'High ephemeral port activity',
    description: 'Connections to high ports (>10000) that were denied',
    kql: `AZFWNetworkRule
| where toint(DestinationPort) > 10000 and Action == "Deny"
| summarize Count=count() by SourceIp, DestinationPort
| order by Count desc
| take 30`,
  },
  {
    id: 'dns-traffic',
    category: 'Network Rules',
    title: 'DNS traffic (port 53)',
    description: 'All DNS traffic through the firewall',
    kql: `AZFWNetworkRule
| where DestinationPort == "53" or Protocol == "UDP"
| summarize Count=count() by SourceIp, DestinationIp, Action
| order by Count desc
| take 30`,
  },
  {
    id: 'failed-then-allowed',
    category: 'Security',
    title: 'IPs with both allow and deny',
    description: 'Source IPs that had both allowed and denied connections',
    kql: `AZFWNetworkRule
| summarize Actions=make_set(Action) by SourceIp
| where array_length(Actions) > 1
| order by SourceIp asc`,
  },
  {
    id: 'all-tables-summary',
    category: 'Overview',
    title: 'All rule types summary',
    description: 'Total hit count per firewall table type',
    kql: `union
  (AZFWNetworkRule     | summarize Count=count() | extend Type="NetworkRule"),
  (AZFWApplicationRule | summarize Count=count() | extend Type="ApplicationRule"),
  (AZFWNatRule         | summarize Count=count() | extend Type="NatRule"),
  (AZFWThreatIntel     | summarize Count=count() | extend Type="ThreatIntel")
| project Type, Count
| order by Count desc`,
  },
  {
    id: 'action-summary',
    category: 'Overview',
    title: 'Action summary (Allow vs Deny)',
    description: 'Total allow vs deny counts across all rule types',
    kql: `union AZFWNetworkRule, AZFWApplicationRule, AZFWThreatIntel
| summarize Count=count() by Action
| order by Count desc`,
  },
  {
    id: 'recent-denies',
    category: 'Overview',
    title: 'Recent denies (last 100)',
    description: 'Most recent 100 denied connections across all tables',
    kql: `union
  (AZFWNetworkRule     | where Action == "Deny" | project TimeGenerated, Type="Network",     SourceIp, DestinationIp, DestinationPort, Protocol, Rule),
  (AZFWApplicationRule | where Action == "Deny" | project TimeGenerated, Type="Application", SourceIp, DestinationIp=coalesce(Fqdn,""), DestinationPort, Protocol, Rule),
  (AZFWThreatIntel     | where Action == "Deny" | project TimeGenerated, Type="ThreatIntel", SourceIp, DestinationIp, DestinationPort, Protocol, Rule=ThreatDescription)
| order by TimeGenerated desc
| take 100`,
  },
  {
    id: 'top-talkers-full',
    category: 'Overview',
    title: 'Top talkers (src+dst pairs)',
    description: 'Source-destination IP pairs with highest traffic',
    kql: `AZFWNetworkRule
| summarize Count=count(), Actions=make_set(Action) by SourceIp, DestinationIp
| order by Count desc
| take 25`,
  },
];

// ─── Types ────────────────────────────────────────────────────────────────────
interface Props {
  ctx: FirewallContext;
  workspace: string;
  startTime: string;
  endTime: string;
  activePreset: string;
  onStartTimeChange: (v: string) => void;
  onEndTimeChange: (v: string) => void;
  onActivePresetChange: (v: string) => void;
}

// ─── Component ────────────────────────────────────────────────────────────────
export default function KqlQueryBox({ ctx, workspace, startTime, endTime, activePreset, onStartTimeChange, onEndTimeChange, onActivePresetChange }: Props) {
  const { accounts } = useMsal();
  const currentUserId = accounts[0]?.localAccountId ?? accounts[0]?.homeAccountId ?? '';
  const [showCustom, setShowCustom] = useState(false);
  const [kql, setKql]               = useState('');
  const [nlQuery, setNlQuery]       = useState('');
  const [loadingNl, setLoadingNl]   = useState(false);
  const [loading, setLoading]       = useState(false);
  const [elapsed, setElapsed]       = useState(0);
  const abortRef  = useRef<AbortController | null>(null);
  const timerRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const [result, setResult]         = useState<KqlResult | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);
  const [templateSearch, setTemplateSearch] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('All');
  const [copied, setCopied]         = useState(false);

  // Saved templates (from API)
  const [savedTemplates, setSavedTemplates]   = useState<SavedKqlTemplate[]>([]);
  const [loadingSaved, setLoadingSaved]         = useState(false);
  const [showSaveDialog, setShowSaveDialog]     = useState(false);
  const [saveForm, setSaveForm]                 = useState({ name: '', description: '' });
  const [saving, setSaving]                     = useState(false);
  const [deletingId, setDeletingId]             = useState<string | null>(null);

  const loadSaved = useCallback(async () => {
    setLoadingSaved(true);
    try {
      const res = await apiService.firewallKqlTemplatesList();
      setSavedTemplates(res.templates ?? []);
    } catch {
      // silently fail — redis may be unavailable
    } finally {
      setLoadingSaved(false);
    }
  }, []);

  // Favourites (persisted in Redis)
  const [favouriteIds, setFavouriteIds] = useState<Set<string>>(new Set());
  const [togglingFav, setTogglingFav]   = useState<string | null>(null);

  const loadFavourites = useCallback(async () => {
    try {
      const res = await apiService.firewallKqlFavouritesList();
      setFavouriteIds(new Set(res.favourite_ids ?? []));
    } catch { /* redis may be unavailable */ }
  }, []);

  const toggleFavourite = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setTogglingFav(id);
    // Optimistic update
    setFavouriteIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    try {
      await apiService.firewallKqlFavouritesToggle(id);
    } catch {
      // Revert on failure
      setFavouriteIds((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
      });
    } finally {
      setTogglingFav(null);
    }
  };

  // Load saved templates once on mount
  useEffect(() => { loadSaved(); loadFavourites(); }, [loadSaved, loadFavourites]);

  const handleSave = async () => {
    if (!saveForm.name.trim() || !kql.trim()) return;
    setSaving(true);
    try {
      const t = await apiService.firewallKqlTemplatesSave({
        name: saveForm.name.trim(),
        description: saveForm.description.trim(),
        kql,
      });
      setSavedTemplates((prev) => [t, ...prev]);
      setShowSaveDialog(false);
      setSaveForm({ name: '', description: '' });
    } catch {
      // keep dialog open on error
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await apiService.firewallKqlTemplatesDelete(id);
      setSavedTemplates((prev) => prev.filter((t) => t.id !== id));
    } catch {
      // silently fail
    } finally {
      setDeletingId(null);
    }
  };

  // Table sort + column filter state (reset when new result arrives)
  type SortDir = 'asc' | 'desc';
  const [sortCol, setSortCol]         = useState<number | null>(null);
  const [sortDir, setSortDir]         = useState<SortDir>('asc');
  const [colFilters, setColFilters]   = useState<Record<number, Set<string>>>({})
  const [filterOpen, setFilterOpen]   = useState<number | null>(null);
  const [filterSearch, setFilterSearch] = useState('');
  const filterPanelRef = useRef<HTMLDivElement>(null);
  const [kqlLimit, setKqlLimit] = useState(5000);

  // Close filter panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (filterPanelRef.current && !filterPanelRef.current.contains(e.target as Node)) {
        setFilterOpen(null);
        setFilterSearch('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const toggleSort = (idx: number) => {
    if (sortCol === idx) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortCol(idx); setSortDir('asc'); }
  };

  const toggleFilterValue = (idx: number, val: string) => {
    setColFilters((prev) => {
      const next = new Set(prev[idx] ?? []);
      if (next.has(val)) next.delete(val); else next.add(val);
      return { ...prev, [idx]: next };
    });
  };

  const clearColFilter = (idx: number) => {
    setColFilters((prev) => { const n = { ...prev }; delete n[idx]; return n; });
  };

  const PAGE_SIZE = 1000;
  const [page, setPage] = useState(0);

  // Reset page when result changes
  useEffect(() => { setPage(0); }, [result]);

  const visibleRows = useMemo(() => {
    if (!result) return [];
    let rows = result.rows.slice() as string[][];
    for (const [idxStr, checked] of Object.entries(colFilters)) {
      if (!checked.size) continue;
      const idx = Number(idxStr);
      rows = rows.filter((r) => checked.has(String(r[idx] ?? '')));
    }
    if (sortCol !== null) {
      const si = sortCol;
      rows.sort((a, b) => {
        const av = String(a[si] ?? ''), bv = String(b[si] ?? '');
        const nA = Number(av), nB = Number(bv);
        if (!isNaN(nA) && !isNaN(nB)) return sortDir === 'asc' ? nA - nB : nB - nA;
        return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      });
    }
    return rows;
  }, [result, colFilters, sortCol, sortDir]);

  const totalPages = Math.ceil(visibleRows.length / PAGE_SIZE);
  const pageRows   = visibleRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Virtual scroll for the KQL results table
  const kqlScrollRef = useRef<HTMLDivElement>(null);
  const KQL_ROW_HEIGHT = 30;
  const kqlRowVirtualizer = useVirtualizer({
    count: visibleRows.length,
    getScrollElement: () => kqlScrollRef.current,
    estimateSize: () => KQL_ROW_HEIGHT,
    overscan: 25,
  });

  const categories = useMemo(() => {
    const cats = Array.from(new Set(KQL_TEMPLATES.map((t) => t.category)));
    return ['All', 'Favourites', ...cats, 'Saved'];
  }, []);

  const filteredTemplates = useMemo(() => {
    if (selectedCategory === 'Saved') {
      return savedTemplates
        .filter((t) => !templateSearch ||
          t.name.toLowerCase().includes(templateSearch.toLowerCase()) ||
          t.description.toLowerCase().includes(templateSearch.toLowerCase())
        )
        .map((t) => ({
          id: t.id,
          category: 'Saved' as string,
          title: t.name,
          description: t.description || `Saved by ${t.user_name}`,
          kql: t.kql,
          _saved: t,
        }));
    }

    // Build full list (built-in + saved) for Favourites or category filter
    const builtIn = KQL_TEMPLATES.map((t) => ({ ...t, _saved: undefined }));
    const fromSaved = savedTemplates.map((t) => ({
      id: t.id,
      category: 'Saved' as string,
      title: t.name,
      description: t.description || `Saved by ${t.user_name}`,
      kql: t.kql,
      _saved: t,
    }));
    const all = [...builtIn, ...fromSaved];

    return all.filter((t) => {
      if (selectedCategory === 'Favourites') return favouriteIds.has(t.id);
      const matchCat = selectedCategory === 'All' || t.category === selectedCategory;
      const matchSearch = !templateSearch ||
        t.title.toLowerCase().includes(templateSearch.toLowerCase()) ||
        t.description.toLowerCase().includes(templateSearch.toLowerCase());
      return matchCat && matchSearch;
    });
  }, [selectedCategory, templateSearch, savedTemplates, favouriteIds]);

  const resolveWindow = useCallback(() => {
    // Recompute "now" at run time so idle tabs always use current time
    const fmt = (d: Date) => d.toISOString().slice(0, 16);
    const now = new Date();
    const PRESET_MS: Record<string, number> = {
      '15m': 15 * 60 * 1000,
      '1h':  60 * 60 * 1000,
      '6h':  6 * 3600 * 1000,
      '24h': 24 * 3600 * 1000,
      '7d':  7 * 86400 * 1000,
      '30d': 30 * 86400 * 1000,
      '90d': 90 * 86400 * 1000,
    };
    if (activePreset in PRESET_MS) {
      return {
        start: new Date(now.getTime() - PRESET_MS[activePreset]).toISOString(),
        end:   now.toISOString(),
      };
    }
    return {
      start: new Date(startTime + ':00Z').toISOString(),
      end:   new Date(endTime   + ':00Z').toISOString(),
    };
  }, [activePreset, startTime, endTime]);

  const runKql = async () => {
    if (!kql.trim() || !workspace.trim()) return;
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    const signal = abortRef.current.signal;
    setLoading(true);
    setElapsed(0);
    setResult(null);
    setSortCol(null);
    setColFilters({});
    setFilterOpen(null);
    timerRef.current = setInterval(() => setElapsed((s) => s + 1), 1000);
    const { start, end } = resolveWindow();

    try {
      // Single streaming fetch — no polling loop, no accumulated requests
      const headers = apiService.getFirewallHeaders();
      const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? '';
      const url = `${baseUrl}/api/firewall/${ctx.subscription_id}/${ctx.resource_group}/${ctx.policy_name}/logs/kql/stream`;
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ workspace_id: workspace, kql, start_time: start, end_time: end, limit: kqlLimit }),
        signal,
      });

      if (!response.ok || !response.body) {
        setResult({ columns: [], rows: [], total: 0, error: `Server error ${response.status}`, kql_executed: kql });
        return;
      }

      // Read NDJSON stream — each non-empty line is a JSON event
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';          // keep incomplete last line in buffer
        for (const line of lines) {
          if (!line.trim()) continue;     // heartbeat — skip
          try {
            const evt = JSON.parse(line) as { status: string; result: KqlResult };
                      if (evt.status === 'done' || evt.status === 'error') {
                        // Defer heavy setState off the current microtask so the browser can breathe
                        queueMicrotask(() => {
                          setResult(evt.result ?? { columns: [], rows: [], total: 0, error: 'Query failed', kql_executed: kql });
                        });
                        reader.cancel();
                        break outer;
                      }
          } catch { /* non-JSON line — ignore */ }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && (err.name === 'AbortError')) return;
      setResult({ columns: [], rows: [], total: 0, error: 'Query failed', kql_executed: kql });
    } finally {
      clearInterval(timerRef.current!);
      timerRef.current = null;
      setLoading(false);
      abortRef.current = null;
    }
  };

  const cancelKql = () => {
    abortRef.current?.abort();
    clearInterval(timerRef.current!);
    timerRef.current = null;
    setLoading(false);
    abortRef.current = null;
  };

  const generateKql = async () => {
    if (!nlQuery.trim()) return;
    setLoadingNl(true);
    try {
      const res = await apiService.firewallNlToKql(nlQuery);
      setKql(res.kql);
    } catch {
      toast.error('AI → KQL failed. Check that the AI service is configured.');
    } finally {
      setLoadingNl(false);
    }
  };

  const copyKql = useCallback(() => {
    navigator.clipboard.writeText(kql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [kql]);

  const exportCsv = useCallback(() => {
    if (!result) return;
    const rows = [result.columns, ...result.rows.map((r) => (r as string[]))];
    const csv = rows.map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `fw-kql-${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  return (
    <div className="space-y-4">
      {/* ── Unified card ─────────────────────────────────────────────── */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-4">
        {/* Header — matches Filter Builder */}
        <div className="flex items-center gap-2">
          <CodeBracketIcon className="w-4 h-4 text-gray-500" />
          <span className="text-sm font-semibold text-gray-800">KQL Query</span>
        </div>

        {/* Workspace + time — identical layout to filter builder */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1">Log Analytics Workspace</label>
            <input type="text" readOnly placeholder="—" value={workspace.split('/').pop() ?? workspace}
              title={workspace}
              className="w-full border border-gray-200 bg-gray-50 rounded-lg px-3 py-1.5 text-sm text-gray-500 cursor-default select-all" />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1 flex items-center gap-1">
              <ClockIcon className="w-3 h-3" /> Time Range
            </label>
            <div className="flex flex-wrap gap-1">
              {Object.keys({ '15m': 1, '1h': 1, '6h': 1, '24h': 1, '7d': 1, '30d': 1, '90d': 1 }).map((label) => (
                <button key={label}
                  onClick={() => {
                    const PRESET_MS: Record<string, number> = { '15m': 15*60e3, '1h': 3600e3, '6h': 6*3600e3, '24h': 24*3600e3, '7d': 7*86400e3, '30d': 30*86400e3, '90d': 90*86400e3 };
                    const now = new Date();
                    onStartTimeChange(new Date(now.getTime() - PRESET_MS[label]).toISOString().slice(0, 16));
                    onEndTimeChange(now.toISOString().slice(0, 16));
                    setShowCustom(false);
                    onActivePresetChange(label);
                  }}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                    !showCustom && activePreset === label
                      ? 'bg-indigo-500 text-white border-indigo-500'
                      : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'
                  }`}>{label}</button>
              ))}
              <button onClick={() => setShowCustom((v) => !v)}
                className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${showCustom ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}>
                Custom
              </button>
            </div>
            {showCustom && (
              <div className="flex gap-2 items-center mt-1.5">
                <input type="datetime-local" value={startTime} onChange={(e) => onStartTimeChange(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                <span className="text-xs text-gray-400">to</span>
                <input type="datetime-local" value={endTime} onChange={(e) => onEndTimeChange(e.target.value)}
                  className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                <span className="text-xs font-medium text-gray-400">UTC</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Describe what you want to find in plain English…"
            value={nlQuery}
            onChange={(e) => setNlQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && generateKql()}
            className="flex-1 text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          />
          <button
            onClick={generateKql}
            disabled={loadingNl || !nlQuery.trim()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium whitespace-nowrap"
          >
            <SparklesIcon className="w-3.5 h-3.5" />
            {loadingNl ? 'Generating…' : 'AI → KQL'}
          </button>
        </div>

        {/* Template picker toggle */}
        <div>
          <button
            onClick={() => setShowTemplates((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 font-medium"
          >
            {showTemplates ? <ChevronUpIcon className="w-3.5 h-3.5" /> : <ChevronDownIcon className="w-3.5 h-3.5" />}
            {showTemplates ? 'Hide templates' : `Browse ${KQL_TEMPLATES.length} KQL templates`}
          </button>

          {showTemplates && (
            <div className="mt-2 border border-gray-200 rounded-xl overflow-hidden">
              {/* Category + search bar */}
              <div className="flex gap-2 p-2 bg-gray-50 border-b border-gray-200 flex-wrap">
                <input
                  type="text"
                  placeholder="Search templates…"
                  value={templateSearch}
                  onChange={(e) => setTemplateSearch(e.target.value)}
                  className="flex-1 min-w-[160px] text-xs border border-gray-300 rounded-lg px-2 py-1 focus:ring-1 focus:ring-indigo-400 focus:outline-none"
                />
                <div className="flex gap-1 flex-wrap">
                  {categories.map((cat) => (
                    <button
                      key={cat}
                      onClick={() => setSelectedCategory(cat)}
                      className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                        selectedCategory === cat
                          ? 'bg-indigo-500 text-white border-indigo-500'
                          : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'
                      }`}
                    >
                      {cat}
                    </button>
                  ))}
                </div>
              </div>
              {/* Template list */}
              <div className="max-h-64 overflow-y-auto divide-y divide-gray-100">
                {selectedCategory === 'Saved' && loadingSaved && (
                  <p className="text-xs text-gray-400 text-center py-4">Loading…</p>
                )}
                {filteredTemplates.map((t) => (
                  <div key={t.id} className="flex items-start gap-1 pr-1 hover:bg-indigo-50 transition-colors group">
                    {/* Star favourite button */}
                    <button
                      onClick={(e) => toggleFavourite(t.id, e)}
                      disabled={togglingFav === t.id}
                      className="mt-2 ml-1 p-1 shrink-0 rounded hover:bg-yellow-50 transition-colors"
                      title={favouriteIds.has(t.id) ? 'Remove from favourites' : 'Add to favourites'}
                    >
                      {favouriteIds.has(t.id)
                        ? <StarIconSolid className="w-3.5 h-3.5 text-yellow-400" />
                        : <StarIcon className="w-3.5 h-3.5 text-gray-300 group-hover:text-yellow-300" />}
                    </button>
                    <button
                      onClick={() => { setKql(t.kql); setShowTemplates(false); }}
                      className="flex-1 text-left px-2 py-2"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="text-xs font-medium text-gray-800 group-hover:text-indigo-700">
                          {t.title}
                        </span>
                        <span className="text-xs text-gray-400 shrink-0 mt-0.5">
                          {'_saved' in t && t._saved ? t._saved.user_name : t.category}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500 mt-0.5">{t.description}</p>
                    </button>
                    {'_saved' in t && t._saved && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(t.id); }}
                        disabled={deletingId === t.id}
                        className="mt-2 p-1 text-gray-300 hover:text-red-500 rounded hover:bg-red-50 transition-colors shrink-0"
                        title="Delete"
                      >
                        <TrashIcon className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
                {filteredTemplates.length === 0 && (
                  <p className="text-xs text-gray-400 text-center py-4">
                    {selectedCategory === 'Saved' ? 'No saved queries yet — save one above.'
                      : selectedCategory === 'Favourites' ? 'No favourites yet — click the star on any template.'
                      : 'No templates match'}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        {/* KQL editor */}
        <KqlEditor
          value={kql}
          onChange={setKql}
          onRun={runKql}
          height="180px"
        />
        <p className="text-xs text-gray-400">Time range from picker is always applied · If your KQL also filters time, the shorter window wins · Add <code className="font-mono">| take N</code> in your query to override the row limit · LA enforces a 64 MB response cap (~90k rows)</p>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-400">Max rows:</span>
          <select
            value={kqlLimit}
            onChange={e => setKqlLimit(Number(e.target.value))}
            className="text-xs border border-gray-300 rounded-lg px-2 py-1 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          >
            {[1000, 2000, 5000, 10000, 25000, 50000, 100000].map(n => <option key={n} value={n}>{n.toLocaleString()}</option>)}
          </select>
          {kql && (
            <button onClick={() => setKql('')} className="flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600">
              <XMarkIcon className="w-3.5 h-3.5" /> Clear
            </button>
          )}
          <div className="flex-1" />
          {kql && (
            <button
              onClick={copyKql}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors font-medium"
            >
              <ClipboardDocumentIcon className="w-3.5 h-3.5" />
              {copied ? 'Copied!' : 'Copy'}
            </button>
          )}
          {kql && (
            <button
              onClick={() => setShowSaveDialog(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 transition-colors font-medium"
            >
              <BookmarkIcon className="w-3.5 h-3.5" /> Save
            </button>
          )}
          <button
            onClick={loading ? cancelKql : runKql}
            disabled={!loading && (!kql.trim() || !workspace.trim())}
            className={`flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg transition-colors font-medium ${
              loading
                ? 'bg-red-500 hover:bg-red-600 text-white'
                : 'bg-indigo-500 hover:bg-indigo-600 text-white disabled:opacity-50 disabled:cursor-not-allowed'
            }`}
          >
            {loading ? (
              <>
                <svg className="animate-spin w-3.5 h-3.5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                </svg>
                {elapsed > 0 ? `${elapsed}s — Cancel` : 'Running…'}
              </>
            ) : (
              <><PlayIcon className="w-3.5 h-3.5" /> Run Query</>
            )}
          </button>
        </div>
      </div>

      {/* ── Save dialog ──────────────────────────────────────────── */}
      {showSaveDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowSaveDialog(false)}>
          <div className="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-md space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-gray-800">Save KQL Query</span>
              <button onClick={() => setShowSaveDialog(false)} className="p-1 text-gray-400 hover:text-gray-600 rounded">
                <XMarkIcon className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">Name <span className="text-red-500">*</span></label>
                <input
                  autoFocus
                  type="text"
                  placeholder="e.g. Denied connections last 24h"
                  value={saveForm.name}
                  onChange={(e) => setSaveForm((f) => ({ ...f, name: e.target.value }))}
                  onKeyDown={(e) => e.key === 'Enter' && handleSave()}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-700 mb-1">Description</label>
                <input
                  type="text"
                  placeholder="Optional: what does this query find?"
                  value={saveForm.description}
                  onChange={(e) => setSaveForm((f) => ({ ...f, description: e.target.value }))}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <p className="text-xs text-gray-400">Visible to everyone with firewall access. Anyone can delete shared queries.</p>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setShowSaveDialog(false)} className="px-4 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !saveForm.name.trim()}
                className="px-4 py-1.5 text-sm bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 disabled:opacity-50 font-medium"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────── */}
      {result && (
        <div className="space-y-2">
          {result.error ? (
            <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
              {result.error}
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 p-3 rounded-lg bg-blue-50 border border-blue-200 text-sm font-medium text-blue-800">
                <CodeBracketIcon className="w-4 h-4" />
                {visibleRows.length.toLocaleString()} row{visibleRows.length !== 1 ? 's' : ''}
                {result.total !== visibleRows.length && (
                  <span className="text-xs font-normal text-blue-600">(filtered from {result.total.toLocaleString()})</span>
                )}
                {result.total > 0 && (
                  <button
                    onClick={exportCsv}
                    className="ml-auto flex items-center gap-1.5 px-3 py-1 text-xs border border-blue-300 bg-white text-blue-700 rounded-lg hover:bg-blue-50 font-medium"
                  >
                    <ArrowDownTrayIcon className="w-3.5 h-3.5" /> Export CSV
                  </button>
                )}
              </div>
              {result.rows.length > 0 && (
                <div
                  ref={kqlScrollRef}
                  className="overflow-x-auto overflow-y-auto rounded-xl border border-gray-200"
                  style={{ height: '600px' }}
                >
                  <table className="text-xs border-collapse min-w-full">
                    <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
                      <tr>
                        {result.columns.map((col, idx) => {
                          const checkedSet   = colFilters[idx] ?? new Set<string>();
                          const isFiltered   = checkedSet.size > 0;
                          const isFilterOpen = filterOpen === idx;
                          const allVals = Array.from(
                            new Set(result.rows.map((r) => String((r as string[])[idx] ?? '')))
                          ).filter(Boolean).sort((a, b) => a.localeCompare(b));
                          const searchLower  = filterSearch.toLowerCase();
                          const filteredVals = isFilterOpen && filterSearch
                            ? allVals.filter((v) => v.toLowerCase().includes(searchLower))
                            : allVals;
                          const allChecked = filteredVals.length > 0 && filteredVals.every((v) => checkedSet.has(v));
                          return (
                            <th key={col} className="px-3 py-0 text-left text-gray-500 font-medium whitespace-nowrap relative group select-none">
                              <div className="flex items-center gap-0.5 py-2">
                                <button
                                  onClick={() => toggleSort(idx)}
                                  className="flex items-center gap-0.5 hover:text-gray-800 font-medium text-xs"
                                >
                                  {col}
                                  {sortCol === idx
                                    ? sortDir === 'asc'
                                      ? <ChevronUpIcon className="w-3 h-3 text-indigo-500" />
                                      : <ChevronDownIcon className="w-3 h-3 text-indigo-500" />
                                    : <ChevronUpIcon className="w-3 h-3 opacity-0 group-hover:opacity-30" />}
                                </button>
                                <button
                                  onClick={(ev) => {
                                    ev.stopPropagation();
                                    if (isFilterOpen) { setFilterOpen(null); setFilterSearch(''); }
                                    else { setFilterOpen(idx); setFilterSearch(''); }
                                  }}
                                  className={`ml-auto p-0.5 rounded hover:bg-gray-200 transition-colors ${isFiltered ? 'text-indigo-500' : 'text-gray-300 hover:text-gray-500'}`}
                                  title="Filter"
                                >
                                  <FunnelIcon className="w-3 h-3" />
                                </button>
                              </div>
                              {isFilterOpen && (
                                <div
                                  ref={filterPanelRef}
                                  className="absolute z-50 top-full left-0 mt-1 w-52 bg-white border border-gray-200 rounded-xl shadow-lg"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <div className="p-2 border-b border-gray-100">
                                    <input autoFocus type="text" placeholder="Search values…"
                                      value={filterSearch}
                                      onChange={(e) => setFilterSearch(e.target.value)}
                                      onKeyDown={(e) => e.key === 'Escape' && (setFilterOpen(null), setFilterSearch(''))}
                                      className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                                    />
                                  </div>
                                  <div className="px-2 py-1 border-b border-gray-100">
                                    <label className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-800 cursor-pointer py-0.5">
                                      <input type="checkbox" checked={allChecked}
                                        onChange={() => {
                                          setColFilters((prev) => {
                                            const next = new Set(prev[idx] ?? []);
                                            if (allChecked) filteredVals.forEach((v) => next.delete(v));
                                            else filteredVals.forEach((v) => next.add(v));
                                            return { ...prev, [idx]: next };
                                          });
                                        }}
                                        className="accent-indigo-500"
                                      />
                                      <span className="font-medium">(Select all)</span>
                                    </label>
                                  </div>
                                  <div className="max-h-52 overflow-y-auto px-2 py-1">
                                    {filteredVals.length === 0 && <p className="text-xs text-gray-400 py-2 text-center">No values</p>}
                                    {filteredVals.map((val) => (
                                      <label key={val} className="flex items-center gap-2 text-xs text-gray-700 hover:text-gray-900 cursor-pointer py-0.5">
                                        <input type="checkbox" checked={checkedSet.has(val)}
                                          onChange={() => toggleFilterValue(idx, val)}
                                          className="accent-indigo-500"
                                        />
                                        <span className="truncate" title={val}>{val || '(empty)'}</span>
                                      </label>
                                    ))}
                                  </div>
                                  {isFiltered && (
                                    <div className="px-2 py-1.5 border-t border-gray-100">
                                      <button
                                        onClick={() => { clearColFilter(idx); setFilterOpen(null); setFilterSearch(''); }}
                                        className="text-xs text-red-500 hover:text-red-700 font-medium"
                                      >
                                        Clear filter
                                      </button>
                                    </div>
                                  )}
                                </div>
                              )}
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {(() => {
                        const vItems = kqlRowVirtualizer.getVirtualItems();
                        const paddingTop    = vItems.length > 0 ? vItems[0].start : 0;
                        const paddingBottom = vItems.length > 0 ? kqlRowVirtualizer.getTotalSize() - vItems[vItems.length - 1].end : 0;
                        const colCount = result?.columns?.length ?? 1;
                        return (
                          <>
                            {paddingTop > 0 && <tr><td style={{ height: paddingTop }} colSpan={colCount} /></tr>}
                            {vItems.map((virtualRow) => {
                              const row = visibleRows[virtualRow.index] as unknown[];
                              return (
                                <tr key={virtualRow.index} className="hover:bg-gray-50 transition-colors border-b border-gray-100">
                                  {row.map((cell, j) => (
                                    <td key={j} className="px-3 py-1.5 text-gray-700 whitespace-nowrap max-w-xs overflow-hidden text-ellipsis" title={String(cell ?? '')}>
                                      {String(cell ?? '')}
                                    </td>
                                  ))}
                                </tr>
                              );
                            })}
                            {paddingBottom > 0 && <tr><td style={{ height: paddingBottom }} colSpan={colCount} /></tr>}
                          </>
                        );
                      })()}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

'use client';

import { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import {
  MagnifyingGlassIcon,
  PlusIcon,
  TrashIcon,
  XMarkIcon,
  ClockIcon,
  ArrowDownTrayIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  FunnelIcon,
  CodeBracketIcon,
  AdjustmentsHorizontalIcon,
  SignalIcon,
  StopIcon,
} from '@heroicons/react/24/outline';
import type { LogEntry, LogSearchResult, FirewallContext } from '../../types/firewall';
import { apiService } from '../../services/apiService';
import KqlQueryBox from './KqlQueryBox';

export interface LogCondition {
  id: string;
  field: string;
  operator: string;
  value: string;
}

export interface LogConditionGroup {
  id: string;
  conditions: LogCondition[];
  intraConnector: 'AND' | 'OR'; // operator between conditions within this group
  interConnector: 'AND' | 'OR'; // operator from this group to the next
}

export interface LogSearchQuery {
  workspace_id: string;
  groups: LogConditionGroup[];
  start_time: string;
  end_time: string;
  limit: number;
}

interface Props {
  ctx: FirewallContext;
  defaultWorkspace?: string;
  onSearch: (query: LogSearchQuery) => void;
  onClear: () => void;
  loading?: boolean;
  result?: LogSearchResult | null;
  workspace: string;
  groups: LogConditionGroup[];
  startTime: string;
  endTime: string;
  limit: number;
  activePreset: string;
  onWorkspaceChange: (v: string) => void;
  onGroupsChange: (v: LogConditionGroup[]) => void;
  onStartTimeChange: (v: string) => void;
  onEndTimeChange: (v: string) => void;
  onLimitChange: (v: number) => void;
  onActivePresetChange: (v: string) => void;
}

const LOG_FIELDS = [
  { value: 'source_ip',       label: 'Source' },
  { value: 'dest_ip',         label: 'Destination IP' },
  { value: 'dest_fqdn',       label: 'FQDN / URL' },
  { value: 'dest_port',       label: 'Port' },
  { value: 'protocol',        label: 'Protocol' },
  { value: 'action',          label: 'Action' },
  { value: 'type',            label: 'Rule Type' },
  { value: 'rule_name',       label: 'Rule Name' },
  { value: 'rule_collection', label: 'Rule Collection' },
  { value: 'rule_group',      label: 'Rule Group' },
];

const OPERATORS = [
  { value: 'contains',     label: 'contains' },
  { value: 'not_contains', label: 'does not contain' },
  { value: 'equals',       label: 'equals' },
  { value: 'not_equals',   label: 'not equals' },
  { value: 'starts_with',  label: 'starts with' },
  { value: 'ends_with',    label: 'ends with' },
];

const IP_FIELDS = new Set(['source_ip', 'dest_ip']);
const DROPDOWN_FIELDS = new Set(['protocol', 'action', 'type']);
const IP_OPERATORS     = OPERATORS.filter(o => ['equals','not_equals','starts_with','ends_with'].includes(o.value));
const DROPDOWN_OPERATORS = OPERATORS.filter(o => ['equals','not_equals'].includes(o.value));

function operatorsFor(field: string) {
  if (DROPDOWN_FIELDS.has(field)) return DROPDOWN_OPERATORS;
  if (IP_FIELDS.has(field))       return IP_OPERATORS;
  return OPERATORS;
}

const VALUE_HINTS: Record<string, string[]> = {
  action:    ['Allow', 'Deny'],
  type:      ['Network', 'Application', 'NAT', 'ThreatIntel'],
  protocol:  ['TCP', 'UDP', 'ICMP', 'HTTPS', 'HTTP'],
};

const ACTION_COLORS: Record<string, string> = {
  Allow: 'bg-green-100 text-green-700',
  Deny:  'bg-red-100 text-red-700',
  DNAT:  'bg-yellow-100 text-yellow-700',
};

function newCond(): LogCondition {
  return { id: crypto.randomUUID(), field: 'action', operator: 'equals', value: '' };
}
export function newGroup(): LogConditionGroup {
  return { id: crypto.randomUUID(), conditions: [newCond()], intraConnector: 'AND', interConnector: 'AND' };
}

// Quick preset time ranges
function presets(): { label: string; start: string; end: string }[] {
  const now = new Date();
  const fmt = (d: Date) => d.toISOString().slice(0, 16); // yyyy-MM-ddTHH:mm in UTC
  return [
    { label: '15m', start: fmt(new Date(now.getTime() - 15 * 60 * 1000)),   end: fmt(now) },
    { label: '1h',  start: fmt(new Date(now.getTime() - 60 * 60 * 1000)),   end: fmt(now) },
    { label: '6h',  start: fmt(new Date(now.getTime() - 6 * 3600 * 1000)),  end: fmt(now) },
    { label: '24h', start: fmt(new Date(now.getTime() - 24 * 3600 * 1000)), end: fmt(now) },
    { label: '7d',  start: fmt(new Date(now.getTime() - 7 * 86400 * 1000)),  end: fmt(now) },
    { label: '30d', start: fmt(new Date(now.getTime() - 30 * 86400 * 1000)), end: fmt(now) },
    { label: '90d', start: fmt(new Date(now.getTime() - 90 * 86400 * 1000)), end: fmt(now) },
  ];
}

// Recompute a preset's window at call time so idle pages always use "now" as end
function resolvePresetWindow(label: string): { start: string; end: string } | null {
  return presets().find(p => p.label === label) ?? null;
}


export default function LogSearch({ ctx, defaultWorkspace, onSearch, onClear, loading, result, workspace, groups, startTime, endTime, limit, activePreset, onWorkspaceChange, onGroupsChange, onStartTimeChange, onEndTimeChange, onLimitChange, onActivePresetChange }: Props) {
  const [showCustom, setShowCustom] = useState(false);
  const [activeTab, setActiveTab] = useState<'filter' | 'kql'>('filter');

  // Group operations
  const addGroup = () => onGroupsChange([...groups, newGroup()]);
  const removeGroup = (gid: string) => onGroupsChange(groups.filter(x => x.id !== gid));
  const setIntra = (gid: string, v: 'AND' | 'OR') =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, intraConnector: v } : x));
  const setInter = (gid: string, v: 'AND' | 'OR') =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, interConnector: v } : x));

  // Condition operations
  const addCond = (gid: string) =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, conditions: [...x.conditions, newCond()] } : x));
  const removeCond = (gid: string, cid: string) =>
    onGroupsChange(groups.map(x => x.id === gid ? { ...x, conditions: x.conditions.filter(c => c.id !== cid) } : x));
  const updateCond = (gid: string, cid: string, patch: Partial<LogCondition>) =>
    onGroupsChange(groups.map(x => x.id === gid
      ? { ...x, conditions: x.conditions.map(c => c.id === cid ? { ...c, ...patch } : c) }
      : x));

  const clear = () => { onGroupsChange([newGroup()]); onClear(); };

  const canSearch = workspace.trim().length > 0;

  // ── Live stream ────────────────────────────────────────────────────────
  const POLL_MS      = 10_000; // poll every 10 s
  // Lookback window per poll — must be > POLL_MS so consecutive polls always overlap.
  // Keeping it tight (30 s = 2× poll interval) minimises the chance of hitting MAX_LIVE
  // per poll on high-volume firewalls. 15 s of overlap absorbs any poll jitter.
  // NOTE: for sustained traffic > MAX_LIVE entries per 30 s (~167 logs/sec) a
  // server-side streaming endpoint (SSE/WebSocket) would be needed instead of polling.
  const LOOKBACK_S   = 30;     // fetch last 30 s on every poll
  const MAX_LIVE     = 5_000;  // cap live buffer

  const [liveMode,     setLiveMode]     = useState(false);
  const [liveEntries,  setLiveEntries]  = useState<LogEntry[]>([]);
  const [liveLoading,  setLiveLoading]  = useState(false);
  const intervalRef  = useRef<ReturnType<typeof setInterval> | null>(null);
  const seenKeysRef  = useRef<Set<string>>(new Set());

  const liveKey = (e: LogEntry) =>
    `${e.TimeGenerated}|${e.SourceIp}|${e.DestinationIp}|${e.DestinationPort}|${e.Rule}`;

  const pollLogs = useCallback(async () => {
    if (!workspace.trim()) return;
    setLiveLoading(true);
    try {
      const now   = new Date();
      const start = new Date(now.getTime() - LOOKBACK_S * 1000).toISOString();
      const res   = await apiService.firewallSearchLogs(
        ctx.subscription_id, ctx.resource_group, ctx.policy_name,
        {
          workspace_id: workspace.trim(),
          groups: groups.map(g => ({
            conditions: g.conditions
              .filter(c => c.value.trim())
              .map(c => ({ field: c.field, operator: c.operator, value: c.value })),
            intra_connector: g.intraConnector,
            inter_connector: g.interConnector,
          })),
          start_time: start,
          end_time:   now.toISOString(),
          limit: MAX_LIVE,
        },
      );
      const fresh = res.entries.filter(e => {
        const k = liveKey(e);
        if (seenKeysRef.current.has(k)) return false;
        seenKeysRef.current.add(k);
        return true;
      });
      if (fresh.length > 0) {
        // Sort newest-first within the batch before prepending
        fresh.sort((a, b) => b.TimeGenerated.localeCompare(a.TimeGenerated));
        setLiveEntries(prev => [...fresh, ...prev].slice(0, MAX_LIVE));
      }
    } catch { /* best-effort */ }
    finally { setLiveLoading(false); }
  // groups / workspace captured via closure; deps listed to satisfy linter
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace, ctx, groups]);

  const startLive = useCallback(() => {
    seenKeysRef.current.clear();
    setLiveEntries([]);
    setLiveMode(true);
    pollLogs();
    intervalRef.current = setInterval(pollLogs, POLL_MS);
  }, [pollLogs]);

  const stopLive = useCallback(() => {
    setLiveMode(false);
    if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; }
  }, []);

  // Cleanup on unmount
  useEffect(() => () => { if (intervalRef.current) clearInterval(intervalRef.current); }, []);

  const submit = () => {
    if (!workspace.trim()) return;
    // If a preset is active, recompute the window at run time so "now" is always current.
    // For custom ranges startTime/endTime are UTC strings from toISOString() — append 'Z'
    // so new Date() parses them as UTC, not local time.
    const window = !showCustom ? resolvePresetWindow(activePreset) : null;
    const start = window ? window.start + ':00Z' : startTime + ':00Z';
    const end   = window ? window.end   + ':00Z' : endTime   + ':00Z';
    // Sync displayed time inputs to the resolved window
    if (window) {
      onStartTimeChange(window.start);
      onEndTimeChange(window.end);
    }
    onSearch({
      workspace_id: workspace.trim(),
      groups,
      start_time: new Date(start).toISOString(),
      end_time:   new Date(end).toISOString(),
      limit,
    });
  };

  // Build expression preview
  const buildPreview = (): string => {
    const activeGroups = groups
      .map(g => ({ ...g, conditions: g.conditions.filter(c => c.value.trim()) }))
      .filter(g => g.conditions.length > 0);
    if (activeGroups.length === 0) return '';
    const fl = (f: string) => LOG_FIELDS.find(x => x.value === f)?.label ?? f;
    const ol = (o: string) => OPERATORS.find(x => x.value === o)?.label ?? o;
    const groupExprs = activeGroups.map(g => {
      const parts = g.conditions.map(c => `${fl(c.field)} ${ol(c.operator)} "${c.value}"`);
      return parts.length === 1 ? parts[0] : `(${parts.join(` ${g.intraConnector} `)})`;
    });
    if (groupExprs.length === 1) return groupExprs[0];
    let r = groupExprs[0];
    for (let i = 1; i < groupExprs.length; i++) {
      r = `${r} ${activeGroups[i - 1].interConnector} ${groupExprs[i]}`;
    }
    return r;
  };
  const preview = buildPreview();
  const COLS = [
    { key: 'time',        label: 'Time',        defaultW: 140 },
    { key: 'action',      label: 'Action',      defaultW: 80  },
    { key: 'type',        label: 'Type',        defaultW: 90  },
    { key: 'source',      label: 'Source',      defaultW: 120 },
    { key: 'destination', label: 'Destination', defaultW: 160 },
    { key: 'port',        label: 'Port',        defaultW: 60  },
    { key: 'proto',       label: 'Proto',       defaultW: 70  },
    { key: 'rule',        label: 'Rule',        defaultW: 180 },
    { key: 'collection',  label: 'Collection',  defaultW: 150 },
    { key: 'group',       label: 'Rule Group',  defaultW: 160 },
  ];
  const [colWidths, setColWidths] = useState<Record<string, number>>(
    Object.fromEntries(COLS.map((c) => [c.key, c.defaultW]))
  );
  const dragState = useRef<{ key: string; startX: number; startW: number } | null>(null);

  // Live table column widths (separate from static results table)
  const LIVE_COLS = [
    { key: 'time',        label: 'Time (UTC)',   defaultW: 140 },
    { key: 'action',      label: 'Action',       defaultW: 80  },
    { key: 'type',        label: 'Type',         defaultW: 90  },
    { key: 'source',      label: 'Source',       defaultW: 120 },
    { key: 'destination', label: 'Destination',  defaultW: 160 },
    { key: 'port',        label: 'Port',         defaultW: 60  },
    { key: 'proto',       label: 'Proto',        defaultW: 70  },
    { key: 'rule',        label: 'Rule',         defaultW: 180 },
    { key: 'collection',  label: 'Collection',   defaultW: 150 },
    { key: 'group',       label: 'Rule Group',   defaultW: 160 },
  ];
  const [liveColWidths, setLiveColWidths] = useState<Record<string, number>>(
    Object.fromEntries(LIVE_COLS.map((c) => [c.key, c.defaultW]))
  );
  const liveDragState = useRef<{ key: string; startX: number; startW: number } | null>(null);

  const onLiveResizeStart = useCallback((key: string, e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = liveColWidths[key];
    liveDragState.current = { key, startX, startW };
    const onMove = (me: MouseEvent) => {
      const delta = me.clientX - startX;
      setLiveColWidths((prev) => ({ ...prev, [key]: Math.max(50, startW + delta) }));
    };
    const onUp = () => {
      liveDragState.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [liveColWidths]);

  // Sort & column filter state
  type SortDir = 'asc' | 'desc';
  const [sortCol, setSortCol]         = useState<string>('time');
  const [sortDir, setSortDir]         = useState<SortDir>('desc');
  // checked values per column (empty set = show all)
  const [colFilters, setColFilters]   = useState<Record<string, Set<string>>>({});
  const [filterOpen, setFilterOpen]   = useState<string | null>(null);
  const [filterSearch, setFilterSearch] = useState<string>('');
  const filterPanelRef = useRef<HTMLDivElement>(null);

  // Close filter panel on outside click
  useCallback(() => {
    const handler = (e: MouseEvent) => {
      if (filterPanelRef.current && !filterPanelRef.current.contains(e.target as Node)) {
        setFilterOpen(null);
        setFilterSearch('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const COL_FIELD: Record<string, (e: LogEntry) => string> = {
    time:        (e) => e.TimeGenerated ?? '',
    action:      (e) => e.Action ?? '',
    type:        (e) => e.RuleType ?? '',
    source:      (e) => e.SourceIp ?? '',
    destination: (e) => e.DestinationIp || e.TargetUrl || '',
    port:        (e) => e.DestinationPort ?? '',
    proto:       (e) => e.Protocol ?? '',
    rule:        (e) => e.Rule ?? '',
    collection:  (e) => e.RuleCollection ?? '',
    group:       (e) => e.RuleCollectionGroup ?? '',
  };

  const toggleSort = (key: string) => {
    if (sortCol === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortCol(key); setSortDir('asc'); }
  };

  const toggleFilterValue = (colKey: string, val: string) => {
    setColFilters((prev) => {
      const next = new Set(prev[colKey] ?? []);
      if (next.has(val)) next.delete(val); else next.add(val);
      return { ...prev, [colKey]: next };
    });
  };

  const clearColFilter = (colKey: string) => {
    setColFilters((prev) => { const n = { ...prev }; delete n[colKey]; return n; });
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const visibleEntries = useMemo(() => {
    if (!result) return [];
    let rows = result.entries.slice();
    for (const [key, checked] of Object.entries(colFilters)) {
      if (!checked.size) continue;
      const getter = COL_FIELD[key];
      rows = rows.filter((e) => checked.has(getter(e)));
    }
    const getter = COL_FIELD[sortCol];
    rows.sort((a, b) => {
      const av = getter(a), bv = getter(b);
      return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    return rows;
  }, [result, colFilters, sortCol, sortDir]);

  // Pre-compute unique values per column — cannot call useMemo inside .map()
  const colUniqueVals = useMemo<Record<string, string[]>>(() => {
    if (!result) return {};
    return Object.fromEntries(
      COLS.map((c) => [
        c.key,
        Array.from(new Set(result.entries.map(COL_FIELD[c.key])))
          .filter(Boolean)
          .sort((a, b) => a.localeCompare(b)),
      ])
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  const onResizeStart = useCallback((key: string, e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = colWidths[key];
    dragState.current = { key, startX, startW };

    const onMove = (me: MouseEvent) => {
      const delta = me.clientX - startX;
      const newW  = Math.max(50, startW + delta);
      setColWidths((prev) => ({ ...prev, [key]: newW }));
    };
    const onUp = () => {
      dragState.current = null;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [colWidths]);

  return (
    <div className="space-y-4">
      {/* ── Sub-tab switcher ────────────────────────────────────────────── */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1 w-fit">
        <button
          onClick={() => setActiveTab('filter')}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
            activeTab === 'filter' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <AdjustmentsHorizontalIcon className="w-3.5 h-3.5" />
          Filter Builder
        </button>
        <button
          onClick={() => setActiveTab('kql')}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
            activeTab === 'kql' ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          <CodeBracketIcon className="w-3.5 h-3.5" />
          KQL Query
        </button>
      </div>

      {/* ── KQL tab ─────────────────────────────────────────────────────── */}
      {activeTab === 'kql' && (
        <KqlQueryBox
          ctx={ctx}
          workspace={workspace}
          startTime={startTime}
          endTime={endTime}
          activePreset={activePreset}
          onStartTimeChange={onStartTimeChange}
          onEndTimeChange={onEndTimeChange}
          onActivePresetChange={onActivePresetChange}
        />
      )}

      {/* ── Filter Builder tab ──────────────────────────────────────────── */}
      {activeTab === 'filter' && (<>
      {/* ── Controls ─────────────────────────────────────────────────────── */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-4">
        <div className="flex items-center gap-2">
          <MagnifyingGlassIcon className="w-4 h-4 text-gray-500" />
          <span className="text-sm font-semibold text-gray-800">Firewall Log Search</span>
        </div>

        {/* Workspace + time range */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1">Log Analytics Workspace</label>
            <input
              type="text"
              readOnly
              placeholder="—"
              value={workspace.split('/').pop() ?? workspace}
              title={workspace}
              className="w-full border border-gray-200 bg-gray-50 rounded-lg px-3 py-1.5 text-sm text-gray-500 cursor-default select-all"
            />
          </div>
          <div className="md:col-span-2">
            <label className="block text-xs font-medium text-gray-700 mb-1 flex items-center gap-1">
              <ClockIcon className="w-3 h-3" /> Time Range
            </label>
            <div className="flex flex-wrap gap-1">
              {presets().map((p) => (
                <button
                  key={p.label}
                  onClick={() => { onStartTimeChange(p.start); onEndTimeChange(p.end); setShowCustom(false); onActivePresetChange(p.label); }}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${!showCustom && activePreset === p.label ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}
                >
                  {p.label}
                </button>
              ))}
              <button
                onClick={() => setShowCustom((v) => !v)}
                className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${showCustom ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'}`}
              >Custom</button>
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

        {/* Max results */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <div className="ml-auto flex items-center gap-1.5">
            <span className="text-xs text-gray-400">Max results:</span>
            <select
              value={limit}
              onChange={(e) => onLimitChange(Number(e.target.value))}
              className="text-xs border border-gray-300 rounded-lg px-2 py-1 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            >
              {[50, 100, 200, 500, 1000, 2000, 5000].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>

        {/* Group-based condition builder */}
        <div className="space-y-2">
          {groups.map((group, gIdx) => (
            <div key={group.id}>
              {/* Group box */}
              <div className="border border-gray-200 rounded-xl p-3 space-y-2 bg-gray-50/40">
                {/* Group header: intra-connector + remove */}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400 font-medium">Group {gIdx + 1}</span>
                  <div className="flex items-center gap-0.5 bg-gray-100 rounded-lg p-0.5">
                    {(['AND', 'OR'] as const).map(l => (
                      <button key={l} onClick={() => setIntra(group.id, l)}
                        className={`px-2 py-0.5 text-xs font-semibold rounded-md transition-colors ${
                          group.intraConnector === l
                            ? l === 'AND' ? 'bg-indigo-500 text-white' : 'bg-orange-500 text-white'
                            : 'text-gray-500 hover:text-gray-700'
                        }`}>{l}</button>
                    ))}
                  </div>
                  <span className="text-xs text-gray-400">{group.intraConnector === 'AND' ? 'all must match' : 'any must match'}</span>
                  {groups.length > 1 && (
                    <button onClick={() => removeGroup(group.id)}
                      className="ml-auto p-1 text-gray-400 hover:text-red-500 rounded hover:bg-red-50 transition-colors">
                      <TrashIcon className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>

                {/* Conditions within group */}
                {group.conditions.map((cond, cIdx) => (
                  <div key={cond.id} className="flex items-center gap-2 flex-wrap">
                    {cIdx === 0
                      ? <span className="text-xs font-medium text-gray-400 w-10 text-right shrink-0">If</span>
                      : <span className={`text-xs font-semibold w-10 text-right shrink-0 ${group.intraConnector === 'AND' ? 'text-indigo-600' : 'text-orange-500'}`}>
                          {group.intraConnector}
                        </span>
                    }
                    <select value={cond.field} onChange={e => {
                        const f = e.target.value;
                        const op = !operatorsFor(f).find(o => o.value === cond.operator) ? 'equals' : cond.operator;
                        updateCond(group.id, cond.id, { field: f, value: '', operator: op });
                      }}
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                      {LOG_FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                    </select>
                    <select value={cond.operator} onChange={e => updateCond(group.id, cond.id, { operator: e.target.value })}
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                      {operatorsFor(cond.field).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    {VALUE_HINTS[cond.field] ? (
                      <select value={cond.value} onChange={e => updateCond(group.id, cond.id, { value: e.target.value })}
                        className="flex-1 min-w-[140px] text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                        <option value="">— select —</option>
                        {VALUE_HINTS[cond.field].map(v => <option key={v} value={v}>{v}</option>)}
                      </select>
                    ) : (
                      <input type="text" placeholder={logFieldPlaceholder(cond.field)} value={cond.value}
                        onChange={e => updateCond(group.id, cond.id, { value: e.target.value })}
                        onKeyDown={e => e.key === 'Enter' && canSearch && submit()}
                        className="flex-1 min-w-[160px] text-sm border border-gray-300 rounded-lg px-2 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
                    )}
                    {group.conditions.length > 1 && (
                      <button onClick={() => removeCond(group.id, cond.id)}
                        className="p-1.5 text-gray-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors">
                        <TrashIcon className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
                <button onClick={() => addCond(group.id)}
                  className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800 font-medium">
                  <PlusIcon className="w-3.5 h-3.5" /> Add condition
                </button>
              </div>

              {/* Inter-group connector */}
              {gIdx < groups.length - 1 && (
                <div className="flex justify-center my-1">
                  <div className="flex items-center gap-0.5 bg-white rounded-lg p-0.5 border border-gray-200 shadow-sm">
                    {(['AND', 'OR'] as const).map(l => (
                      <button key={l} onClick={() => setInter(group.id, l)}
                        className={`px-2.5 py-0.5 text-xs font-semibold rounded-md transition-colors ${
                          group.interConnector === l
                            ? l === 'AND' ? 'bg-indigo-500 text-white' : 'bg-orange-500 text-white'
                            : 'text-gray-500 hover:text-gray-700'
                        }`}>{l}</button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="flex items-center gap-2 pt-1 flex-wrap">
          <button onClick={addGroup}
            className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 font-medium border border-dashed border-gray-300 rounded-lg px-2 py-1 hover:border-gray-400 transition-colors">
            <PlusIcon className="w-3.5 h-3.5" /> Add group
          </button>
          {preview && (
            <div className="w-full mt-1 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg font-mono text-xs text-gray-500 break-all">
              <span className="text-gray-400 mr-1.5 select-none">where</span>{preview}
            </div>
          )}
          <div className="flex-1" />
          <button onClick={clear}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
            <XMarkIcon className="w-3.5 h-3.5" /> Clear
          </button>
          <button
            onClick={liveMode ? stopLive : startLive}
            disabled={!canSearch}
            className={`flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg transition-colors font-medium disabled:opacity-50 disabled:cursor-not-allowed
              ${liveMode
                ? 'bg-red-500 hover:bg-red-600 text-white'
                : 'bg-emerald-500 hover:bg-emerald-600 text-white'}`}
          >
            {liveMode
              ? <><StopIcon className="w-3.5 h-3.5" /> Stop Stream</>  
              : <><SignalIcon className="w-3.5 h-3.5" /> Live Stream</>}
          </button>
          <button onClick={submit} disabled={!canSearch || loading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium">
            <MagnifyingGlassIcon className="w-3.5 h-3.5" />{loading ? 'Querying…' : 'Run Query'}
          </button>
        </div>
      </div>

      {/* ── Live stream panel ─────────────────────────────────────────── */}
      {liveMode && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 p-3 rounded-lg text-sm font-medium bg-emerald-50 text-emerald-800 border border-emerald-200">
            <span className="inline-flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              Live Stream
            </span>
            <span className="text-xs font-normal text-emerald-600">
              {liveEntries.length} entries accumulated · polling every 10 s (30 s lookback per poll)
            </span>
            {liveLoading && (
              <span className="text-xs text-emerald-500 italic">Polling…</span>
            )}
            <button
              onClick={() => { setLiveEntries([]); seenKeysRef.current.clear(); }}
              className="ml-auto text-xs text-emerald-700 hover:text-emerald-900 border border-emerald-300 rounded px-2 py-0.5 hover:bg-emerald-100 transition-colors"
            >
              Clear
            </button>
          </div>

          {liveEntries.length === 0 && !liveLoading && (
            <div className="text-center py-8 text-sm text-gray-400 italic">
              Waiting for new log entries…
            </div>
          )}

          {liveEntries.length > 0 && (
            <div className="overflow-x-auto rounded-xl border border-emerald-200 max-h-96 overflow-y-auto">
              <table
                className="text-xs border-collapse"
                style={{ tableLayout: 'fixed', width: LIVE_COLS.reduce((s, c) => s + liveColWidths[c.key], 0) }}
              >
                <colgroup>
                  {LIVE_COLS.map((c) => <col key={c.key} style={{ width: liveColWidths[c.key] }} />)}
                </colgroup>
                <thead className="bg-emerald-50 border-b border-emerald-200 sticky top-0">
                  <tr className="text-emerald-700 text-left">
                    {LIVE_COLS.map((c) => (
                      <th
                        key={c.key}
                        className="px-3 py-0 whitespace-nowrap select-none relative group"
                        style={{ width: liveColWidths[c.key] }}
                      >
                        <div className="flex items-center py-2 font-medium text-xs">{c.label}</div>
                        <span
                          onMouseDown={(e) => onLiveResizeStart(c.key, e)}
                          className="absolute right-0 top-0 h-full w-2 cursor-col-resize flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                        >
                          <span className="w-0.5 h-4 bg-emerald-400 rounded-full" />
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-emerald-100">
                  {liveEntries.map((entry, i) => (
                    <tr key={i} className="hover:bg-emerald-50/50 transition-colors">
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap text-gray-500">
                        {new Date(entry.TimeGenerated).toLocaleString('sv', { timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </td>
                      <td className="px-3 py-1.5 overflow-hidden">
                        <span className={`px-1.5 py-0.5 rounded-full text-xs font-medium ${ACTION_COLORS[entry.Action] ?? 'bg-gray-100 text-gray-600'}`}>
                          {entry.Action}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">{entry.RuleType}</td>
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap" title={entry.SourceIp}>{entry.SourceIp || '—'}</td>
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap" title={entry.DestinationIp || entry.TargetUrl}>{entry.DestinationIp || entry.TargetUrl || '—'}</td>
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap">{entry.DestinationPort || '—'}</td>
                      <td className="px-3 py-1.5 overflow-hidden text-ellipsis whitespace-nowrap">{entry.Protocol || '—'}</td>
                      <td className="px-3 py-1.5 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.Rule}>{entry.Rule || '—'}</td>
                      <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollection}>{entry.RuleCollection || '—'}</td>
                      <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollectionGroup}>{entry.RuleCollectionGroup || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────── */}
      {result && (
        <div className="space-y-3">
          <div className={`flex items-center gap-2 p-3 rounded-lg text-sm font-medium ${
            result.total > 0 ? 'bg-blue-50 text-blue-800 border border-blue-200' : 'bg-gray-50 text-gray-600 border border-gray-200'
          }`}>
            <MagnifyingGlassIcon className="w-4 h-4" />
            {result.total} of {result.matched.toLocaleString()} log entr{result.matched !== 1 ? 'ies' : 'y'}
            <span className="font-normal text-xs ml-1">
              ({new Date(result.start_time).toLocaleString('sv', { timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })} → {new Date(result.end_time).toLocaleString('sv', { timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })} UTC)
            </span>
            {result.entries.length > 0 && (
              <button
                onClick={() => {
                  const headers = ['Time', 'Action', 'Type', 'Source', 'Destination', 'Port', 'Protocol', 'Rule', 'Collection'];
                  const rows = result.entries.map((e) => [
                    e.TimeGenerated,
                    e.Action,
                    e.RuleType,
                    e.SourceIp,
                    e.DestinationIp || e.TargetUrl,
                    e.DestinationPort,
                    e.Protocol,
                    e.Rule,
                    e.RuleCollection,
                  ]);
                  const csv = [headers, ...rows].map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
                  const blob = new Blob([csv], { type: 'text/csv' });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url;
                  a.download = `firewall-logs-${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.csv`;
                  a.click();
                  URL.revokeObjectURL(url);
                }}
                className="ml-auto flex items-center gap-1.5 px-3 py-1 text-xs border border-blue-300 bg-white text-blue-700 rounded-lg hover:bg-blue-50 transition-colors font-medium"
              >
                <ArrowDownTrayIcon className="w-3.5 h-3.5" />
                Export CSV
              </button>
            )}
          </div>

          {result.entries.length > 0 && (
            <div className="overflow-x-auto rounded-xl border border-gray-200">
              <table className="text-xs border-collapse" style={{ tableLayout: 'fixed', width: COLS.reduce((s, c) => s + colWidths[c.key], 0) }}>
                <colgroup>
                  {COLS.map((c) => <col key={c.key} style={{ width: colWidths[c.key] }} />)}
                </colgroup>
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr className="text-gray-500 text-left">
                    {COLS.map((c) => {
                      const checkedSet   = colFilters[c.key] ?? new Set<string>();
                      const isFiltered   = checkedSet.size > 0;
                      const isFilterOpen = filterOpen === c.key;
                      // All unique values for this column from the full result set
                      const allVals = colUniqueVals[c.key] ?? [];
                      const searchLower  = filterSearch.toLowerCase();
                      const filteredVals = filterOpen === c.key && filterSearch
                        ? allVals.filter((v) => v.toLowerCase().includes(searchLower))
                        : allVals;
                      const allChecked   = filteredVals.length > 0 && filteredVals.every((v) => checkedSet.has(v));
                      return (
                        <th
                          key={c.key}
                          className="px-3 py-0 whitespace-nowrap select-none relative group"
                          style={{ width: colWidths[c.key] ?? c.defaultW }}
                        >
                          <div className="flex items-center gap-0.5 py-2">
                            <button
                              onClick={() => toggleSort(c.key)}
                              className="flex items-center gap-0.5 hover:text-gray-800 font-medium text-xs"
                            >
                              {c.label}
                              {sortCol === c.key
                                ? sortDir === 'asc'
                                  ? <ChevronUpIcon className="w-3 h-3 text-indigo-500" />
                                  : <ChevronDownIcon className="w-3 h-3 text-indigo-500" />
                                : <ChevronUpIcon className="w-3 h-3 opacity-0 group-hover:opacity-30" />}
                            </button>
                            <button
                              onClick={(ev) => {
                                ev.stopPropagation();
                                if (isFilterOpen) { setFilterOpen(null); setFilterSearch(''); }
                                else { setFilterOpen(c.key); setFilterSearch(''); }
                              }}
                              className={`ml-auto p-0.5 rounded hover:bg-gray-200 transition-colors ${isFiltered ? 'text-indigo-500' : 'text-gray-300 hover:text-gray-500'}`}
                              title="Filter"
                            >
                              <FunnelIcon className="w-3 h-3" />
                            </button>
                          </div>

                          {/* Floating dropdown panel */}
                          {isFilterOpen && (
                            <div
                              ref={filterPanelRef}
                              className="absolute z-50 top-full left-0 mt-1 w-52 bg-white border border-gray-200 rounded-xl shadow-lg"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {/* Search */}
                              <div className="p-2 border-b border-gray-100">
                                <input
                                  autoFocus
                                  type="text"
                                  placeholder="Search values…"
                                  value={filterSearch}
                                  onChange={(e) => setFilterSearch(e.target.value)}
                                  onKeyDown={(e) => e.key === 'Escape' && (setFilterOpen(null), setFilterSearch(''))}
                                  className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-1 focus:ring-indigo-400"
                                />
                              </div>
                              {/* Select all */}
                              <div className="px-2 py-1 border-b border-gray-100">
                                <label className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-800 cursor-pointer py-0.5">
                                  <input
                                    type="checkbox"
                                    checked={allChecked}
                                    onChange={() => {
                                      setColFilters((prev) => {
                                        const next = new Set(prev[c.key] ?? []);
                                        if (allChecked) filteredVals.forEach((v) => next.delete(v));
                                        else filteredVals.forEach((v) => next.add(v));
                                        return { ...prev, [c.key]: next };
                                      });
                                    }}
                                    className="accent-indigo-500"
                                  />
                                  <span className="font-medium">(Select all)</span>
                                </label>
                              </div>
                              {/* Values list */}
                              <div className="max-h-52 overflow-y-auto px-2 py-1">
                                {filteredVals.length === 0 && (
                                  <p className="text-xs text-gray-400 py-2 text-center">No values</p>
                                )}
                                {filteredVals.map((val) => (
                                  <label key={val} className="flex items-center gap-2 text-xs text-gray-700 hover:text-gray-900 cursor-pointer py-0.5">
                                    <input
                                      type="checkbox"
                                      checked={checkedSet.has(val)}
                                      onChange={() => toggleFilterValue(c.key, val)}
                                      className="accent-indigo-500"
                                    />
                                    <span className="truncate" title={val}>{val || '(empty)'}</span>
                                  </label>
                                ))}
                              </div>
                              {/* Footer */}
                              {isFiltered && (
                                <div className="px-2 py-1.5 border-t border-gray-100">
                                  <button
                                    onClick={() => { clearColFilter(c.key); setFilterOpen(null); setFilterSearch(''); }}
                                    className="text-xs text-red-500 hover:text-red-700 font-medium"
                                  >
                                    Clear filter
                                  </button>
                                </div>
                              )}
                            </div>
                          )}

                          {/* Resize handle */}
                          <span
                            onMouseDown={(e) => onResizeStart(c.key, e)}
                            className="absolute right-0 top-0 h-full w-2 cursor-col-resize flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            <span className="w-0.5 h-4 bg-gray-400 rounded-full" />
                          </span>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {visibleEntries.map((entry, i) => (
                    <tr key={i} className="hover:bg-gray-50 transition-colors">
                      <td className="px-3 py-1.5 font-mono whitespace-nowrap text-gray-500 overflow-hidden text-ellipsis">
                        {new Date(entry.TimeGenerated).toLocaleString('sv', { timeZone: 'UTC', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                      </td>
                      <td className="px-3 py-1.5 overflow-hidden">
                        <span className={`px-1.5 py-0.5 rounded-full text-xs font-medium ${ACTION_COLORS[entry.Action] ?? 'bg-gray-100 text-gray-600'}`}>
                          {entry.Action}
                        </span>
                      </td>
                      <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap">{entry.RuleType}</td>
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap" title={entry.SourceIp}>{entry.SourceIp || '—'}</td>
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap" title={entry.DestinationIp || entry.TargetUrl}>{entry.DestinationIp || entry.TargetUrl || '—'}</td>
                      <td className="px-3 py-1.5 font-mono overflow-hidden text-ellipsis whitespace-nowrap">{entry.DestinationPort || '—'}</td>
                      <td className="px-3 py-1.5 overflow-hidden text-ellipsis whitespace-nowrap">{entry.Protocol || '—'}</td>
                      <td className="px-3 py-1.5 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.Rule}>{entry.Rule || '—'}</td>
                      <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollection}>{entry.RuleCollection || '—'}</td>
                      <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollectionGroup}>{entry.RuleCollectionGroup || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
      </>)}
    </div>
  );
}

function logFieldPlaceholder(field: string): string {
  const map: Record<string, string> = {
    source_ip:       'e.g. 10.33.5.1',
    dest_ip:         'e.g. 52.239.0.1',
    dest_port:       'e.g. 443',
    dest_fqdn:       'e.g. *.microsoft.com',
    rule_name:       'e.g. prod-allow',
    rule_collection: 'e.g. from-chkp',
    rule_group:      'e.g. checkpoint-rcg',
  };
  return map[field] ?? '';
}

'use client';

import { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
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

const IP_FIELDS       = new Set(['source_ip', 'dest_ip']);
const FQDN_FIELDS     = new Set(['dest_fqdn']);
const PORT_FIELDS     = new Set(['dest_port']);
const DROPDOWN_FIELDS = new Set(['protocol', 'action', 'type']);
const IP_OPERATORS: { value: string; label: string }[] = [
  { value: 'matches',     label: 'matches' },
  { value: 'is_exactly',  label: 'is exactly' },
  { value: 'matches_in',  label: 'matches in' },
  { value: 'exactly_in',  label: 'exactly in' },
  { value: 'starts_with', label: 'starts with' },
  { value: 'ends_with',   label: 'ends with' },
];
const FQDN_OPERATORS: { value: string; label: string }[] = [
  { value: 'contains',     label: 'contains' },
  { value: 'not_contains', label: 'does not contain' },
  { value: 'equals',       label: 'equals' },
  { value: 'exactly_in',   label: 'exactly in' },
  { value: 'starts_with',  label: 'starts with' },
  { value: 'ends_with',    label: 'ends with' },
];
const PORT_OPERATORS: { value: string; label: string }[] = [
  { value: 'equals',      label: 'equals' },
  { value: 'not_equals',  label: 'not equals' },
  { value: 'contains',    label: 'contains' },
  { value: 'exactly_in',  label: 'exactly in' },
];
const DROPDOWN_OPERATORS = OPERATORS.filter(o => ['equals','not_equals'].includes(o.value));

function operatorsFor(field: string) {
  if (DROPDOWN_FIELDS.has(field)) return DROPDOWN_OPERATORS;
  if (IP_FIELDS.has(field))       return IP_OPERATORS;
  if (FQDN_FIELDS.has(field))     return FQDN_OPERATORS;
  if (PORT_FIELDS.has(field))     return PORT_OPERATORS;
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


const IN_OPERATORS = new Set(['matches_in', 'exactly_in']);

/** Bubble/tag input — value stored as comma-separated string */
function TagInput({ value, onChange, placeholder, onSubmit, operator }: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  onSubmit?: () => void;
  operator?: string;
}) {
  const [draft, setDraft]           = useState('');
  const [pendingDelete, setPending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const tags = value ? value.split(',').map(t => t.trim()).filter(Boolean) : [];

  const isMatchesIn = operator === 'matches_in';
  const pillCls = isMatchesIn
    ? 'bg-violet-50 text-violet-700 ring-1 ring-violet-200'
    : 'bg-blue-50 text-blue-700 ring-1 ring-blue-200';

  const addTag = (raw: string) => {
    const t = raw.trim();
    if (!t || tags.includes(t)) { setDraft(''); return; }
    onChange([...tags, t].join(', '));
    setDraft('');
    setPending(false);
  };

  const removeTag = (idx: number) => {
    onChange(tags.filter((_, i) => i !== idx).join(', '));
    setPending(false);
    inputRef.current?.focus();
  };

  return (
    <div
      className="flex flex-wrap items-center gap-1 flex-1 min-w-[160px] min-h-[34px] px-2 py-1.5 border border-gray-300 rounded-lg bg-white focus-within:border-indigo-400 focus-within:ring-1 focus-within:ring-indigo-300 cursor-text transition-colors"
      onClick={() => inputRef.current?.focus()}
    >
      {tags.map((tag, i) => (
        <span
          key={i}
          className={`inline-flex items-center gap-1 text-[11px] font-mono font-medium rounded px-1.5 py-[2px] ${pillCls} ${i === tags.length - 1 && pendingDelete ? 'ring-red-400 bg-red-50 text-red-600' : ''}`}
        >
          {tag}
          <button
            type="button"
            onMouseDown={e => { e.preventDefault(); removeTag(i); }}
            tabIndex={-1}
            className="opacity-40 hover:opacity-100 transition-opacity"
          >
            <XMarkIcon className="w-2.5 h-2.5" />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        type="text"
        value={draft}
        placeholder={tags.length === 0 ? (placeholder ?? 'e.g. 10.0.0.1, 192.168.0.0/24') : ''}
        onChange={e => { setDraft(e.target.value); setPending(false); }}
        onKeyDown={e => {
          if ((e.key === 'Enter' || e.key === ',') && draft.trim()) {
            e.preventDefault(); addTag(draft);
          } else if (e.key === 'Enter' && !draft && onSubmit) {
            e.preventDefault(); onSubmit();
          } else if (e.key === 'Backspace' && !draft && tags.length > 0) {
            if (pendingDelete) removeTag(tags.length - 1);
            else setPending(true);
          } else {
            setPending(false);
          }
        }}
        onBlur={() => { addTag(draft); setPending(false); }}
        className="flex-1 min-w-[80px] text-sm outline-none bg-transparent placeholder:text-gray-400 placeholder:text-xs"
      />
    </div>
  );
}

/** Helper to render rule field with special styling for "Default Deny" */
function renderRuleField(value: string | undefined): React.ReactNode {
  const val = value || '—';
  const isDefaultDeny = val === 'Default Deny';
  
  if (isDefaultDeny) {
    return (
      <span className="text-xs italic text-gray-400" title="Traffic denied by default firewall policy (no matching rule)">
        default deny
      </span>
    );
  }
  
  return val;
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
  // Real-time streaming via WebSocket (1-3 second latency from Event Hub)
  // Use same limit as log search results (virtualizer can handle large datasets)

  const [liveMode,     setLiveMode]     = useState(false);
  const [liveEntries,  setLiveEntries]  = useState<LogEntry[]>([]);
  const [liveLoading,  setLiveLoading]  = useState(false);
  const wsRef        = useRef<WebSocket | null>(null);
  const seenKeysRef  = useRef<Set<string>>(new Set());
  const limitRef     = useRef(limit);
  
  // Client-side batching to prevent excessive re-renders
  const pendingLogsRef = useRef<LogEntry[]>([]);
  const flushTimerRef  = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Virtual scrolling for live stream
  const liveScrollRef = useRef<HTMLDivElement>(null);
  const LIVE_ROW_HEIGHT = 30;
  const liveRowVirtualizer = useVirtualizer({
    count: liveEntries.length,
    getScrollElement: () => liveScrollRef.current,
    estimateSize: () => LIVE_ROW_HEIGHT,
    overscan: 20,
  });

  const liveKey = (e: LogEntry) =>
    `${e.TimeGenerated}|${e.SourceIp}|${e.DestinationIp}|${e.DestinationPort}|${e.Rule}`;

  // Update limitRef when limit changes
  useEffect(() => {
    limitRef.current = limit;
  }, [limit]);

  // Batch flush function - processes pending logs in one React update
  const flushPendingLogs = useCallback(() => {
    if (pendingLogsRef.current.length === 0) return;
    
    const newLogs = pendingLogsRef.current;
    pendingLogsRef.current = [];
    
    setLiveEntries(prev => {
      // Combine existing logs with new logs (append new to end for chronological order)
      const combined = [...prev, ...newLogs];
      
      // Sort by TimeGenerated ASCENDING (oldest first, newest last - like a terminal)
      combined.sort((a, b) => a.TimeGenerated.localeCompare(b.TimeGenerated));
      
      // Remove duplicates and keep only the LAST N logs (most recent within limit)
      const seen = new Set<string>();
      const deduplicated: LogEntry[] = [];
      
      // Process from end backwards to keep newest logs when trimming to limit
      for (let i = combined.length - 1; i >= 0 && deduplicated.length < limitRef.current; i--) {
        const entry = combined[i];
        const k = liveKey(entry);
        if (!seen.has(k)) {
          seen.add(k);
          deduplicated.unshift(entry); // Add to front since iterating backwards
        }
      }
      
      // Update seenKeysRef
      seenKeysRef.current = seen;
      
      return deduplicated;
    });
    
    // Scroll to bottom to show newest logs (natural streaming flow)
    setTimeout(() => {
      const lastIndex = Math.max(0, liveRowVirtualizer.options.count - 1);
      liveRowVirtualizer.scrollToIndex(lastIndex, { align: 'end' });
    }, 0);
  }, [liveRowVirtualizer]);

  const startLive = useCallback(() => {
    if (!workspace.trim()) return;
    
    seenKeysRef.current.clear();
    setLiveEntries([]);
    setLiveMode(true);
    setLiveLoading(true);

    // Build WebSocket URL
    const baseUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    const wsUrl = baseUrl.replace(/^http/, 'ws') + 
      `/api/firewall/${ctx.subscription_id}/${ctx.resource_group}/${ctx.policy_name}/logs/stream`;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('[Stream] WebSocket connected');
        setLiveLoading(false);
        
        // Send filter configuration
        const filters = {
          groups: groups.map(g => ({
            conditions: g.conditions
              .filter(c => c.value.trim())
              .map(c => ({ field: c.field, operator: c.operator, value: c.value })),
            intra_connector: g.intraConnector,
            inter_connector: g.interConnector,
          })),
        };
        ws.send(JSON.stringify({ filters }));
        console.log('[Stream] Filters sent:', filters);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          
          // Handle ping messages (keep-alive)
          if (data.type === 'ping') {
            return;
          }
          
          if (data.log) {
            const entry = data.log as LogEntry;
            const k = liveKey(entry);
            
            // Deduplicate
            if (!seenKeysRef.current.has(k)) {
              // Add to pending buffer instead of immediate state update
              pendingLogsRef.current.push(entry);
              
              // Schedule batch flush (100ms debounce for smooth rendering)
              if (flushTimerRef.current) {
                clearTimeout(flushTimerRef.current);
              }
              flushTimerRef.current = setTimeout(() => {
                flushPendingLogs();
                flushTimerRef.current = null;
              }, 100);
            }
          }
        } catch (err) {
          console.error('[Stream] Failed to parse message:', err);
        }
      };

      ws.onerror = (error) => {
        console.error('[Stream] WebSocket error:', error);
        setLiveLoading(false);
      };

      ws.onclose = () => {
        console.log('[Stream] WebSocket disconnected');
        setLiveLoading(false);
      };

    } catch (err) {
      console.error('[Stream] Failed to create WebSocket:', err);
      setLiveLoading(false);
      setLiveMode(false);
    }
  }, [workspace, ctx, groups, limit, liveRowVirtualizer]);

  const stopLive = useCallback(() => {
    setLiveMode(false);
    
    // Clear flush timer and do final flush
    if (flushTimerRef.current) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    flushPendingLogs();
    
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, [flushPendingLogs]);

  // Update filters when groups change (if streaming is active)
  useEffect(() => {
    if (liveMode && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const filters = {
        groups: groups.map(g => ({
          conditions: g.conditions
            .filter(c => c.value.trim())
            .map(c => ({ field: c.field, operator: c.operator, value: c.value })),
          intra_connector: g.intraConnector,
          inter_connector: g.interConnector,
        })),
      };
      wsRef.current.send(JSON.stringify({ filters }));
      console.log('[Stream] Filters updated:', filters);
    }
  }, [liveMode, groups]);

  // Cleanup on unmount
  useEffect(() => () => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

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

  // Sort & column filter state (for live stream)
  const [liveSortCol, setLiveSortCol]         = useState<string>('time');
  const [liveSortDir, setLiveSortDir]         = useState<SortDir>('asc'); // Default ascending for streaming
  const [liveColFilters, setLiveColFilters]   = useState<Record<string, Set<string>>>({});
  const [liveFilterOpen, setLiveFilterOpen]   = useState<string | null>(null);
  const [liveFilterSearch, setLiveFilterSearch] = useState<string>('');
  const liveFilterPanelRef = useRef<HTMLDivElement>(null);

  // Close filter panels on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (filterPanelRef.current && !filterPanelRef.current.contains(e.target as Node)) {
        setFilterOpen(null);
        setFilterSearch('');
      }
      if (liveFilterPanelRef.current && !liveFilterPanelRef.current.contains(e.target as Node)) {
        setLiveFilterOpen(null);
        setLiveFilterSearch('');
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

  // Live stream sort & filter functions
  const toggleLiveSort = (key: string) => {
    if (liveSortCol === key) setLiveSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setLiveSortCol(key); setLiveSortDir('asc'); }
  };

  const toggleLiveFilterValue = (colKey: string, val: string) => {
    setLiveColFilters((prev) => {
      const next = new Set(prev[colKey] ?? []);
      if (next.has(val)) next.delete(val); else next.add(val);
      return { ...prev, [colKey]: next };
    });
  };

  const clearLiveColFilter = (colKey: string) => {
    setLiveColFilters((prev) => { const n = { ...prev }; delete n[colKey]; return n; });
  };

  // Filtered and sorted live entries
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const visibleLiveEntries = useMemo(() => {
    let rows = liveEntries.slice();
    for (const [key, checked] of Object.entries(liveColFilters)) {
      if (!checked.size) continue;
      const getter = COL_FIELD[key];
      rows = rows.filter((e) => checked.has(getter(e)));
    }
    const getter = COL_FIELD[liveSortCol];
    rows.sort((a, b) => {
      const av = getter(a), bv = getter(b);
      return liveSortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    return rows;
  }, [liveEntries, liveColFilters, liveSortCol, liveSortDir]);

  // Pre-compute unique values per column for live stream
  const liveColUniqueVals = useMemo<Record<string, string[]>>(() => {
    if (liveEntries.length === 0) return {};
    return Object.fromEntries(
      LIVE_COLS.map((c) => [
        c.key,
        Array.from(new Set(liveEntries.map(COL_FIELD[c.key])))
          .filter(Boolean)
          .sort((a, b) => a.localeCompare(b)),
      ])
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveEntries]);

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

  // Virtual scroll container ref — used by the row virtualizer
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const ROW_HEIGHT = 30; // px — must match actual row height (py-1.5 + text-xs)
  const rowVirtualizer = useVirtualizer({
    count: visibleEntries.length,
    getScrollElement: () => tableScrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 25,
  });

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
              {[200, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000].map((n) => <option key={n} value={n}>{n.toLocaleString()}</option>)}
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
                        const ops = operatorsFor(f); const op = ops.find(o => o.value === cond.operator) ? cond.operator : ops[0].value;
                        updateCond(group.id, cond.id, { field: f, value: '', operator: op });
                      }}
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                      {LOG_FIELDS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                    </select>
                    <select value={cond.operator} onChange={e => updateCond(group.id, cond.id, { operator: e.target.value })}
                      className="text-sm min-w-[140px] border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                      {operatorsFor(cond.field).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    {VALUE_HINTS[cond.field] ? (
                      <select value={cond.value} onChange={e => updateCond(group.id, cond.id, { value: e.target.value })}
                        className="flex-1 min-w-[140px] text-sm border border-gray-300 rounded-lg px-2 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none">
                        <option value="">— select —</option>
                        {VALUE_HINTS[cond.field].map(v => <option key={v} value={v}>{v}</option>)}
                      </select>
                    ) : IN_OPERATORS.has(cond.operator) ? (
                      <TagInput
                        value={cond.value}
                        onChange={v => updateCond(group.id, cond.id, { value: v })}
                        placeholder={logFieldPlaceholder(cond.field, cond.operator)}
                        onSubmit={canSearch ? submit : undefined}
                        operator={cond.operator}
                      />
                    ) : (
                      <input type="text" placeholder={logFieldPlaceholder(cond.field, cond.operator)} value={cond.value}
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
      {(liveMode || liveEntries.length > 0) && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 p-3 rounded-lg text-sm font-medium bg-emerald-50 text-emerald-800 border border-emerald-200">
            <span className="inline-flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${liveMode ? 'bg-emerald-500 animate-pulse' : 'bg-gray-400'}`} />
              Live Stream {!liveMode && '(Stopped)'}
            </span>
            <span className="text-xs font-normal text-emerald-600">
              {visibleLiveEntries.length.toLocaleString()} / {liveEntries.length.toLocaleString()} displayed · {limit.toLocaleString()} limit · real-time stream
            </span>
            {liveLoading && (
              <span className="text-xs text-emerald-500 italic">Connecting…</span>
            )}
            <div className="ml-auto flex items-center gap-2">
              {liveEntries.length > 0 && (
                <button
                  onClick={() => {
                    const headers = ['Time', 'Action', 'Type', 'Source', 'Destination', 'Port', 'Protocol', 'Rule', 'Collection', 'Collection Group'];
                    const rows = visibleLiveEntries.map((e) => [
                      e.TimeGenerated,
                      e.Action,
                      e.RuleType,
                      e.SourceIp,
                      e.DestinationIp || e.TargetUrl,
                      e.DestinationPort,
                      e.Protocol,
                      e.Rule,
                      e.RuleCollection,
                      e.RuleCollectionGroup,
                    ]);
                    const csv = [headers, ...rows].map((r) => r.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
                    const blob = new Blob([csv], { type: 'text/csv' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `firewall-livestream-${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.csv`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }}
                  className="flex items-center gap-1.5 px-3 py-1 text-xs border border-emerald-300 bg-white text-emerald-700 rounded-lg hover:bg-emerald-50 transition-colors font-medium"
                >
                  <ArrowDownTrayIcon className="w-3.5 h-3.5" />
                  Export CSV
                </button>
              )}
              <button
                onClick={() => { setLiveEntries([]); seenKeysRef.current.clear(); }}
                className="text-xs text-emerald-700 hover:text-emerald-900 border border-emerald-300 rounded px-2 py-0.5 hover:bg-emerald-100 transition-colors"
              >
                Clear
              </button>
            </div>
          </div>

          {liveEntries.length === 0 && !liveLoading && (
            <div className="text-center py-8 text-sm text-gray-400 italic">
              Waiting for new log entries…
            </div>
          )}

          {liveEntries.length > 0 && (
            <div 
              ref={liveScrollRef}
              className="overflow-x-auto rounded-xl border border-emerald-200 overflow-y-auto"
              style={{ height: '600px' }}
            >
              <table
                className="text-xs border-collapse"
                style={{ tableLayout: 'fixed', width: LIVE_COLS.reduce((s, c) => s + liveColWidths[c.key], 0) }}
              >
                <colgroup>
                  {LIVE_COLS.map((c) => <col key={c.key} style={{ width: liveColWidths[c.key] }} />)}
                </colgroup>
                <thead className="bg-emerald-50 border-b border-emerald-200 sticky top-0 z-10">
                  <tr className="text-emerald-700 text-left">
                    {LIVE_COLS.map((c) => {
                      const checkedSet = new Set(liveColFilters[c.key] ?? []);
                      const uniqueVals = liveColUniqueVals[c.key] ?? [];
                      const isFilterOpen = liveFilterOpen === c.key;
                      const isFiltered = checkedSet.size > 0;
                      const searchLower = liveFilterSearch.toLowerCase();
                      const filteredVals = uniqueVals.filter((v) => v.toLowerCase().includes(searchLower));
                      const allChecked = filteredVals.length > 0 && filteredVals.every((v) => checkedSet.has(v));
                      return (
                        <th
                          key={c.key}
                          className="px-3 py-0 whitespace-nowrap select-none relative group"
                          style={{ width: liveColWidths[c.key] }}
                        >
                          <div className="flex items-center gap-0.5 py-2">
                            <button
                              onClick={() => toggleLiveSort(c.key)}
                              className="flex items-center gap-0.5 hover:text-emerald-900 font-medium text-xs"
                            >
                              {c.label}
                              {liveSortCol === c.key
                                ? liveSortDir === 'asc'
                                  ? <ChevronUpIcon className="w-3 h-3 text-emerald-600" />
                                  : <ChevronDownIcon className="w-3 h-3 text-emerald-600" />
                                : <ChevronUpIcon className="w-3 h-3 opacity-0 group-hover:opacity-30" />}
                            </button>
                            <button
                              onClick={(ev) => {
                                ev.stopPropagation();
                                if (isFilterOpen) { setLiveFilterOpen(null); setLiveFilterSearch(''); }
                                else { setLiveFilterOpen(c.key); setLiveFilterSearch(''); }
                              }}
                              className={`ml-auto p-0.5 rounded hover:bg-emerald-200 transition-colors ${isFiltered ? 'text-emerald-600' : 'text-emerald-300 hover:text-emerald-500'}`}
                              title="Filter"
                            >
                              <FunnelIcon className="w-3 h-3" />
                            </button>
                          </div>

                          {/* Filter dropdown panel */}
                          {isFilterOpen && (
                            <div
                              ref={liveFilterPanelRef}
                              className="absolute z-50 top-full left-0 mt-1 w-52 bg-white border border-emerald-200 rounded-xl shadow-lg"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <div className="p-2 border-b border-emerald-100">
                                <input
                                  autoFocus
                                  type="text"
                                  placeholder="Search values…"
                                  value={liveFilterSearch}
                                  onChange={(e) => setLiveFilterSearch(e.target.value)}
                                  onKeyDown={(e) => e.key === 'Escape' && (setLiveFilterOpen(null), setLiveFilterSearch(''))}
                                  className="w-full text-xs border border-emerald-300 rounded-lg px-2 py-1 focus:outline-none focus:ring-1 focus:ring-emerald-400"
                                />
                              </div>
                              <div className="px-2 py-1 border-b border-emerald-100">
                                <label className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-800 cursor-pointer py-0.5">
                                  <input
                                    type="checkbox"
                                    checked={allChecked}
                                    onChange={() => {
                                      setLiveColFilters((prev) => {
                                        const next = new Set(prev[c.key] ?? []);
                                        if (allChecked) filteredVals.forEach((v) => next.delete(v));
                                        else filteredVals.forEach((v) => next.add(v));
                                        return { ...prev, [c.key]: next };
                                      });
                                    }}
                                    className="accent-emerald-500"
                                  />
                                  <span className="font-medium">(Select all)</span>
                                </label>
                              </div>
                              <div className="max-h-52 overflow-y-auto px-2 py-1">
                                {filteredVals.length === 0 && (
                                  <p className="text-xs text-gray-400 py-2 text-center">No values</p>
                                )}
                                {filteredVals.map((val) => (
                                  <label key={val} className="flex items-center gap-2 text-xs text-gray-700 hover:text-gray-900 cursor-pointer py-0.5">
                                    <input
                                      type="checkbox"
                                      checked={checkedSet.has(val)}
                                      onChange={() => toggleLiveFilterValue(c.key, val)}
                                      className="accent-emerald-500"
                                    />
                                    <span className="truncate" title={val}>{val || '(empty)'}</span>
                                  </label>
                                ))}
                              </div>
                              {isFiltered && (
                                <div className="px-2 py-1.5 border-t border-emerald-100">
                                  <button
                                    onClick={() => { clearLiveColFilter(c.key); setLiveFilterOpen(null); setLiveFilterSearch(''); }}
                                    className="text-xs text-red-500 hover:text-red-700 font-medium"
                                  >
                                    Clear filter
                                  </button>
                                </div>
                              )}
                            </div>
                          )}

                          <span
                            onMouseDown={(e) => onLiveResizeStart(c.key, e)}
                            className="absolute right-0 top-0 h-full w-2 cursor-col-resize flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            <span className="w-0.5 h-4 bg-emerald-400 rounded-full" />
                          </span>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody className="divide-y divide-emerald-100">
                  {(() => {
                    const vItems = liveRowVirtualizer.getVirtualItems();
                    const paddingTop = vItems.length > 0 ? vItems[0].start : 0;
                    const paddingBottom = vItems.length > 0 ? liveRowVirtualizer.getTotalSize() - vItems[vItems.length - 1].end : 0;
                    
                    return (
                      <>
                        {paddingTop > 0 && (
                          <tr><td style={{ height: paddingTop }} colSpan={LIVE_COLS.length} /></tr>
                        )}
                        {vItems.map((virtualRow) => {
                          const entry = visibleLiveEntries[virtualRow.index];
                          if (!entry) return null;  // Safety check for filtered results
                          const key = liveKey(entry);
                          return (
                            <tr key={key} className="hover:bg-emerald-50/50 transition-colors">
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
                              <td className="px-3 py-1.5 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.Rule}>{renderRuleField(entry.Rule)}</td>
                              <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollection}>{renderRuleField(entry.RuleCollection)}</td>
                              <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollectionGroup}>{renderRuleField(entry.RuleCollectionGroup)}</td>
                            </tr>
                          );
                        })}
                        {paddingBottom > 0 && (
                          <tr><td style={{ height: paddingBottom }} colSpan={LIVE_COLS.length} /></tr>
                        )}
                      </>
                    );
                  })()}
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
            {result.total !== visibleEntries.length && (
              <span className="text-xs font-normal text-blue-600">(filtered to {visibleEntries.length.toLocaleString()})</span>
            )}
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
            <div
              ref={tableScrollRef}
              className="overflow-x-auto overflow-y-auto rounded-xl border border-gray-200"
              style={{ height: '600px' }}
            >
              <table className="text-xs border-collapse" style={{ tableLayout: 'fixed', width: COLS.reduce((s, c) => s + colWidths[c.key], 0) }}>
                <colgroup>
                  {COLS.map((c) => <col key={c.key} style={{ width: colWidths[c.key] }} />)}
                </colgroup>
                <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
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
                <tbody>
                  {(() => {
                    const vItems = rowVirtualizer.getVirtualItems();
                    const paddingTop    = vItems.length > 0 ? vItems[0].start : 0;
                    const paddingBottom = vItems.length > 0 ? rowVirtualizer.getTotalSize() - vItems[vItems.length - 1].end : 0;
                    return (
                      <>
                        {paddingTop > 0 && <tr><td style={{ height: paddingTop }} colSpan={COLS.length} /></tr>}
                        {vItems.map((virtualRow) => {
                          const entry = visibleEntries[virtualRow.index];
                          return (
                            <tr key={virtualRow.index} className="hover:bg-gray-50 transition-colors border-b border-gray-100">
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
                              <td className="px-3 py-1.5 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.Rule}>{renderRuleField(entry.Rule)}</td>
                              <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollection}>{renderRuleField(entry.RuleCollection)}</td>
                              <td className="px-3 py-1.5 text-gray-500 overflow-hidden text-ellipsis whitespace-nowrap" title={entry.RuleCollectionGroup}>{renderRuleField(entry.RuleCollectionGroup)}</td>
                            </tr>
                          );
                        })}
                        {paddingBottom > 0 && <tr><td style={{ height: paddingBottom }} colSpan={COLS.length} /></tr>}
                      </>
                    );
                  })()}
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

function logFieldPlaceholder(field: string, operator?: string): string {
  if (operator === 'matches_in') {
    if (field === 'source_ip' || field === 'dest_ip')
      return 'e.g. 10.0.0.1, 192.168.0.0/24, 10.33.5.2';
  }
  if (operator === 'exactly_in') {
    if (field === 'source_ip' || field === 'dest_ip') return 'e.g. 10.0.0.1, 52.239.0.1';
    if (field === 'dest_fqdn')  return 'e.g. *.microsoft.com, api.github.com';
    if (field === 'dest_port')  return 'e.g. 80, 443, 8080';
  }
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

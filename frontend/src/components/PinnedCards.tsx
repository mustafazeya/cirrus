'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { apiService } from '../services/apiService';
import {
  PlusIcon,
  XMarkIcon,
  CurrencyDollarIcon,
  HeartIcon,
  ShieldCheckIcon,
  ClipboardDocumentCheckIcon,
  ArrowPathRoundedSquareIcon,
  ArrowPathIcon,
  PencilSquareIcon,
} from '@heroicons/react/24/outline';
import type { SavedView } from '../types/savedViews';

// ─── Types ────────────────────────────────────────────────────────────────────

type Metric = 'cost' | 'health' | 'security' | 'compliance' | 'changes';
type CardSize = 'sm' | 'md' | 'lg';

const REFRESH_INTERVAL_MS = 5 * 60 * 1000;  // 5 minutes for live metrics
const COST_CACHE_MS       = 60 * 60 * 1000; // 1 hour — matches backend cost cache TTL

interface CardLiveData {
  loading: boolean;
  value: number | null;
  currency?: string;
  error: boolean;
}

interface SubscriptionOption {
  subscription_id: string;
  display_name: string;
}

interface PinnedCardsProps {
  subscriptions: SubscriptionOption[];
}

// ─── Metric config ────────────────────────────────────────────────────────────

const METRIC_CONFIG: Record<Metric, {
  label: string;
  icon: React.ReactNode;
  iconColor: string;
  showsPeriod: boolean;
  unit: 'currency' | 'percent' | 'count';
  thresholds?: [number, number];
  changesThresholds?: [number, number];
}> = {
  cost: {
    label: 'Cost spend',
    icon: <CurrencyDollarIcon className="h-3.5 w-3.5" />,
    iconColor: 'text-emerald-500',
    showsPeriod: true,
    unit: 'currency',
  },
  health: {
    label: 'Resource health',
    icon: <HeartIcon className="h-3.5 w-3.5" />,
    iconColor: 'text-rose-400',
    showsPeriod: false,
    unit: 'percent',
    thresholds: [70, 90],
  },
  security: {
    label: 'Defender score',
    icon: <ShieldCheckIcon className="h-3.5 w-3.5" />,
    iconColor: 'text-blue-500',
    showsPeriod: false,
    unit: 'percent',
    thresholds: [60, 80],
  },
  compliance: {
    label: 'Policy compliance',
    icon: <ClipboardDocumentCheckIcon className="h-3.5 w-3.5" />,
    iconColor: 'text-violet-500',
    showsPeriod: false,
    unit: 'percent',
    thresholds: [70, 90],
  },
  changes: {
    label: 'Changes (24h)',
    icon: <ArrowPathRoundedSquareIcon className="h-3.5 w-3.5" />,
    iconColor: 'text-orange-500',
    showsPeriod: false,
    unit: 'count',
    changesThresholds: [10, 30],
  },
};

const SIZE_CONFIG: Record<CardSize, { label: string; minW: string; valueSize: string; showMetricLabel: boolean; showFullSubs: boolean }> = {
  sm: { label: 'Small',  minW: 'min-w-[140px]', valueSize: 'text-lg',   showMetricLabel: false, showFullSubs: false },
  md: { label: 'Medium', minW: 'min-w-[180px]', valueSize: 'text-xl',   showMetricLabel: true,  showFullSubs: false },
  lg: { label: 'Large',  minW: 'min-w-[240px]', valueSize: 'text-2xl',  showMetricLabel: true,  showFullSubs: true  },
};

const DATE_RANGE_LABELS: Record<string, string> = {
  this_month: 'This month',
  last_month: 'Last month',
  last_7_days: 'Last 7 days',
  last_30_days: 'Last 30 days',
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function resolveDates(dateRange: string) {
  const today = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const end = fmt(today);
  if (dateRange === 'last_month') {
    const e = new Date(today.getFullYear(), today.getMonth(), 0);
    return { startDate: fmt(new Date(e.getFullYear(), e.getMonth(), 1)), endDate: fmt(e) };
  }
  if (dateRange === 'last_7_days') {
    const d = new Date(today); d.setDate(d.getDate() - 7);
    return { startDate: fmt(d), endDate: end };
  }
  if (dateRange === 'last_30_days') {
    const d = new Date(today); d.setDate(d.getDate() - 30);
    return { startDate: fmt(d), endDate: end };
  }
  return { startDate: `${today.getFullYear()}-${pad(today.getMonth() + 1)}-01`, endDate: end };
}

function formatValue(metric: Metric, data: CardLiveData): string {
  if (data.error || data.value === null) return '—';
  const cfg = METRIC_CONFIG[metric];
  if (cfg.unit === 'percent') return `${data.value.toFixed(1)}%`;
  if (cfg.unit === 'count') return String(data.value);
  const v = data.value;
  const prefix = data.currency === 'USD' ? '$' : `${data.currency ?? ''} `;
  if (v >= 1_000_000) return `${prefix}${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${prefix}${(v / 1_000).toFixed(1)}k`;
  return `${prefix}${v.toFixed(0)}`;
}

function valueColor(metric: Metric, data: CardLiveData): string {
  if (data.value === null) return 'text-gray-400';
  const cfg = METRIC_CONFIG[metric];
  if (cfg.unit === 'percent' && cfg.thresholds) {
    const [warn, good] = cfg.thresholds;
    if (data.value >= good) return 'text-green-600';
    if (data.value >= warn) return 'text-amber-500';
    return 'text-red-500';
  }
  if (cfg.unit === 'count' && cfg.changesThresholds) {
    const [ok, warn] = cfg.changesThresholds;
    if (data.value <= ok) return 'text-gray-800';
    if (data.value <= warn) return 'text-amber-500';
    return 'text-red-500';
  }
  return 'text-gray-900';
}

function timeAgoShort(ms: number): string {
  const diff = Math.floor((Date.now() - ms) / 1000);
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function PinnedCards({ subscriptions }: PinnedCardsProps) {
  const [cards, setCards] = useState<SavedView[]>([]);
  const [liveData, setLiveData] = useState<Record<string, CardLiveData>>({});
  const fetchedAtRef = useRef<Record<string, number>>({}); // per-card last-fetch ms timestamp
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<number | null>(null);
  const [, setTick] = useState(0); // force re-render for time-ago label
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [subSearch, setSubSearch] = useState('');
  const [form, setForm] = useState({
    name: '',
    metric: 'cost' as Metric,
    size: 'md' as CardSize,
    selectedSubs: [] as string[],
    dateRange: 'this_month',
  });
  const [saving, setSaving] = useState(false);
  const cardsRef = useRef<SavedView[]>([]);
  cardsRef.current = cards;

  const subMap = Object.fromEntries(subscriptions.map(s => [s.subscription_id, s.display_name]));

  const fetchCardLiveData = useCallback(async (card: SavedView, force = false) => {
    const metric = (card.filters.card_metric ?? 'cost') as Metric;
    const isCost = metric === 'cost';

    // Skip cost re-fetches within the 1-hour backend cache window unless forced
    if (!force && isCost) {
      const lastFetch = fetchedAtRef.current[card.id];
      if (lastFetch && Date.now() - lastFetch < COST_CACHE_MS) return;
    }
    const subIds: string[] = card.filters.subscriptions ?? [];
    if (subIds.length === 0) {
      setLiveData(prev => ({ ...prev, [card.id]: { loading: false, value: null, error: true } }));
      return;
    }
    setLiveData(prev => ({ ...prev, [card.id]: { loading: true, value: null, error: false } }));
    try {
      let value: number | null = null;
      let currency: string | undefined;
      if (metric === 'cost') {
        const { startDate, endDate } = resolveDates(card.filters.date_range ?? 'this_month');
        const res = await apiService.getSubscriptionCosts(subIds, startDate, endDate, null);
        value = res?.total_cost ?? null; currency = res?.currency || 'USD';
      } else if (metric === 'health') {
        const res = await apiService.getResourceHealth(subIds);
        value = res?.overall_health_percentage ?? null;
      } else if (metric === 'security') {
        const res = await apiService.getSecurityScore(subIds);
        value = res?.overall_score ?? null;
      } else if (metric === 'compliance') {
        const res = await apiService.getPolicyCompliance(subIds);
        value = res?.overall_compliance_percentage ?? null;
      } else if (metric === 'changes') {
        const res = await apiService.getChangeAnalysis(subIds, 24);
        value = res?.total_changes ?? null;
      }
      setLiveData(prev => ({ ...prev, [card.id]: { loading: false, value, currency, error: false } }));
      fetchedAtRef.current[card.id] = Date.now();
    } catch {
      setLiveData(prev => ({ ...prev, [card.id]: { loading: false, value: null, error: true } }));
    }
  }, []);

  const refreshAll = useCallback(async (silent = false) => {
    const current = cardsRef.current;
    if (current.length === 0) return;
    if (!silent) setRefreshing(true);
    // Manual refresh forces all cards; silent timer respects cost cache
    const force = !silent;
    await Promise.all(current.map(c => fetchCardLiveData(c, force)));
    setLastRefreshed(Date.now());
    if (!silent) setRefreshing(false);
  }, [fetchCardLiveData]);

  const loadHomeCards = useCallback(async () => {
    try {
      const res = await apiService.listSavedViews();
      const home = (res.views ?? []).filter((v: SavedView) => v.view_type === 'home');
      setCards(home);
      return home;
    } catch {
      return [];
    } finally {
      setInitialLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    loadHomeCards().then(home => {
      if (home.length > 0) {
        Promise.all(home.map(fetchCardLiveData)).then(() => setLastRefreshed(Date.now()));
      } else {
        setLastRefreshed(Date.now());
      }
    });
  }, [loadHomeCards, fetchCardLiveData]);

  // Auto-refresh every 5 minutes
  useEffect(() => {
    const id = setInterval(() => refreshAll(true), REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refreshAll]);

  // Tick every 30s so time-ago label stays current
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const handleDelete = async (id: string) => {
    try {
      await apiService.deleteSavedView(id);
      setCards(prev => prev.filter(c => c.id !== id));
      setLiveData(prev => { const n = { ...prev }; delete n[id]; return n; });
    } catch {}
  };

  const resetForm = () => {
    setForm({ name: '', metric: 'cost', size: 'md', selectedSubs: [], dateRange: 'this_month' });
    setSubSearch('');
    setEditingId(null);
  };

  const handleEdit = (card: SavedView) => {
    setEditingId(card.id);
    setForm({
      name: card.name,
      metric: (card.filters.card_metric ?? 'cost') as Metric,
      size: (card.filters.card_size ?? 'md') as CardSize,
      selectedSubs: card.filters.subscriptions ?? [],
      dateRange: card.filters.date_range ?? 'this_month',
    });
    setAdding(true);
  };

  const handleSave = async () => {
    if (!form.name.trim() || form.selectedSubs.length === 0) return;
    setSaving(true);
    try {
      const filters = {
        subscriptions: form.selectedSubs,
        date_range: form.dateRange as any,
        card_metric: form.metric,
        card_size: form.size,
      };
      if (editingId) {
        await apiService.updateSavedView(editingId, { name: form.name.trim(), filters });
        await loadHomeCards();
        fetchCardLiveData(cardsRef.current.find(c => c.id === editingId)!);
      } else {
        await apiService.createSavedView({ name: form.name.trim(), view_type: 'home', filters });
        const home = await loadHomeCards();
        home.forEach(card => { if (!liveData[card.id]) fetchCardLiveData(card); });
      }
      resetForm();
      setAdding(false);
    } catch {} finally {
      setSaving(false);
    }
  };

  const filteredSubs = subscriptions.filter(s =>
    s.display_name.toLowerCase().includes(subSearch.toLowerCase())
  );

  if (initialLoading) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-[96px] bg-gray-100 rounded-xl animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-widest">Watchlist</span>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-[10px] text-gray-400">
              Refreshed {timeAgoShort(lastRefreshed)}
            </span>
          )}
          <button
            onClick={() => refreshAll(false)}
            disabled={refreshing}
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-40"
            title="Refresh now"
          >
            <ArrowPathIcon className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <div className="p-4 space-y-4">
        {/* ── Card grid ── */}
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3">
          {cards.length === 0 && !adding && (
            <p className="text-xs text-gray-400 py-6 px-2">
              No watchlist cards yet. Add one to track a specific metric across selected subscriptions.
            </p>
          )}
          {cards.map(card => {
            const metric = (card.filters.card_metric ?? 'cost') as Metric;
            const size = (card.filters.card_size ?? 'md') as CardSize;
            const cfg = METRIC_CONFIG[metric];
            const sizeCfg = SIZE_CONFIG[size];
            const data = liveData[card.id] ?? { loading: true, value: null, error: false };
            const subIds = card.filters.subscriptions ?? [];
            const valueStr = formatValue(metric, data);
            const color = valueColor(metric, data);
            const periodLabel = cfg.showsPeriod
              ? (DATE_RANGE_LABELS[card.filters.date_range ?? 'this_month'] ?? card.filters.date_range)
              : 'Live';

            const subLabel = subIds.length === 0
              ? 'No subscriptions'
              : subIds.length === 1
                ? (subMap[subIds[0]] ?? subIds[0])
                : sizeCfg.showFullSubs
                  ? subIds.slice(0, 3).map(id => subMap[id] ?? id).join(', ') + (subIds.length > 3 ? ` +${subIds.length - 3}` : '')
                  : `${subMap[subIds[0]] ?? subIds[0]}${subIds.length > 1 ? ` +${subIds.length - 1}` : ''}`;

            return (
              <div
                key={card.id}
                className={`relative bg-gray-50 border border-gray-200 rounded-xl p-4 shadow-sm group hover:border-gray-300 transition-colors`}
              >
                {/* Hover actions: edit + delete */}
                <div className="absolute top-2 right-2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => handleEdit(card)}
                    aria-label="Edit card"
                    className="p-0.5 rounded hover:bg-blue-50 text-gray-300 hover:text-blue-500"
                  >
                    <PencilSquareIcon className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => handleDelete(card.id)}
                    aria-label="Remove card"
                    className="p-0.5 rounded hover:bg-red-50 text-gray-300 hover:text-red-400"
                  >
                    <XMarkIcon className="h-3.5 w-3.5" />
                  </button>
                </div>

                {/* Icon + card name */}
                <div className="flex items-center gap-1.5 mb-2 pr-5">
                  <span className={cfg.iconColor}>{cfg.icon}</span>
                  <span className="text-xs font-semibold text-gray-600 truncate">{card.name}</span>
                </div>

                {/* Value */}
                {data.loading ? (
                  <div className="h-7 w-20 bg-gray-200 rounded animate-pulse mb-1.5" />
                ) : (
                  <p className={`${sizeCfg.valueSize} font-bold leading-none mb-1.5 ${color}`}>{valueStr}</p>
                )}

                {/* Metric label (md/lg only) */}
                {sizeCfg.showMetricLabel && (
                  <p className="text-xs text-gray-500 font-medium">{cfg.label}</p>
                )}

                {/* Subscription name */}
                <p
                  className="text-xs text-gray-600 font-medium truncate mt-1"
                  title={subIds.map(id => subMap[id] ?? id).join(', ')}
                >
                  {subLabel}
                </p>
                <p className="text-xs text-gray-400 mt-0.5">{periodLabel}</p>
              </div>
            );
          })}

          {/* Add card button */}
          <button
            onClick={() => setAdding(true)}
            className="flex flex-col items-center justify-center gap-1.5 px-5 border border-dashed border-gray-300 rounded-xl text-gray-400 hover:border-blue-400 hover:text-blue-600 transition-colors min-h-[96px]"
          >
            <PlusIcon className="h-4 w-4" />
            <span className="text-xs font-medium">Add card</span>
          </button>
        </div>

        {/* ── Add / Edit card form ── */}
        {adding && (
          <div
            ref={el => { if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }}
            className="border border-gray-200 rounded-xl p-5 bg-gray-50"
          >
            <div className="flex items-center justify-between mb-4">
              <h4 className="text-sm font-semibold text-gray-800">
                {editingId ? 'Edit Watchlist Card' : 'New Watchlist Card'}
              </h4>
              <button onClick={() => { setAdding(false); resetForm(); }} className="text-gray-400 hover:text-gray-600">
                <XMarkIcon className="h-4 w-4" />
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Name */}
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Card name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. Platform — MTD cost"
                  className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              {/* Period — cost only, else live note */}
              {form.metric === 'cost' ? (
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Period</label>
                  <select
                    value={form.dateRange}
                    onChange={e => setForm(f => ({ ...f, dateRange: e.target.value }))}
                    className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="this_month">This month</option>
                    <option value="last_month">Last month</option>
                    <option value="last_7_days">Last 7 days</option>
                    <option value="last_30_days">Last 30 days</option>
                  </select>
                </div>
              ) : (
                <div className="flex items-end pb-2">
                  <p className="text-xs text-gray-400">Always fetched live, auto-refreshes every 5 min.</p>
                </div>
              )}

              {/* Metric */}
              <div className="md:col-span-2">
                <label className="block text-xs font-medium text-gray-600 mb-2">Metric</label>
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                  {(Object.entries(METRIC_CONFIG) as [Metric, typeof METRIC_CONFIG[Metric]][]).map(([key, cfg]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setForm(f => ({ ...f, metric: key }))}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium border transition-colors ${
                        form.metric === key
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                      }`}
                    >
                      <span className={form.metric === key ? 'text-blue-600' : cfg.iconColor}>{cfg.icon}</span>
                      {cfg.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Size */}
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-2">Card size</label>
                <div className="flex gap-2">
                  {(Object.entries(SIZE_CONFIG) as [CardSize, typeof SIZE_CONFIG[CardSize]][]).map(([key, s]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setForm(f => ({ ...f, size: key }))}
                      className={`flex-1 py-2 rounded-lg text-xs font-medium border transition-colors ${
                        form.size === key
                          ? 'border-blue-500 bg-blue-50 text-blue-700'
                          : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                      }`}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Subscriptions */}
              <div className="md:col-span-2">
                <label className="block text-xs font-medium text-gray-600 mb-1">
                  Subscriptions
                  {form.selectedSubs.length > 0 && (
                    <span className="ml-1 text-blue-600">({form.selectedSubs.length} selected)</span>
                  )}
                </label>
                <input
                  type="text"
                  value={subSearch}
                  onChange={e => setSubSearch(e.target.value)}
                  placeholder="Search subscriptions…"
                  className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 mb-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
                />
                <div className="max-h-40 overflow-y-auto border border-gray-100 rounded-lg divide-y divide-gray-50 bg-white">
                  {filteredSubs.length === 0 ? (
                    <p className="px-3 py-2 text-xs text-gray-400">No subscriptions found</p>
                  ) : (
                    filteredSubs.slice(0, 50).map(sub => (
                      <label key={sub.subscription_id} className="flex items-center gap-2.5 px-3 py-2 hover:bg-gray-50 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={form.selectedSubs.includes(sub.subscription_id)}
                          onChange={e =>
                            setForm(f => ({
                              ...f,
                              selectedSubs: e.target.checked
                                ? [...f.selectedSubs, sub.subscription_id]
                                : f.selectedSubs.filter(id => id !== sub.subscription_id),
                            }))
                          }
                          className="rounded border-gray-300 text-blue-500 focus:ring-blue-400"
                        />
                        <span className="text-xs text-gray-700 truncate">{sub.display_name}</span>
                      </label>
                    ))
                  )}
                </div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 mt-4 pt-4 border-t border-gray-200">
              <button
                type="button"
                onClick={() => { setAdding(false); resetForm(); }}
                className="px-4 py-1.5 text-sm text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !form.name.trim() || form.selectedSubs.length === 0}
                className="px-4 py-1.5 text-sm font-medium bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {saving ? 'Saving…' : editingId ? 'Update Card' : 'Save Card'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}



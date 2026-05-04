'use client';

import React, { useState, useEffect, useRef } from 'react';
import { PlusIcon, ArrowPathIcon, ClockIcon, XMarkIcon, ChartBarIcon } from '@heroicons/react/24/outline';
import { apiService } from '../../services/apiService';
import { useAppStore } from '../../store/appStore';
import type { FirewallContext, FirewallDiagnosticInfo } from '../../types/firewall';
import FirewallMetricsCard, { FirewallMetricsCardConfig } from './FirewallMetricsCard';
import { FW_METRIC_CATALOGUE } from '../../types/firewall';

function uid() { return Math.random().toString(36).slice(2, 9); }

const TIME_PRESETS = [
  { label: '1h',   hours: 1 },
  { label: '6h',   hours: 6 },
  { label: '12h',  hours: 12 },
  { label: '24h',  hours: 24 },
  { label: '7d',   hours: 168 },
  { label: '30d',  hours: 720 },
];

function defaultCard(metricName = 'NetworkRuleHit'): FirewallMetricsCardConfig {
  const def = FW_METRIC_CATALOGUE.find((m) => m.name === metricName) ?? FW_METRIC_CATALOGUE[0];
  const now = new Date();
  const start = new Date(now.getTime() - 24 * 3600 * 1000);
  return {
    id:          uid(),
    metricName:  def.name,
    aggregation: def.defaultAgg,
    splitBy:     '',
    preset:      24,
    customStart: start.toISOString(),
    customEnd:   now.toISOString(),
    granularity: 'auto',
  };
}

/** Build a localStorage key scoped to user + firewall policy. */
function metricsStorageKey(userId: string, ctx: FirewallContext) {
  return `fw_metric_cards:${userId}:${ctx.subscription_id}:${ctx.resource_group}:${ctx.policy_name}`;
}

function loadCards(key: string): FirewallMetricsCardConfig[] | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    return null;
  } catch {
    return null;
  }
}

function saveCards(key: string, cards: FirewallMetricsCardConfig[]) {
  try { localStorage.setItem(key, JSON.stringify(cards)); } catch { /* quota exceeded */ }
}

interface GlobalTime {
  preset:      number;
  customStart: string;
  customEnd:   string;
}

interface Props {
  ctx: FirewallContext;
}

export default function FirewallMetrics({ ctx }: Props) {
  const { user } = useAppStore();
  const storageKey = metricsStorageKey(user?.id ?? 'anon', ctx);

  const [diagInfo, setDiagInfo]       = useState<FirewallDiagnosticInfo | null>(null);
  const [loadingDiag, setLoadingDiag] = useState(true);
  const [diagError, setDiagError]     = useState<string | null>(null);
  const [cards, setCards]             = useState<FirewallMetricsCardConfig[]>(
    () => loadCards(storageKey) ?? [defaultCard('NetworkRuleHit'), defaultCard('SNATPortUtilization')],
  );
  const [globalTime, setGlobalTime]   = useState<GlobalTime | null>(null);
  const [showCustom, setShowCustom]   = useState(false);
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd]     = useState('');

  // Persist cards whenever they change
  useEffect(() => { saveCards(storageKey, cards); }, [storageKey, cards]);

  useEffect(() => {
    (async () => {
      setLoadingDiag(true);
      setDiagError(null);
      try {
        const res = await apiService.firewallGetDiagnosticInfo(
          ctx.subscription_id, ctx.resource_group, ctx.policy_name,
        );
        setDiagInfo(res);
      } catch {
        setDiagError('Could not resolve the Azure Firewall instance linked to this policy.');
      } finally {
        setLoadingDiag(false);
      }
    })();
  }, [ctx.subscription_id, ctx.resource_group, ctx.policy_name]);

  const applyPreset = (hours: number) => {
    setShowCustom(false);
    setGlobalTime({ preset: hours, customStart: '', customEnd: '' });
  };

  const applyCustom = () => {
    if (!customStart || !customEnd) return;
    setGlobalTime({ preset: 0, customStart: new Date(customStart).toISOString(), customEnd: new Date(customEnd).toISOString() });
  };

  const clearGlobal = () => { setGlobalTime(null); setShowCustom(false); };

  const newCardRef = useRef<HTMLDivElement>(null);

  const addCard = (metricName?: string) => {
    setCards((prev) => [...prev, defaultCard(metricName)]);
    // Scroll to the new card after React re-renders
    setTimeout(() => {
      newCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
  };
  const removeCard = (id: string) => setCards((prev) => prev.filter((c) => c.id !== id));
  const updateCard = (id: string, cfg: FirewallMetricsCardConfig) =>
    setCards((prev) => prev.map((c) => (c.id === id ? cfg : c)));

  if (loadingDiag) {
    return (
      <div className="flex items-center justify-center gap-2 py-20 text-gray-400 text-sm">
        <ArrowPathIcon className="w-5 h-5 animate-spin" /> Resolving firewall instance…
      </div>
    );
  }

  if (diagError || !diagInfo?.firewall_found) {
    return (
      <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-5 text-sm text-yellow-800 space-y-1">
        <p className="font-semibold">Firewall instance not found</p>
        <p className="text-xs">{diagError ?? 'No Azure Firewall is linked to this policy in the subscription.'}</p>
      </div>
    );
  }

  const fwName = diagInfo.firewall_name!;
  const fwRg   = diagInfo.firewall_resource_group ?? ctx.resource_group;

  const activePresetLabel = globalTime
    ? globalTime.preset
      ? TIME_PRESETS.find((p) => p.hours === globalTime.preset)?.label ?? `${globalTime.preset}h`
      : 'Custom'
    : null;

  return (
    <div className="space-y-5">

      {/* ── Unified toolbar ───────────────────────────────────────────── */}
      <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-100">

        {/* Row 1: header */}
        <div className="px-5 py-3">
          <h3 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
            <ChartBarIcon className="w-4 h-4 text-indigo-500" />
            Firewall Metrics
          </h3>
        </div>

        {/* Row 2: firewall info + global time range */}
        <div className="px-5 py-3">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
            <div className="md:col-span-2">
              <label className="block text-xs font-medium text-gray-700 mb-1">Firewall</label>
              <input
                type="text"
                readOnly
                value={fwName}
                className="w-full border border-gray-200 bg-gray-50 rounded-lg px-3 py-1.5 text-sm text-gray-500 cursor-default select-all"
              />
            </div>
            <div className="md:col-span-2">
              <label className="block text-xs font-medium text-gray-700 mb-1 flex items-center gap-1">
                <ClockIcon className="w-3 h-3" /> Time Range
              </label>
              <div className="flex flex-wrap gap-1">
                {TIME_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    onClick={() => applyPreset(p.hours)}
                    className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                      !showCustom && globalTime?.preset === p.hours
                        ? 'bg-indigo-500 text-white border-indigo-500'
                        : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
                <button
                  onClick={() => setShowCustom((v) => !v)}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                    showCustom || globalTime?.preset === 0
                      ? 'bg-indigo-500 text-white border-indigo-500'
                      : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'
                  }`}
                >
                  Custom
                </button>
              </div>
              {globalTime && !showCustom && (
                <div className="flex items-center gap-2 mt-1.5">
                  <span className="text-xs text-indigo-600 font-medium">{activePresetLabel} applied to all cards</span>
                  <button onClick={clearGlobal} className="flex items-center gap-0.5 text-xs text-gray-400 hover:text-gray-700 transition-colors">
                    <XMarkIcon className="w-3 h-3" /> Reset
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Row 3: custom date pickers (conditional) */}
        {showCustom && (
          <div className="px-5 pb-3">
            <div className="flex gap-2 items-center mt-1.5">
              <input type="datetime-local" value={customStart} onChange={(e) => setCustomStart(e.target.value)}
                className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
              <span className="text-xs text-gray-400">to</span>
              <input type="datetime-local" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)}
                className="border border-gray-300 rounded-lg px-2 py-1 text-xs focus:ring-2 focus:ring-indigo-500 focus:outline-none" />
              <button
                onClick={applyCustom}
                disabled={!customStart || !customEnd}
                className="px-3 py-1 bg-indigo-600 text-white text-xs font-medium rounded-lg hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
              >
                Apply to all
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ── Cards ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 gap-5">
        {cards.map((card, idx) => (
          <div key={card.id} ref={idx === cards.length - 1 ? newCardRef : undefined}>
            <FirewallMetricsCard
              config={card}
              subscriptionId={ctx.subscription_id}
              resourceGroup={fwRg}
              firewallName={fwName}
              onRemove={() => removeCard(card.id)}
              onChange={(cfg) => updateCard(card.id, cfg)}
              globalTime={globalTime}
            />
          </div>
        ))}

        {/* Dashed add card tile */}
        <button
          onClick={() => addCard()}
          className="flex flex-col items-center justify-center gap-1.5 py-10 border border-dashed border-gray-300 rounded-xl text-gray-400 hover:border-indigo-400 hover:text-indigo-600 transition-colors"
        >
          <PlusIcon className="w-5 h-5" />
          <span className="text-xs font-medium">Add card</span>
        </button>
      </div>
    </div>
  );
}


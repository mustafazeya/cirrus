'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ChartBarIcon,
  XMarkIcon,
  ArrowPathIcon,
  AdjustmentsHorizontalIcon,
} from '@heroicons/react/24/outline';
import { apiService } from '../../services/apiService';
import { FW_METRIC_CATALOGUE, FwMetricResult, FwMetricDef } from '../../types/firewall';

// ─── Palette for multi-series lines ───────────────────────────────────────────
const SERIES_COLORS = [
  '#3b82f6', '#ef4444', '#10b981', '#f59e0b',
  '#8b5cf6', '#06b6d4', '#f97316', '#ec4899',
];

// ─── Granularity helper ────────────────────────────────────────────────────────
// Azure Firewall metrics: PT1M native grain. Granularity ladder per Azure Monitor rules.
function autoInterval(hours: number): string {
  if (hours <= 1)   return 'PT1M';
  if (hours <= 6)   return 'PT5M';
  if (hours <= 12)  return 'PT15M';
  if (hours <= 24)  return 'PT30M';
  if (hours <= 168) return 'PT1H';
  if (hours <= 720) return 'PT6H';
  return 'PT12H';
}

const GRANULARITY_OPTIONS = [
  { iso: 'PT1M',  label: '1 min'   },
  { iso: 'PT5M',  label: '5 min'   },
  { iso: 'PT15M', label: '15 min'  },
  { iso: 'PT30M', label: '30 min'  },
  { iso: 'PT1H',  label: '1 hour'  },
  { iso: 'PT6H',  label: '6 hours' },
  { iso: 'PT12H', label: '12 hours'},
  { iso: 'PT1D',  label: '1 day'   },
];

function isoToMinutes(iso: string): number {
  const map: Record<string, number> = {
    'PT1M': 1, 'PT5M': 5, 'PT15M': 15, 'PT30M': 30,
    'PT1H': 60, 'PT6H': 360, 'PT12H': 720, 'PT1D': 1440,
  };
  return map[iso] ?? 60;
}

/** Valid granularities for a given time window: produces ≥12 and ≤1440 data points. */
function validGranularities(hours: number) {
  return GRANULARITY_OPTIONS.filter((g) => {
    const pts = (hours * 60) / isoToMinutes(g.iso);
    return pts >= 12 && pts <= 1440;
  });
}

// ─── Human label for units ─────────────────────────────────────────────────────
function fmtUnit(unit: string, v: number): string {
  switch (unit) {
    case 'Bytes':
      if (v >= 1e9) return `${(v / 1e9).toFixed(1)} GB`;
      if (v >= 1e6) return `${(v / 1e6).toFixed(1)} MB`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(1)} KB`;
      return `${v} B`;
    case 'BitsPerSecond':
      if (v >= 1e9) return `${(v / 1e9).toFixed(1)} Gbps`;
      if (v >= 1e6) return `${(v / 1e6).toFixed(1)} Mbps`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(1)} Kbps`;
      return `${v} bps`;
    case 'Milliseconds': return `${v.toFixed(1)} ms`;
    case 'Percent':      return `${v.toFixed(1)}%`;
    default:
      if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
      if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
      return v.toLocaleString();
  }
}

// ─── Compact tick formatter ────────────────────────────────────────────────────
function fmtTick(unit: string, v: number): string {
  switch (unit) {
    case 'Bytes':
      if (v >= 1e9) return `${(v / 1e9).toFixed(0)}G`;
      if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
      return String(v);
    case 'BitsPerSecond':
      if (v >= 1e9) return `${(v / 1e9).toFixed(0)}G`;
      if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
      return String(v);
    case 'Milliseconds': return `${v.toFixed(0)}ms`;
    case 'Percent':      return `${v.toFixed(0)}%`;
    default:
      if (v >= 1e6) return `${(v / 1e6).toFixed(0)}M`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
      return String(v);
  }
}

// ─── Time range presets ────────────────────────────────────────────────────────
const TIME_PRESETS = [
  { label: '1h',   hours: 1 },
  { label: '6h',   hours: 6 },
  { label: '12h',  hours: 12 },
  { label: '24h',  hours: 24 },
  { label: '7d',   hours: 168 },
  { label: '30d',  hours: 720 },
  { label: 'Custom', hours: 0 },
];

function buildTimespan(hours: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

// ─── Multi-series SVG line chart ──────────────────────────────────────────────
function MetricChart({
  result,
  unit,
}: {
  result: FwMetricResult;
  unit: string;
}) {
  const [hovered, setHovered] = useState<{ seriesIdx: number; ptIdx: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (!result.series.length)
    return <p className="text-xs text-gray-400 italic py-4 text-center">No data points returned for this time range.</p>;

  // Flatten all values to find global y range
  const allVals = result.series.flatMap((s) => s.data.map((d) => d.v));
  const allTimes = result.series.flatMap((s) => s.data.map((d) => new Date(d.t).getTime()));
  const maxV = Math.max(...allVals, 0);
  const minT = Math.min(...allTimes);
  const maxT = Math.max(...allTimes);
  const tRange = maxT - minT || 1;

  // Y-axis ticks
  const TICK_COUNT = 5;
  const rawStep = maxV / TICK_COUNT;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep || 1)));
  const step = Math.ceil(rawStep / magnitude) * magnitude || 1;
  const yTicks = Array.from({ length: TICK_COUNT + 1 }, (_, i) => i * step);
  const yMax = yTicks[yTicks.length - 1] || 1;

  // Layout — all in SVG units (viewBox)
  const Y_AXIS_W  = 52;
  const PAD_TOP   = 12;
  const PAD_RIGHT  = 20;   // room for last x-label
  const X_LABEL_H = 34;   // space below plot for x-axis labels (2-row date+time)
  const TOTAL_W   = 900;
  const TOTAL_H   = 260;
  const plotH     = TOTAL_H - PAD_TOP - X_LABEL_H;

  const toX = (t: number) => Y_AXIS_W + ((t - minT) / tRange) * (TOTAL_W - Y_AXIS_W - PAD_RIGHT);
  const toY = (v: number) => PAD_TOP + plotH - (v / yMax) * plotH;
  const baseY = toY(0);

  // X-axis tick selection
  const firstSeries = result.series[0]?.data ?? [];
  const xTickIndices: number[] = [];
  if (firstSeries.length > 0) {
    const maxTicks = 6;
    const step2 = Math.max(1, Math.ceil(firstSeries.length / maxTicks));
    for (let i = 0; i < firstSeries.length; i += step2) xTickIndices.push(i);
    if (xTickIndices[xTickIndices.length - 1] !== firstSeries.length - 1)
      xTickIndices.push(firstSeries.length - 1);
  }

  // Tooltip content
  const hovPt = hovered ? result.series[hovered.seriesIdx]?.data[hovered.ptIdx] ?? null : null;
  const hovX  = hovPt ? toX(new Date(hovPt.t).getTime()) : null;

  return (
    <div className="relative">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${TOTAL_W} ${TOTAL_H}`}
        className="w-full"
        style={{ height: 290, overflow: 'visible' }}
      >
        <defs>
          {result.series.map((_, si) => (
            <linearGradient key={si} id={`mg${si}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={SERIES_COLORS[si % SERIES_COLORS.length]} stopOpacity="0.18" />
              <stop offset="100%" stopColor={SERIES_COLORS[si % SERIES_COLORS.length]} stopOpacity="0.01" />
            </linearGradient>
          ))}
        </defs>

        {/* y-axis grid lines + labels */}
        {yTicks.map((tick, i) => {
          const y = toY(tick);
          return (
            <g key={i}>
              <line x1={Y_AXIS_W} y1={y} x2={TOTAL_W - PAD_RIGHT} y2={y} stroke="#f3f4f6" strokeWidth="1" />
              <text x={Y_AXIS_W - 5} y={y} textAnchor="end" dominantBaseline="middle" fontSize="11" fill="#9ca3af">
                {fmtTick(unit, tick)}
              </text>
            </g>
          );
        })}

        {/* y-axis spine */}
        <line x1={Y_AXIS_W} y1={PAD_TOP} x2={Y_AXIS_W} y2={baseY} stroke="#e5e7eb" strokeWidth="1" />
        {/* x-axis baseline */}
        <line x1={Y_AXIS_W} y1={baseY} x2={TOTAL_W - PAD_RIGHT} y2={baseY} stroke="#e5e7eb" strokeWidth="1" />

        {/* X-axis tick labels — date shown only when it changes, otherwise just time */}
        {(() => {
          let lastDate = '';
          return xTickIndices.map((idx) => {
            const d = firstSeries[idx];
            if (!d) return null;
            const dt     = new Date(d.t);
            const x      = toX(dt.getTime());
            const isFirst = idx === 0;
            const isLast  = idx === firstSeries.length - 1;
            const anchor  = isFirst ? 'start' : isLast ? 'end' : 'middle';
            const dateStr = dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            const timeStr = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
            const showDate = dateStr !== lastDate;
            if (showDate) lastDate = dateStr;
            return (
              <g key={idx}>
                <line x1={x} y1={baseY} x2={x} y2={baseY + 4} stroke="#d1d5db" strokeWidth="1" />
                {showDate ? (
                  <>
                    <text x={x} y={baseY + 13} textAnchor={anchor} fontSize="10" fontWeight="600" fill="#6b7280">{dateStr}</text>
                    <text x={x} y={baseY + 24} textAnchor={anchor} fontSize="10" fill="#9ca3af">{timeStr}</text>
                  </>
                ) : (
                  <text x={x} y={baseY + 14} textAnchor={anchor} fontSize="11" fill="#9ca3af">{timeStr}</text>
                )}
              </g>
            );
          });
        })()}

        {/* Series: filled area + line */}
        {result.series.map((series, si) => {
          if (!series.data.length) return null;
          const color = SERIES_COLORS[si % SERIES_COLORS.length];
          const pts = series.data.map((d) => ({
            x: toX(new Date(d.t).getTime()),
            y: toY(d.v),
            d,
          }));
          const polyline = pts.map((p) => `${p.x},${p.y}`).join(' ');
          const area = [
            `${pts[0].x},${baseY}`,
            ...pts.map((p) => `${p.x},${p.y}`),
            `${pts[pts.length - 1].x},${baseY}`,
          ].join(' ');
          return (
            <g key={si}>
              <polygon points={area} fill={`url(#mg${si})`} />
              <polyline
                points={polyline}
                fill="none"
                stroke={color}
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {pts.map((p, pi) => {
                const isHov = hovered?.seriesIdx === si && hovered?.ptIdx === pi;
                return (
                  <circle
                    key={pi}
                    cx={p.x} cy={p.y}
                    r={isHov ? 6 : 3}
                    fill={color}
                    stroke="white"
                    strokeWidth="1.5"
                    style={{ transition: 'r 0.1s', cursor: 'crosshair' }}
                    onMouseEnter={() => setHovered({ seriesIdx: si, ptIdx: pi })}
                    onMouseLeave={() => setHovered(null)}
                  />
                );
              })}
            </g>
          );
        })}

        {/* Hover crosshair + tooltip */}
        {hovPt && hovX !== null && (
          <g>
            <line x1={hovX} y1={PAD_TOP} x2={hovX} y2={baseY}
              stroke="#6b7280" strokeWidth="1" strokeDasharray="3 2" opacity="0.6" />
            {(() => {
              const si = hovered!.seriesIdx;
              const color = SERIES_COLORS[si % SERIES_COLORS.length];
              const tipW = 190; const tipH = result.series.length > 1 ? 54 : 44;
              const tipX = hovX + 200 > TOTAL_W - PAD_RIGHT ? hovX - tipW - 10 : hovX + 10;
              const tipY = Math.max(PAD_TOP, toY(hovPt.v) - tipH / 2);
              const lbl = new Date(hovPt.t).toLocaleString(undefined, {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
              });
              return (
                <g>
                  <rect x={tipX} y={tipY} width={tipW} height={tipH} rx="5" fill="#1f2937" opacity="0.95" />
                  <rect x={tipX} y={tipY} width={3} height={tipH} rx="2" fill={color} />
                  <text x={tipX + 12} y={tipY + 16} fontSize="11" fill="#d1d5db">{lbl}</text>
                  {result.series.length > 1 && (
                    <text x={tipX + 12} y={tipY + 30} fontSize="11" fill={color} fontStyle="italic">
                      {result.series[si].name}
                    </text>
                  )}
                  <text x={tipX + 12} y={tipY + (result.series.length > 1 ? 46 : 36)} fontSize="13" fontWeight="600" fill="white">
                    {fmtUnit(unit, hovPt.v)}
                  </text>
                </g>
              );
            })()}
          </g>
        )}
      </svg>

      {/* Legend (only shown when multiple series) */}
      {result.series.length > 1 && (
        <div className="flex flex-wrap gap-3 mt-3 pt-2 border-t border-gray-100">
          {result.series.map((s, si) => (
            <div key={si} className="flex items-center gap-1.5 text-xs text-gray-600">
              <span className="w-3 h-0.5 rounded-full inline-block" style={{ backgroundColor: SERIES_COLORS[si % SERIES_COLORS.length] }} />
              {s.name}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Props ────────────────────────────────────────────────────────────────────
export interface FirewallMetricsCardConfig {
  id:          string;
  metricName:  string;
  aggregation: string;
  splitBy:     string;
  preset:      number;   // hours; 0 = custom
  customStart: string;
  customEnd:   string;
  granularity: string;   // ISO 8601 interval e.g. 'PT5M' | 'auto'
}

interface Props {
  config: FirewallMetricsCardConfig;
  subscriptionId: string;
  resourceGroup:  string;
  firewallName:   string;
  onRemove:       () => void;
  onChange:       (cfg: FirewallMetricsCardConfig) => void;
  /** When set, overrides all individual card time settings */
  globalTime?: { preset: number; customStart: string; customEnd: string } | null;
}

export default function FirewallMetricsCard({
  config,
  subscriptionId,
  resourceGroup,
  firewallName,
  onRemove,
  onChange,
  globalTime,
}: Props) {
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [result, setResult]     = useState<FwMetricResult | null>(null);
  const [showCfg, setShowCfg]   = useState(false);

  const def: FwMetricDef | undefined = FW_METRIC_CATALOGUE.find((m) => m.name === config.metricName);

  const fetch = useCallback(async () => {
    if (!firewallName || !def) return;
    setLoading(true);
    setError(null);
    try {
      // Global time overrides card-level time when set
      const effective = globalTime ?? config;
      const hours = effective.preset || 24;
      const { start, end } = effective.preset
        ? buildTimespan(effective.preset)
        : { start: effective.customStart, end: effective.customEnd };
      const interval = (config.granularity && config.granularity !== 'auto')
        ? config.granularity
        : autoInterval(hours);
      const res = await apiService.firewallGetAzureMetrics(
        subscriptionId, resourceGroup, firewallName,
        config.metricName, config.aggregation,
        interval, start, end,
        config.splitBy,
      );
      setResult(res as FwMetricResult);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load metric');
    } finally {
      setLoading(false);
    }
  }, [config, globalTime, subscriptionId, resourceGroup, firewallName, def]);

  useEffect(() => { fetch(); }, [fetch]);

  const update = (patch: Partial<FirewallMetricsCardConfig>) => onChange({ ...config, ...patch });

  const totalPts = result?.series.reduce((s, sr) => s + sr.data.length, 0) ?? 0;
  const latestVal = result?.series[0]?.data.at(-1)?.v ?? null;

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-gray-100">
        <div className="flex items-center gap-2.5 min-w-0">
          <ChartBarIcon className="w-4 h-4 text-indigo-500 shrink-0" />
          <span className="text-sm font-semibold text-gray-800 truncate">{def?.label ?? config.metricName}</span>
          {globalTime && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium shrink-0">global time</span>
          )}
          {latestVal !== null && !loading && (
            <span className="text-sm text-gray-400 tabular-nums">{fmtUnit(def?.unit ?? '', latestVal)} latest</span>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setShowCfg((v) => !v)}
            className={`p-1.5 rounded-lg hover:bg-gray-100 transition-colors ${showCfg ? 'bg-indigo-50 text-indigo-600' : 'text-gray-400'}`}
            title="Configure"
          >
            <AdjustmentsHorizontalIcon className="w-4 h-4" />
          </button>
          <button onClick={fetch} className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400" title="Refresh">
            <ArrowPathIcon className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button onClick={onRemove} className="p-1.5 rounded-lg hover:bg-red-50 text-gray-300 hover:text-red-500" title="Remove card">
            <XMarkIcon className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Config panel */}
      {showCfg && (
        <div className="px-5 py-4 bg-gray-50 border-b border-gray-100 grid grid-cols-2 md:grid-cols-5 gap-4">
          {/* Metric selector */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Metric</label>
            <select
              value={config.metricName}
              onChange={(e) => {
                const newDef = FW_METRIC_CATALOGUE.find((m) => m.name === e.target.value)!;
                update({ metricName: e.target.value, aggregation: newDef.defaultAgg, splitBy: '' });
              }}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
            >
              {FW_METRIC_CATALOGUE.map((m) => (
                <option key={m.name} value={m.name}>{m.label}</option>
              ))}
            </select>
          </div>

          {/* Aggregation */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Aggregation</label>
            <select
              value={config.aggregation}
              onChange={(e) => update({ aggregation: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
            >
              {(def?.aggregations ?? ['Average']).map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </div>

          {/* Split / Dimension */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Split by</label>
            <select
              value={config.splitBy}
              onChange={(e) => update({ splitBy: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent disabled:opacity-40 disabled:cursor-not-allowed"
              disabled={(def?.dimensions.length ?? 0) === 0}
            >
              <option value="">{def?.dimensions.length ? 'No split' : 'No dimensions'}</option>
              {(def?.dimensions ?? []).map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>

          {/* Granularity */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">
              Granularity
            </label>
            <select
              value={config.granularity ?? 'auto'}
              onChange={(e) => update({ granularity: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
            >
              <option value="auto">Auto ({GRANULARITY_OPTIONS.find(g => g.iso === autoInterval(config.preset || 24))?.label ?? 'auto'})</option>
              {validGranularities(config.preset || 24).map((g) => (
                <option key={g.iso} value={g.iso}>{g.label}</option>
              ))}
            </select>
            <p className="text-[10px] text-gray-400 leading-tight">
              {(() => {
                const hours = config.preset || 24;
                const iso = (config.granularity && config.granularity !== 'auto') ? config.granularity : autoInterval(hours);
                const pts = Math.round((hours * 60) / isoToMinutes(iso));
                return `~${pts} data points`;
              })()}
            </p>
          </div>

          {/* Time range */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-gray-500 uppercase tracking-wide">Time range</label>
            <div className="flex flex-wrap gap-1.5">
              {TIME_PRESETS.map((p) => (
                <button
                  key={p.label}
                  onClick={() => update({ preset: p.hours })}
                  className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                    config.preset === p.hours
                      ? 'bg-indigo-500 text-white border-indigo-500'
                      : 'bg-white text-gray-500 border-gray-300 hover:border-indigo-300'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            {config.preset === 0 && (
              <div className="flex flex-col gap-1.5 mt-1">
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wide">From</span>
                  <input
                    type="datetime-local"
                    value={config.customStart.slice(0, 16)}
                    onChange={(e) => update({ customStart: new Date(e.target.value).toISOString() })}
                    className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </div>
                <div className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-gray-400 uppercase tracking-wide">To</span>
                  <input
                    type="datetime-local"
                    value={config.customEnd.slice(0, 16)}
                    onChange={(e) => update({ customEnd: new Date(e.target.value).toISOString() })}
                    className="w-full border border-gray-200 rounded-lg px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Chart body */}
      <div className="px-5 pt-4 pb-5">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-14 text-gray-400 text-sm">
            <ArrowPathIcon className="w-4 h-4 animate-spin" /> Loading metrics…
          </div>
        ) : error ? (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
            {error}
          </div>
        ) : result ? (
          <MetricChart result={result} unit={def?.unit ?? ''} />
        ) : null}
      </div>

      {/* Footer meta */}
      {result && !loading && (
        <div className="px-5 pb-3.5 flex items-center justify-between text-xs text-gray-400 border-t border-gray-50 pt-2">
          <span>{totalPts.toLocaleString()} pts · {result.interval} granularity</span>
          <span>
            {result.split_by ? `Split by ${result.split_by} · ${result.series.length} series` : `${result.series.length} series`}
          </span>
        </div>
      )}
    </div>
  );
}

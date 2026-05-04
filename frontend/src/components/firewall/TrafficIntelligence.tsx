'use client';

import React from 'react';
import { fmtN } from '../../utils/format';
import type { TrafficIntelligenceResult } from '../../types/firewall';
import {
  ShieldExclamationIcon,
  ChartBarIcon,
  ArrowsRightLeftIcon,
  GlobeAltIcon,
  BoltIcon,
  ExclamationTriangleIcon,
  ServerStackIcon,
  FireIcon,
} from '@heroicons/react/24/outline';

interface Props {
  result: TrafficIntelligenceResult;
  onRuleClick?: (ruleName: string, ruleCollection: string, ruleType: string) => void;
}

function HBarChart({
  rows,
  labelKey,
  countKey,
  colorClass = 'bg-red-500',
  limit = 10,
}: {
  rows: Record<string, unknown>[];
  labelKey: string;
  countKey: string;
  colorClass?: string;
  limit?: number;
}) {
  const top = rows.slice(0, limit);
  const max = Number(top[0]?.[countKey] ?? 1);
  return (
    <div className="space-y-1.5">
      {top.map((row, i) => {
        const label = String(row[labelKey] ?? '—');
        const count = Number(row[countKey] ?? 0);
        const w = (count / max) * 100;
        return (
          <div key={i} className="flex items-center gap-2 text-xs">
            <span className="shrink-0 w-5 text-gray-400 tabular-nums text-right">{i + 1}.</span>
            <span className="shrink-0 w-44 font-mono truncate text-gray-700" title={label}>{label}</span>
            <div className="flex-1 bg-gray-100 rounded-full h-3.5 overflow-hidden">
              <div className={`${colorClass} h-3.5 rounded-full`} style={{ width: `${w}%` }} />
            </div>
            <span className="shrink-0 w-20 text-right tabular-nums text-gray-600">{fmtN(count)}</span>
          </div>
        );
      })}
      {rows.length === 0 && (
        <p className="text-xs text-gray-400 italic">No data in selected window</p>
      )}
    </div>
  );
}

function SectionCard({
  title,
  icon,
  children,
  badge,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  badge?: { label: string; color: string };
}) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
          {icon}
          {title}
        </h4>
        {badge && (
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${badge.color}`}>
            {badge.label}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function DenyTrend({ rows }: { rows: { T: string; Count: number }[] }) {
  const [hovered, setHovered] = React.useState<number | null>(null);

  if (!rows.length) return <p className="text-xs text-gray-400 italic">No deny events in window</p>;

  const DENY_COLOR = '#ef4444';

  // Layout — matches FirewallMetricsCard exactly
  const Y_AXIS_W  = 52;
  const PAD_TOP   = 12;
  const PAD_RIGHT = 20;
  const X_LABEL_H = 34;
  const TOTAL_W   = 900;
  const TOTAL_H   = 260;
  const plotH     = TOTAL_H - PAD_TOP - X_LABEL_H;

  const allTimes = rows.map((r) => new Date(r.T).getTime());
  const minT = Math.min(...allTimes);
  const maxT = Math.max(...allTimes);
  const tRange = maxT - minT || 1;

  const maxV = Math.max(...rows.map((r) => r.Count), 1);
  const TICK_COUNT = 5;
  const rawStep = maxV / TICK_COUNT;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep || 1)));
  const step = Math.ceil(rawStep / magnitude) * magnitude || 1;
  const yTicks = Array.from({ length: TICK_COUNT + 1 }, (_, i) => i * step);
  const yMax = yTicks[yTicks.length - 1] || 1;

  const toX = (t: number) => Y_AXIS_W + ((t - minT) / tRange) * (TOTAL_W - Y_AXIS_W - PAD_RIGHT);
  const toY = (v: number) => PAD_TOP + plotH - (v / yMax) * plotH;
  const baseY = toY(0);

  // X-axis tick selection — up to 6 evenly spread ticks
  const maxTicks = 6;
  const tickStep = Math.max(1, Math.ceil(rows.length / maxTicks));
  const xTickIndices: number[] = [];
  for (let i = 0; i < rows.length; i += tickStep) xTickIndices.push(i);
  if (xTickIndices[xTickIndices.length - 1] !== rows.length - 1) xTickIndices.push(rows.length - 1);

  const pts = rows.map((r) => ({ x: toX(new Date(r.T).getTime()), y: toY(r.Count), r }));
  const polyline = pts.map((p) => `${p.x},${p.y}`).join(' ');
  const area = [`${pts[0].x},${baseY}`, ...pts.map((p) => `${p.x},${p.y}`), `${pts[pts.length - 1].x},${baseY}`].join(' ');

  const hovPt = hovered !== null ? pts[hovered] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${TOTAL_W} ${TOTAL_H}`}
        className="w-full"
        style={{ height: 290, overflow: 'visible' }}
      >
        <defs>
          <linearGradient id="denyTrendGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={DENY_COLOR} stopOpacity="0.18" />
            <stop offset="100%" stopColor={DENY_COLOR} stopOpacity="0.01" />
          </linearGradient>
        </defs>

        {/* y-axis grid lines + labels */}
        {yTicks.map((tick, i) => {
          const y = toY(tick);
          const label = fmtN(tick);
          return (
            <g key={i}>
              <line x1={Y_AXIS_W} y1={y} x2={TOTAL_W - PAD_RIGHT} y2={y} stroke="#f3f4f6" strokeWidth="1" />
              <text x={Y_AXIS_W - 5} y={y} textAnchor="end" dominantBaseline="middle" fontSize="11" fill="#9ca3af">{label}</text>
            </g>
          );
        })}

        {/* y-axis spine + x baseline */}
        <line x1={Y_AXIS_W} y1={PAD_TOP} x2={Y_AXIS_W} y2={baseY} stroke="#e5e7eb" strokeWidth="1" />
        <line x1={Y_AXIS_W} y1={baseY} x2={TOTAL_W - PAD_RIGHT} y2={baseY} stroke="#e5e7eb" strokeWidth="1" />

        {/* X-axis tick labels — show date only when it changes */}
        {(() => {
          let lastDate = '';
          return xTickIndices.map((idx) => {
            const p = pts[idx];
            if (!p) return null;
            const dt = new Date(p.r.T);
            const isFirst = idx === 0;
            const isLast = idx === rows.length - 1;
            const anchor = isFirst ? 'start' : isLast ? 'end' : 'middle';
            const dateStr = dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            const timeStr = dt.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
            const showDate = dateStr !== lastDate;
            if (showDate) lastDate = dateStr;
            return (
              <g key={idx}>
                <line x1={p.x} y1={baseY} x2={p.x} y2={baseY + 4} stroke="#d1d5db" strokeWidth="1" />
                {showDate ? (
                  <>
                    <text x={p.x} y={baseY + 13} textAnchor={anchor} fontSize="10" fontWeight="600" fill="#6b7280">{dateStr}</text>
                    <text x={p.x} y={baseY + 24} textAnchor={anchor} fontSize="10" fill="#9ca3af">{timeStr}</text>
                  </>
                ) : (
                  <text x={p.x} y={baseY + 14} textAnchor={anchor} fontSize="11" fill="#9ca3af">{timeStr}</text>
                )}
              </g>
            );
          });
        })()}

        {/* Filled area + line */}
        <polygon points={area} fill="url(#denyTrendGrad)" />
        <polyline points={polyline} fill="none" stroke={DENY_COLOR} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />

        {/* Dots */}
        {pts.map((p, i) => (
          <circle
            key={i}
            cx={p.x} cy={p.y}
            r={hovered === i ? 6 : 3}
            fill={DENY_COLOR}
            stroke="white"
            strokeWidth="1.5"
            style={{ transition: 'r 0.1s', cursor: 'crosshair' }}
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
          />
        ))}

        {/* Hover crosshair + tooltip */}
        {hovPt && (
          <g>
            <line x1={hovPt.x} y1={PAD_TOP} x2={hovPt.x} y2={baseY}
              stroke="#6b7280" strokeWidth="1" strokeDasharray="3 2" opacity="0.6" />
            {(() => {
              const tipW = 190; const tipH = 44;
              const tipX = hovPt.x + 200 > TOTAL_W - PAD_RIGHT ? hovPt.x - tipW - 10 : hovPt.x + 10;
              const tipY = Math.max(PAD_TOP, hovPt.y - tipH / 2);
              const lbl = new Date(hovPt.r.T).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
              return (
                <g>
                  <rect x={tipX} y={tipY} width={tipW} height={tipH} rx="5" fill="#1f2937" opacity="0.95" />
                  <rect x={tipX} y={tipY} width={3} height={tipH} rx="2" fill={DENY_COLOR} />
                  <text x={tipX + 12} y={tipY + 16} fontSize="11" fill="#d1d5db">{lbl}</text>
                  <text x={tipX + 12} y={tipY + 36} fontSize="13" fontWeight="600" fill="white">{fmtN(hovPt.r.Count)} denies</text>
                </g>
              );
            })()}
          </g>
        )}
      </svg>
    </div>
  );
}

function EWRatio({ ew }: { ew: TrafficIntelligenceResult['east_west'] }) {
  const total = ew.total || 1;
  const inPct  = ((ew.internal / total) * 100).toFixed(1);
  const outPct = ((ew.external / total) * 100).toFixed(1);
  return (
    <div className="space-y-3">
      <div className="flex gap-4 text-xs">
        <div>
          <span className="font-semibold text-indigo-700">{inPct}%</span>
          <span className="text-gray-500 ml-1">East-West (RFC1918)</span>
          <div className="text-gray-400 tabular-nums">{fmtN(ew.internal)} flows</div>
        </div>
        <div>
          <span className="font-semibold text-orange-600">{outPct}%</span>
          <span className="text-gray-500 ml-1">North-South (Internet)</span>
          <div className="text-gray-400 tabular-nums">{fmtN(ew.external)} flows</div>
        </div>
      </div>
      <div className="flex rounded-full overflow-hidden h-4">
        <div className="bg-indigo-500" style={{ width: `${inPct}%` }} title={`E-W: ${inPct}%`} />
        <div className="bg-orange-400 flex-1" title={`N-S: ${outPct}%`} />
      </div>
      <p className="text-xs text-gray-400">
        {Number(inPct) > 80
          ? 'Predominantly internal traffic — good segmentation signal'
          : Number(outPct) > 50
          ? 'High north-south traffic — review internet egress rules'
          : 'Balanced traffic mix'}
      </p>
    </div>
  );
}

export default function TrafficIntelligence({ result, onRuleClick }: Props) {
  const totalDenied = result.total_deny_events || result.denied_sources.reduce((s, r) => s + r.Count, 0);
  const hasThreatIntel = result.threat_intel_hits.length > 0;
  const hasAnomalies = result.protocol_anomalies.length > 0;
  const threatTotal = result.threat_intel_hits.reduce((s, r) => s + r.Count, 0);

  return (
    <div className="space-y-5">
      {/* ── KPI row ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        {[
          {
            label: 'Total Deny Events',
            value: fmtN(totalDenied),
            sub: `across ${result.days}d window`,
            color: 'text-red-600',
            bg: 'bg-red-50 border-red-200',
          },
          {
            label: 'Unique Deny Sources',
            value: fmtN(result.unique_deny_sources ?? result.denied_sources.length),
            sub: result.unique_deny_sources != null ? 'distinct IPs blocked' : `top ${result.denied_sources.length} shown`,
            color: 'text-orange-600',
            bg: 'bg-orange-50 border-orange-200',
          },
          {
            label: 'Rules Fired',
            value: fmtN(result.unique_rules_hit ?? result.top_rules.length),
            sub: result.unique_rules_hit != null ? 'distinct rules with traffic' : `top ${result.top_rules.length} shown`,
            color: 'text-indigo-700',
            bg: 'bg-blue-50 border-blue-200',
          },
          {
            label: 'Unique Talkers',
            value: fmtN(result.unique_talkers ?? result.top_talkers.length),
            sub: result.unique_talkers != null ? 'distinct source IPs (all)' : `top ${result.top_talkers.length} shown`,
            color: 'text-purple-700',
            bg: 'bg-purple-50 border-purple-200',
          },
          {
            label: 'Threat Intel Hits',
            value: fmtN(threatTotal),
            sub: threatTotal > 0 ? `${result.threat_intel_hits.length} distinct FQDNs` : 'none detected',
            color: hasThreatIntel ? 'text-red-700' : 'text-gray-500',
            bg: hasThreatIntel ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-200',
          },
        ].map((kpi) => (
          <div key={kpi.label} className={`border rounded-xl p-3 ${kpi.bg}`}>
            <div className={`text-2xl font-bold tabular-nums ${kpi.color}`}>{kpi.value}</div>
            <div className="text-xs font-medium text-gray-700 mt-0.5">{kpi.label}</div>
            <div className="text-xs text-gray-400 mt-0.5">{kpi.sub}</div>
          </div>
        ))}
      </div>

      {/* ── Deny Trend ─────────────────────────────────────────────────── */}
      <SectionCard
        title={`Deny Traffic Trend — last ${result.days}d`}
        icon={<FireIcon className="w-4 h-4 text-red-500" />}
        badge={totalDenied > 0 ? { label: `${fmtN(totalDenied)} events`, color: 'bg-red-100 text-red-700' } : undefined}
      >
        <DenyTrend rows={result.deny_trend} />
      </SectionCard>

      {/* ── Top denied sources + destinations ───────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard title="Top Denied Sources" icon={<ShieldExclamationIcon className="w-4 h-4 text-red-500" />}>
          <HBarChart rows={result.denied_sources as unknown as Record<string, unknown>[]} labelKey="Source" countKey="Count" colorClass="bg-red-400" />
        </SectionCard>
        <SectionCard title="Top Denied Destinations" icon={<GlobeAltIcon className="w-4 h-4 text-orange-500" />}>
          <HBarChart rows={result.denied_destinations as unknown as Record<string, unknown>[]} labelKey="Dest" countKey="Count" colorClass="bg-orange-400" />
        </SectionCard>
      </div>

      {/* ── Top denied ports + top rules ────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard title="Top Denied Ports" icon={<BoltIcon className="w-4 h-4 text-yellow-600" />}>
          <div className="space-y-1.5">
            {result.denied_ports.slice(0, 10).map((row, i) => {
              const max = result.denied_ports[0]?.Count ?? 1;
              const w = (row.Count / max) * 100;
              return (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="shrink-0 w-5 text-gray-400 text-right">{i + 1}.</span>
                  <span className="shrink-0 w-20 font-mono text-gray-700">{row.Port}/{row.Protocol}</span>
                  <div className="flex-1 bg-gray-100 rounded-full h-3.5 overflow-hidden">
                    <div className="bg-yellow-400 h-3.5 rounded-full" style={{ width: `${w}%` }} />
                  </div>
                  <span className="shrink-0 w-16 text-right tabular-nums text-gray-600">{fmtN(row.Count)}</span>
                </div>
              );
            })}
            {result.denied_ports.length === 0 && <p className="text-xs text-gray-400 italic">No data</p>}
          </div>
        </SectionCard>

        <SectionCard title="Top Active Rules" icon={<ChartBarIcon className="w-4 h-4 text-indigo-500" />}>
          <div className="space-y-1.5">
            {result.top_rules.slice(0, 10).map((row, i) => {
              const max = result.top_rules[0]?.Count ?? 1;
              const w = (row.Count / max) * 100;
              const clickable = !!onRuleClick;
              return (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="shrink-0 w-5 text-gray-400 text-right">{i + 1}.</span>
                  <button
                    className={`shrink-0 w-48 text-left rounded-lg px-1 -mx-1 py-0.5 transition-colors ${
                      clickable ? 'hover:bg-indigo-50 cursor-pointer group' : 'cursor-default'
                    }`}
                    onClick={() => onRuleClick?.(row.Rule, row.RuleCollection, row.RuleType)}
                    title={clickable ? `View definition & logs for: ${row.Rule}` : row.Rule}
                  >
                    <div className={`font-medium truncate ${clickable ? 'text-indigo-600 group-hover:underline' : 'text-gray-700'}`}>{row.Rule}</div>
                    <div className="text-gray-400 truncate">{row.RuleCollection} · <span className={row.RuleType === 'Application' ? 'text-purple-600' : row.RuleType === 'NAT' ? 'text-orange-500' : 'text-indigo-600'}>{row.RuleType}</span></div>
                  </button>
                  <div className="flex-1 bg-gray-100 rounded-full h-3.5 overflow-hidden">
                    <div className="bg-indigo-500 h-3.5 rounded-full" style={{ width: `${w}%` }} />
                  </div>
                  <span className="shrink-0 w-16 text-right tabular-nums text-gray-600">{fmtN(row.Count)}</span>
                </div>
              );
            })}
            {result.top_rules.length === 0 && <p className="text-xs text-gray-400 italic">No data</p>}
          </div>
        </SectionCard>
      </div>

      {/* ── Top talkers + E/W ratio ──────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard title="Top Talkers (all traffic)" icon={<ServerStackIcon className="w-4 h-4 text-purple-500" />}>
          <HBarChart rows={result.top_talkers as unknown as Record<string, unknown>[]} labelKey="Source" countKey="Count" colorClass="bg-purple-400" />
        </SectionCard>
        <SectionCard
          title="East-West vs North-South"
          icon={<ArrowsRightLeftIcon className="w-4 h-4 text-indigo-500" />}
          badge={{ label: `${fmtN(result.east_west.total)} flows`, color: 'bg-indigo-100 text-indigo-700' }}
        >
          <EWRatio ew={result.east_west} />
        </SectionCard>
      </div>

      {/* ── NAT Rule Activity ───────────────────────────────────────────── */}
      <SectionCard
        title="NAT Rule Activity"
        icon={<ArrowsRightLeftIcon className="w-4 h-4 text-orange-500" />}
        badge={result.nat_activity.length > 0
          ? { label: `${result.nat_activity.length} DNAT rules`, color: 'bg-orange-100 text-orange-700' }
          : { label: 'No data', color: 'bg-gray-100 text-gray-500' }}
      >
        {result.nat_activity.length === 0 ? (
          <p className="text-xs text-gray-400 italic py-2">No NAT rule hits in the selected time window.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-gray-500 text-left">
                  <th className="py-1.5 pr-3">Rule</th>
                  <th className="py-1.5 pr-3">Ext Port</th>
                  <th className="py-1.5 pr-3">Proto</th>
                  <th className="py-1.5 pr-3">Translated To</th>
                  <th className="py-1.5 pr-3 text-right">Unique Sources</th>
                  <th className="py-1.5 pr-3 text-right">Unique Ext IPs</th>
                  <th className="py-1.5 text-right">Hits</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {result.nat_activity.map((row, i) => (
                  <tr key={i} className="text-gray-700 hover:bg-gray-50">
                    <td className="py-1.5 pr-3 font-medium max-w-[160px] truncate" title={row.RuleName}>{row.RuleName}</td>
                    <td className="py-1.5 pr-3 font-mono">{row.ExternalPort || '*'}</td>
                    <td className="py-1.5 pr-3">
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-orange-100 text-orange-700">{row.Protocol}</span>
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-gray-600">
                      {row.TranslatedIp ? `${row.TranslatedIp}${row.TranslatedPort ? `:${row.TranslatedPort}` : ''}` : '—'}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{fmtN(row.UniqueSources)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{fmtN(row.UniqueExternalIPs)}</td>
                    <td className="py-1.5 text-right tabular-nums font-medium">{fmtN(row.Count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* ── NAT Top Sources ─────────────────────────────────────────────── */}
      <SectionCard
        title="NAT Top Sources"
        icon={<ArrowsRightLeftIcon className="w-4 h-4 text-orange-500" />}
        badge={result.nat_top_sources.length > 0
          ? { label: `${result.nat_top_sources.length} sources`, color: 'bg-orange-100 text-orange-700' }
          : { label: 'No data', color: 'bg-gray-100 text-gray-500' }}
      >
        {result.nat_top_sources.length === 0 ? (
          <p className="text-xs text-gray-400 italic py-2">No NAT traffic in the selected time window.</p>
        ) : (
          <HBarChart
            rows={result.nat_top_sources as unknown as Record<string, unknown>[]}
            labelKey="Source"
            countKey="Count"
            colorClass="bg-orange-400"
          />
        )}
      </SectionCard>

      {/* ── DNS Top Queries ─────────────────────────────────────────────── */}
      <SectionCard
        title="Top DNS Queries (Proxy)"
        icon={<GlobeAltIcon className="w-4 h-4 text-cyan-500" />}
        badge={result.dns_top_queries.length > 0
          ? { label: `${result.dns_top_queries.length} domains`, color: 'bg-cyan-100 text-cyan-700' }
          : { label: 'No data', color: 'bg-gray-100 text-gray-500' }}
      >
        {result.dns_top_queries.length === 0 ? (
          <p className="text-xs text-gray-400 italic py-2">No DNS proxy logs in the selected time window. Ensure DNS proxy is enabled and AZFWDnsQuery logs are flowing.</p>
        ) : (
          <HBarChart
            rows={result.dns_top_queries as unknown as Record<string, unknown>[]}
            labelKey="QueryName"
            countKey="Count"
            colorClass="bg-cyan-500"
          />
        )}
      </SectionCard>

      {/* ── Protocol anomalies ──────────────────────────────────────────── */}
      {hasAnomalies && (
        <SectionCard
          title="Protocol / Port Anomalies"
          icon={<ExclamationTriangleIcon className="w-4 h-4 text-yellow-600" />}
          badge={{ label: `${result.protocol_anomalies.length} unusual ports`, color: 'bg-yellow-100 text-yellow-700' }}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-gray-500 text-left">
                  <th className="py-1.5 pr-4">Protocol</th>
                  <th className="py-1.5 pr-4">Port</th>
                  <th className="py-1.5 pr-4 text-right">Hits</th>
                  <th className="py-1.5">Note</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {result.protocol_anomalies.map((row, i) => (
                  <tr key={i} className="text-gray-700">
                    <td className="py-1.5 pr-4 font-mono">{row.Protocol}</td>
                    <td className="py-1.5 pr-4 font-mono">{row.Port}</td>
                    <td className="py-1.5 pr-4 text-right tabular-nums">{fmtN(row.Count)}</td>
                    <td className="py-1.5 text-gray-400">Non-standard port for {row.Protocol}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}

      {/* ── Top Denied Combinations ─────────────────────────────────────── */}
      <SectionCard
        title="Top Denied Traffic Combinations"
        icon={<ShieldExclamationIcon className="w-4 h-4 text-red-500" />}
        badge={result.denied_combinations.length > 0
          ? { label: `${result.denied_combinations.length} combinations`, color: 'bg-red-100 text-red-700' }
          : { label: 'No data', color: 'bg-gray-100 text-gray-500' }}
      >
        {result.denied_combinations.length === 0 ? (
          <p className="text-xs text-gray-400 italic py-2">No denied traffic combinations found in the selected time range.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-gray-500 text-left">
                  <th className="py-1.5 pr-3">Source</th>
                  <th className="py-1.5 pr-3">Destination</th>
                  <th className="py-1.5 pr-3">Port</th>
                  <th className="py-1.5 pr-3">Proto</th>
                  <th className="py-1.5 pr-3">Matched Rule / Reason</th>
                  <th className="py-1.5 pr-3">Collection</th>
                  <th className="py-1.5 text-right">Hits</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {result.denied_combinations.map((row, i) => {
                  const isDefault = row.Rule === '(default action)' || row.Rule === '(no rule)';
                  return (
                    <tr key={i} className="text-gray-700 hover:bg-gray-50">
                      <td className="py-1.5 pr-3 font-mono">{row.SourceIp || '—'}</td>
                      <td className="py-1.5 pr-3 font-mono max-w-[160px] truncate" title={row.Destination}>{row.Destination || '—'}</td>
                      <td className="py-1.5 pr-3 font-mono">{row.Port || '*'}</td>
                      <td className="py-1.5 pr-3">
                        <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-medium ${
                          row.RuleType === 'Application' ? 'bg-purple-100 text-purple-700'
                          : row.RuleType === 'NAT' ? 'bg-orange-100 text-orange-700'
                          : 'bg-indigo-100 text-indigo-700'
                        }`}>{row.Protocol}</span>
                      </td>
                      <td className="py-1.5 pr-3 max-w-[200px] truncate" title={row.Rule}>
                        {isDefault
                          ? <span className="italic text-gray-400">Default deny (no matching rule)</span>
                          : <span className="font-medium text-red-700">{row.Rule}</span>
                        }
                      </td>
                      <td className="py-1.5 pr-3 text-gray-400 max-w-[120px] truncate" title={row.RuleCollection}>{row.RuleCollection || '—'}</td>
                      <td className="py-1.5 text-right tabular-nums font-medium">{fmtN(row.Count)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* ── Threat Intel ────────────────────────────────────────────────── */}
      {hasThreatIntel ? (
        <SectionCard
          title="Threat Intelligence Hits"
          icon={<ShieldExclamationIcon className="w-4 h-4 text-red-600" />}
          badge={{ label: `${fmtN(result.threat_intel_hits.reduce((s, r) => s + r.Count, 0))} blocked`, color: 'bg-red-100 text-red-700' }}
        >
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-gray-500 text-left">
                  <th className="py-1.5 pr-4">Target</th>
                  <th className="py-1.5 pr-4">Threat</th>
                  <th className="py-1.5 pr-4">Action</th>
                  <th className="py-1.5 text-right">Hits</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {result.threat_intel_hits.map((row, i) => (
                  <tr key={i} className="text-gray-700">
                    <td className="py-1.5 pr-4 font-mono truncate max-w-xs" title={row.Fqdn}>{row.Fqdn}</td>
                    <td className="py-1.5 pr-4 text-red-700">{row.ThreatDescription}</td>
                    <td className="py-1.5 pr-4"><span className="bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full">{row.Action}</span></td>
                    <td className="py-1.5 text-right tabular-nums">{fmtN(row.Count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ) : (
        <div className="bg-green-50 border border-green-200 rounded-xl p-4 text-xs text-green-800 flex items-center gap-2">
          <ShieldExclamationIcon className="w-4 h-4 text-green-500 shrink-0" />
          No Threat Intelligence hits in this window — AZFWThreatIntel table empty or not enabled.
        </div>
      )}

    </div>
  );
}

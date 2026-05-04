'use client';

import { useState } from 'react';
import type { OverlapPair } from '../../types/firewall';
import {
  ChevronDownIcon,
  ChevronRightIcon,
  ExclamationTriangleIcon,
  DocumentDuplicateIcon,
  BoltIcon,
  ArrowDownTrayIcon,
} from '@heroicons/react/24/outline';

interface Props {
  overlaps: OverlapPair[];
  totalScanned: number;
  summary: { exact_duplicates: number; same_action_overlaps: number; conflicting_rules: number };
  analysisMode?: string;
}

const SEVERITY_META = {
  exact_duplicate: {
    label: 'Exact Duplicate',
    color: 'bg-red-50 border-red-200',
    badge: 'bg-red-100 text-red-700 border-red-300',
    icon: <DocumentDuplicateIcon className="w-4 h-4 text-red-500" />,
  },
  same_action: {
    label: 'Overlapping (same action)',
    color: 'bg-orange-50 border-orange-200',
    badge: 'bg-orange-100 text-orange-700 border-orange-300',
    icon: <ExclamationTriangleIcon className="w-4 h-4 text-orange-500" />,
  },
  conflict: {
    label: 'Conflicting Actions',
    color: 'bg-yellow-50 border-yellow-200',
    badge: 'bg-yellow-100 text-yellow-800 border-yellow-300',
    icon: <BoltIcon className="w-4 h-4 text-yellow-600" />,
  },
} as const;

function OverlapCard({ pair, index }: { pair: OverlapPair; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const meta = SEVERITY_META[pair.severity] ?? SEVERITY_META.same_action;

  return (
    <div className={`border rounded-lg mb-3 ${meta.color}`}>
      <button
        className="w-full px-4 py-3 flex items-start gap-3 text-left"
        onClick={() => setExpanded((e) => !e)}
      >
        {expanded
          ? <ChevronDownIcon className="w-4 h-4 text-gray-500 shrink-0 mt-0.5" />
          : <ChevronRightIcon className="w-4 h-4 text-gray-500 shrink-0 mt-0.5" />}
        <div className="shrink-0 mt-0.5">{meta.icon}</div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-gray-900">
              #{index + 1} — {meta.label}
            </span>
            <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${meta.badge}`}>
              {pair.rule_category.charAt(0).toUpperCase() + pair.rule_category.slice(1)}
            </span>
          </div>
          <p className="text-xs text-gray-700">
            <span className="font-medium">{pair.rule1.name}</span>
            {' '}<span className="text-gray-400">in</span>{' '}
            {pair.rule1.rule_group} › {pair.rule1.rule_collection}
            {' '}↔{' '}
            <span className="font-medium">{pair.rule2.name}</span>
            {' '}<span className="text-gray-400">in</span>{' '}
            {pair.rule2.rule_group} › {pair.rule2.rule_collection}
          </p>
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4">
          {/* Overlap details */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            {pair.source_overlap.length > 0 && (
              <div className="bg-white rounded-lg p-3 border border-gray-200">
                <div className="font-semibold text-gray-700 mb-1">Source Overlap</div>
                {pair.source_overlap.map((s, i) => (
                  <div key={i} className="font-mono text-gray-600">{s}</div>
                ))}
              </div>
            )}
            {(pair.destination_ip_overlap.length > 0 || pair.destination_fqdn_overlap.length > 0) && (
              <div className="bg-white rounded-lg p-3 border border-gray-200">
                <div className="font-semibold text-gray-700 mb-1">Destination Overlap</div>
                {pair.destination_ip_overlap.map((d, i) => (
                  <div key={i} className="font-mono text-gray-600">{d}</div>
                ))}
                {pair.destination_fqdn_overlap.map((d, i) => (
                  <div key={i} className="font-mono text-gray-600">{d}</div>
                ))}
              </div>
            )}
            {pair.port_overlap.length > 0 && (
              <div className="bg-white rounded-lg p-3 border border-gray-200">
                <div className="font-semibold text-gray-700 mb-1">Port Overlap</div>
                <div className="font-mono text-gray-600">{pair.port_overlap.join(', ')}</div>
              </div>
            )}
          </div>

          {/* Side-by-side rule detail */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[pair.rule1, pair.rule2].map((rule, ri) => (
              <div key={ri} className="bg-white rounded-lg p-3 border border-gray-200 text-xs space-y-1">
                <div className="font-semibold text-gray-900 mb-2">Rule {ri + 1}: {rule.name}</div>
                <div><span className="text-gray-500">Group:</span> {rule.rule_group} (p{rule.rule_group_priority})</div>
                <div><span className="text-gray-500">Collection:</span> {rule.rule_collection} (p{rule.rule_collection_priority})</div>
                <div><span className="text-gray-500">Action:</span>
                  <span className={`ml-1 font-medium ${rule.action === 'Allow' ? 'text-green-600' : 'text-red-600'}`}>
                    {rule.action}
                  </span>
                </div>
                <div><span className="text-gray-500">Sources:</span> {rule.source_addresses.join(', ') || '—'}</div>
                <div><span className="text-gray-500">Destinations:</span> {[...rule.destination_addresses, ...rule.destination_fqdns, ...rule.destination_urls].join(', ') || '—'}</div>
                <div><span className="text-gray-500">Ports:</span> {rule.destination_ports.join(', ') || '—'}</div>
                <div><span className="text-gray-500">Protocols:</span> {rule.protocols.join(', ')}</div>
              </div>
            ))}
          </div>

          {/* Recommendation */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-900">
            <span className="font-semibold">Recommendation: </span>{pair.recommendation}
          </div>
        </div>
      )}
    </div>
  );
}

export default function OverlapAnalysis({ overlaps, totalScanned, summary, analysisMode }: Props) {
  const [filterSeverity, setFilterSeverity] = useState<string>('all');

  const shown = filterSeverity === 'all' ? overlaps : overlaps.filter((o) => o.severity === filterSeverity);


  function exportCsv(severity: 'exact_duplicate' | 'same_action' | 'conflict', filename: string) {
    const rows = overlaps.filter((o) => o.severity === severity);
    if (!rows.length) return;
    const headers = [
      'Severity', 'Category',
      'Rule1 Name', 'Rule1 Group', 'Rule1 Collection', 'Rule1 Action',
      'Rule1 Sources', 'Rule1 Destinations', 'Rule1 Ports', 'Rule1 Protocols',
      'Rule2 Name', 'Rule2 Group', 'Rule2 Collection', 'Rule2 Action',
      'Rule2 Sources', 'Rule2 Destinations', 'Rule2 Ports', 'Rule2 Protocols',
      'Source Overlap', 'Destination IP Overlap', 'Destination FQDN Overlap', 'Port Overlap',
      'Recommendation',
    ];
    const csvRows = rows.map((o) => [
      o.severity, o.rule_category,
      o.rule1.name, o.rule1.rule_group, o.rule1.rule_collection, o.rule1.action,
      o.rule1.source_addresses.join('; '),
      [...o.rule1.destination_addresses, ...o.rule1.destination_fqdns, ...o.rule1.destination_urls].join('; '),
      o.rule1.destination_ports.join('; '),
      o.rule1.protocols.join('; '),
      o.rule2.name, o.rule2.rule_group, o.rule2.rule_collection, o.rule2.action,
      o.rule2.source_addresses.join('; '),
      [...o.rule2.destination_addresses, ...o.rule2.destination_fqdns, ...o.rule2.destination_urls].join('; '),
      o.rule2.destination_ports.join('; '),
      o.rule2.protocols.join('; '),
      o.source_overlap.join('; '),
      o.destination_ip_overlap.join('; '),
      o.destination_fqdn_overlap.join('; '),
      o.port_overlap.join('; '),
      o.recommendation,
    ]);
    const csv = [headers, ...csvRows]
      .map((row) => row.map((c) => `"${String(c ?? '').replace(/"/g, '""')}"`).join(','))
      .join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      {/* Analysis mode info */}
      {/* Policy-wide scan stats (bucket_stats) intentionally not shown in UI — see backend overlap logic comments */}

      {/* KPI strip */}
      <div className="grid grid-cols-3 gap-3">
        <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-center">
          <div className="text-2xl font-bold text-red-700">{summary.exact_duplicates}</div>
          <div className="text-xs text-red-600 mt-0.5">Exact Duplicates</div>
        </div>
        <div className="bg-orange-50 border border-orange-200 rounded-xl p-4 text-center">
          <div className="text-2xl font-bold text-orange-600">{summary.same_action_overlaps}</div>
          <div className="text-xs text-orange-600 mt-0.5">Same Action Overlaps</div>
        </div>
        <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4 text-center">
          <div className="text-2xl font-bold text-yellow-700">{summary.conflicting_rules}</div>
          <div className="text-xs text-yellow-600 mt-0.5">Conflicting Actions</div>
        </div>
      </div>

      {/* Severity filter + exports */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2 text-sm">
          {(['all', 'exact_duplicate', 'same_action', 'conflict'] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilterSeverity(s)}
              className={`px-3 py-1.5 rounded-full border transition-colors ${
                filterSeverity === s
                  ? 'bg-indigo-500 text-white border-indigo-500'
                  : 'bg-white text-gray-600 border-gray-300 hover:border-indigo-300'
              }`}
            >
              {s === 'all' ? 'All' : SEVERITY_META[s]?.label ?? s}
              {s !== 'all' && (
                <span className="ml-1.5 font-semibold">
                  ({s === 'exact_duplicate' ? summary.exact_duplicates : s === 'same_action' ? summary.same_action_overlaps : summary.conflicting_rules})
                </span>
              )}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          {summary.exact_duplicates > 0 && (
            <button
              onClick={() => exportCsv('exact_duplicate', 'firewall-exact-duplicates.csv')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-300 text-red-700 rounded-lg hover:bg-red-50 transition-colors"
            >
              <ArrowDownTrayIcon className="w-3.5 h-3.5" />
              Export Duplicates ({summary.exact_duplicates})
            </button>
          )}
          {summary.same_action_overlaps > 0 && (
            <button
              onClick={() => exportCsv('same_action', 'firewall-same-action-overlaps.csv')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-orange-300 text-orange-700 rounded-lg hover:bg-orange-50 transition-colors"
            >
              <ArrowDownTrayIcon className="w-3.5 h-3.5" />
              Export Overlaps ({summary.same_action_overlaps})
            </button>
          )}
          {summary.conflicting_rules > 0 && (
            <button
              onClick={() => exportCsv('conflict', 'firewall-conflicting-rules.csv')}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-yellow-400 text-yellow-800 rounded-lg hover:bg-yellow-50 transition-colors"
            >
              <ArrowDownTrayIcon className="w-3.5 h-3.5" />
              Export Conflicts ({summary.conflicting_rules})
            </button>
          )}
        </div>
      </div>

      {/* Scanned info */}
      <p className="text-xs text-gray-500">
        {overlaps.length} overlap pair{overlaps.length !== 1 ? 's' : ''} found across {totalScanned} rules scanned.
        Showing {shown.length}.
      </p>

      {/* Cards */}
      {shown.length === 0 ? (
        <div className="text-center py-12">
          <div className="w-12 h-12 mx-auto bg-green-100 rounded-full flex items-center justify-center mb-3">
            <span className="text-2xl">✓</span>
          </div>
          <p className="text-gray-500 text-sm">No overlaps detected for the selected filter.</p>
        </div>
      ) : (
        <div className="max-h-[65vh] overflow-y-auto pr-1 space-y-0">
          {shown.map((pair, i) => (
            <OverlapCard key={i} pair={pair} index={i} />
          ))}
        </div>
      )}
    </div>
  );
}

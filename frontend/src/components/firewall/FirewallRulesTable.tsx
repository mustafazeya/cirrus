'use client';

import { fmtN } from '../../utils/format';

import { useState, useMemo } from 'react';
import type { FirewallRule } from '../../types/firewall';
import {
  MagnifyingGlassIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ArrowDownTrayIcon,
} from '@heroicons/react/24/outline';

interface Props {
  rules: FirewallRule[];
  title?: string;
  showMatchReasons?: boolean;
  highlightOrphan?: boolean;
}

const ACTION_COLORS: Record<string, string> = {
  Allow: 'bg-green-100 text-green-800 border-green-200',
  Deny: 'bg-red-100 text-red-800 border-red-200',
  DNAT: 'bg-yellow-100 text-yellow-800 border-yellow-200',
  SNAT: 'bg-blue-100 text-blue-800 border-blue-200',
  Unknown: 'bg-gray-100 text-gray-600 border-gray-200',
};

const TYPE_COLORS: Record<string, string> = {
  NetworkRule: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  ApplicationRule: 'bg-purple-50 text-purple-700 border-purple-200',
  NatRule: 'bg-orange-50 text-orange-700 border-orange-200',
};

function badgeClass(map: Record<string, string>, key: string, fallback = ''): string {
  return map[key] ?? map['Unknown'] ?? fallback;
}

function truncate(items: string[], max = 3): { shown: string[]; more: number } {
  return { shown: items.slice(0, max), more: Math.max(0, items.length - max) };
}

function RuleBadge({ label, colorClass }: { label: string; colorClass: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${colorClass}`}>
      {label}
    </span>
  );
}

function ExpandedRuleDetail({ rule }: { rule: FirewallRule }) {
  return (
    <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 text-xs text-gray-600 border-t border-gray-100 pt-3">
      {rule.source_addresses.length > 0 && (
        <div>
          <span className="font-semibold text-gray-700">Source Addresses:</span>
          <ul className="mt-1 space-y-0.5">
            {rule.source_addresses.map((a) => <li key={a} className="font-mono">{a}</li>)}
          </ul>
        </div>
      )}
      {rule.destination_addresses.length > 0 && (
        <div>
          <span className="font-semibold text-gray-700">Destination Addresses:</span>
          <ul className="mt-1 space-y-0.5">
            {rule.destination_addresses.map((a) => <li key={a} className="font-mono">{a}</li>)}
          </ul>
        </div>
      )}
      {rule.destination_fqdns.length > 0 && (
        <div>
          <span className="font-semibold text-gray-700">FQDNs:</span>
          <ul className="mt-1 space-y-0.5">
            {rule.destination_fqdns.map((f) => <li key={f} className="font-mono">{f}</li>)}
          </ul>
        </div>
      )}
      {rule.destination_urls.length > 0 && (
        <div>
          <span className="font-semibold text-gray-700">Target URLs:</span>
          <ul className="mt-1 space-y-0.5">
            {rule.destination_urls.map((u) => <li key={u} className="font-mono">{u}</li>)}
          </ul>
        </div>
      )}
      {rule.destination_ports.length > 0 && (
        <div>
          <span className="font-semibold text-gray-700">Ports:</span>
          <span className="ml-1 font-mono">{rule.destination_ports.join(', ')}</span>
        </div>
      )}
      {rule.protocols.length > 0 && (
        <div>
          <span className="font-semibold text-gray-700">Protocols:</span>
          <span className="ml-1">{rule.protocols.join(', ')}</span>
        </div>
      )}
      {rule.translated_address && (
        <div>
          <span className="font-semibold text-gray-700">Translated Address:</span>
          <span className="ml-1 font-mono">{rule.translated_address}
          {rule.translated_port ? `:${rule.translated_port}` : ''}</span>
        </div>
      )}
      {rule._match_reasons && rule._match_reasons.length > 0 && (
        <div className="md:col-span-2">
          <span className="font-semibold text-green-700">Match reasons:</span>
          <ul className="mt-1 list-disc list-inside space-y-0.5 text-green-700">
            {rule._match_reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
      {rule.log_hits !== undefined && (
        <div>
          <span className="font-semibold text-gray-700">Log hits ({rule.days_observed}d):</span>
          <span className={`ml-1 font-semibold ${rule.log_hits === 0 ? 'text-red-600' : 'text-green-600'}`}>
            {fmtN(rule.log_hits)}
          </span>
        </div>
      )}
      <div className="text-gray-400 text-xs md:col-span-2">
        {rule.rule_group} › {rule.rule_collection} (priority {rule.rule_collection_priority})
      </div>
    </div>
  );
}

function RuleRow({ rule, showMatchReasons, highlightOrphan }: {
  rule: FirewallRule;
  showMatchReasons: boolean;
  highlightOrphan: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const isOrphan = highlightOrphan && rule.log_hits === 0;

  const srcSummary = truncate(rule.source_addresses, 2);
  const dstSummary = truncate(
    [...rule.destination_addresses, ...rule.destination_fqdns, ...rule.destination_urls],
    2,
  );

  return (
    <div
      className={`border rounded-lg mb-2 transition-shadow hover:shadow-sm ${
        isOrphan ? 'border-red-200 bg-red-50/30' : 'border-gray-200 bg-white'
      }`}
    >
      <button
        className="w-full px-4 py-3 flex items-center gap-3 text-left"
        onClick={() => setExpanded((e) => !e)}
      >
        {expanded
          ? <ChevronDownIcon className="w-4 h-4 text-gray-400 shrink-0" />
          : <ChevronRightIcon className="w-4 h-4 text-gray-400 shrink-0" />}

        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="font-medium text-gray-900 text-sm truncate">{rule.name}</span>
            <RuleBadge label={rule.rule_type.replace('Rule', '')} colorClass={badgeClass(TYPE_COLORS, rule.rule_type)} />
            <RuleBadge label={rule.action || rule.rule_collection_action} colorClass={badgeClass(ACTION_COLORS, rule.action || rule.rule_collection_action)} />
            {isOrphan && <RuleBadge label="Orphan – 0 hits" colorClass="bg-red-100 text-red-700 border-red-300" />}
            {rule.log_hits !== undefined && rule.log_hits > 0 && (
              <RuleBadge label={`${fmtN(rule.log_hits)} hits`} colorClass="bg-green-100 text-green-700 border-green-200" />
            )}
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-gray-500">
            <span>
              <span className="font-medium">Src:</span>{' '}
              {srcSummary.shown.join(', ')}{srcSummary.more > 0 ? ` +${srcSummary.more}` : ''}
            </span>
            <span>
              <span className="font-medium">Dst:</span>{' '}
              {dstSummary.shown.join(', ')}{dstSummary.more > 0 ? ` +${dstSummary.more}` : ''}
            </span>
            {rule.destination_ports.length > 0 && (
              <span>
                <span className="font-medium">Ports:</span>{' '}
                {rule.destination_ports.slice(0, 4).join(', ')}{rule.destination_ports.length > 4 ? ' …' : ''}
              </span>
            )}
            {rule.protocols.length > 0 && (
              <span>
                <span className="font-medium">Proto:</span> {rule.protocols.join('/')}
              </span>
            )}
          </div>
        </div>
      </button>
      {expanded && (
        <div className="px-4 pb-4">
          <ExpandedRuleDetail rule={rule} />
        </div>
      )}
    </div>
  );
}

export default function FirewallRulesTable({ rules, title, showMatchReasons = false, highlightOrphan = false }: Props) {
  const [search, setSearch] = useState('');
  const [filterType, setFilterType] = useState('all');
  const [filterAction, setFilterAction] = useState('all');

  const filtered = useMemo(() => {
    const s = search.toLowerCase();
    return rules.filter((r) => {
      if (filterType !== 'all' && !r.rule_type.toLowerCase().includes(filterType)) return false;
      if (filterAction !== 'all' && r.action.toLowerCase() !== filterAction) return false;
      if (!s) return true;
      return (
        r.name.toLowerCase().includes(s) ||
        r.source_addresses.some((a) => a.includes(s)) ||
        r.destination_addresses.some((a) => a.includes(s)) ||
        r.destination_fqdns.some((f) => f.toLowerCase().includes(s)) ||
        r.destination_ports.some((p) => p.includes(s)) ||
        r.rule_group.toLowerCase().includes(s) ||
        r.rule_collection.toLowerCase().includes(s)
      );
    });
  }, [rules, search, filterType, filterAction]);

  function exportCsv() {
    const headers = ['Name', 'Type', 'Action', 'Src', 'Dst', 'Ports', 'Protocols', 'Group', 'Collection', 'Log Hits'];
    const rows = filtered.map((r) => [
      r.name,
      r.rule_type,
      r.action,
      r.source_addresses.join(';'),
      [...r.destination_addresses, ...r.destination_fqdns].join(';'),
      r.destination_ports.join(';'),
      r.protocols.join(';'),
      r.rule_group,
      r.rule_collection,
      r.log_hits ?? '',
    ]);
    const csv = [headers, ...rows].map((row) => row.map((c) => `"${c}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'firewall-rules.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      {title && (
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900">{title}</h3>
          <span className="text-xs text-gray-500">{filtered.length} of {rules.length} rules</span>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-[200px]">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            className="w-full pl-9 pr-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="Filter by name, IP, FQDN, port…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <select
          className="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
        >
          <option value="all">All Types</option>
          <option value="network">Network</option>
          <option value="application">Application</option>
          <option value="nat">NAT</option>
        </select>
        <select
          className="text-sm border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
          value={filterAction}
          onChange={(e) => setFilterAction(e.target.value)}
        >
          <option value="all">All Actions</option>
          <option value="allow">Allow</option>
          <option value="deny">Deny</option>
          <option value="dnat">DNAT</option>
        </select>
        <button
          onClick={exportCsv}
          className="flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
        >
          <ArrowDownTrayIcon className="w-4 h-4" />
          Export
        </button>
      </div>

      {/* Rules list */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-gray-400">No rules match the current filters.</div>
      ) : (
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {filtered.map((rule, idx) => (
            <RuleRow
              key={`${rule.rule_collection}-${rule.name}-${idx}`}
              rule={rule}
              showMatchReasons={showMatchReasons}
              highlightOrphan={highlightOrphan}
            />
          ))}
        </div>
      )}
    </div>
  );
}

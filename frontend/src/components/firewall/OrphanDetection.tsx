'use client';

import { useState } from 'react';
import type { OrphanResult, FirewallRule } from '../../types/firewall';
import {
  ExclamationCircleIcon,
  CheckCircleIcon,
  TrashIcon,
  ChevronDownIcon,
} from '@heroicons/react/24/outline';
import FirewallRulesTable from './FirewallRulesTable';

interface Props {
  result: OrphanResult;
  onDrilldown?: (rule: FirewallRule) => void;
}

export default function OrphanDetection({ result, onDrilldown }: Props) {
  const [showOrphans, setShowOrphans] = useState(true);
  const [showActive,  setShowActive]  = useState(false);

  const orphanPercent = result.total_rules > 0
    ? ((result.orphan_count / result.total_rules) * 100).toFixed(1)
    : '0';

  // Sort active rules by hit count descending
  const activeRulesSorted = [...result.active_rules].sort(
    (a, b) => (b.log_hits ?? 0) - (a.log_hits ?? 0),
  );

  return (
    <div className="space-y-5">
      {/* Summary KPIs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-red-50 border border-red-200 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <TrashIcon className="w-4 h-4 text-red-500" />
            <span className="text-xs text-red-600 font-medium">Orphan Rules</span>
          </div>
          <div className="text-2xl font-bold text-red-700">{result.orphan_count}</div>
          <div className="text-xs text-red-500 mt-0.5">{orphanPercent}% of total</div>
        </div>
        <div className="bg-green-50 border border-green-200 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircleIcon className="w-4 h-4 text-green-500" />
            <span className="text-xs text-green-600 font-medium">Active Rules</span>
          </div>
          <div className="text-2xl font-bold text-green-700">{result.active_count}</div>
          <div className="text-xs text-green-500 mt-0.5">with log activity</div>
        </div>
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs text-gray-600 font-medium">Total Rules</span>
          </div>
          <div className="text-2xl font-bold text-gray-700">{result.total_rules}</div>
          <div className="text-xs text-gray-500 mt-0.5">{result.days_observed}-day window</div>
        </div>
        <div className={`border rounded-xl p-4 ${result.log_available ? 'bg-blue-50 border-blue-200' : 'bg-yellow-50 border-yellow-200'}`}>
          <div className="flex items-center gap-2 mb-1">
            {result.log_available
              ? <CheckCircleIcon className="w-4 h-4 text-blue-500" />
              : <ExclamationCircleIcon className="w-4 h-4 text-yellow-500" />}
            <span className={`text-xs font-medium ${result.log_available ? 'text-blue-600' : 'text-yellow-600'}`}>
              Logs
            </span>
          </div>
          <div className={`text-sm font-semibold ${result.log_available ? 'text-blue-700' : 'text-yellow-700'}`}>
            {result.log_available ? 'Available' : 'Not found'}
          </div>
          <div className="text-xs text-gray-500 mt-0.5 truncate" title={result.workspace_id}>
            {result.workspace_id.split('/').pop() || result.workspace_id}
          </div>
        </div>
      </div>

      {!result.log_available && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-xs text-yellow-800">
          <strong>No Firewall logs found</strong> in the selected workspace for the past {result.days_observed} days.
          Ensure Azure Firewall diagnostic settings are configured to stream{' '}
          <em>AzureFirewallNetworkRule</em> and <em>AzureFirewallApplicationRule</em> logs to this workspace.
          All rules are shown as orphans since hit data is unavailable.
        </div>
      )}

      {/* ── Orphan rules section ──────────────────────────────────── */}
      <div className="border border-gray-200 rounded-xl overflow-hidden">
        <button
          type="button"
          onClick={() => setShowOrphans((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 bg-red-50 hover:bg-red-100 transition-colors"
        >
          <div className="flex items-center gap-2">
            <TrashIcon className="w-4 h-4 text-red-500" />
            <span className="text-sm font-semibold text-red-700">
              Orphan Rules
            </span>
            <span className="text-xs bg-red-200 text-red-800 rounded-full px-2 py-0.5 font-medium">
              {result.orphan_count}
            </span>
          </div>
          <ChevronDownIcon className={`w-4 h-4 text-red-400 transition-transform ${showOrphans ? 'rotate-180' : ''}`} />
        </button>
        {showOrphans && (
          <div className="p-4">
            <FirewallRulesTable
              rules={result.orphan_rules}
              highlightOrphan={true}
              title={result.orphan_count === 0 ? 'No orphan rules detected' : undefined}
            />
          </div>
        )}
      </div>

      {/* ── Active rules section ──────────────────────────────────── */}
      <div className="border border-gray-200 rounded-xl overflow-hidden">
        <button
          type="button"
          onClick={() => setShowActive((v) => !v)}
          className="w-full flex items-center justify-between px-4 py-3 bg-green-50 hover:bg-green-100 transition-colors"
        >
          <div className="flex items-center gap-2">
            <CheckCircleIcon className="w-4 h-4 text-green-500" />
            <span className="text-sm font-semibold text-green-700">
              Active Rules — with hit counts
            </span>
            <span className="text-xs bg-green-200 text-green-800 rounded-full px-2 py-0.5 font-medium">
              {result.active_count}
            </span>
          </div>
          <ChevronDownIcon className={`w-4 h-4 text-green-400 transition-transform ${showActive ? 'rotate-180' : ''}`} />
        </button>
        {showActive && (
          <div className="p-4">
            <p className="text-xs text-gray-400 mb-3">
              Sorted by hit count (highest first) over the {result.days_observed}-day window.
            </p>
            <FirewallRulesTable
              rules={activeRulesSorted}
              title={result.active_count === 0 ? 'No active rules found' : undefined}
            />
          </div>
        )}
      </div>
    </div>
  );
}


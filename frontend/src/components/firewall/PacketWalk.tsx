'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import {
  ArrowRightIcon,
  CheckCircleIcon,
  ExclamationTriangleIcon,
  XCircleIcon,
  InformationCircleIcon,
  ShieldExclamationIcon,
} from '@heroicons/react/24/outline';
import CirrusAvatar from '../CirrusAvatar';
import type { PacketWalkResult, PacketWalkStageRoute, PacketWalkStageNsg, PacketWalkStageFw, PacketWalkStageSource } from '../../types/firewall';

interface Props {
  subscriptionId: string;
  resourceGroup: string;
  policyName: string;
  onAnalyze: (req: PacketWalkRequest) => void;
  loading?: boolean;
  result?: PacketWalkResult | null;
  source: string;
  dest: string;
  port: string;
  protocol: string;
  onSourceChange: (v: string) => void;
  onDestChange: (v: string) => void;
  onPortChange: (v: string) => void;
  onProtocolChange: (v: string) => void;
}

export interface PacketWalkRequest {
  source: string;
  destination: string;
  port: string;
  protocol: string;
}

const PROTOCOLS = ['TCP', 'UDP', 'ICMP', 'HTTPS', 'HTTP'];

function StageCard({ title, ok, partial, skipped, children }: { title: string; ok?: boolean; partial?: boolean; skipped?: boolean; children: React.ReactNode }) {
  const borderBg = skipped
    ? 'border-gray-100 bg-gray-50/50 opacity-60'
    : partial
    ? 'border-amber-200 bg-amber-50/30'
    : ok === true  ? 'border-green-200 bg-green-50/30'
    : ok === false ? 'border-red-200 bg-red-50/30'
    : 'border-gray-200 bg-white';
  return (
    <div className={`border rounded-xl p-4 space-y-2 ${borderBg}`}>
      <div className="flex items-center gap-2">
        {skipped   ? <InformationCircleIcon className="w-4 h-4 text-gray-400" /> :
         partial   ? <ExclamationTriangleIcon className="w-4 h-4 text-amber-500" /> :
         ok === true  ? <CheckCircleIcon className="w-4 h-4 text-green-500" /> :
         ok === false ? <XCircleIcon className="w-4 h-4 text-red-500" /> :
                        <InformationCircleIcon className="w-4 h-4 text-gray-400" />}
        <span className="text-sm font-semibold text-gray-700">{title}</span>
      </div>
      {children}
    </div>
  );
}

function SourceStage({ stage }: { stage: PacketWalkStageSource }) {
  const typeLabel: Record<string, string> = {
    azure_nic:           'Azure NIC',
    azure_subnet:        'Azure Subnet',
    external_via_gateway:'External via Gateway',
    external_unknown:    'External (unknown)',
  };
  return (
    <StageCard title="Source Resolution" ok={stage.type !== 'external_unknown'} skipped={false}>
      <p className="text-xs text-gray-600">{stage.detail}</p>
      <div className="flex flex-wrap gap-2 mt-1">
        <Chip label="Type" value={typeLabel[stage.type] ?? stage.type} />
        {stage.subnet_cidr && <Chip label="Subnet" value={stage.subnet_cidr} />}
        {stage.gateway_name && <Chip label="Gateway" value={stage.gateway_name} />}
        {stage.circuit_name && stage.circuit_name !== 'unknown' && <Chip label="ER/VPN Peer" value={stage.circuit_name} />}
        {stage.nsg_id && <Chip label="NSG" value={stage.nsg_id.split('/').pop() ?? ''} />}
      </div>
    </StageCard>
  );
}

function RouteStage({ stage }: { stage: PacketWalkStageRoute }) {
  const routes = stage.all_matching_routes ?? [];
  return (
    <StageCard title="Route Evaluation" ok={stage.reaches_firewall} skipped={false}>
      <p className="text-xs text-gray-600">{stage.detail}</p>
      <div className="flex flex-wrap gap-2 mt-1">
        {stage.winning_prefix && <Chip label="Prefix" value={stage.winning_prefix} />}
        {stage.route_source && <Chip label="Source" value={stage.route_source} />}
        {stage.next_hop_type && <Chip label="Next Hop" value={`${stage.next_hop_type} ${stage.next_hop_ip ?? ''}`.trim()} />}
        {stage.bgp_peer && <Chip label="BGP Peer" value={stage.bgp_peer} />}
      </div>
      {routes.length > 1 && (
        <details className="mt-2">
          <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600">
            {routes.length} candidate route{routes.length > 1 ? 's' : ''} evaluated (longest prefix wins)
          </summary>
          <table className="mt-1 w-full text-xs">
            <thead>
              <tr className="text-gray-400">
                <th className="text-left font-normal pb-0.5 pr-3">Prefix</th>
                <th className="text-left font-normal pb-0.5 pr-3">Source</th>
                <th className="text-left font-normal pb-0.5 pr-3">Next Hop</th>
              </tr>
            </thead>
            <tbody>
              {routes.map((r, i) => (
                <tr key={i} className={r.winning ? 'font-semibold text-gray-800' : 'text-gray-500'}>
                  <td className="pr-3 font-mono">{r.winning ? '▶ ' : ''}{r.prefix}</td>
                  <td className="pr-3">{r.source}</td>
                  <td className="font-mono">{r.nextHopType}{r.nextHopIp ? ` ${r.nextHopIp}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {stage.security_flag && (
        <div className="mt-2 p-2.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700 font-medium flex gap-1.5">
          <ShieldExclamationIcon className="w-4 h-4 shrink-0 mt-0.5" />
          {stage.security_flag}
        </div>
      )}
    </StageCard>
  );
}

function NsgStage({ stage }: { stage: PacketWalkStageNsg }) {
  return (
    <StageCard title="NSG (Outbound)" ok={stage.verdict === 'allow' || !stage.has_nsg} skipped={!stage.has_nsg}>
      <p className="text-xs text-gray-600">{stage.detail}</p>
      {stage.matching_rule && (
        <div className="flex flex-wrap gap-2 mt-1">
          <Chip label="Rule" value={stage.matching_rule} />
          {stage.priority !== undefined && <Chip label="Priority" value={String(stage.priority)} />}
        </div>
      )}
      {(stage.near_misses ?? []).length > 0 && (
        <details className="mt-1">
          <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600">
            {stage.near_misses!.length} near-miss{stage.near_misses!.length > 1 ? 'es' : ''}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {stage.near_misses!.map((m, i) => (
              <li key={i} className="text-xs text-gray-500 pl-2 border-l-2 border-gray-200">{m}</li>
            ))}
          </ul>
        </details>
      )}
    </StageCard>
  );
}

function FwStage({ stage }: { stage: PacketWalkStageFw }) {
  const allowed = stage.verdict === 'allow' || stage.action?.toLowerCase() === 'allow';
  const partials = stage.partial_coverage_rules ?? [];
  const isPartial = !allowed && partials.length > 0;
  return (
    <StageCard title="Firewall Rule Evaluation" ok={allowed ? true : isPartial ? undefined : false} partial={isPartial} skipped={false}>
      <p className="text-xs text-gray-600">
        {isPartial
          ? `${partials.length} sub-range${partials.length > 1 ? 's' : ''} within the source are allowed by specific rules — the rest is implicitly denied.`
          : stage.detail}
      </p>
      {stage.matching_rule && (
        <div className="flex flex-wrap gap-2 mt-1">
          <Chip label="Rule" value={stage.matching_rule} />
          {stage.rule_collection && <Chip label="Collection" value={stage.rule_collection} />}
          {stage.rule_group && <Chip label="Group" value={stage.rule_group} />}
          {stage.group_priority !== undefined && <Chip label="Group Priority" value={String(stage.group_priority)} />}
          {stage.collection_priority !== undefined && <Chip label="Coll. Priority" value={String(stage.collection_priority)} />}
          {stage.rule_type && <Chip label="Type" value={stage.rule_type} />}
        </div>
      )}
      {partials.length > 0 && (
        <details className="mt-2" open>
          <summary className="text-xs font-medium text-amber-600 cursor-pointer">
            ⚠ {partials.length} sub-range{partials.length > 1 ? 's' : ''} within this source are allowed
          </summary>
          <ul className="mt-1 space-y-1.5">
            {partials.map((p, i) => (
              <li key={i} className="text-xs pl-2 border-l-2 border-amber-300 space-y-0.5">
                <div>
                  <span className="font-mono text-gray-700">{(p.covered_ranges ?? []).join(', ')}</span>
                  <span className="text-gray-400 mx-1">→</span>
                  <span className="font-medium text-green-600">{p.action ?? 'Allow'}</span>
                </div>
                <div className="text-gray-500">
                  via <span className="font-medium text-gray-700">{p.matching_rule}</span>
                  {p.rule_collection && <span className="text-gray-400"> in {p.rule_collection}</span>}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
      {(stage.near_misses ?? []).length > 0 && (
        <details className="mt-1">
          <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600">
            {stage.near_misses!.length} near-miss{stage.near_misses!.length > 1 ? 'es' : ''}
          </summary>
          <ul className="mt-1 space-y-1">
            {stage.near_misses!.map((m, i) => (
              <li key={i} className="text-xs text-gray-500 pl-2 border-l-2 border-gray-200">
                <span className={`font-medium ${m.action?.toLowerCase() === 'allow' ? 'text-green-600' : 'text-red-600'}`}>
                  {m.rule}
                </span>
                {m.collection && <span className="text-gray-400"> in {m.collection}</span>}
                {' — '}{m.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </StageCard>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span className="text-gray-400">{label}:</span>
      <span className="font-mono text-gray-700 bg-gray-100 px-1.5 py-0.5 rounded">{value}</span>
    </span>
  );
}

export default function PacketWalk({ onAnalyze, loading, result, source, dest, port, protocol, onSourceChange, onDestChange, onPortChange, onProtocolChange }: Props) {
  const canRun = source.trim().length > 0 && dest.trim().length > 0 && port.trim().length > 0;

  const run = () => {
    if (!canRun) return;
    onAnalyze({ source: source.trim(), destination: dest.trim(), port: port.trim(), protocol });
  };

  return (
    <div className="space-y-4">
      {/* Input form */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-4">
        <div className="flex items-center gap-2">
          <ArrowRightIcon className="w-4 h-4 text-gray-500" />
          <span className="text-sm font-semibold text-gray-800">Packet Walk</span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Source IP or CIDR</label>
            <input
              value={source}
              onChange={e => onSourceChange(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && canRun && run()}
              placeholder="e.g. 10.33.68.5 or 10.33.0.0/16"
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Destination IP, CIDR, or FQDN</label>
            <input
              value={dest}
              onChange={e => onDestChange(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && canRun && run()}
              placeholder="e.g. google.com or 52.12.14.54"
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Port</label>
            <input
              value={port}
              onChange={e => onPortChange(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && canRun && run()}
              placeholder="443"
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Protocol</label>
            <select
              value={protocol}
              onChange={e => onProtocolChange(e.target.value)}
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-1.5 bg-white focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            >
              {PROTOCOLS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs text-gray-400">
          <InformationCircleIcon className="w-3.5 h-3.5 shrink-0" />
          Source and destination accept single IPs or CIDRs. FQDN destinations are DNS-resolved before rule matching.
          BGP route fetch from gateways may take up to 60 s.
        </div>

        <div className="flex justify-end">
          <button
            onClick={run}
            disabled={!canRun || loading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm bg-indigo-500 text-white rounded-lg hover:bg-indigo-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed font-medium"
          >
            <ArrowRightIcon className="w-3.5 h-3.5" />
            {loading ? 'Analysing…' : 'Analyse'}
          </button>
        </div>
      </div>

      {/* Results */}
      {result && (
        <div className="space-y-4">
          {/* Security flags */}
          {result.security_flags.length > 0 && (
            <div className="p-3 bg-red-50 border border-red-300 rounded-xl space-y-1">
              {result.security_flags.map((f, i) => (
                <div key={i} className="flex gap-2 text-xs text-red-700">
                  <ShieldExclamationIcon className="w-4 h-4 shrink-0 mt-0.5" />
                  <span>{f}</span>
                </div>
              ))}
            </div>
          )}

          {/* Stage pipeline */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <SourceStage stage={result.stages.source_resolution} />
            {result.stages.route_evaluation
              ? <RouteStage stage={result.stages.route_evaluation} />
              : <StageCard title="Route Evaluation" skipped><p className="text-xs text-gray-400">Skipped — source is external or routing data unavailable.</p></StageCard>
            }
            {result.stages.nsg_evaluation
              ? <NsgStage stage={result.stages.nsg_evaluation} />
              : <StageCard title="NSG (Outbound)" skipped><p className="text-xs text-gray-400">Skipped — no NSG found or source outside Azure.</p></StageCard>
            }
            {result.stages.firewall_evaluation
              ? <FwStage stage={result.stages.firewall_evaluation} />
              : <StageCard title="Firewall Rule Evaluation" skipped><p className="text-xs text-gray-400">Skipped.</p></StageCard>
            }
          </div>

          {/* AI analysis */}
          {result.ai_analysis && typeof result.ai_analysis === 'string' && result.ai_analysis.trim() && (
            <div className="flex gap-3 px-1">
              <div className="flex flex-col items-center pt-1 gap-2">
                <div className="w-8 h-8 rounded-lg overflow-hidden shrink-0">
                  <CirrusAvatar size="sm" />
                </div>
                <div className="w-px flex-1 bg-gray-100" />
              </div>
              <div className="flex-1 min-w-0 pb-2">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">CIRRUS Analysis</p>
                <div className="prose prose-sm prose-gray max-w-none
                  prose-p:text-gray-800 prose-p:leading-relaxed prose-p:my-1.5
                  prose-strong:text-gray-900 prose-strong:font-semibold
                  prose-code:text-[11.5px] prose-code:bg-gray-100 prose-code:border prose-code:border-gray-200 prose-code:rounded prose-code:px-1.5 prose-code:py-0.5 prose-code:font-mono prose-code:text-gray-700 prose-code:before:content-none prose-code:after:content-none
                  prose-blockquote:border-l-amber-400 prose-blockquote:bg-amber-50 prose-blockquote:py-1 prose-blockquote:pr-3 prose-blockquote:rounded-r-lg prose-blockquote:text-amber-900 prose-blockquote:not-italic prose-blockquote:my-2">
                  <ReactMarkdown>{result.ai_analysis}</ReactMarkdown>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Inline markdown renderer for RCA card content — bold, code, plain text only.
const RcaMd = ({ children }: { children: string }) => (
  <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    components={{
      p: ({ node, ...props }: any) => <span {...props} className="leading-relaxed" />,
      ul: ({ node, ...props }: any) => <ul {...props} className="list-none space-y-1.5 mt-1" />,
      li: ({ node, ...props }: any) => <li {...props} className="leading-relaxed text-gray-700" />,
      strong: ({ node, ...props }: any) => <strong {...props} className="font-semibold text-gray-900" />,
      code: ({ node, inline, ...props }: any) => (
        inline ? <code {...props} className="px-1 py-0.5 bg-gray-100 rounded text-xs font-mono text-gray-800" /> : <code {...props} />
      ),
      h1: ({ node, ...props }: any) => <strong {...props} />,
      h2: ({ node, ...props }: any) => <strong {...props} />,
      h3: ({ node, ...props }: any) => <strong {...props} />,
    }}
  >{children}</ReactMarkdown>
);

const NOISE_OPS = [
  'microsoft.authorization/policies/audit/action',
  'microsoft.authorization/roleassignments',
  'microsoft.resources/checkpolicy',
];

const CATEGORY_META: Record<string, { border: string; bg: string; label: string }> = {
  no_issue_detected:    { border: 'border-green-400',  bg: 'bg-green-50',  label: 'No Issue' },
  configuration_change: { border: 'border-orange-400', bg: 'bg-orange-50', label: 'Config Change' },
  azure_platform:       { border: 'border-red-400',    bg: 'bg-red-50',    label: 'Platform' },
  network_connectivity: { border: 'border-yellow-400', bg: 'bg-yellow-50', label: 'Network' },
  resource_exhaustion:  { border: 'border-red-400',    bg: 'bg-red-50',    label: 'Exhaustion' },
  security:             { border: 'border-red-500',    bg: 'bg-red-50',    label: 'Security' },
  external_dependency:  { border: 'border-blue-400',   bg: 'bg-blue-50',   label: 'External' },
  unknown:              { border: 'border-gray-300',   bg: 'bg-gray-50',   label: 'Unknown' },
};

interface RcaResultCardProps {
  result: any;
  /** When true wraps in a `border-t pt-4` container (RCA panel usage) */
  bordered?: boolean;
}

export default function RcaResultCard({ result, bordered = false }: RcaResultCardProps) {
  const confidence = Math.round((result.analysis?.confidence || 0) * 100);
  const category: string = result.analysis?.root_cause_category || 'unknown';

  const visibilityGaps: string[] = Array.isArray(result.visibility_gaps) ? result.visibility_gaps : [];
  const alternatives: string[] = Array.isArray(result.analysis?.alternative_explanations) ? result.analysis.alternative_explanations : [];
  const checklist: string[] = Array.isArray(result.manual_checklist) ? result.manual_checklist : [];
  const timeline = Array.isArray(result.timeline) ? result.timeline.slice(0, 20) : [];

  const evidenceRows: Array<{ source?: string; stat?: string; finding?: string }> =
    Array.isArray(result.analysis?.confidence_reasoning) ? result.analysis.confidence_reasoning : [];
  const timelineRows: Array<{ time?: string; event?: string }> =
    Array.isArray(result.analysis?.timeline_summary) ? result.analysis.timeline_summary : [];

  const signalEvents = timeline
    .filter((item: any) => !NOISE_OPS.some(n => (item.details?.operation_name || item.title || '').toLowerCase().includes(n)))
    .slice(0, 8);

  const meta = CATEGORY_META[category] || CATEGORY_META.unknown;
  const confidenceBarColor = confidence >= 80 ? 'bg-green-500' : confidence >= 65 ? 'bg-yellow-400' : 'bg-red-400';

  const meaningfulGaps = visibilityGaps.filter((g: string) =>
    !g.toLowerCase().startsWith('metrics collection failed') &&
    !g.toLowerCase().startsWith('diagnostic_coverage collection failed') &&
    !g.toLowerCase().startsWith('network collection failed')
  );

  return (
    <div className={`space-y-4 ${bordered ? 'border-t pt-4' : ''}`}>

      {/* Verdict card */}
      <div className={`rounded-lg border-l-4 ${meta.border} ${meta.bg} px-4 py-3`}>
        <div className="flex items-start justify-between gap-3 mb-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">Root Cause</span>
          <span className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full border ${meta.border} text-gray-700 bg-white`}>
            {meta.label}
          </span>
        </div>
        <div className="text-sm text-gray-900 leading-relaxed font-medium">
          <RcaMd>{result.analysis?.root_cause || ''}</RcaMd>
        </div>
        <div className="mt-3 flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div className={`h-full rounded-full ${confidenceBarColor}`} style={{ width: `${confidence}%` }} />
          </div>
          <span className="text-xs text-gray-500 whitespace-nowrap">Confidence {confidence}%</span>
        </div>
      </div>

      {/* Evidence signal counts */}
      {(() => {
        const s = result.evidence_summary || {};
        const pills = [
          { label: 'Changes', value: s.changes_count },
          { label: 'Log events', value: s.resource_log_events_count },
          { label: 'Health signals', value: s.health_signals },
          { label: 'Metric signals', value: s.resource_metric_signals },
        ].filter(i => i.value != null);
        return pills.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {pills.map(i => (
              <span key={i.label} className="text-xs text-gray-500 bg-gray-100 border border-gray-200 rounded px-2 py-1">
                {i.label}: <strong className="text-gray-700">{i.value}</strong>
              </span>
            ))}
          </div>
        ) : null;
      })()}

      {/* Evidence Assessment */}
      {evidenceRows.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">Evidence Assessment</p>
          <div className="divide-y divide-gray-100 rounded-lg border border-gray-200 overflow-hidden">
            {evidenceRows.map((row, i) => (
              <div key={i} className="flex gap-3 px-3 py-2.5 bg-white text-sm">
                <div className="shrink-0 w-32">
                  {row.source && <span className="text-xs font-semibold text-gray-600">{row.source}</span>}
                  {row.stat && <div className="text-xs text-gray-400 font-mono mt-0.5 truncate">{row.stat}</div>}
                </div>
                <span className="text-gray-700 leading-relaxed min-w-0">
                  <RcaMd>{row.finding || ''}</RcaMd>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Timeline */}
      {(timelineRows.length > 0 || signalEvents.length > 0) && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">Timeline</p>
          <div className="relative pl-4 space-y-0">
            <div className="absolute left-[7px] top-1 bottom-1 w-px bg-gray-200" />
            {timelineRows.map((row, i) => (
              <div key={i} className="relative flex gap-3 pb-3">
                <div className="shrink-0 w-3 h-3 rounded-full bg-white border-2 border-gray-400 mt-0.5 z-10" />
                <div className="text-sm min-w-0">
                  {row.time && <span className="font-mono text-xs text-gray-400 mr-2">{row.time}</span>}
                  <span className="text-gray-700">{row.event}</span>
                </div>
              </div>
            ))}
            {signalEvents.map((item: any, i: number) => {
              const ts = item.timestamp
                ? new Date(item.timestamp).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC'
                : '';
              return (
                <div key={`ev-${i}`} className="relative flex gap-3 pb-3">
                  <div className="shrink-0 w-3 h-3 rounded-full bg-white border-2 border-blue-400 mt-0.5 z-10" />
                  <div className="text-sm min-w-0">
                    {ts && <span className="font-mono text-xs text-gray-400 mr-2">{ts}</span>}
                    <span className="text-gray-700">{item.title}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Alternative Explanations */}
      {alternatives.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">Alternative Explanations</p>
          <div className="space-y-1.5">
            {alternatives.map((a: string, i: number) => (
              <div key={i} className="flex gap-2 text-sm text-gray-600">
                <span className="shrink-0 text-gray-300 mt-0.5">◦</span>
                <RcaMd>{a}</RcaMd>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Next Steps */}
      {checklist.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">Next Steps</p>
          <div className="space-y-2">
            {checklist.map((item: string, i: number) => (
              <div key={i} className="flex gap-3 text-sm">
                <span className="shrink-0 w-5 h-5 rounded border border-gray-300 flex items-center justify-center text-xs text-gray-400 font-mono mt-0.5">{i + 1}</span>
                <span className="text-gray-700 leading-relaxed">{item}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Visibility Gaps */}
      {meaningfulGaps.length > 0 && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2.5">
          <p className="text-xs font-semibold text-amber-700 mb-1.5">Visibility Gaps</p>
          <div className="space-y-1">
            {meaningfulGaps.map((g: string, i: number) => (
              <div key={i} className="text-xs text-amber-800 flex gap-1.5">
                <svg className="shrink-0 w-3.5 h-3.5 text-amber-600 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
                <span>{g}</span>
              </div>
            ))}
          </div>
        </div>
      )}

    </div>
  );
}

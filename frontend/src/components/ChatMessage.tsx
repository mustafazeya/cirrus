import React, { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import UserAvatar from './UserAvatar';
import CirrusAvatar from './CirrusAvatar';
import AnimatedHourglass from './AnimatedHourglass';
import CostVisualization from './CostVisualization';
import MetricsChart from './MetricsChart';
import { useAppStore } from '../store/appStore';
import RcaResultCard from './RcaResultCard';
import { ClipboardDocumentIcon, ArrowPathIcon, CheckIcon, ArrowDownTrayIcon, ArrowUpIcon, HandThumbUpIcon, HandThumbDownIcon } from '@heroicons/react/24/outline';
import { HandThumbUpIcon as HandThumbUpSolid, HandThumbDownIcon as HandThumbDownSolid } from '@heroicons/react/24/solid';
import toast from 'react-hot-toast';
import { apiService } from '../services/apiService';

// ── ThinkingBubble ────────────────────────────────────────────────────────────
// Defined at module scope (NOT inside ChatMessage) so React never remounts it
// between renders, allowing useState / useEffect to work correctly.
const ThinkingBubble: React.FC = () => {
  const dotBase: React.CSSProperties = {
    width: 7,
    height: 7,
    borderRadius: '50%',
    display: 'inline-block',
    background: 'linear-gradient(135deg, #3385ff, #000099)',
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div
        className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm shadow-sm"
        style={{ padding: '12px 18px' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={dotBase} className="animate-bounce" />
          <span style={{ ...dotBase, animationDelay: '150ms' }} className="animate-bounce" />
          <span style={{ ...dotBase, animationDelay: '300ms' }} className="animate-bounce" />
        </div>
      </div>
      <span className="text-[10px] text-gray-400 pl-1">CIRRUS AI is thinking...</span>
    </div>
  );
};
// ───────────────────────────────────────────────────────────────────────────────

interface ChatMessageProps {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  messageType: 'user' | 'ai' | 'error' | 'loading' | 'rca_result';
  timestamp: string;
  queryResult?: any;
  actions?: Array<{ label: string; query: string }>;
  onActionClick?: (query: string) => void;
  onRerun?: (content: string) => void;
  conversationId?: string;
}

export default function ChatMessage({
  id,
  role,
  content,
  messageType,
  timestamp,
  queryResult,
  actions,
  onActionClick,
  onRerun,
  conversationId,
}: ChatMessageProps) {
  const isUser = role === 'user';
  const { user, messageLayoutStyle } = useAppStore();
  const [copied, setCopied] = useState(false);
  const [exported, setExported] = useState(false);
  const messageRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [feedbackGiven, setFeedbackGiven] = useState<'positive' | 'negative' | null>(null);
  const [showCorrectionBox, setShowCorrectionBox] = useState(false);
  const [correction, setCorrection] = useState('');
  const [correctionSent, setCorrectionSent] = useState(false);

  const handleFeedback = async (type: 'positive' | 'negative') => {
    if (!conversationId || feedbackGiven === type) return;
    try {
      await apiService.submitMessageFeedback({
        conversation_id: conversationId,
        message_id: id,
        feedback: type,
      });
      setFeedbackGiven(type);
      if (type === 'negative') {
        setShowCorrectionBox(true);
      } else {
        toast.success('Thanks for the feedback!');
      }
    } catch {
      toast.error('Failed to send feedback');
    }
  };

  const handleSendCorrection = async () => {
    if (!conversationId || !correction.trim()) {
      setShowCorrectionBox(false);
      toast.success('Feedback noted.');
      return;
    }
    try {
      await apiService.submitMessageFeedback({
        conversation_id: conversationId,
        message_id: id,
        feedback: 'negative',
        correction: correction.trim(),
      });
      setCorrectionSent(true);
      setShowCorrectionBox(false);
      toast.success("Got it — I'll do better next time.");
    } catch {
      toast.error('Failed to send correction');
    }
  };

  const renderFeedbackButtons = () => (
    <div className="flex items-center gap-1">
      <button
        onClick={() => handleFeedback('positive')}
        className={`p-1 rounded transition-colors ${
          feedbackGiven === 'positive'
            ? 'text-green-600 dark:text-green-400'
            : 'text-gray-400 hover:text-green-600 dark:hover:text-green-400'
        }`}
        title="Good response"
        disabled={!!feedbackGiven}
      >
        {feedbackGiven === 'positive'
          ? <HandThumbUpSolid className="w-3.5 h-3.5" />
          : <HandThumbUpIcon className="w-3.5 h-3.5" />}
      </button>
      <button
        onClick={() => handleFeedback('negative')}
        className={`p-1 rounded transition-colors ${
          feedbackGiven === 'negative'
            ? 'text-rose-600 dark:text-rose-400'
            : 'text-gray-400 hover:text-rose-600 dark:hover:text-rose-400'
        }`}
        title="Bad response"
        disabled={!!feedbackGiven}
      >
        {feedbackGiven === 'negative'
          ? <HandThumbDownSolid className="w-3.5 h-3.5" />
          : <HandThumbDownIcon className="w-3.5 h-3.5" />}
      </button>
    </div>
  );

  const renderCorrectionBox = () => {
    if (!showCorrectionBox) return null;
    return (
      <div className="mt-2 flex flex-col gap-1.5">
        <textarea
          autoFocus
          value={correction}
          onChange={e => setCorrection(e.target.value)}
          placeholder="What was wrong? (optional)"
          className="w-full text-xs rounded border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 p-2 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-400"
          rows={2}
        />
        <div className="flex gap-2">
          <button
            onClick={handleSendCorrection}
            className="text-xs px-2.5 py-1 bg-rose-600 text-white rounded hover:bg-rose-700 transition-colors"
          >
            {correction.trim() ? 'Send correction' : 'Skip'}
          </button>
          <button
            onClick={() => { setShowCorrectionBox(false); setCorrection(''); toast.success('Feedback noted.'); }}
            className="text-xs px-2.5 py-1 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  };

  // ── Loading guard — must come before any layout branch ──────────────────────
  // Both 'fullwidth' and 'bubble' layouts render via ReactMarkdown; when content
  // is empty (as it is for loading messages) they produce nothing visible.
  // Returning ThinkingBubble here unconditionally short-circuits all of that.
  if (messageType === 'loading') {
    return (
      <div className="flex gap-3 px-4 py-4 animate-fadeIn justify-start border-b border-slate-100">
        <div className="flex-shrink-0 w-8 h-8 rounded-full overflow-hidden">
          <CirrusAvatar size="sm" variant="circle" />
        </div>
        <ThinkingBubble />
      </div>
    );
  }
  // ────────────────────────────────────────────────────────────────────────────

  // Inline RCA result — renders the structured card in the chat thread
  if (messageType === 'rca_result' && queryResult) {
    return (
      <div className="flex gap-3 p-4 animate-fadeIn justify-start">
        <div className="flex-shrink-0 w-8 h-8 rounded-full overflow-hidden">
          <CirrusAvatar size="sm" variant="circle" />
        </div>
        <div className="flex-1 min-w-0">
          <RcaResultCard result={queryResult} />
        </div>
      </div>
    );
  }
  // ────────────────────────────────────────────────────────────────────────────
  const formattedTime = new Date(timestamp).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
  });

  // Copy to clipboard handler
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      toast.success('Copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      toast.error('Failed to copy');
    }
  };

  // Move to top of this message
  const handleMoveToTop = () => {
    messageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // Export to CSV handler
  const handleExportCSV = () => {
    try {
      let data: any[] = [];
      let headers: string[] = [];
      
      // Extract data from queryResult
      if (queryResult?.tables && Array.isArray(queryResult.tables)) {
        // Log Analytics format
        const table = queryResult.tables[0];
        if (table && table.columns && table.rows) {
          headers = table.columns.map((col: any) => col.name);
          data = table.rows.map((row: any[]) => {
            const obj: any = {};
            table.columns.forEach((col: any, idx: number) => {
              obj[col.name] = row[idx];
            });
            return obj;
          });
        }
      } else if (queryResult?.data?.comparison) {
        // Cost comparison format - flatten to rows
        const comparison = queryResult.data.comparison;
        const periods = Object.keys(comparison);
        
        // Create summary row with totals
        const summaryData = periods.map(period => ({
          Period: period,
          'Total Cost': comparison[period].total_cost,
          Currency: comparison[period].currency,
          'Start Date': comparison[period].date_range.start.split('T')[0],
          'End Date': comparison[period].date_range.end.split('T')[0]
        }));
        
        // If there's a breakdown, include it
        let breakdownData: any[] = [];
        periods.forEach(period => {
          const breakdown = comparison[period].breakdown;
          if (breakdown && Array.isArray(breakdown) && breakdown.length > 0) {
            breakdownData.push(...breakdown.map((item: any) => ({
              Period: period,
              ...item
            })));
          }
        });
        
        // Use breakdown if available, otherwise use summary
        if (breakdownData.length > 0) {
          data = breakdownData;
          if (data.length > 0) {
            headers = Object.keys(data[0]);
          }
        } else {
          data = summaryData;
          headers = Object.keys(data[0]);
        }
      } else if (queryResult?.results && Array.isArray(queryResult.results)) {
        // Simple results array format
        data = queryResult.results;
        if (data.length > 0) {
          headers = Object.keys(data[0]);
        }
      } else if (queryResult?.breakdown && Array.isArray(queryResult.breakdown)) {
        // Cost breakdown format
        data = queryResult.breakdown;
        if (data.length > 0) {
          headers = Object.keys(data[0]);
        }
      } else if (queryResult?.data && Array.isArray(queryResult.data)) {
        // Generic data array format
        data = queryResult.data;
        if (data.length > 0) {
          headers = Object.keys(data[0]);
        }
      } else if (Array.isArray(queryResult)) {
        // QueryResult might be directly an array
        data = queryResult;
        if (data.length > 0) {
          headers = Object.keys(data[0]);
        }
      }

      if (data.length === 0) {
        toast.error('No data to export');
        return;
      }

      if (headers.length === 0) {
        toast.error('No headers found in data');
        return;
      }

      // Convert to CSV
      const rows = data.map(row => 
        headers.map(header => {
          const value = row[header];
          // Handle null/undefined
          if (value === null || value === undefined) return '';
          // Handle objects/arrays
          if (typeof value === 'object') return JSON.stringify(value);
          // Escape quotes and wrap in quotes if contains comma, quote, or newline
          const stringValue = String(value);
          if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
            return `"${stringValue.replace(/"/g, '""')}"`;
          }
          return stringValue;
        })
      );

      const csvContent = [
        headers.join(','),
        ...rows.map(row => row.join(','))
      ].join('\n');

      // Create download
      const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
      const link = document.createElement('a');
      const url = URL.createObjectURL(blob);
      link.setAttribute('href', url);
      link.setAttribute('download', `cirrus-results-${new Date().toISOString().split('T')[0]}.csv`);
      link.style.visibility = 'hidden';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      
      setExported(true);
      toast.success(`Exported ${data.length} rows to CSV`);
      setTimeout(() => setExported(false), 2000);
    } catch (err) {
      console.error('CSV export error:', err);
      toast.error('Failed to export CSV');
    }
  };

  // Build a styled HTML document from the rendered markdown for export
  const buildExportHtml = (title: string): string => {
    const body = contentRef.current?.innerHTML || content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>${title}</title><style>
body{font-family:'Segoe UI',Arial,sans-serif;font-size:13px;line-height:1.6;color:#111;max-width:860px;margin:40px auto;padding:0 24px}
h1{font-size:20px;margin:24px 0 8px}h2{font-size:16px;margin:18px 0 6px}h3{font-size:14px;margin:14px 0 4px}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:12px}
th{background:#f4f4f4;font-weight:600}
code{background:#f4f4f4;padding:1px 4px;border-radius:3px;font-size:11px;font-family:monospace}
pre{background:#f4f4f4;padding:12px;border-radius:4px;overflow-x:auto}
ul,ol{padding-left:20px}li{margin:2px 0}
.cirrus-header{border-bottom:2px solid #0066cc;padding-bottom:12px;margin-bottom:20px}
.cirrus-footer{border-top:1px solid #eee;padding-top:8px;margin-top:24px;font-size:11px;color:#888}
</style></head><body>
<div class="cirrus-header">
  <div style="color:#0066cc;font-weight:700;font-size:16px">CIRRUS AI Analysis</div>
  <div style="color:#888;font-size:11px">${new Date().toLocaleString()}</div>
</div>
${body}
<div class="cirrus-footer">Generated by CIRRUS AI · ${new Date().toLocaleDateString()}</div>
</body></html>`;
  };

  // Export to PDF via browser print dialog
  const handleExportPDF = () => {
    const win = window.open('', '_blank');
    if (!win) {
      toast.error('Allow popups to export PDF');
      return;
    }
    win.document.write(buildExportHtml('CIRRUS Analysis'));
    win.document.close();
    win.focus();
    // Short delay so styles fully apply before the print dialog opens
    setTimeout(() => { win.print(); }, 400);
  };

  // Export to Word-compatible HTML (.doc)
  const handleExportDOCX = () => {
    const body = contentRef.current?.innerHTML || content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const wordHtml = `<html xmlns:o="urn:schemas-microsoft-com:office:office"
    xmlns:w="urn:schemas-microsoft-com:office:word"
    xmlns="http://www.w3.org/TR/REC-html40"><head>
<meta charset="UTF-8">
<!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View></w:WordDocument></xml><![endif]-->
<style>
body{font-family:Calibri,Arial,sans-serif;font-size:11pt;line-height:1.5}
h1{font-size:16pt}h2{font-size:14pt}h3{font-size:12pt}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #999;padding:4pt 8pt;font-size:10pt}
th{background:#f0f0f0;font-weight:bold}
code,pre{font-family:'Courier New',monospace;font-size:10pt;background:#f8f8f8}
</style></head><body>
<h2 style="color:#0066cc">CIRRUS AI Analysis</h2>
<p style="color:#888;font-size:9pt">${new Date().toLocaleString()}</p>
${body}
<p style="color:#888;font-size:9pt;border-top:1px solid #eee;padding-top:8pt">Generated by CIRRUS AI</p>
</body></html>`;
    const blob = new Blob(['\uFEFF', wordHtml], { type: 'application/msword' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `cirrus-analysis-${new Date().toISOString().split('T')[0]}.doc`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(link.href);
    toast.success('Exported as Word document');
  };

  // Rerun query handler
  const handleRerun = () => {
    if (onRerun) {
      onRerun(content);
      toast.success('Rerunning...');
    }
  };

  // Render avatar based on message type
  const renderAvatar = () => {
    // Error messages keep the CIRRUS avatar — same calm visual rhythm as AI replies
    return (
      <div className="flex-shrink-0 w-8 h-8 rounded-full overflow-hidden">
        <CirrusAvatar size="sm" variant="circle" />
      </div>
    );
  };

  // Render table if query result contains one
  const renderQueryResult = () => {
    if (!queryResult) return null;

    // Handle Azure Monitor Metrics format (azure_metrics query type)
    if (queryResult.query_type === 'azure_metrics' && queryResult.results) {
      return (
        <div className="mt-4 space-y-4">
          <MetricsChart data={queryResult} />
          {/* Still render the table below the chart */}
          {renderMetricsTable(queryResult.results)}
        </div>
      );
    }

    // Handle cost analysis data (from AI conversation endpoint)
    // Check if queryResult has cost-specific fields (daily_trend, breakdown, total_cost)
    if (queryResult.daily_trend || queryResult.breakdown || queryResult.total_cost !== undefined) {
      return (
        <div className="mt-4">
          <CostVisualization data={queryResult} />
        </div>
      );
    }

    // Handle Log Analytics table format (tables array with columns and rows)
    if (queryResult.tables && Array.isArray(queryResult.tables)) {
      const table = queryResult.tables[0]; // Get first table
      if (!table || !table.columns || !table.rows) return null;

      // Convert to array of objects for easier rendering
      const results = table.rows.map((row: any[]) => {
        const obj: any = {};
        table.columns.forEach((col: any, idx: number) => {
          obj[col.name] = row[idx];
        });
        return obj;
      });

      if (results.length === 0) {
        return (
          <div className="mt-4 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg text-sm text-gray-600 dark:text-gray-400">
            No results found
          </div>
        );
      }

      return (
        <div className="mt-4 overflow-x-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
          <div className="inline-block min-w-full align-middle">
            <div className="overflow-hidden border border-gray-200 dark:border-gray-700 rounded-lg">
              <table className="w-full sm:min-w-full divide-y divide-gray-200 dark:divide-gray-700">
                <thead className="bg-gray-50 dark:bg-gray-800">
                  <tr>
                    {table.columns.map((col: any) => (
                      <th
                        key={col.name}
                        className="px-2 sm:px-4 py-2 sm:py-3 text-left text-xs font-medium text-gray-600 dark:text-gray-400 uppercase tracking-wider"
                      >
                        {col.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-700">
                  {results.slice(0, 100).map((row: any, idx: number) => (
                    <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                      {table.columns.map((col: any) => (
                        <td
                          key={col.name}
                          className="px-2 sm:px-4 py-2 sm:py-3 text-xs sm:text-sm text-gray-900 dark:text-gray-100 break-words max-w-xs"
                        >
                          {row[col.name] !== null && row[col.name] !== undefined
                            ? typeof row[col.name] === 'object'
                              ? JSON.stringify(row[col.name])
                              : String(row[col.name])
                            : '-'}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {results.length > 100 && (
                <div className="px-2 sm:px-4 py-2 sm:py-3 bg-gray-50 dark:bg-gray-800 text-xs sm:text-sm text-gray-600 dark:text-gray-400 text-center">
                  Showing first 100 of {results.length} results
                </div>
              )}
            </div>
          </div>
        </div>
      );
    }

    // Handle simple results array format
    if (queryResult.results && Array.isArray(queryResult.results)) {
      return (
        <div className="mt-4 overflow-x-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
          <table className="w-full sm:min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                {Object.keys(queryResult.results[0] || {}).map((key) => (
                  <th
                    key={key}
                    className="px-3 sm:px-6 py-2 sm:py-3 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider"
                  >
                    {key}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white dark:bg-gray-900 divide-y divide-gray-200 dark:divide-gray-700">
              {queryResult.results.map((row: any, idx: number) => (
                <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                  {Object.values(row).map((value: any, cellIdx: number) => (
                    <td
                      key={cellIdx}
                      className="px-3 sm:px-6 py-2 sm:py-4 text-xs sm:text-sm text-gray-900 dark:text-gray-100 break-words max-w-xs"
                    >
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    // Otherwise show raw JSON
    return (
      <details className="mt-4">
        <summary className="cursor-pointer text-sm text-blue-600 dark:text-blue-400 hover:underline">
          View raw data
        </summary>
        <pre className="mt-2 p-2 sm:p-4 bg-gray-100 dark:bg-gray-800 rounded-lg text-xs overflow-x-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
          {JSON.stringify(queryResult, null, 2)}
        </pre>
      </details>
    );
  };

  // Helper function to render metrics table
  const renderMetricsTable = (results: any[]) => {
    if (!results || results.length === 0) return null;

    return (
      <div className="overflow-x-auto border border-gray-200 rounded-lg">
        <table className="w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-600 uppercase">Metric</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-600 uppercase">Unit</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-gray-600 uppercase">Data Points</th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-100">
            {results.map((metric: any, idx: number) => {
              const timeseries = metric.timeseries || [];
              const dataPointCount = timeseries.reduce((sum: number, series: any) => 
                sum + (series.data?.length || 0), 0
              );

              return (
                <tr key={idx} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-sm font-medium text-gray-900">{metric.name}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{metric.unit || '-'}</td>
                  <td className="px-4 py-3 text-sm text-gray-600">{dataPointCount}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  // Full-width layout (ChatGPT style)
  if (messageLayoutStyle === 'fullwidth') {
    return isUser ? (
      // USER MESSAGE - Right-aligned bubble
      <div className="flex justify-end px-4 py-3 border-b border-slate-100 dark:border-slate-800">
        <div className="flex gap-3 items-end max-w-2xl group">
          <div className="flex flex-col items-end gap-1">
            {/* Bubble */}
            <div className="bg-slate-100 text-slate-900 rounded-2xl rounded-br-sm px-4 py-3">
              <p className="text-sm leading-relaxed whitespace-pre-wrap">{content}</p>
            </div>
            {/* Timestamp + actions — visible on hover */}
            <div className="flex items-center gap-3 opacity-0 group-hover:opacity-100 transition-opacity duration-150 pr-1">
              <span className="text-[10px] text-gray-400">{formattedTime}</span>
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-indigo-600 transition-colors"
                title="Copy message"
              >
                {copied ? <><CheckIcon className="w-3 h-3" /><span>Copied</span></> : <><ClipboardDocumentIcon className="w-3 h-3" /><span>Copy</span></>}
              </button>
              {onRerun && (
                <button
                  onClick={handleRerun}
                  className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-indigo-600 transition-colors"
                  title="Rerun"
                >
                  <ArrowPathIcon className="w-3 h-3" />
                  <span>Rerun</span>
                </button>
              )}
            </div>
          </div>
          <UserAvatar 
            photoUrl={user?.photoUrl} 
            name={user?.name || user?.email}
            size="md"
          />
        </div>
      </div>
    ) : (
      // AI MESSAGE - Full container width
      <div ref={messageRef} className="w-full border-b border-slate-100 dark:border-slate-800 bg-white dark:bg-gray-900">
        <div className="px-4 py-6">
          <div className="flex gap-4 md:gap-6">
            <div className="flex-shrink-0">
              {renderAvatar()}
            </div>
            <div className="flex-1 min-w-0">
              <div ref={contentRef} className="text-sm text-gray-900 dark:text-gray-100 prose prose-sm max-w-none dark:prose-invert">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    a: ({ node, ...props }) => (
                      <a
                        {...props}
                        className="text-blue-600 dark:text-blue-400 hover:underline"
                        target="_blank"
                        rel="noopener noreferrer"
                      />
                    ),
                    code: ({ node, inline, className, children, ...props }: any) => {
                      const match = /language-(\w+)/.exec(className || '');
                      const language = match ? match[1] : '';
                      
                      return inline ? (
                        <code
                          {...props}
                          className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs font-mono"
                        >
                          {children}
                        </code>
                      ) : (
                        <div className="my-4 rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700">
                          {language && (
                            <div className="bg-gray-100 dark:bg-gray-800 px-4 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                              <span>{language.toUpperCase()}</span>
                              <button
                                onClick={() => {
                                  navigator.clipboard.writeText(String(children));
                                  toast.success('Code copied to clipboard');
                                }}
                                className="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
                                title="Copy code"
                              >
                                <ClipboardDocumentIcon className="w-4 h-4" />
                              </button>
                            </div>
                          )}
                          <div className="bg-gray-50 dark:bg-gray-900">
                            <SyntaxHighlighter
                              language={language || 'text'}
                              style={oneLight}
                              customStyle={{
                                margin: 0,
                                padding: '1rem',
                                fontSize: '0.875rem',
                                lineHeight: '1.5',
                                background: 'transparent'
                              }}
                              wrapLongLines={true}
                              showLineNumbers={language && String(children).split('\n').length > 5}
                            >
                              {String(children).replace(/\n$/, '')}
                            </SyntaxHighlighter>
                          </div>
                        </div>
                      );
                    },
                    ul: ({ node, ...props }) => (
                      <ul {...props} className="list-disc list-inside space-y-1 my-2" />
                    ),
                    ol: ({ node, ...props }) => (
                      <ol {...props} className="list-decimal list-inside space-y-1 my-2" />
                    ),
                    table: ({ node, ...props }) => (
                      <div className="overflow-x-auto my-4" style={{ WebkitOverflowScrolling: 'touch' }}>
                        <table
                          {...props}
                          className="w-full sm:min-w-full divide-y divide-gray-200 dark:divide-gray-700 border border-gray-200 dark:border-gray-700 rounded-lg"
                        />
                      </div>
                    ),
                    thead: ({ node, ...props }) => (
                      <thead {...props} className="bg-gray-50 dark:bg-gray-800" />
                    ),
                    th: ({ node, ...props }) => (
                      <th
                        {...props}
                        className="px-4 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-400 uppercase tracking-wider"
                      />
                    ),
                    td: ({ node, ...props }) => (
                      <td
                        {...props}
                        className="px-2 sm:px-4 py-2 text-xs sm:text-sm text-gray-900 dark:text-gray-100 border-t border-gray-200 dark:border-gray-700 break-words"
                      />
                    ),
                    p: ({ node, ...props }) => <p {...props} className="my-1 leading-relaxed" />,
                    h1: ({ node, ...props }) => (
                      <h1 {...props} className="text-lg font-bold mt-3 mb-2" />
                    ),
                    h2: ({ node, ...props }) => (
                      <h2 {...props} className="text-base font-bold mt-2 mb-1" />
                    ),
                    h3: ({ node, ...props }) => (
                      <h3 {...props} className="text-sm font-bold mt-2 mb-1" />
                    ),
                  }}
                >
                  {content}
                </ReactMarkdown>
              </div>

              {renderQueryResult()}

              {actions && actions.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {actions.map((action, idx) => (
                    <button
                      key={idx}
                      onClick={() => onActionClick?.(action.query)}
                      className="px-3 py-1.5 text-xs font-medium bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900/50 rounded-md transition-colors"
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              )}

              <div className="mt-3 text-xs text-gray-400 dark:text-gray-500">
                {formattedTime}
              </div>

              {!isUser && (
                <div className="mt-3 flex gap-3">
                  <button
                    onClick={handleCopy}
                    className="flex items-center gap-1.5 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
                    title="Copy message"
                  >
                    {copied ? (
                      <>
                        <CheckIcon className="w-3.5 h-3.5" />
                        <span>Copied</span>
                      </>
                    ) : (
                      <>
                        <ClipboardDocumentIcon className="w-3.5 h-3.5" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>

                  {queryResult && (
                    <button
                      onClick={handleExportCSV}
                      className="flex items-center gap-1.5 px-2 py-1 text-xs text-green-600 hover:text-green-700 dark:text-green-500 dark:hover:text-green-400 transition-colors"
                      title="Export to CSV"
                    >
                      {exported ? (
                        <>
                          <CheckIcon className="w-3.5 h-3.5" />
                          <span>Exported</span>
                        </>
                      ) : (
                        <>
                          <ArrowDownTrayIcon className="w-3.5 h-3.5" />
                          <span>Export CSV</span>
                        </>
                      )}
                    </button>
                  )}

                  {content.length > 100 && (
                    <>
                      <button
                        onClick={handleExportPDF}
                        className="flex items-center gap-1.5 px-2 py-1 text-xs text-rose-600 hover:text-rose-700 dark:text-rose-400 dark:hover:text-rose-300 transition-colors"
                        title="Export as PDF"
                      >
                        <ArrowDownTrayIcon className="w-3.5 h-3.5" />
                        <span>PDF</span>
                      </button>
                      <button
                        onClick={handleExportDOCX}
                        className="flex items-center gap-1.5 px-2 py-1 text-xs text-indigo-600 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors"
                        title="Export as Word document"
                      >
                        <ArrowDownTrayIcon className="w-3.5 h-3.5" />
                        <span>Word</span>
                      </button>
                    </>
                  )}

                  <button
                    onClick={handleMoveToTop}
                    className="flex items-center gap-1.5 px-2 py-1 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-500 dark:hover:text-blue-400 transition-colors"
                    title="Move to top of conversation"
                  >
                    <ArrowUpIcon className="w-3.5 h-3.5" />
                    <span>Move to Top</span>
                  </button>

                  {conversationId && renderFeedbackButtons()}
                </div>
              )}

              {renderCorrectionBox()}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Bubble layout (default)
  return (
    <div
      ref={messageRef}
      className={`flex gap-3 px-4 py-3 animate-fadeIn border-b border-slate-100 dark:border-slate-800 ${
        isUser ? 'justify-end' : 'justify-start'
      }`}
    >
      {!isUser && renderAvatar()}

      {isUser ? (
        <div className="flex flex-col items-end gap-1 max-w-3xl group">
          <div className="bg-slate-100 text-slate-900 rounded-2xl rounded-br-sm px-4 py-3">
            <div className="text-sm leading-relaxed whitespace-pre-wrap">{content}</div>
          </div>
          {/* Timestamp + actions on hover */}
          <div className="flex items-center gap-3 opacity-0 group-hover:opacity-100 transition-opacity duration-150 pr-1">
            <span className="text-[10px] text-slate-400">{formattedTime}</span>
            <button onClick={handleCopy} className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-indigo-600 transition-colors" title="Copy">
              {copied ? <><CheckIcon className="w-3 h-3" /><span>Copied</span></> : <><ClipboardDocumentIcon className="w-3 h-3" /><span>Copy</span></>}
            </button>
            {onRerun && (
              <button onClick={handleRerun} className="flex items-center gap-1 text-[10px] text-slate-400 hover:text-indigo-600 transition-colors" title="Rerun">
                <ArrowPathIcon className="w-3 h-3" /><span>Rerun</span>
              </button>
            )}
          </div>
        </div>
      ) : (
      <div
        className="max-w-3xl bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-2xl rounded-tl-sm p-4 shadow-sm"
      >
        {messageType === 'error' && (
          <div className="flex items-center gap-2 mb-3 pb-3 border-b border-slate-100 dark:border-slate-700">
            <svg className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
            <span className="text-[11px] font-medium text-amber-600 dark:text-amber-400 tracking-wide uppercase">Unable to complete request</span>
            {onRerun && (
              <button
                onClick={() => onRerun(content)}
                className="ml-auto flex items-center gap-1 text-[11px] text-slate-500 hover:text-indigo-600 dark:hover:text-indigo-400 transition-colors font-medium"
              >
                <ArrowPathIcon className="w-3 h-3" />
                Try again
              </button>
            )}
          </div>
        )}
        <div
          ref={!isUser ? contentRef : undefined}
          className={`text-sm ${
            isUser ? 'text-white' : 'text-gray-900 dark:text-gray-100'
          } prose prose-sm max-w-none dark:prose-invert`}
        >
          {isUser ? (
            // User messages render as plain text
            <div className="whitespace-pre-wrap">{content}</div>
          ) : (
            // AI messages render as markdown with formatting
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // Style links
                a: ({ node, ...props }) => (
                  <a
                    {...props}
                    className="text-blue-600 dark:text-blue-400 hover:underline"
                    target="_blank"
                    rel="noopener noreferrer"
                  />
                ),
                // Style code blocks with syntax highlighting
                code: ({ node, inline, className, children, ...props }: any) => {
                  const match = /language-(\w+)/.exec(className || '');
                  const language = match ? match[1] : '';
                  
                  return inline ? (
                    <code
                      {...props}
                      className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-xs font-mono"
                    >
                      {children}
                    </code>
                  ) : (
                    <div className="my-4 rounded-lg overflow-hidden border border-gray-200 dark:border-gray-700">
                      {language && (
                        <div className="bg-gray-100 dark:bg-gray-800 px-4 py-2 text-xs font-medium text-gray-700 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                          <span>{language.toUpperCase()}</span>
                          <button
                            onClick={() => {
                              navigator.clipboard.writeText(String(children));
                              toast.success('Code copied to clipboard');
                            }}
                            className="text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
                            title="Copy code"
                          >
                            <ClipboardDocumentIcon className="w-4 h-4" />
                          </button>
                        </div>
                      )}
                      <div className="bg-gray-50 dark:bg-gray-900">
                        <SyntaxHighlighter
                          language={language || 'text'}
                          style={oneLight}
                          customStyle={{
                            margin: 0,
                            padding: '1rem',
                            fontSize: '0.875rem',
                            lineHeight: '1.5',
                            background: 'transparent'
                          }}
                          wrapLongLines={true}
                          showLineNumbers={language && String(children).split('\n').length > 5}
                        >
                          {String(children).replace(/\n$/, '')}
                        </SyntaxHighlighter>
                      </div>
                    </div>
                  );
                },
                // Style lists
                ul: ({ node, ...props }) => (
                  <ul {...props} className="list-disc list-inside space-y-1 my-2" />
                ),
                ol: ({ node, ...props }) => (
                  <ol {...props} className="list-decimal list-inside space-y-1 my-2" />
                ),
                // Style tables (for future table support)
                table: ({ node, ...props }) => (
                  <div className="overflow-x-auto my-4" style={{ WebkitOverflowScrolling: 'touch' }}>
                    <table
                      {...props}
                      className="w-full sm:min-w-full divide-y divide-gray-200 dark:divide-gray-700 border border-gray-200 dark:border-gray-700 rounded-lg"
                    />
                  </div>
                ),
                thead: ({ node, ...props }) => (
                  <thead {...props} className="bg-gray-50 dark:bg-gray-800" />
                ),
                th: ({ node, ...props }) => (
                  <th
                    {...props}
                    className="px-4 py-2 text-left text-xs font-medium text-gray-600 dark:text-gray-400 uppercase tracking-wider"
                  />
                ),
                td: ({ node, ...props }) => (
                  <td
                    {...props}
                    className="px-4 py-2 text-sm text-gray-900 dark:text-gray-100 border-t border-gray-200 dark:border-gray-700"
                  />
                ),
                // Style paragraphs
                p: ({ node, ...props }) => <p {...props} className="my-1 leading-relaxed" />,
                // Style headings
                h1: ({ node, ...props }) => (
                  <h1 {...props} className="text-lg font-bold mt-3 mb-2" />
                ),
                h2: ({ node, ...props }) => (
                  <h2 {...props} className="text-base font-bold mt-2 mb-1" />
                ),
                h3: ({ node, ...props }) => (
                  <h3 {...props} className="text-sm font-bold mt-2 mb-1" />
                ),
              }}
            >
              {content}
            </ReactMarkdown>
          )}
        </div>

        {/* Render query result table */}
        {renderQueryResult()}

        {/* Render action buttons */}
        {actions && actions.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {actions.map((action, idx) => (
              <button
                key={idx}
                onClick={() => onActionClick?.(action.query)}
                className="px-3 py-1.5 text-xs font-medium bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-900/50 rounded-md transition-colors"
              >
                {action.label}
              </button>
            ))}
          </div>
        )}

        <div className="mt-2 text-xs text-gray-400 dark:text-gray-500">{formattedTime}</div>

        {/* Copy button for AI responses */}
        {!isUser && (
          <div className="mt-2 flex gap-2">
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
              title="Copy response"
            >
              {copied ? (
                <>
                  <CheckIcon className="w-3 h-3" />
                  <span>Copied</span>
                </>
              ) : (
                <>
                  <ClipboardDocumentIcon className="w-3 h-3" />
                  <span>Copy all</span>
                </>
              )}
            </button>
            {queryResult && (
              <button
                onClick={handleExportCSV}
                className="flex items-center gap-1 px-2 py-1 text-xs text-green-600 hover:text-green-700 dark:text-green-500 dark:hover:text-green-400 transition-colors"
                title="Export to CSV"
              >
                {exported ? (
                  <>
                    <CheckIcon className="w-3 h-3" />
                    <span>Exported</span>
                  </>
                ) : (
                  <>
                    <ArrowDownTrayIcon className="w-3 h-3" />
                    <span>Export CSV</span>
                  </>
                )}
              </button>
            )}
            {content.length > 100 && (
              <>
                <button
                  onClick={handleExportPDF}
                  className="flex items-center gap-1 px-2 py-1 text-xs text-rose-600 hover:text-rose-700 dark:text-rose-400 dark:hover:text-rose-300 transition-colors"
                  title="Export as PDF"
                >
                  <ArrowDownTrayIcon className="w-3 h-3" />
                  <span>PDF</span>
                </button>
                <button
                  onClick={handleExportDOCX}
                  className="flex items-center gap-1 px-2 py-1 text-xs text-indigo-600 hover:text-indigo-700 dark:text-indigo-400 dark:hover:text-indigo-300 transition-colors"
                  title="Export as Word document"
                >
                  <ArrowDownTrayIcon className="w-3 h-3" />
                  <span>Word</span>
                </button>
              </>
            )}
            <button
              onClick={handleMoveToTop}
              className="flex items-center gap-1 px-2 py-1 text-xs text-blue-600 hover:text-blue-700 dark:text-blue-500 dark:hover:text-blue-400 transition-colors"
              title="Move to top of conversation"
            >
              <ArrowUpIcon className="w-3 h-3" />
              <span>Move to Top</span>
            </button>

            {conversationId && renderFeedbackButtons()}
          </div>
        )}

        {renderCorrectionBox()}
      </div>
      )} {/* end AI branch of isUser ternary */}

      {isUser && (
        <UserAvatar 
          photoUrl={user?.photoUrl} 
          name={user?.name || user?.email}
          size="md"
        />
      )}
    </div>
  );
}

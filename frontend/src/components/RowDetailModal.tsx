'use client';

import { XMarkIcon, ClipboardDocumentIcon, CheckIcon } from '@heroicons/react/24/outline';
import { useState, useEffect } from 'react';

interface RowDetailModalProps {
  row: Record<string, any>;
  visibleColumns?: Set<string>;
  onClose: () => void;
}

export default function RowDetailModal({ row, visibleColumns, onClose }: RowDetailModalProps) {
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const handleCopyField = async (fieldName: string, value: any) => {
    const textValue = formatValueForCopy(value);
    await navigator.clipboard.writeText(textValue);
    setCopiedField(fieldName);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const handleCopyAll = async () => {
    const jsonText = JSON.stringify(row, null, 2);
    await navigator.clipboard.writeText(jsonText);
    setCopiedField('__all__');
    setTimeout(() => setCopiedField(null), 2000);
  };

  const formatValueForCopy = (value: any): string => {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value, null, 2);
    return String(value);
  };

  const formatValueForDisplay = (value: any): React.ReactNode => {
    if (value === null || value === undefined) {
      return <span className="text-gray-400 italic">null</span>;
    }

    if (typeof value === 'boolean') {
      return <span className={value ? 'text-green-600' : 'text-red-600'}>{String(value)}</span>;
    }

    if (typeof value === 'object') {
      return (
        <pre className="bg-gray-50 p-3 rounded text-xs overflow-x-auto max-h-96 overflow-y-auto border border-gray-200">
          {JSON.stringify(value, null, 2)}
        </pre>
      );
    }

    const stringValue = String(value);

    // Check if it's an ISO timestamp
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(stringValue)) {
      try {
        const date = new Date(stringValue);
        return (
          <div className="space-y-1">
            <div className="text-gray-900">{stringValue}</div>
            <div className="text-sm text-gray-500">
              {date.toLocaleString('en-US', {
                year: 'numeric',
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                timeZoneName: 'short'
              })}
            </div>
          </div>
        );
      } catch (e) {
        // Not a valid date, fall through
      }
    }

    // Check if it's a URL
    if (/^https?:\/\//.test(stringValue)) {
      return (
        <a 
          href={stringValue} 
          target="_blank" 
          rel="noopener noreferrer"
          className="text-blue-600 hover:text-blue-800 underline break-all"
        >
          {stringValue}
        </a>
      );
    }

    // Long text with line breaks
    if (stringValue.length > 100 || stringValue.includes('\n')) {
      return (
        <div className="whitespace-pre-wrap break-words bg-gray-50 p-3 rounded text-sm max-h-96 overflow-y-auto border border-gray-200">
          {stringValue}
        </div>
      );
    }

    return <div className="break-words">{stringValue}</div>;
  };

  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      onClose();
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose();
    }
  };

  // Add keyboard listener
  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown as any);
    return () => document.removeEventListener('keydown', handleKeyDown as any);
  }, [onClose]);

  // Simple alphabetical sort - fast and predictable
  // Filter out truly empty values (null, undefined, empty string) but keep zeros and other data
  const totalFields = Object.entries(row);
  const allFields = totalFields
    .filter(([_, value]) => value !== null && value !== undefined && value !== '')
    .sort(([a], [b]) => a.localeCompare(b));
  
  const fields = visibleColumns 
    ? allFields.filter(([fieldName]) => visibleColumns.has(fieldName))
    : allFields;

  return (
    <div 
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"
      onClick={handleBackdropClick}
    >
      <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex flex-col gap-1">
            <h2 className="text-xl font-semibold text-gray-900">Row Details</h2>
            <p className="text-sm text-gray-500">
              Showing {fields.length} fields out of {totalFields.length} total
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopyAll}
              className="flex items-center gap-2 px-3 py-2 text-sm bg-gray-100 hover:bg-gray-200 text-gray-700 rounded transition-colors"
              title="Copy entire row as JSON"
            >
              {copiedField === '__all__' ? (
                <>
                  <CheckIcon className="h-4 w-4 text-green-600" />
                  <span>Copied!</span>
                </>
              ) : (
                <>
                  <ClipboardDocumentIcon className="h-4 w-4" />
                  <span>Copy All</span>
                </>
              )}
            </button>
            <button
              onClick={onClose}
              className="p-2 hover:bg-gray-100 rounded transition-colors"
              title="Close (Esc)"
            >
              <XMarkIcon className="h-6 w-6 text-gray-500" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="overflow-y-auto flex-1 p-6">
          <div className="space-y-4">
            {fields.map(([fieldName, value]) => (
              <div key={fieldName} className="border-b border-gray-100 pb-4 last:border-b-0">
                <div className="flex items-start justify-between gap-4 mb-2">
                  <h3 className="font-medium text-gray-700 text-sm min-w-0 flex-shrink-0">
                    {fieldName}
                  </h3>
                  <button
                    onClick={() => handleCopyField(fieldName, value)}
                    className="flex items-center gap-1 px-2 py-1 text-xs bg-gray-50 hover:bg-gray-100 text-gray-600 rounded transition-colors flex-shrink-0"
                    title="Copy field value"
                  >
                    {copiedField === fieldName ? (
                      <>
                        <CheckIcon className="h-3 w-3 text-green-600" />
                        <span className="text-green-600">Copied</span>
                      </>
                    ) : (
                      <>
                        <ClipboardDocumentIcon className="h-3 w-3" />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                </div>
                <div className="text-gray-900">
                  {formatValueForDisplay(value)}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

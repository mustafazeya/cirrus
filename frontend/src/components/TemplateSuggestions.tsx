'use client';

import { useState, useEffect } from 'react';
import { SparklesIcon, ArrowRightIcon } from '@heroicons/react/24/outline';
import { apiService } from '../services/apiService';
import type { QueryTemplateListItem } from '../types/templates';
import { getCategoryIcon } from '../constants/templateCategories';

interface TemplateSuggestionsProps {
  query: string;
  onSelectTemplate: (template: QueryTemplateListItem) => void;
  className?: string;
}

export default function TemplateSuggestions({ 
  query, 
  onSelectTemplate,
  className = ''
}: TemplateSuggestionsProps) {
  const [suggestions, setSuggestions] = useState<QueryTemplateListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);

  useEffect(() => {
    // Only fetch suggestions if query is long enough
    if (query.trim().length < 3) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    // Create abort controller for this request
    const abortController = new AbortController();
    
    const debounceTimer = setTimeout(() => {
      fetchSuggestions(query, abortController.signal);
    }, 500); // Increased debounce to 500ms to reduce requests

    return () => {
      clearTimeout(debounceTimer);
      abortController.abort(); // Cancel in-flight request when query changes
    };
  }, [query]);

  const fetchSuggestions = async (searchQuery: string, signal?: AbortSignal) => {
    try {
      setLoading(true);
      const response = await apiService.request({
        method: 'GET',
        url: '/api/templates/suggestions',
        params: {
          query: searchQuery,
          limit: 3
        },
        signal // Pass abort signal to request
      });

      if (response.suggestions && response.suggestions.length > 0) {
        setSuggestions(response.suggestions);
        setShowSuggestions(true);
      } else {
        setSuggestions([]);
        setShowSuggestions(false);
      }
    } catch (error: any) {
      // Ignore abort errors (they're expected when query changes)
      if (error.name === 'AbortError' || error.name === 'CanceledError') {
        return;
      }
      console.error('Failed to fetch template suggestions:', error);
      setSuggestions([]);
      setShowSuggestions(false);
    } finally {
      setLoading(false);
    }
  };

  if (!showSuggestions || suggestions.length === 0) {
    return null;
  }

  return (
    <div className={`bg-white border border-indigo-100 border-l-4 border-l-indigo-400 rounded-xl shadow-sm p-3 ${className}`}>
      <div className="flex items-center gap-2 mb-2">
        <SparklesIcon className="h-3.5 w-3.5 text-indigo-600" />
        <span className="text-xs font-semibold text-indigo-700">Suggested templates</span>
        {loading && (
          <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-indigo-400"></div>
        )}
      </div>
      
      <div className="space-y-2">
        {suggestions.map((template) => (
          <button
            key={template.id}
            onClick={() => {
              onSelectTemplate(template);
              setShowSuggestions(false);
            }}
            className="w-full text-left p-2 bg-slate-50 hover:bg-indigo-50 border border-slate-100 hover:border-indigo-200 rounded-lg transition-all group"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  {(() => { const Icon = getCategoryIcon(template.category); return <Icon className="w-4 h-4 text-indigo-400 flex-shrink-0" />; })()}
                  <span className="text-sm font-medium text-gray-900 truncate">
                    {template.name}
                  </span>
                  {template.is_system && (
                    <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
                      System
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500 line-clamp-2">
                  {template.description || template.query}
                </p>
              </div>
              <ArrowRightIcon className="h-4 w-4 text-gray-400 group-hover:text-blue-600 flex-shrink-0 transition-colors" />
            </div>
          </button>
        ))}
      </div>
      
      <button
        onClick={() => setShowSuggestions(false)}
        className="mt-2 text-[11px] text-indigo-500 hover:text-indigo-700 transition-colors font-medium"
      >
        Dismiss
      </button>
    </div>
  );
}

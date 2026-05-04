'use client';

import { useState, useEffect, useRef, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { useIsAuthenticated } from '@azure/msal-react';
import { useAppStore } from '../../store/appStore';
import {
  BookmarkIcon,
  SparklesIcon,
  AdjustmentsHorizontalIcon,
  ChevronDownIcon,
  ChevronUpIcon,
} from '@heroicons/react/24/outline';
import UnifiedFilterBar from '../../components/UnifiedFilterBar';
import ConversationView from '../../components/ConversationView';
import ViewSelector from '../../components/ViewSelector';
import SaveViewDialog from '../../components/SaveViewDialog';
import type { SavedView, SavedViewFilters } from '../../types/savedViews';
import toast from 'react-hot-toast';

function AIPageContent() {
  const isAuthenticated = useIsAuthenticated();
  const { 
    user, 
    setNaturalLanguageQuery, 
    setGlobalScope, 
    setSelectedSubscriptions,
    selectedSubscriptionId,
    selectedResourceGroup,
    selectedResourceType,
    selectedResource,
    globalScope,
    setSelectedSubscription,
    setSelectedResourceGroup,
    setSelectedResourceType,
    setSelectedResource,
    setSelectedQueryViewId
  } = useAppStore();
  const searchParams = useSearchParams();
  const [isMaximized, setIsMaximized] = useState(false);

  // Collapsible filter bar — persisted so it survives navigation
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  useEffect(() => {
    setFiltersCollapsed(localStorage.getItem('aiFiltersCollapsed') === 'true');
  }, []);
  const toggleFilters = () => {
    setFiltersCollapsed(prev => {
      const next = !prev;
      localStorage.setItem('aiFiltersCollapsed', String(next));
      return next;
    });
  };

  const [unifiedFilters, setUnifiedFilters] = useState<{
    subscription?: string;
    resourceGroup?: string;
    resourceType?: string;
    resource?: string;
    globalScope?: boolean;
  }>({});
  const hasLoadedUrlQuery = useRef(false);
  const [initialQuery, setInitialQuery] = useState<string>('');
  const [shouldAutoExecute, setShouldAutoExecute] = useState(false);
  
  // Saved views state
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [viewSelectorKey, setViewSelectorKey] = useState(0);
  const [isLoadingFromView, setIsLoadingFromView] = useState(false);
  
  // Set browser tab title
  useEffect(() => { document.title = 'CIRRUS AI'; return () => { document.title = 'CIRRUS'; }; }, []);
  
  // Detect URL parameters and populate query input
  useEffect(() => {
    if (!hasLoadedUrlQuery.current) {
      const queryParam = searchParams.get('q');
      const executeParam = searchParams.get('execute');
      const scopeParam = searchParams.get('scope'); // activity, security, performance, general
      const subsParam = searchParams.get('subs'); // comma-separated subscription IDs
      
      // Handle subscriptions passed from dashboard
      if (subsParam) {
        const subIds = subsParam.split(',');
        console.log('Setting subscriptions from URL:', subIds);
        setSelectedSubscriptions(subIds);
        
        // If multiple subscriptions, use global scope (conversation mode)
        // If single subscription, could use specific subscription mode, but let's keep it simple with global
        setGlobalScope(true);
        console.log(`Set to global scope with ${subIds.length} subscription(s) selected from dashboard`);
      }
      
      if (queryParam) {
        setNaturalLanguageQuery(queryParam);
        setInitialQuery(queryParam);
        hasLoadedUrlQuery.current = true;
        
        // If execute=true, trigger query execution
        if (executeParam === 'true') {
          console.log('Auto-executing query from URL:', queryParam);
          setShouldAutoExecute(true);
        }
      }
      
      // If scope is provided (e.g., from dashboard quick actions), show a helpful message
      if (scopeParam) {
        const scopeMessages: Record<string, string> = {
          activity: 'Ask about recent changes, resource operations, or who did what',
          security: 'Query security events, compliance issues, or access logs',
          performance: 'Analyze performance metrics, resource utilization, or bottlenecks',
          general: 'Ask anything about your Azure resources and infrastructure'
        };
        
        const message = scopeMessages[scopeParam] || scopeMessages.general;
        console.log(`Navigated with ${scopeParam} scope: ${message}`);
        
        // Set a pre-populated query based on scope if no query provided
        if (!queryParam) {
          const scopeQueries: Record<string, string> = {
            activity: 'Show me recent activity in my subscriptions',
            security: 'Are there any security issues I should know about?',
            performance: 'Which resources are consuming the most?',
            general: ''
          };
          
          const suggestedQuery = scopeQueries[scopeParam];
          if (suggestedQuery) {
            setNaturalLanguageQuery(suggestedQuery);
            setInitialQuery(suggestedQuery);
            
            // Auto-execute if we have execute=true
            if (executeParam === 'true') {
              setShouldAutoExecute(true);
            }
          }
        }
      }
      
      hasLoadedUrlQuery.current = true;
    }
  }, [searchParams, setNaturalLanguageQuery, setGlobalScope, setSelectedSubscriptions]);

  // Handle saved view selection
  const handleViewSelected = (view: SavedView) => {
    console.log(' Loading saved query view:', view.name);
    console.log(' View filters:', view.filters);
    
    // Set flag to prevent clearing the view when filters change
    setIsLoadingFromView(true);
    
    // Apply filters from the view
    if (view.filters.subscription_id) {
      setSelectedSubscription(view.filters.subscription_id);
    } else {
      setSelectedSubscription('');
    }
    
    if (view.filters.resource_group) {
      setSelectedResourceGroup(view.filters.resource_group);
    } else {
      setSelectedResourceGroup('');
    }
    
    if (view.filters.resource_type) {
      setSelectedResourceType(view.filters.resource_type);
    } else {
      setSelectedResourceType('');
    }
    
    if (view.filters.resource) {
      setSelectedResource(view.filters.resource);
    } else {
      setSelectedResource('');
    }
    
    if (view.filters.global_scope !== undefined) {
      setGlobalScope(view.filters.global_scope);
    }
    
    // Clear the flag after a short delay
    setTimeout(() => setIsLoadingFromView(false), 100);
    
    toast.success(`Loaded view: ${view.name}`);
  };

  // Handle view saved - refresh selector
  const handleViewSaved = () => {
    setViewSelectorKey(prev => prev + 1);
  };

  // Get current filters for saving
  const getCurrentFilters = (): SavedViewFilters => {
    return {
      subscription_id: selectedSubscriptionId || null,
      resource_group: selectedResourceGroup || null,
      resource_type: selectedResourceType || null,
      resource: selectedResource || null,
      global_scope: globalScope,
    };
  };

  const handleUnifiedFilterChange = (newFilters: {
    subscription?: string;
    resourceGroup?: string;
    resourceType?: string;
    resource?: string;
    globalScope?: boolean;
  }) => {
    // Note: UnifiedFilterBar now handles clearing selected view when filters change
    setUnifiedFilters(newFilters);
  };

  if (!isAuthenticated || !user) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Please sign in to use CIRRUS AI
          </h2>
        </div>
      </div>
    );
  }

  // Build active-filter chip labels for the collapsed strip
  const activeChips: string[] = [
    unifiedFilters.globalScope ? 'Global scope' : null,
    unifiedFilters.subscription ? `Sub: ${unifiedFilters.subscription.slice(0, 8)}…` : null,
    unifiedFilters.resourceGroup ? unifiedFilters.resourceGroup : null,
    unifiedFilters.resourceType  ? unifiedFilters.resourceType.split('/').pop()! : null,
    unifiedFilters.resource      ? unifiedFilters.resource : null,
  ].filter(Boolean) as string[];

  return (
    /* Full-height flex column — fills app-main exactly, like a real chat app */
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Top bar: title + actions ── */}
      <div className="flex-shrink-0 flex items-center justify-between gap-3 px-4 sm:px-6 lg:px-8 py-3 border-b border-slate-100 bg-white">
        <div className="flex items-center gap-2.5 min-w-0">
          <h1 className="text-[15px] font-bold text-slate-900 tracking-tight whitespace-nowrap">CIRRUS AI</h1>
          <span className="hidden sm:inline-flex items-center gap-1 px-2 py-0.5 bg-indigo-50 text-indigo-600 text-[10px] font-semibold rounded-full border border-indigo-100 uppercase tracking-wide flex-shrink-0">
            <SparklesIcon className="h-2.5 w-2.5" />
            Azure OpenAI
          </span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <ViewSelector
            key={viewSelectorKey}
            viewType="query"
            onViewSelected={handleViewSelected}
          />
          <button
            onClick={() => setSaveViewDialogOpen(true)}
            className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-[12.5px] font-semibold"
            title="Save current filters as a view"
          >
            <BookmarkIcon className="h-3.5 w-3.5" />
            Save View
          </button>
        </div>
      </div>

      {/* ── Filter bar — collapsible, hidden when maximized ── */}
      {!isMaximized && (
        filtersCollapsed ? (
          /* Collapsed: slim 40px strip showing active filters */
          <div className="flex-shrink-0 flex items-center gap-2 px-4 sm:px-6 lg:px-8 h-10 border-b border-slate-100 bg-slate-50/80">
            <AdjustmentsHorizontalIcon className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
            <div className="flex-1 flex items-center gap-1.5 min-w-0 overflow-hidden">
              {activeChips.length > 0 ? (
                activeChips.map(chip => (
                  <span key={chip} className="inline-flex items-center px-2 py-0.5 rounded-full bg-indigo-50 border border-indigo-100 text-[11px] font-medium text-indigo-700 whitespace-nowrap flex-shrink-0">
                    {chip}
                  </span>
                ))
              ) : (
                <span className="text-[11.5px] text-slate-400">No scope selected</span>
              )}
            </div>
            <button
              onClick={toggleFilters}
              className="flex-shrink-0 flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11.5px] font-medium text-slate-500 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
            >
              Filters
              <ChevronDownIcon className="w-3 h-3" />
            </button>
          </div>
        ) : (
          /* Expanded: compact filter band — same visual weight as a toolbar */
          <div className="flex-shrink-0 border-b border-slate-100 bg-white">
            <div className="px-4 sm:px-6 lg:px-8 pt-2.5 pb-2.5">
              <UnifiedFilterBar
                onFilterChange={handleUnifiedFilterChange}
                isLoadingFromView={isLoadingFromView}
                compact
              />
            </div>
            <div className="flex items-center justify-end px-4 sm:px-6 lg:px-8 pb-2">
              <button
                onClick={toggleFilters}
                className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11.5px] font-medium text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                title="Collapse filters"
              >
                <ChevronUpIcon className="w-3 h-3" />
                Collapse filters
              </button>
            </div>
          </div>
        )
      )}

      {/* ── Chat — fills all remaining space ── */}
      <div className={`flex-1 min-h-0 px-4 sm:px-6 lg:px-8 py-4 ${
        isMaximized ? 'fixed inset-0 z-50 bg-white' : ''
      }`}>
        <ConversationView
          selectedFilters={unifiedFilters}
          isMaximized={isMaximized}
          onToggleMaximize={() => setIsMaximized(!isMaximized)}
          initialQuery={initialQuery}
          autoExecute={shouldAutoExecute}
        />
      </div>

      <SaveViewDialog
        isOpen={saveViewDialogOpen}
        onClose={() => setSaveViewDialogOpen(false)}
        currentFilters={getCurrentFilters()}
        viewType="query"
        onSaved={handleViewSaved}
      />
    </div>
  );
}

export default function AIPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-screen">Loading...</div>}>
      <AIPageContent />
    </Suspense>
  );
}
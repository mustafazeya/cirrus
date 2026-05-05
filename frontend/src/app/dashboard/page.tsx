'use client';

import { useState, useEffect, useMemo } from 'react';
import { useIsAuthenticated } from '@azure/msal-react';
import { useRouter } from 'next/navigation';
import { useAppStore, getVisibleSubscriptions } from '../../store/appStore';
import { apiService } from '../../services/apiService';
import toast from 'react-hot-toast';
import FloatingAIAssistant from '../../components/FloatingAIAssistant';
import UserAccess from '../../components/UserAccess';
import ViewSelector from '../../components/ViewSelector';
import SaveViewDialog from '../../components/SaveViewDialog';
import type { SavedView, SavedViewFilters } from '../../types/savedViews';
import { 
  ChevronDownIcon, 
  ChevronUpIcon,
  CurrencyDollarIcon,
  ShieldCheckIcon,
  ClockIcon,
  ChartBarIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  ServerIcon,
  ArrowDownTrayIcon,
  FunnelIcon,
  MagnifyingGlassIcon,
  BookmarkIcon,
  RectangleStackIcon,
  MapPinIcon,
} from '@heroicons/react/24/outline';
import MultiSearchableSelect from '../../components/MultiSearchableSelect';

interface Resource {
  id: string;
  name: string;
  type: string;
  location: string;
  resource_group: string;
  subscription_id: string;
  tags: Record<string, string>;
}

interface ResourceTypeGroup {
  type: string;
  displayName: string;
  count: number;
  resources: Resource[];
  expanded: boolean;
}

interface CostData {
  current_month: number;
  last_month: number;
  trend: 'up' | 'down' | 'stable';
  currency?: string;
  daily_trend?: Array<{ date: string; cost: number }>;
  breakdown?: Array<{ cost: number; subscriptionname?: string; servicename?: string; resourcetype?: string; resourcelocation?: string }>;
}

interface PolicyComplianceData {
  overall_compliance_percentage: number;
  total_resources: number;
  compliant_resources: number;
  non_compliant_resources: number;
  exempt_resources: number;
  conflict_resources?: number;
  unknown_resources: number;
  by_subscription?: any[];
  message?: string;
}

interface SecurityScoreData {
  overall_score: number;
  current_score: number;
  max_score: number;
  by_subscription?: any[];
  calculation_method?: string;
  note?: string;
  message?: string;
}

export default function DashboardPage() {
  const isAuthenticated = useIsAuthenticated();
  const router = useRouter();
  const { 
    user, 
    subscriptions, 
    selectedSubscriptions, 
    setSelectedSubscriptions,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly,
    setSelectedDashboardViewId
  } = useAppStore();
  
  // Get visible subscriptions based on filter preference
  const visibleSubscriptions = getVisibleSubscriptions(
    subscriptions,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly
  );

  const [loading, setLoading] = useState(false);
  const [resources, setResources] = useState<Resource[]>([]);
  const [selectedLocations, setSelectedLocations] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedTypes, setExpandedTypes] = useState<Set<string>>(new Set());
  const [costData, setCostData] = useState<CostData | null>(null);
  const [subscriptionCosts, setSubscriptionCosts] = useState<Record<string, { current: number; last: number; currency: string }>>({});
  const [loadingCosts, setLoadingCosts] = useState(false);
  const [policyCompliance, setPolicyCompliance] = useState<PolicyComplianceData | null>(null);
  const [securityScore, setSecurityScore] = useState<SecurityScoreData | null>(null);
  const [loadingGovernance, setLoadingGovernance] = useState(false);
  const [loadingPolicy, setLoadingPolicy] = useState(false);
  const [loadingSecurityScore, setLoadingSecurityScore] = useState(false);
  const [resourceHealth, setResourceHealth] = useState<any>(null);
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [changeAnalysis, setChangeAnalysis] = useState<any>(null);
  const [loadingChanges, setLoadingChanges] = useState(false);
  const [changeFilters, setChangeFilters] = useState<Record<string, Set<string>>>({});
  const [changeSearchTerm, setChangeSearchTerm] = useState('');
  const [activeChangeFilterColumn, setActiveChangeFilterColumn] = useState<string | null>(null);
  const [changeFilterSearchTerms, setChangeFilterSearchTerms] = useState<Record<string, string>>({});
  const [resourceGroupBy, setResourceGroupBy] = useState<'type' | 'resourceGroup'>('type');
  const [showHealthDrilldown, setShowHealthDrilldown] = useState(false);
  
  // Saved views state
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [viewSelectorKey, setViewSelectorKey] = useState(0);
  const [isLoadingFromView, setIsLoadingFromView] = useState(false);

  // Wrapper functions to clear selected view when filters change manually
  const handleSubscriptionsChange = (subs: string[]) => {
    if (!isLoadingFromView) {
      setSelectedDashboardViewId(null);
    }
    setSelectedSubscriptions(subs);
  };

  const handleLocationsChange = (locs: string[]) => {
    if (!isLoadingFromView) {
      setSelectedDashboardViewId(null);
    }
    setSelectedLocations(locs);
  };

  const handleSearchQueryChange = (query: string) => {
    if (!isLoadingFromView) {
      setSelectedDashboardViewId(null);
    }
    setSearchQuery(query);
  };

  // Auto-fetch when subscriptions change
  useEffect(() => {
    if (selectedSubscriptions.length === 0) {
      setResources([]);
      setCostData(null);
      setPolicyCompliance(null);
      setSecurityScore(null);
      setResourceHealth(null);
      setChangeAnalysis(null);
      return;
    }
    
    fetchResources();
    fetchCostData();
    fetchGovernanceData();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSubscriptions]);

  // Auto-refresh every 5 minutes
  useEffect(() => {
    if (selectedSubscriptions.length === 0) return;

    const refreshInterval = setInterval(() => {
      fetchResources();
      fetchCostData();
      fetchGovernanceData();
    }, 5 * 60 * 1000); // 5 minutes

    return () => clearInterval(refreshInterval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSubscriptions]);

  const fetchCostData = async () => {
    if (selectedSubscriptions.length === 0) {
      setCostData(null);
      return;
    }

    try {
      setLoadingCosts(true);
      
      // Calculate date ranges (using UTC to avoid timezone issues)
      const now = new Date();
      
      // Current month: from 1st of this month at 00:00:00 UTC to now
      const currentMonthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0));
      const currentMonthEnd = now;
      
      // Last month: full month from 1st to last day at 23:59:59 UTC
      const lastMonthStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1, 0, 0, 0));
      const lastMonthEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0, 23, 59, 59));
      
      const currentResponse = await apiService.getSubscriptionCosts(
        selectedSubscriptions,
        currentMonthStart.toISOString(),
        currentMonthEnd.toISOString(),
        'ServiceName'
      );
      
      const previousResponse = await apiService.getSubscriptionCosts(
        selectedSubscriptions,
        lastMonthStart.toISOString(),
        lastMonthEnd.toISOString(),
        'ServiceName'
      );
      
      const currentData = currentResponse;
      const previousData = previousResponse;
      
      const totalCurrentMonth = currentData?.total_cost || 0;
      const totalLastMonth = previousData?.total_cost || 0;
      const currency = currentData?.currency || 'SEK';
      
      // Determine trend
      let trend: 'up' | 'down' | 'stable' = 'stable';
      if (totalLastMonth > 0) {
        const changePercent = ((totalCurrentMonth - totalLastMonth) / totalLastMonth) * 100;
        if (Math.abs(changePercent) < 5) {
          trend = 'stable';
        } else if (changePercent > 0) {
          trend = 'up';
        } else {
          trend = 'down';
        }
      } else if (totalCurrentMonth > 0) {
        trend = 'up';
      }
      
      setCostData({
        current_month: totalCurrentMonth,
        last_month: totalLastMonth,
        trend,
        currency,
        breakdown: currentData?.breakdown || []
      });
      
      // Extract per-subscription costs from existing API response (no extra API calls!)
      if (selectedSubscriptions.length > 1) {
        const subCosts: Record<string, { current: number; last: number; currency: string }> = {};
        
        const currentBySubscription = currentData?.by_subscription || {};
        const previousBySubscription = previousData?.by_subscription || {};
        
        for (const subId of selectedSubscriptions) {
          subCosts[subId] = {
            current: currentBySubscription[subId] || 0,
            last: previousBySubscription[subId] || 0,
            currency
          };
        }
        
        setSubscriptionCosts(subCosts);
      } else {
        setSubscriptionCosts({});
      }
    } catch (error) {
      console.error('Failed to fetch cost data:', error);
      toast.error('Failed to load cost data');
      setCostData(null);
      setSubscriptionCosts({});
    } finally {
      setLoadingCosts(false);
    }
  };

  const fetchGovernanceData = async () => {
    if (selectedSubscriptions.length === 0) {
      setPolicyCompliance(null);
      setSecurityScore(null);
      setResourceHealth(null);
      return;
    }

    // Snapshot subscriptions at call time — avoids stale closure reads in async continuations
    const subs = [...selectedSubscriptions];

    // Each card has its own loading flag so they render independently
    setLoadingPolicy(true);
    setLoadingSecurityScore(true);
    setLoadingGovernance(true); // covers health + changes

    const policyP = apiService.getPolicyCompliance(subs)
      .then(r => { setPolicyCompliance((r as any) ?? null); })
      .catch(() => { setPolicyCompliance(null); })
      .finally(() => setLoadingPolicy(false));

    const securityP = apiService.getSecurityScore(subs)
      .then(r => { setSecurityScore((r as any) ?? null); })
      .catch(() => { setSecurityScore(null); })
      .finally(() => setLoadingSecurityScore(false));

    const healthP = apiService.getResourceHealth(subs)
      .then(r => { setResourceHealth(r ?? null); })
      .catch(() => { setResourceHealth(null); });

    const changesP = apiService.getChangeAnalysis(subs, 24)
      .then(r => { setChangeAnalysis(r ?? null); })
      .catch(() => { setChangeAnalysis(null); });

    // Clear shared governance spinner once health + changes are done
    Promise.allSettled([healthP, changesP]).finally(() => setLoadingGovernance(false));
  };

  const fetchResources = async () => {
    try {
      setLoading(true);
      const allResources: Resource[] = [];

      for (const subId of selectedSubscriptions) {
        try {
          const response = await apiService.getResources(subId);
          
          // Handle different response structures
          let subResources: any[] = [];
          if (Array.isArray(response)) {
            subResources = response;
          } else if (response?.data && Array.isArray(response.data)) {
            subResources = response.data;
          } else if ((response as any)?.resources && Array.isArray((response as any).resources)) {
            subResources = (response as any).resources;
          }

          const mappedResources = subResources.map((r: any) => ({
            id: r.id || '',
            name: r.name || '',
            type: r.type || '',
            location: r.location || '',
            resource_group: r.resource_group || r.resourceGroup || '',
            subscription_id: subId,
            tags: r.tags || {}
          }));
          
          allResources.push(...mappedResources);
        } catch (error) {
          console.error(`Failed to fetch resources for subscription ${subId}:`, error);
          // Continue with other subscriptions
        }
      }

      setResources(allResources);
    } catch (error) {
      console.error('Failed to fetch resources:', error);
      toast.error('Failed to load resources');
    } finally {
      setLoading(false);
      setLastRefreshTime(new Date());
      setIsRefreshing(false);
    }
  };

  const handleManualRefresh = async () => {
    if (selectedSubscriptions.length === 0 || isRefreshing) return;
    
    setIsRefreshing(true);
    toast.success('Refreshing dashboard data...');
    
    await Promise.all([
      fetchResources(),
      fetchCostData(),
      fetchGovernanceData()
    ]);
  };

  // Handle saved view selection
  const handleViewSelected = (view: SavedView) => {
    setIsLoadingFromView(true);
    
    // Apply filters from the view
    if (view.filters.subscriptions) {
      setSelectedSubscriptions(view.filters.subscriptions);
    }
    
    if (view.filters.locations) {
      setSelectedLocations(view.filters.locations);
    } else {
      setSelectedLocations([]);
    }
    
    if (view.filters.search_query) {
      setSearchQuery(view.filters.search_query);
    } else {
      setSearchQuery('');
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
      subscriptions: selectedSubscriptions,
      locations: selectedLocations.length > 0 ? selectedLocations : undefined,
      search_query: searchQuery || undefined,
    };
  };

  // Get unique locations from resources
  const availableLocations = useMemo(() => {
    const locations = new Set(resources.map(r => r.location));
    return Array.from(locations).sort();
  }, [resources]);

  // Helper function for display names
  const getResourceTypeDisplayName = (type: string) => {
    const parts = type.split('/');
    return parts.length > 1 ? parts[parts.length - 1] : type;
  };

  // Filter and group resources by type
  const filteredAndGroupedResources = useMemo(() => {
    let filtered = resources;

    // Apply location filter
    if (selectedLocations.length > 0) {
      filtered = filtered.filter(r => selectedLocations.includes(r.location));
    }

    // Apply search query
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(r =>
        r.name.toLowerCase().includes(query) ||
        r.type.toLowerCase().includes(query) ||
        Object.values(r.tags || {}).some(v => v.toLowerCase().includes(query))
      );
    }

    // Group by resource type
    const grouped = new Map<string, Resource[]>();
    filtered.forEach(resource => {
      const type = resource.type;
      if (!grouped.has(type)) {
        grouped.set(type, []);
      }
      grouped.get(type)!.push(resource);
    });

    // Convert to array and sort by count
    const groupedArray: ResourceTypeGroup[] = Array.from(grouped.entries())
      .map(([type, resources]) => ({
        type,
        displayName: getResourceTypeDisplayName(type),
        count: resources.length,
        resources: resources.sort((a, b) => a.name.localeCompare(b.name)),
        expanded: expandedTypes.has(type)
      }))
      .sort((a, b) => b.count - a.count);

    return groupedArray;
  }, [resources, selectedLocations, searchQuery, expandedTypes]);

  // Filter and group resources by resource group
  const filteredAndGroupedByRG = useMemo(() => {
    let filtered = resources;

    if (selectedLocations.length > 0) {
      filtered = filtered.filter(r => selectedLocations.includes(r.location));
    }

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(r =>
        r.name.toLowerCase().includes(query) ||
        r.type.toLowerCase().includes(query) ||
        r.resource_group.toLowerCase().includes(query) ||
        Object.values(r.tags || {}).some(v => v.toLowerCase().includes(query))
      );
    }

    const grouped = new Map<string, { displayName: string; resources: Resource[] }>();
    filtered.forEach(resource => {
      const rgKey = resource.resource_group.toLowerCase();
      if (!grouped.has(rgKey)) {
        grouped.set(rgKey, { displayName: resource.resource_group, resources: [] });
      }
      grouped.get(rgKey)!.resources.push(resource);
    });

    return Array.from(grouped.entries())
      .map(([rgKey, { displayName, resources }]) => ({
        type: rgKey,
        displayName,
        count: resources.length,
        resources: resources.sort((a, b) => a.name.localeCompare(b.name)),
        expanded: expandedTypes.has(rgKey),
      }))
      .sort((a, b) => b.count - a.count);
  }, [resources, selectedLocations, searchQuery, expandedTypes]);

  const toggleTypeExpansion = (type: string) => {
    setExpandedTypes(prev => {
      const newSet = new Set(prev);
      if (newSet.has(type)) {
        newSet.delete(type);
      } else {
        newSet.add(type);
      }
      return newSet;
    });
  };

  const toggleSubscription = (subId: string) => {
    const newSelection = selectedSubscriptions.includes(subId)
      ? selectedSubscriptions.filter(id => id !== subId)
      : [...selectedSubscriptions, subId];
    setSelectedSubscriptions(newSelection);
  };

  const selectAllSubscriptions = () => {
    setSelectedSubscriptions(visibleSubscriptions.map(s => s.subscription_id));
  };

  const clearAllSubscriptions = () => {
    setSelectedSubscriptions([]);
  };

  if (!isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center">
          <h2 className="text-2xl font-bold text-slate-900">Please sign in</h2>
          <p className="mt-2 text-slate-600">You need to be authenticated to view the dashboard</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">Resource Dashboard</h1>
            <p className="mt-2 text-slate-500 text-sm">
              View and manage resources across your Azure subscriptions
            </p>
          </div>
          <div className="flex flex-col items-start sm:items-end gap-2">
            <div className="flex flex-wrap items-center gap-2">
              {/* Saved Views */}
              <ViewSelector
                key={viewSelectorKey}
                viewType="dashboard"
                onViewSelected={handleViewSelected}
              />
              <button
                onClick={() => setSaveViewDialogOpen(true)}
                disabled={selectedSubscriptions.length === 0}
                className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors text-sm font-medium"
                title="Save current filters as a view"
              >
                <BookmarkIcon className="h-4 w-4" />
                <span>Save View</span>
              </button>
              
              {/* Refresh Button */}
              <button
                onClick={handleManualRefresh}
                disabled={selectedSubscriptions.length === 0 || isRefreshing}
                className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-300 text-slate-700 rounded-md hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-sm font-medium"
              >
                <svg
                  className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                {isRefreshing ? 'Refreshing...' : 'Refresh'}
              </button>
            </div>
            {lastRefreshTime && (
              <div className="text-xs text-slate-500 flex items-center gap-1">
                <ClockIcon className="w-3 h-3" />
                Last updated: {lastRefreshTime.toLocaleTimeString()}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white border border-slate-200 rounded-xl shadow-sm mb-6">
        <div className="px-5 py-4">
          {/* Filter Header with Active Filters Summary */}
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <FunnelIcon className="h-4 w-4 text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">Scope Filters</span>
              {(selectedSubscriptions.length > 0 || selectedLocations.length > 0 || searchQuery) && (
                <button
                  onClick={() => {
                    handleSubscriptionsChange([]);
                    handleLocationsChange([]);
                    handleSearchQueryChange('');
                  }}
                  className="text-xs text-indigo-500 hover:text-indigo-700 font-medium"
                >
                  Clear All
                </button>
              )}
            </div>
            {(selectedSubscriptions.length > 0 || selectedLocations.length > 0 || searchQuery) && (
              <div className="text-xs text-slate-500">
                {[
                  selectedSubscriptions.length > 0 && `${selectedSubscriptions.length} Subscription${selectedSubscriptions.length !== 1 ? 's' : ''}`,
                  selectedLocations.length > 0 && `${selectedLocations.length} Location${selectedLocations.length !== 1 ? 's' : ''}`,
                  searchQuery && 'Search active',
                ].filter(Boolean).join(' • ')}
              </div>
            )}
          </div>

          {/* Filter Controls - Same Height */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Subscription Filter */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <RectangleStackIcon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Subscriptions ({selectedSubscriptions.length}/10)
                </label>
              </div>
              <MultiSearchableSelect
                options={visibleSubscriptions.map(sub => ({
                  value: sub.subscription_id,
                  label: sub.display_name,
                  subtitle: sub.subscription_id.slice(0, 13) + '...',
                }))}
                selectedValues={selectedSubscriptions}
                onChange={handleSubscriptionsChange}
                placeholder="Select subscriptions..."
                showSelectAll={true}
                maxSelections={10}
              />
            </div>

            {/* Location Filter */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <MapPinIcon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Location
                </label>
              </div>
              <MultiSearchableSelect
                options={availableLocations.map(loc => ({
                  value: loc,
                  label: loc,
                }))}
                selectedValues={selectedLocations}
                onChange={handleLocationsChange}
                placeholder="Select locations..."
                disabled={resources.length === 0}
                showSelectAll={true}
              />
            </div>

            {/* Search Filter */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <MagnifyingGlassIcon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Search
                </label>
              </div>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => handleSearchQueryChange(e.target.value)}
                placeholder={resources.length === 0 ? "Select subscriptions first" : "Name, type, tags..."}
                disabled={resources.length === 0}
                className="input-field disabled:opacity-50 disabled:cursor-not-allowed text-base sm:text-sm"
              />
            </div>
          </div>

          {/* Selected Filters as Tags */}
          {(selectedSubscriptions.length > 0 || selectedLocations.length > 0) && (
            <div className="mt-3 pt-3 border-t border-slate-200">
              <div className="flex flex-wrap gap-2">
                {/* Subscription Tags */}
                {selectedSubscriptions.map(subId => {
                  const sub = subscriptions.find(s => s.subscription_id === subId);
                  return (
                    <span
                      key={subId}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 sm:py-1 bg-indigo-100 text-indigo-800 rounded-md text-xs font-medium"
                    >
                      <RectangleStackIcon className="h-3 w-3" />
                      <span className="truncate max-w-[150px] sm:max-w-[200px]">
                        {sub?.display_name || subId}
                      </span>
                      <button
                        onClick={() => handleSubscriptionsChange(selectedSubscriptions.filter(id => id !== subId))}
                        className="hover:text-indigo-900 active:text-indigo-950 flex-shrink-0 min-w-[20px] min-h-[20px] flex items-center justify-center"
                        aria-label="Remove"
                      >
                        ×
                      </button>
                    </span>
                  );
                })}

                {/* Location Tags */}
                {selectedLocations.map(loc => (
                  <span
                    key={loc}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1.5 sm:py-1 bg-green-100 text-green-700 rounded-md text-xs font-medium"
                  >
                    <MapPinIcon className="h-3 w-3" />
                    {loc}
                    <button
                      onClick={() => handleLocationsChange(selectedLocations.filter(l => l !== loc))}
                      className="hover:text-green-900 active:text-green-950 flex-shrink-0 min-w-[20px] min-h-[20px] flex items-center justify-center"
                      aria-label="Remove"
                    >
                      ×
                    </button>
                  </span>
                ))}

                {/* Search Tag */}
                {searchQuery && (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 sm:py-1 bg-indigo-100 text-indigo-800 rounded-md text-xs font-medium">
                    <MagnifyingGlassIcon className="h-3 w-3" />
                    Search: "{searchQuery}"
                    <button
                      onClick={() => handleSearchQueryChange('')}
                      className="hover:text-purple-900 active:text-purple-950 flex-shrink-0 min-w-[20px] min-h-[20px] flex items-center justify-center"
                      aria-label="Remove"
                    >
                      ×
                    </button>
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* User Access Information */}
      <div className="mb-6">
        <UserAccess scopeOverride={{ 
          globalScope: false, 
          subscriptionId: selectedSubscriptions.length > 0 ? selectedSubscriptions[0] : null,
          resourceGroup: null,
          resourceId: null
        }} />
      </div>

      {/* Dashboard Content */}
      {selectedSubscriptions.length === 0 ? (
        <div className="bg-slate-50 border border-slate-200 rounded-xl p-6 text-center">
          <p className="text-slate-500">Select at least one subscription to view the dashboard</p>
        </div>
      ) : loading ? (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-12 text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-2 border-indigo-200 border-t-indigo-600 mx-auto"></div>
          <p className="mt-4 text-slate-600">Loading dashboard data...</p>
        </div>
      ) : resources.length === 0 ? (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 text-center">
          <p className="text-yellow-800">No resources found in selected subscriptions</p>
        </div>
      ) : (
        <div className="space-y-6">
          {/* Key Metrics - Top Row */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total Resources</h3>
                <div className="w-10 h-10 bg-indigo-50 rounded-lg flex items-center justify-center">
                  <ServerIcon className="h-5 w-5 text-indigo-500" />
                </div>
              </div>
              <p className="text-3xl font-bold text-slate-900">{resources.length}</p>
              <p className="text-sm text-slate-600 mt-2">
                Across {selectedSubscriptions.length} subscription{selectedSubscriptions.length !== 1 ? 's' : ''}
              </p>
            </div>
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Resource Types</h3>
                <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
                  <ChartBarIcon className="h-5 w-5 text-green-600" />
                </div>
              </div>
              <p className="text-3xl font-bold text-slate-900">{filteredAndGroupedResources.length}</p>
              <p className="text-sm text-slate-600 mt-2">
                Different service types
              </p>
            </div>
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Locations</h3>
                <div className="w-10 h-10 bg-indigo-50 rounded-lg flex items-center justify-center">
                  <svg className="h-5 w-5 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
              </div>
              <p className="text-3xl font-bold text-slate-900">{availableLocations.length}</p>
              <p className="text-sm text-slate-600 mt-2">
                Azure regions in use
              </p>
            </div>
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Tagged Resources</h3>
                <div className="w-10 h-10 bg-indigo-50 rounded-lg flex items-center justify-center">
                  <svg className="h-5 w-5 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
                  </svg>
                </div>
              </div>
              <p className="text-3xl font-bold text-slate-900">
                {resources.filter(r => r.tags && Object.keys(r.tags).length > 0).length}
              </p>
              <p className="text-sm text-slate-600 mt-2">
                {Math.round((resources.filter(r => r.tags && Object.keys(r.tags).length > 0).length / resources.length) * 100)}% have tags
              </p>
            </div>
          </div>

          {/* Cost Overview Row */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Cost Overview */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                  <CurrencyDollarIcon className="h-5 w-5 text-indigo-600" />
                  Cost Overview
                </h2>
                <button
                  onClick={() => router.push('/costs')}
                  onMouseEnter={() => router.prefetch('/costs')}
                  className="text-xs text-indigo-600 hover:text-indigo-700 font-medium"
                >
                  View Details →
                </button>
              </div>
              {loadingCosts ? (
                <div className="text-center py-4">
                  <div className="animate-spin rounded-full h-8 w-8 border-2 border-indigo-200 border-t-indigo-600 mx-auto"></div>
                </div>
              ) : costData ? (
                <div className="space-y-4">
                  <div className="flex justify-between items-end">
                    <div>
                      <p className="text-sm text-slate-600">This Month (Current)</p>
                      <p className="text-3xl font-bold text-slate-900">{costData.current_month.toFixed(2)} {costData.currency || 'SEK'}</p>
                    </div>
                    {costData.last_month > 0 && (
                      <div className="text-right">
                        <p className="text-sm text-slate-600">Last Month</p>
                        <p className="text-xl font-semibold text-slate-500">{costData.last_month.toFixed(2)} {costData.currency || 'SEK'}</p>
                      </div>
                    )}
                  </div>
                  {costData.last_month > 0 ? (
                    <div className="flex items-center gap-2 text-sm">
                      {costData.trend === 'up' ? (
                        <>
                          <ExclamationTriangleIcon className="h-4 w-4 text-red-500" />
                          <span className="text-red-600 font-medium">
                            +{((costData.current_month - costData.last_month) / costData.last_month * 100).toFixed(1)}% from last month
                          </span>
                        </>
                      ) : costData.trend === 'down' ? (
                        <>
                          <CheckCircleIcon className="h-4 w-4 text-green-500" />
                          <span className="text-green-600 font-medium">
                            {((costData.last_month - costData.current_month) / costData.last_month * 100).toFixed(1)}% decrease from last month
                          </span>
                        </>
                      ) : (
                        <>
                          <CheckCircleIcon className="h-4 w-4 text-slate-500" />
                          <span className="text-slate-600 font-medium">
                            No significant change
                          </span>
                        </>
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-sm">
                      <ClockIcon className="h-4 w-4 text-indigo-500" />
                      <span className="text-indigo-600 font-medium">
                        Month-to-date costs for selected subscriptions
                      </span>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-slate-500 text-center py-4">Cost data not available</p>
              )}
            </div>

            {/* Top 5 Cost Drivers */}
            {costData && costData.breakdown && costData.breakdown.length > 0 && (
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                    <CurrencyDollarIcon className="h-5 w-5 text-green-600" />
                    Top 5 Cost Drivers
                  </h2>
                  <button
                    onClick={() => router.push('/costs')}
                    onMouseEnter={() => router.prefetch('/costs')}
                    className="text-xs text-indigo-600 hover:text-indigo-700 font-medium"
                  >
                    View Details →
                  </button>
                </div>
                <div className="space-y-3">
                  {costData.breakdown.slice(0, 5).map((item, index) => {
                    const name = item.subscriptionname || item.servicename || item.resourcetype || item.resourcelocation || 'Unknown';
                    const cost = item.cost;
                    const totalCost = costData.breakdown?.reduce((sum, b) => sum + b.cost, 0) || 1;
                    const percentage = (cost / totalCost) * 100;
                    
                    return (
                      <div 
                        key={index} 
                        onClick={() => router.push('/costs')}
                        className="flex items-center gap-3 cursor-pointer hover:bg-slate-50 p-2 rounded-lg transition-colors"
                      >
                        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white font-bold text-sm">
                          {index + 1}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-1">
                            <p className="text-sm font-medium text-slate-900 truncate">{name}</p>
                            <p className="text-sm font-bold text-slate-900 ml-2">{cost.toFixed(2)} {costData.currency || 'SEK'}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 bg-slate-200 rounded-full h-2">
                              <div 
                                className="bg-indigo-500 h-2 rounded-full transition-all duration-300"
                                style={{ width: `${percentage}%` }}
                              ></div>
                            </div>
                            <span className="text-xs text-slate-500 w-12 text-right">{percentage.toFixed(1)}%</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div className="mt-4 pt-4 border-t border-slate-200 text-sm text-slate-600 text-center">
                  Top 5 represent {((costData.breakdown.slice(0, 5).reduce((sum, item) => sum + item.cost, 0) / (costData.breakdown.reduce((sum, item) => sum + item.cost, 0) || 1)) * 100).toFixed(1)}% of total costs
                </div>
              </div>
            )}
          </div>

          {/* Governance Overview Row */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Policy Compliance */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                  <ShieldCheckIcon className="h-5 w-5 text-indigo-600" />
                  Policy Compliance
                </h2>
              </div>
              {loadingPolicy ? (
                <div className="text-center py-4">
                  <div className="animate-spin rounded-full h-8 w-8 border-2 border-indigo-200 border-t-indigo-600 mx-auto"></div>
                </div>
              ) : policyCompliance && typeof policyCompliance.overall_compliance_percentage === 'number' ? (
                <div className="space-y-4">
                  <div className="flex flex-col items-center">
                    <div className="relative w-32 h-32">
                      <svg className="transform -rotate-90 w-32 h-32">
                        <circle
                          cx="64"
                          cy="64"
                          r="56"
                          stroke="#e5e7eb"
                          strokeWidth="8"
                          fill="none"
                        />
                        <circle
                          cx="64"
                          cy="64"
                          r="56"
                          stroke={policyCompliance.overall_compliance_percentage >= 80 ? '#10b981' : policyCompliance.overall_compliance_percentage >= 60 ? '#f59e0b' : '#ef4444'}
                          strokeWidth="8"
                          fill="none"
                          strokeDasharray={`${(policyCompliance.overall_compliance_percentage / 100) * 351.86} 351.86`}
                          strokeLinecap="round"
                        />
                      </svg>
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <span className="text-3xl font-bold text-slate-900">
                          {policyCompliance.overall_compliance_percentage.toFixed(1)}%
                        </span>
                        <span className="text-xs text-slate-500">Compliant</span>
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-green-500"></div>
                      <span className="text-slate-600">Compliant: {policyCompliance.compliant_resources || 0}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-red-500"></div>
                      <span className="text-slate-600">Non-Compliant: {policyCompliance.non_compliant_resources || 0}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-blue-500"></div>
                      <span className="text-slate-600">Exempt: {policyCompliance.exempt_resources || 0}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="w-3 h-3 rounded-full bg-gray-400"></div>
                      <span className="text-slate-600">Unknown: {policyCompliance.unknown_resources || 0}</span>
                    </div>
                  </div>
                  <div className="text-center text-sm text-slate-500 pt-2 border-t border-slate-200">
                    {policyCompliance.total_resources || 0} total policy evaluations
                  </div>
                </div>
              ) : (
                <div className="text-center py-8">
                  <ShieldCheckIcon className="h-12 w-12 text-slate-300 mx-auto mb-2" />
                  <p className="text-sm text-slate-500">No policy data available</p>
                  <p className="text-xs text-slate-400 mt-1">Policies may not be assigned to these subscriptions</p>
                </div>
              )}
            </div>

            {/* Security Score */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                  <ShieldCheckIcon className="h-5 w-5 text-purple-600" />
                  Defender Security Score
                </h2>
              </div>
              {loadingSecurityScore ? (
                <div className="text-center py-4">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600 mx-auto"></div>
                </div>
              ) : securityScore && typeof securityScore.overall_score === 'number' ? (
                <div className="space-y-4">
                  <div className="flex flex-col items-center">
                    <div className="relative w-32 h-32">
                      <svg className="transform -rotate-90 w-32 h-32">
                        <circle
                          cx="64"
                          cy="64"
                          r="56"
                          stroke="#e5e7eb"
                          strokeWidth="8"
                          fill="none"
                        />
                        <circle
                          cx="64"
                          cy="64"
                          r="56"
                          stroke={securityScore.overall_score >= 80 ? '#10b981' : securityScore.overall_score >= 60 ? '#f59e0b' : '#ef4444'}
                          strokeWidth="8"
                          fill="none"
                          strokeDasharray={`${(securityScore.overall_score / 100) * 351.86} 351.86`}
                          strokeLinecap="round"
                        />
                      </svg>
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <span className="text-3xl font-bold text-slate-900">
                          {securityScore.overall_score.toFixed(1)}%
                        </span>
                        <span className="text-xs text-slate-500">Secure</span>
                      </div>
                    </div>
                  </div>
                  <div className="space-y-2">
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-slate-600">Current Score:</span>
                      <span className="font-semibold text-slate-900">{(securityScore.current_score || 0).toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-slate-600">Maximum Score:</span>
                      <span className="font-semibold text-slate-900">{(securityScore.max_score || 0).toFixed(2)}</span>
                    </div>
                    <div className="pt-2">
                      <div className="w-full bg-slate-200 rounded-full h-2">
                        <div
                          className={`h-2 rounded-full ${
                            securityScore.overall_score >= 80
                              ? 'bg-green-500'
                              : securityScore.overall_score >= 60
                              ? 'bg-yellow-500'
                              : 'bg-red-500'
                          }`}
                          style={{ width: `${securityScore.overall_score}%` }}
                        ></div>
                      </div>
                    </div>
                  </div>
                  <div className="text-center text-sm text-slate-500 pt-2 border-t border-slate-200">
                    Weighted average across {selectedSubscriptions.length} subscription{selectedSubscriptions.length > 1 ? 's' : ''}
                  </div>
                </div>
              ) : (
                <div className="text-center py-8">
                  <ShieldCheckIcon className="h-12 w-12 text-slate-300 mx-auto mb-2" />
                  <p className="text-sm text-slate-500">No security score available</p>
                  <p className="text-xs text-slate-400 mt-1">Microsoft Defender may not be enabled</p>
                </div>
              )}
            </div>
          </div>

          {/* Subscription Comparison & Resource Health */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Subscription Comparison */}
            {selectedSubscriptions.length > 1 && (
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
                <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2 mb-4">
                  <ChartBarIcon className="h-5 w-5 text-purple-600" />
                  Subscription Comparison
                </h2>
                <div className="space-y-4">
                  {selectedSubscriptions.map((subId, index) => {
                    const sub = subscriptions.find(s => s.subscription_id === subId);
                    const subResources = resources.filter(r => r.subscription_id === subId);
                    const subPolicyData = policyCompliance?.by_subscription?.find((p: any) => p.subscription_id === subId);
                    const subSecurityData = securityScore?.by_subscription?.find((s: any) => s.subscription_id === subId);
                    const subCost = subscriptionCosts[subId];
                    
                    return (
                      <div key={subId} className="border border-slate-200 rounded-lg p-4 hover:shadow-sm transition-shadow">
                        <div className="flex items-start justify-between mb-3">
                          <div className="flex-1 min-w-0">
                            <h3 className="text-sm font-semibold text-slate-900 truncate">{sub?.display_name || subId}</h3>
                            <p className="text-xs text-slate-500 truncate">{subId}</p>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                          <div>
                            <p className="text-xs text-slate-600 mb-1">Resources</p>
                            <p className="text-lg font-bold text-indigo-600">{subResources.length}</p>
                          </div>
                          <div>
                            <p className="text-xs text-slate-600 mb-1">Cost (MTD)</p>
                            {subCost ? (
                              <p className="text-lg font-bold text-purple-600">
                                {subCost.current.toFixed(0)} {subCost.currency}
                              </p>
                            ) : (
                              <p className="text-lg font-bold text-slate-400">...</p>
                            )}
                          </div>
                          <div>
                            <p className="text-xs text-slate-600 mb-1">Policy</p>
                            <p className={`text-lg font-bold ${(subPolicyData?.compliance_percentage || 0) >= 80 ? 'text-green-600' : (subPolicyData?.compliance_percentage || 0) >= 60 ? 'text-yellow-600' : 'text-red-600'}`}>
                              {subPolicyData?.compliance_percentage?.toFixed(0) || 'N/A'}{subPolicyData?.compliance_percentage !== undefined ? '%' : ''}
                            </p>
                          </div>
                          <div>
                            <p className="text-xs text-slate-600 mb-1">Security</p>
                            <p className={`text-lg font-bold ${(subSecurityData?.percentage_score || 0) >= 80 ? 'text-green-600' : (subSecurityData?.percentage_score || 0) >= 60 ? 'text-yellow-600' : 'text-red-600'}`}>
                              {subSecurityData?.percentage_score?.toFixed(0) || 'N/A'}{subSecurityData?.percentage_score !== undefined ? '%' : ''}
                            </p>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Resource Health Status */}
            {resourceHealth && resourceHealth.total > 0 ? (
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
                <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2 mb-4">
                  <ServerIcon className="h-5 w-5 text-green-600" />
                  Resource Health
                </h2>
                <div className="space-y-4">
                  <div className="flex flex-col items-center mb-4">
                    <div className="relative w-32 h-32">
                      <svg className="transform -rotate-90 w-32 h-32">
                        <circle cx="64" cy="64" r="56" stroke="#e5e7eb" strokeWidth="8" fill="none" />
                        <circle
                          cx="64" cy="64" r="56"
                          stroke={resourceHealth.overall_health_percentage >= 90 ? '#10b981' : resourceHealth.overall_health_percentage >= 70 ? '#f59e0b' : '#ef4444'}
                          strokeWidth="8" fill="none"
                          strokeDasharray={`${(resourceHealth.overall_health_percentage / 100) * 351.86} 351.86`}
                          strokeLinecap="round"
                        />
                      </svg>
                      <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <span className="text-3xl font-bold text-slate-900">
                          {resourceHealth.overall_health_percentage.toFixed(0)}%
                        </span>
                        <span className="text-xs text-slate-500">of monitored</span>
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex items-center gap-2 p-3 bg-green-50 rounded-lg">
                      <div className="w-3 h-3 rounded-full bg-green-500"></div>
                      <div className="flex-1">
                        <p className="text-xs text-slate-600">Available</p>
                        <p className="text-lg font-bold text-green-700">{resourceHealth.available}</p>
                      </div>
                    </div>
                    <button
                      onClick={() => (resourceHealth.unavailable > 0) && setShowHealthDrilldown(v => !v)}
                      className={`flex items-center gap-2 p-3 rounded-lg text-left w-full ${resourceHealth.unavailable > 0 ? 'bg-red-50 hover:bg-red-100 cursor-pointer' : 'bg-red-50 cursor-default'}`}
                    >
                      <div className="w-3 h-3 rounded-full bg-red-500 flex-shrink-0"></div>
                      <div className="flex-1">
                        <p className="text-xs text-slate-600">Unavailable</p>
                        <p className="text-lg font-bold text-red-700">{resourceHealth.unavailable}</p>
                      </div>
                      {resourceHealth.unavailable > 0 && <ChevronDownIcon className="w-3 h-3 text-red-400 flex-shrink-0" />}
                    </button>
                    <button
                      onClick={() => (resourceHealth.degraded > 0) && setShowHealthDrilldown(v => !v)}
                      className={`flex items-center gap-2 p-3 rounded-lg text-left w-full ${resourceHealth.degraded > 0 ? 'bg-yellow-50 hover:bg-yellow-100 cursor-pointer' : 'bg-yellow-50 cursor-default'}`}
                    >
                      <div className="w-3 h-3 rounded-full bg-yellow-500 flex-shrink-0"></div>
                      <div className="flex-1">
                        <p className="text-xs text-slate-600">Degraded</p>
                        <p className="text-lg font-bold text-yellow-700">{resourceHealth.degraded}</p>
                      </div>
                      {resourceHealth.degraded > 0 && <ChevronDownIcon className="w-3 h-3 text-yellow-400 flex-shrink-0" />}
                    </button>
                    <button
                      onClick={() => (resourceHealth.unknown > 0) && setShowHealthDrilldown(v => !v)}
                      className={`flex items-center gap-2 p-3 rounded-lg text-left w-full ${resourceHealth.unknown > 0 ? 'bg-slate-50 hover:bg-slate-100 cursor-pointer' : 'bg-slate-50 cursor-default'}`}
                    >
                      <div className="w-3 h-3 rounded-full bg-slate-400 flex-shrink-0"></div>
                      <div className="flex-1">
                        <p className="text-xs text-slate-600">Unknown</p>
                        <p className="text-lg font-bold text-slate-700">{resourceHealth.unknown}</p>
                      </div>
                      {resourceHealth.unknown > 0 && <ChevronDownIcon className="w-3 h-3 text-slate-400 flex-shrink-0" />}
                    </button>
                  </div>
                  <div className="text-center text-sm text-slate-500 pt-2 border-t border-slate-200">
                    {resourceHealth.total} of subscription resources support health monitoring
                  </div>

                  {/* Drilldown: non-healthy resources */}
                  {showHealthDrilldown && resourceHealth.non_healthy_resources?.length > 0 && (
                    <div className="mt-2 border-t border-slate-200 pt-3 space-y-2">
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
                        Non-Healthy Resources ({resourceHealth.non_healthy_resources.length})
                      </p>
                      <div className="max-h-64 overflow-y-auto space-y-1.5 pr-1">
                        {resourceHealth.non_healthy_resources.map((r: any, i: number) => {
                          const stateColor =
                            r.availability_state === 'Unavailable' ? 'bg-red-100 text-red-700' :
                            r.availability_state === 'Degraded'    ? 'bg-yellow-100 text-yellow-700' :
                                                                     'bg-slate-100 text-slate-600';
                          return (
                            <div key={i} className="flex items-start gap-2 p-2 rounded-lg bg-slate-50 border border-slate-100">
                              <div className="flex-1 min-w-0">
                                <p className="text-xs font-semibold text-slate-800 truncate">{r.resource_name}</p>
                                <p className="text-[11px] text-slate-400 truncate">{r.resource_type}</p>
                                {r.summary && <p className="text-[11px] text-slate-500 mt-0.5 line-clamp-2">{r.summary}</p>}
                              </div>
                              <span className={`flex-shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${stateColor}`}>
                                {r.availability_state}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : resourceHealth && resourceHealth.total === 0 && (
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
                <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2 mb-4">
                  <ServerIcon className="h-5 w-5 text-slate-400" />
                  Resource Health
                </h2>
                <div className="space-y-4">
                  <div className="flex flex-col items-center justify-center py-8">
                    <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mb-4">
                      <ServerIcon className="h-8 w-8 text-slate-400" />
                    </div>
                    <p className="text-sm font-medium text-slate-700 mb-2">No Resource Health Data Available</p>
                    <p className="text-xs text-slate-500 text-center max-w-md">
                      Resource Health monitoring requires:
                    </p>
                    <ul className="text-xs text-slate-500 mt-3 space-y-1.5 text-left">
                      <li className="flex items-start gap-2">
                        <span className="text-indigo-400 mt-0.5">•</span>
                        <span>Supported Azure resources (VMs, App Services, SQL DBs, etc.)</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-indigo-400 mt-0.5">•</span>
                        <span>Resources must have been running for some time</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <span className="text-indigo-400 mt-0.5">•</span>
                        <span>Reader permissions on Microsoft.ResourceHealth/*</span>
                      </li>
                    </ul>
                    <p className="text-xs text-slate-400 mt-4 italic">
                      Health data will appear automatically once resources start generating health events
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Location Distribution */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <h2 className="text-lg font-semibold text-slate-900 mb-4">Resources by Location</h2>
              <div className="space-y-2">
                {availableLocations.map(location => {
                  const count = resources.filter(r => r.location === location).length;
                  const percentage = (count / resources.length) * 100;
                  return (
                    <div key={location} className="flex justify-between items-center py-2 border-b border-slate-100 last:border-0">
                      <span className="text-sm text-slate-700">{location}</span>
                      <div className="flex items-center gap-2">
                        <div className="w-24 bg-slate-200 rounded-full h-2">
                          <div 
                            className="bg-indigo-500 h-2 rounded-full"
                            style={{ width: `${percentage}%` }}
                          ></div>
                        </div>
                        <span className="text-sm font-medium text-slate-900 w-8 text-right">{count}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Tagging Compliance */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <h2 className="text-lg font-semibold text-slate-900 mb-4">Tagging Overview</h2>
              <div className="space-y-4">
                <div className="flex justify-between items-center">
                  <span className="text-sm text-slate-700">Resources with Tags</span>
                  <span className="text-2xl font-bold text-green-600">
                    {resources.filter(r => r.tags && Object.keys(r.tags).length > 0).length}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-sm text-slate-700">Resources without Tags</span>
                  <span className="text-2xl font-bold text-red-600">
                    {resources.filter(r => !r.tags || Object.keys(r.tags).length === 0).length}
                  </span>
                </div>
                <div className="pt-4 border-t border-slate-200">
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-sm font-medium text-slate-700">Tagging Compliance</span>
                    <span className="text-lg font-bold text-indigo-600">
                      {Math.round((resources.filter(r => r.tags && Object.keys(r.tags).length > 0).length / resources.length) * 100)}%
                    </span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-3">
                    <div 
                      className="bg-indigo-500 h-3 rounded-full"
                      style={{ width: `${(resources.filter(r => r.tags && Object.keys(r.tags).length > 0).length / resources.length) * 100}%` }}
                    ></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Resource Type Groups - Collapsible Details */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900">Resource Details</h2>
              <div className="flex items-center gap-1 bg-slate-100 rounded-lg p-1">
                <button
                  onClick={() => setResourceGroupBy('type')}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                    resourceGroupBy === 'type'
                      ? 'bg-white text-slate-900 shadow-sm'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  By Type
                </button>
                <button
                  onClick={() => setResourceGroupBy('resourceGroup')}
                  className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                    resourceGroupBy === 'resourceGroup'
                      ? 'bg-white text-slate-900 shadow-sm'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  By Resource Group
                </button>
              </div>
            </div>
            <div className="space-y-2">
              {(resourceGroupBy === 'type' ? filteredAndGroupedResources : filteredAndGroupedByRG).map(group => (
                <div key={group.type} className="border border-slate-200 rounded-xl overflow-hidden">
              <button
                onClick={() => toggleTypeExpansion(group.type)}
                className="w-full px-4 py-3 flex justify-between items-center hover:bg-slate-50 transition-colors"
              >
                <div className="flex items-center gap-3">
                  <span className="text-sm font-semibold text-slate-900">{group.displayName}</span>
                  <span className="px-2 py-0.5 bg-indigo-100 text-indigo-700 rounded-md text-xs font-semibold">
                    {group.count}
                  </span>
                  <span className="text-xs text-slate-400">
                    {((group.count / resources.length) * 100).toFixed(1)}%
                  </span>
                </div>
                {group.expanded ? (
                  <ChevronUpIcon className="h-4 w-4 text-slate-400" />
                ) : (
                  <ChevronDownIcon className="h-4 w-4 text-slate-400" />
                )}
              </button>

              {group.expanded && (
                <div className="border-t border-slate-200">
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-100">
                      <thead className="bg-slate-50">
                        <tr>
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                            Name
                          </th>
                          {resourceGroupBy === 'type' ? (
                            <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                              Resource Group
                            </th>
                          ) : (
                            <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                              Type
                            </th>
                          )}
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                            Location
                          </th>
                          <th className="px-4 py-2.5 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">
                            Tags
                          </th>
                        </tr>
                      </thead>
                      <tbody className="bg-white divide-y divide-slate-100">
                        {group.resources.map(resource => (
                          <tr key={resource.id} className="hover:bg-slate-50">
                            <td className="px-4 py-3 whitespace-nowrap text-sm font-medium text-slate-900">
                              {resource.name}
                            </td>
                            {resourceGroupBy === 'type' ? (
                              <td className="px-4 py-3 whitespace-nowrap text-sm text-slate-500">
                                {resource.resource_group}
                              </td>
                            ) : (
                              <td className="px-4 py-3 whitespace-nowrap text-sm text-slate-500">
                                {getResourceTypeDisplayName(resource.type)}
                              </td>
                            )}
                            <td className="px-4 py-3 whitespace-nowrap text-sm text-slate-500">
                              {resource.location}
                            </td>
                            <td className="px-4 py-3 text-sm text-slate-500">
                              {Object.keys(resource.tags || {}).length > 0 ? (
                                <div className="flex flex-wrap gap-1">
                                  {Object.entries(resource.tags).slice(0, 3).map(([key, value]) => (
                                    <span key={key} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded-md text-xs">
                                      {key}: {value}
                                    </span>
                                  ))}
                                  {Object.keys(resource.tags).length > 3 && (
                                    <span className="px-2 py-0.5 bg-slate-100 text-slate-500 rounded-md text-xs">
                                      +{Object.keys(resource.tags).length - 3} more
                                    </span>
                                  )}
                                </div>
                              ) : (
                                <span className="text-slate-400 text-xs">No tags</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
                </div>
              ))}
            </div>
          </div>

          {/* Change Analysis - Resource Changes in Last 24 Hours */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                <ClockIcon className="h-5 w-5 text-indigo-600" />
                Change Analysis (Last 24 Hours)
              </h2>
              {changeAnalysis && changeAnalysis.total_changes > 0 && (
                <span className="text-sm text-slate-500">
                  {changeAnalysis.total_in_period > changeAnalysis.total_changes && (
                    <span className="text-xs">Showing {changeAnalysis.total_changes} of {changeAnalysis.total_in_period} changes</span>
                  )}
                </span>
              )}
            </div>

            {loadingChanges ? (
              <div className="text-center py-8">
                <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-indigo-600 mx-auto"></div>
                <p className="mt-4 text-sm text-slate-600">Loading change analysis...</p>
              </div>
            ) : changeAnalysis && changeAnalysis.success && changeAnalysis.total_changes > 0 ? (
              <div className="space-y-4">
                {/* Statistics Row */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                  <div className="bg-indigo-50 rounded-lg p-4">
                    <p className="text-sm text-slate-600 mb-1">Total Changes</p>
                    <p className="text-2xl font-bold text-indigo-600">{changeAnalysis.total_changes}</p>
                  </div>
                  <div className="bg-green-50 rounded-lg p-4">
                    <p className="text-sm text-slate-600 mb-1">Most Common</p>
                    <p className="text-lg font-semibold text-green-600">
                      {Object.entries(changeAnalysis.statistics.by_change_type).sort((a: any, b: any) => b[1] - a[1])[0]?.[0] || 'N/A'}
                    </p>
                    <p className="text-xs text-slate-500">
                      {String(Object.entries(changeAnalysis.statistics.by_change_type).sort((a: any, b: any) => b[1] - a[1])[0]?.[1] || 0)} changes
                    </p>
                  </div>
                  <div className="bg-purple-50 rounded-lg p-4">
                    <p className="text-sm text-slate-600 mb-1">Resource Types</p>
                    <p className="text-2xl font-bold text-purple-600">
                      {Object.keys(changeAnalysis.statistics.by_resource_type).length}
                    </p>
                  </div>
                </div>

                {/* Filter and Export Controls */}
                <div className="flex flex-wrap items-center gap-2 mb-3">
                  <div className="flex-1 min-w-[200px] relative">
                    <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Search changes..."
                      value={changeSearchTerm}
                      onChange={(e) => setChangeSearchTerm(e.target.value)}
                      className="w-full pl-10 pr-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                    />
                  </div>
                  <button
                    onClick={() => {
                      const headers = ['Time', 'Type', 'Resource', 'Resource Type', 'Resource Group', 'Initiated By', 'Client Type'];
                      const rows = changeAnalysis.changes
                        .filter((change: any) => {
                          if (changeSearchTerm) {
                            const searchLower = changeSearchTerm.toLowerCase();
                            return Object.values(change).some(val => String(val).toLowerCase().includes(searchLower));
                          }
                          return Object.entries(changeFilters).every(([col, vals]) => 
                            vals.size === 0 || vals.has(String(change[col] ?? ''))
                          );
                        })
                        .map((change: any) => {
                          const time = new Date(change.changeTime).toLocaleString();
                          const type = change.changeType || 'Unknown';
                          const resource = change.targetResourceName || 'N/A';
                          const resourceType = change.targetResourceType?.split('/').pop() || 'N/A';
                          const resourceGroup = change.resourceGroup || 'N/A';
                          const initiatedBy = change.initiatedBy || 'System';
                          const clientType = change.clientType || 'N/A';
                          return [time, type, resource, resourceType, resourceGroup, initiatedBy, clientType]
                            .map(v => {
                              const s = String(v);
                              return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
                            });
                        });
                      const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
                      const blob = new Blob([csv], { type: 'text/csv' });
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement('a');
                      a.href = url;
                      a.download = `change-analysis-${new Date().toISOString().split('T')[0]}.csv`;
                      a.click();
                      URL.revokeObjectURL(url);
                      toast.success('Change analysis exported to CSV');
                    }}
                    className="px-4 py-2 text-sm font-medium text-white bg-green-600 rounded-lg hover:bg-green-700 transition-colors flex items-center gap-2"
                  >
                    <ArrowDownTrayIcon className="w-4 h-4" />
                    Export CSV
                  </button>
                  {Object.values(changeFilters).some(s => s.size > 0) && (
                    <button
                      onClick={() => setChangeFilters({})}
                      className="px-4 py-2 text-sm font-medium text-red-600 bg-red-50 border border-red-200 rounded-lg hover:bg-red-100 transition-colors"
                    >
                      Clear Filters
                    </button>
                  )}
                </div>

                {/* Scrollable Change List */}
                <div className="border border-slate-200 rounded-lg overflow-hidden">
                  <div className="max-h-96 overflow-y-auto overflow-x-auto">
                    <table className="min-w-full divide-y divide-slate-200">
                      <thead className="bg-slate-50 sticky top-0">
                        <tr>
                          {[
                            { key: 'changeTime', label: 'Time' },
                            { key: 'changeType', label: 'Type' },
                            { key: 'targetResourceName', label: 'Resource' },
                            { key: 'targetResourceType', label: 'Resource Type' },
                            { key: 'resourceGroup', label: 'Resource Group' },
                            { key: 'initiatedBy', label: 'Initiated By' },
                            { key: 'clientType', label: 'Client Type' }
                          ].map(col => {
                            const hasFilter = changeFilters[col.key] && changeFilters[col.key].size > 0;
                            const filterSearch = changeFilterSearchTerms[col.key] || '';
                            const uniqueVals: string[] = Array.from(new Set(changeAnalysis.changes.map((c: any) => String(c[col.key] ?? ''))));
                            const filteredValues = filterSearch
                              ? uniqueVals.filter((v: string) => v.toLowerCase().includes(filterSearch.toLowerCase()))
                              : uniqueVals;
                            
                            return (
                              <th key={col.key} className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider relative">
                                <div className="flex items-center gap-2">
                                  <span>{col.label}</span>
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setActiveChangeFilterColumn(activeChangeFilterColumn === col.key ? null : col.key);
                                    }}
                                    className={`p-0.5 rounded hover:bg-slate-200 transition-colors ${hasFilter ? 'text-indigo-600' : 'text-slate-400'}`}
                                    title="Filter column"
                                  >
                                    <FunnelIcon className="w-3 h-3" />
                                  </button>
                                </div>

                                {/* Filter Dropdown */}
                                {activeChangeFilterColumn === col.key && (
                                  <div className="absolute top-full left-0 mt-1 w-[calc(100vw-2rem)] sm:w-80 md:w-96 max-w-md bg-white border border-slate-300 rounded-lg shadow-lg z-50 max-h-[60vh] sm:max-h-96 overflow-hidden flex flex-col">
                                    {/* Search within filter */}
                                    <div className="p-3 sm:p-2 border-b border-slate-200">
                                      <input
                                        type="text"
                                        placeholder="Search values..."
                                        value={filterSearch}
                                        onChange={(e) => setChangeFilterSearchTerms(prev => ({...prev, [col.key]: e.target.value}))}
                                        className="w-full px-3 py-2.5 sm:py-1.5 text-base sm:text-sm border border-slate-300 rounded focus:outline-none focus:ring-1 focus:ring-indigo-500 touch-target-small"
                                        onClick={(e) => e.stopPropagation()}
                                      />
                                    </div>
                                    
                                    {/* Action buttons */}
                                    <div className="p-3 sm:p-2 border-b border-slate-200 flex gap-2">
                                      <button
                                        onClick={() => {
                                          const valuesToSelect = filterSearch ? filteredValues : uniqueVals;
                                          setChangeFilters({...changeFilters, [col.key]: new Set(valuesToSelect)});
                                        }}
                                        className="flex-1 px-3 py-2.5 sm:px-2 sm:py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-50 active:bg-indigo-100 rounded"
                                      >
                                        Select {filterSearch ? 'Filtered' : 'All'}
                                      </button>
                                      <button
                                        onClick={() => {
                                          const newFilters = {...changeFilters};
                                          delete newFilters[col.key];
                                          setChangeFilters(newFilters);
                                          setActiveChangeFilterColumn(null);
                                        }}
                                        className="flex-1 px-3 py-2.5 sm:px-2 sm:py-1 text-xs font-medium text-slate-600 hover:bg-slate-50 active:bg-slate-100 rounded"
                                      >
                                        Clear
                                      </button>
                                    </div>
                                    
                                    {/* Values list */}
                                    <div className="overflow-y-auto max-h-[40vh] sm:max-h-64">
                                      {filteredValues.length > 0 ? (
                                        filteredValues.map((value) => {
                                          const isSelected = changeFilters[col.key]?.has(value) || false;
                                          return (
                                            <label
                                              key={value}
                                              className="flex items-center gap-3 sm:gap-2 px-3 py-3.5 sm:py-2 hover:bg-slate-50 active:bg-slate-100 cursor-pointer"
                                            >
                                              <input
                                                type="checkbox"
                                                checked={isSelected}
                                                onChange={() => {
                                                  const newFilters = {...changeFilters};
                                                  if (!newFilters[col.key]) newFilters[col.key] = new Set();
                                                  const filterSet = new Set(newFilters[col.key]);
                                                  if (filterSet.has(value)) {
                                                    filterSet.delete(value);
                                                  } else {
                                                    filterSet.add(value);
                                                  }
                                                  if (filterSet.size === 0) {
                                                    delete newFilters[col.key];
                                                  } else {
                                                    newFilters[col.key] = filterSet;
                                                  }
                                                  setChangeFilters(newFilters);
                                                }}
                                                className="w-4 h-4 text-indigo-600 border-slate-300 rounded focus:ring-indigo-500 flex-shrink-0"
                                              />
                                              <span className="truncate flex-1 text-sm" title={value}>{value}</span>
                                            </label>
                                          );
                                        })
                                      ) : (
                                        <div className="px-3 py-4 text-center text-sm text-slate-500">
                                          No matching values
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                )}
                              </th>
                            );
                          })}
                        </tr>
                      </thead>
                      <tbody className="bg-white divide-y divide-slate-200">
                        {changeAnalysis.changes
                          .filter((change: any) => {
                            if (changeSearchTerm) {
                              const searchLower = changeSearchTerm.toLowerCase();
                              return Object.values(change).some(val => String(val).toLowerCase().includes(searchLower));
                            }
                            return Object.entries(changeFilters).every(([col, vals]) => 
                              vals.size === 0 || vals.has(String(change[col] ?? ''))
                            );
                          })
                          .map((change: any, idx: number) => {
                            const changeTime = new Date(change.changeTime);
                            const changeType = change.changeType || 'Unknown';
                            const changeTypeColor = changeType === 'Create' ? 'text-green-600 bg-green-50' : 
                                                    changeType === 'Delete' ? 'text-red-600 bg-red-50' : 
                                                    'text-indigo-600 bg-indigo-50';
                            
                            return (
                              <tr key={idx} className="hover:bg-slate-50">
                                <td className="px-4 py-3 whitespace-nowrap text-sm text-slate-900">
                                  {changeTime.toLocaleString()}
                                </td>
                                <td className="px-4 py-3 whitespace-nowrap text-sm">
                                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${changeTypeColor}`}>
                                    {changeType}
                                  </span>
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-900 max-w-xs truncate" title={change.targetResourceName}>
                                  {change.targetResourceName || 'N/A'}
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-600 max-w-xs truncate" title={change.targetResourceType}>
                                  {change.targetResourceType?.split('/').pop() || 'N/A'}
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-600 max-w-xs truncate" title={change.resourceGroup}>
                                  {change.resourceGroup || 'N/A'}
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-600 max-w-xs truncate" title={change.initiatedBy}>
                                  {change.initiatedBy || 'System'}
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-600 whitespace-nowrap">
                                  {change.clientType || 'N/A'}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                  <div className="px-4 py-2 bg-slate-50 text-sm text-slate-600 border-t border-slate-200">
                    Showing {changeAnalysis.changes.filter((change: any) => {
                      if (changeSearchTerm) {
                        const searchLower = changeSearchTerm.toLowerCase();
                        return Object.values(change).some(val => String(val).toLowerCase().includes(searchLower));
                      }
                      return Object.entries(changeFilters).every(([col, vals]) => 
                        vals.size === 0 || vals.has(String(change[col] ?? ''))
                      );
                    }).length} of {changeAnalysis.total_changes} changes
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-center py-8">
                <ClockIcon className="h-12 w-12 text-slate-300 mx-auto mb-2" />
                <p className="text-sm text-slate-500">No resource changes detected in the last 24 hours</p>
                <p className="text-xs text-slate-400 mt-1">Changes will appear here when resources are created, updated, or deleted</p>
              </div>
            )}
          </div>
        </div>
      )}
      
      {/* Floating AI Assistant */}
      <FloatingAIAssistant />
      
      {/* Save View Dialog */}
      <SaveViewDialog
        isOpen={saveViewDialogOpen}
        onClose={() => setSaveViewDialogOpen(false)}
        currentFilters={getCurrentFilters()}
        viewType="dashboard"
        onSaved={handleViewSaved}
      />
    </div>
  );
}
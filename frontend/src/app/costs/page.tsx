'use client';

import { useState, useEffect, useMemo, useCallback } from 'react';
import { useIsAuthenticated } from '@azure/msal-react';
import { apiService } from '../../services/apiService';
import { useAppStore, getVisibleSubscriptions } from '../../store/appStore';
import toast from 'react-hot-toast';
import FloatingAIAssistant from '../../components/FloatingAIAssistant';
import UserAccess from '../../components/UserAccess';
import SaveViewDialog from '../../components/SaveViewDialog';
import ViewSelector from '../../components/ViewSelector';
import {
  CurrencyDollarIcon,
  ChartBarIcon,
  ArrowTrendingUpIcon,
  ArrowTrendingDownIcon,
  CalendarIcon,
  FunnelIcon,
  MagnifyingGlassIcon,
  ArrowDownTrayIcon,
  ClockIcon,
  BookmarkIcon,
  FolderIcon,
  RectangleStackIcon,
  Squares2X2Icon
} from '@heroicons/react/24/outline';
import MultiSearchableSelect from '../../components/MultiSearchableSelect';
import CostVisualization from '../../components/CostVisualization';
import type { SavedView } from '../../types/savedViews';

interface CostBreakdownItem {
  subscriptionname?: string;
  servicename?: string;
  location?: string;
  resourcelocation?: string;
  resourcegroupname?: string;
  resourceid?: string;
  cost: number;
}

interface DailyTrendItem {
  date: string;
  cost: number;
}

interface CostData {
  success: boolean;
  total_cost: number;
  currency: string;
  breakdown: CostBreakdownItem[];
  daily_trend: DailyTrendItem[];
  scopes_queried: number;
  scopes_failed: number;
  date_range: {
    start: string;
    end: string;
  };
  query_method?: string;
}

interface CostDataWithAmortized extends CostData {
  amortized_cost?: number;
}

interface BudgetInfo {
  subscription_id: string;
  budget_name: string;
  amount: number;
  currency: string;
  current_spend: number;
  forecasted_spend: number;
  utilization_percent: number;
  time_grain: string;
}

interface BudgetData {
  success: boolean;
  total_budget: number;
  total_current_spend: number;
  total_forecasted_spend: number;
  total_utilization_percent: number;
  currency: string;
  budgets: BudgetInfo[];
  budget_count: number;
}

interface ReservationData {
  success: boolean;
  amortized_cost: number;
  actual_cost: number;
  reservation_savings: number;
  savings_percent: number;
  currency: string;
  reservation_details?: Array<{
    reservation_order_id: string;
    reservation_name: string;
    sku_name: string;
    unused_cost: number;
    total_reserved_hours: number;
    total_used_hours: number;
    utilization_percentage: number;
    unused_hours: number;
    subscriptions: string[];
  }>;
}

export default function CostsPage() {
  const isAuthenticated = useIsAuthenticated();
  const { 
    user, 
    subscriptions, 
    selectedSubscriptions, 
    setSelectedSubscriptions,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly,
    setSelectedCostViewId
  } = useAppStore();
  
  // Get visible subscriptions based on filter preference
  const visibleSubscriptions = getVisibleSubscriptions(
    subscriptions,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly
  );
  
  const [loading, setLoading] = useState(false);
  const [costData, setCostData] = useState<CostDataWithAmortized | null>(null);
  const [budgetData, setBudgetData] = useState<BudgetData | null>(null);
  const [reservationData, setReservationData] = useState<ReservationData | null>(null);
  const [previousPeriodCost, setPreviousPeriodCost] = useState<number | null>(null);
  const [groupBy, setGroupBy] = useState<'ServiceName' | 'Location' | 'ResourceGroupName' | 'ResourceId'>('ServiceName');
  const [dateRange, setDateRange] = useState<'this_month' | 'last_month' | 'last_7_days' | 'last_30_days' | 'custom'>('this_month');
  const [customStartDate, setCustomStartDate] = useState('');
  const [customEndDate, setCustomEndDate] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [lastRefreshTime, setLastRefreshTime] = useState<Date | null>(null);
  
  // Resource group filtering
  const [availableResourceGroups, setAvailableResourceGroups] = useState<{ [key: string]: string[] }>({});
  const [selectedResourceGroups, setSelectedResourceGroups] = useState<{ [key: string]: string[] }>({});
  const [loadingResourceGroups, setLoadingResourceGroups] = useState(false);
  const [enableResourceGroupFilter, setEnableResourceGroupFilter] = useState(false);

  // Saved views
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [viewSelectorKey, setViewSelectorKey] = useState(0); // Force re-render after save
  const [isLoadingFromView, setIsLoadingFromView] = useState(false); // Track if loading from saved view

  // Wrapper functions to clear selected view when filters change manually
  const handleSubscriptionsChange = (subs: string[]) => {
    if (!isLoadingFromView) {
      setSelectedCostViewId(null);
    }
    setSelectedSubscriptions(subs);
  };

  const handleDateRangeChange = (range: typeof dateRange) => {
    if (!isLoadingFromView) {
      setSelectedCostViewId(null);
    }
    setDateRange(range);
  };

  const handleGroupByChange = (group: typeof groupBy) => {
    if (!isLoadingFromView) {
      setSelectedCostViewId(null);
    }
    setGroupBy(group);
  };

  const handleResourceGroupsChange = (rgs: typeof selectedResourceGroups) => {
    if (!isLoadingFromView) {
      setSelectedCostViewId(null);
    }
    setSelectedResourceGroups(rgs);
  };

  // Fetch resource groups for selected subscriptions
  const fetchResourceGroups = useCallback(async () => {
    console.log(' FETCH RESOURCE GROUPS CALLED!', {selectedSubscriptions});
    
    if (selectedSubscriptions.length === 0) {
      console.log(' Early return: no subscriptions');
      setAvailableResourceGroups({});
      setSelectedResourceGroups({});
      return;
    }

    setLoadingResourceGroups(true);
    console.log(' About to call API for resource groups');
    
    try {
      console.log(` Fetching resource groups for ${selectedSubscriptions.length} subscriptions`);
      
      const response = await apiService.getResourceGroupsBatch(selectedSubscriptions);
      console.log(' API call complete, response:', response);
      
      const data = response?.data || response;
      
      console.log(' Raw response data:', data);
      
      // Parse batch_results format: { batch_results: { sub_id: { resource_groups: [...] } } }
      if (data.batch_results) {
        const rgMap: { [key: string]: string[] } = {};
        
        Object.keys(data.batch_results).forEach(subId => {
          const subData = data.batch_results[subId];
          if (subData && subData.resource_groups && Array.isArray(subData.resource_groups)) {
            // Extract resource group names - handle both string arrays and object arrays
            rgMap[subId] = subData.resource_groups.map((rg: any) => 
              typeof rg === 'string' ? rg : rg.name || rg
            );
          } else {
            rgMap[subId] = [];
          }
        });
        
        setAvailableResourceGroups(rgMap);
        
        // Auto-select all resource groups for NEW subscriptions
        // Preserve existing selections for subscriptions that already have them
        setSelectedResourceGroups(prev => {
          const updated: { [key: string]: string[] } = { ...prev };
          
          Object.keys(rgMap).forEach(subId => {
            // If this subscription doesn't have any selection yet, auto-select all its RGs
            if (!updated[subId] || updated[subId].length === 0) {
              updated[subId] = rgMap[subId];
              console.log(` Auto-selecting all RGs for new subscription ${subId}`);
            } else {
              console.log(` Preserving existing RG selection for ${subId}`);
            }
          });
          
          return updated;
        });
        
        console.log(' Resource groups loaded:', rgMap);
      } else {
        console.warn(' No batch_results in response');
        setAvailableResourceGroups({});
        setSelectedResourceGroups({});
      }
    } catch (error) {
      console.error('Error fetching resource groups:', error);
      toast.error('Failed to fetch resource groups');
      setAvailableResourceGroups({});
      setSelectedResourceGroups({});
    } finally {
      setLoadingResourceGroups(false);
    }
  }, [selectedSubscriptions]);

  // Fetch resource groups when subscriptions change
  useEffect(() => {
    console.log(' useEffect triggered! selectedSubscriptions:', selectedSubscriptions);
    if (selectedSubscriptions.length > 0) {
      console.log(' Calling fetchResourceGroups for:', selectedSubscriptions);
      fetchResourceGroups();
    } else {
      console.log(' No subscriptions selected, clearing resource groups');
      setAvailableResourceGroups({});
      setSelectedResourceGroups({});
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSubscriptions]);

  // Calculate date ranges
  const getDateRange = () => {
    const now = new Date();
    let startDate: Date;
    let endDate: Date = now;

    switch (dateRange) {
      case 'this_month':
        startDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0));
        break;
      case 'last_month':
        startDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1, 0, 0, 0));
        endDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0, 23, 59, 59));
        break;
      case 'last_7_days':
        startDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
        break;
      case 'last_30_days':
        startDate = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
        break;
      case 'custom':
        if (customStartDate && customEndDate) {
          startDate = new Date(customStartDate);
          endDate = new Date(customEndDate);
        } else {
          startDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0));
        }
        break;
      default:
        startDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1, 0, 0, 0));
    }

    return { startDate, endDate };
  };

  // Fetch cost data (wrapped in useCallback to prevent stale closures in useEffect)
  const fetchCosts = useCallback(async () => {
    if (selectedSubscriptions.length === 0) {
      toast.error('Please select at least one subscription');
      return;
    }

    if (selectedSubscriptions.length > 10) {
      toast.error('Please select maximum 10 subscriptions at a time');
      return;
    }

    // Don't fetch if custom date range is selected but dates aren't set
    if (dateRange === 'custom' && (!customStartDate || !customEndDate)) {
      console.log(' Waiting for custom date range to be fully specified');
      return;
    }

    setLoading(true);
    
    // Clear previous data to ensure UI updates with new filtered data
    setCostData(null);
    setPreviousPeriodCost(null);
    setReservationData(null);
    
    try {
      const { startDate, endDate } = getDateRange();
      
      console.log(` Fetching costs for ${selectedSubscriptions.length} subscriptions from ${startDate.toISOString()} to ${endDate.toISOString()}`);
      
      // Build resource group filter ONLY if the toggle is enabled
      const resourceGroupsFilter: { [key: string]: string[] } = {};
      let hasActiveFilter = false;
      
      if (enableResourceGroupFilter) {
        // Log current state for debugging
        console.log(' Current selectedResourceGroups:', selectedResourceGroups);
        console.log(' Current availableResourceGroups:', availableResourceGroups);
        
        Object.keys(selectedResourceGroups).forEach(subId => {
          const rgs = selectedResourceGroups[subId] || [];
          const allRgs = availableResourceGroups[subId] || [];
          
          console.log(` Sub ${subId}: selected ${rgs.length}, available ${allRgs.length}`);
          
          // Include subscription in filter if:
          // 1. Some RGs are selected (not zero)
          // 2. Not all RGs are selected (active filtering)
          if (rgs.length > 0 && rgs.length < allRgs.length) {
            resourceGroupsFilter[subId] = rgs;
            hasActiveFilter = true;
            console.log(` Filtering ${subId}: ${rgs.length}/${allRgs.length} resource groups selected`);
          } else if (rgs.length === 0 && allRgs.length > 0) {
            // If no RGs selected but some exist, don't include this subscription at all
            console.log(` Skipping ${subId}: no resource groups selected`);
          } else {
            console.log(` Querying all resource groups for ${subId}`);
          }
        });
        
        if (hasActiveFilter) {
          console.log(' Active resource group filters:', resourceGroupsFilter);
        } else {
          console.log(' No resource group filter active - querying all');
        }
      } else {
        console.log(' Resource group filtering disabled - querying all RGs');
      }
      
      // Filter out subscriptions where no RGs are selected (only if RG filter is enabled)
      const subsToQuery = enableResourceGroupFilter
        ? selectedSubscriptions.filter(subId => {
            const rgs = selectedResourceGroups[subId] || [];
            const allRgs = availableResourceGroups[subId] || [];
            // Include subscription if: has RGs selected, or no RGs available (will query whole sub)
            return rgs.length > 0 || allRgs.length === 0;
          })
        : selectedSubscriptions; // If filter disabled, query all selected subscriptions
      
      console.log(` Subscriptions to query: ${subsToQuery.length}/${selectedSubscriptions.length}`);
      
      if (subsToQuery.length === 0) {
        toast.error('Please select at least one resource group to query');
        setLoading(false);
        return;
      }
      
      if (subsToQuery.length < selectedSubscriptions.length) {
        console.log(` Querying ${subsToQuery.length}/${selectedSubscriptions.length} subscriptions (some have no RGs selected)`);
      }
      
      console.log(' About to call API with:', {
        subscriptions: subsToQuery,
        resourceGroups: hasActiveFilter ? resourceGroupsFilter : undefined,
        hasFilter: hasActiveFilter
      });
      
      // Single API call to fetch both ActualCost and AmortizedCost
      const response = await apiService.getSubscriptionCostsWithBothMetrics(
        subsToQuery,
        startDate.toISOString(),
        endDate.toISOString(),
        groupBy,
        hasActiveFilter ? resourceGroupsFilter : undefined
      );

      console.log(' RAW API response:', response);
      console.log(' response type:', typeof response);
      console.log(' response keys:', response ? Object.keys(response) : 'null');

      // apiService already unwraps response.data, so response IS the data
      const combinedData = response;
      
      console.log(' Combined cost data received:', combinedData);
      console.log(' Group by:', groupBy);
      console.log(' Breakdown sample:', combinedData?.breakdown?.slice(0, 3));
      console.log(' Total cost:', combinedData?.total_cost);
      console.log(' Amortized cost:', combinedData?.amortized_cost);
      
      // Data already includes both actual and amortized costs + daily trend
      setCostData(combinedData as CostDataWithAmortized);
      
      // Calculate reservation analysis from already-fetched data (no additional API call needed!)
      if (combinedData.total_cost && combinedData.amortized_cost) {
        const savings = combinedData.total_cost - combinedData.amortized_cost;
        const savingsPercent = (savings / combinedData.total_cost * 100);
        
        setReservationData({
          success: true,
          amortized_cost: combinedData.amortized_cost,
          actual_cost: combinedData.total_cost,
          reservation_savings: savings,
          savings_percent: savingsPercent,
          currency: combinedData.currency,
          reservation_details: []
        });
        
        console.log(' Reservation analysis calculated from existing data (no API call)');
      }
      
      // Stagger additional API calls to prevent rate limiting (5-10s delays)
      // Backend has MAX_PARALLEL_QUERIES=1 and BATCH_DELAY=5s between batches
      console.log(' Scheduling additional API calls with 8s and 16s delays to avoid rate limits');
      setTimeout(() => fetchPreviousPeriodCost(startDate, endDate), 8000);   // 8s delay
      setTimeout(() => fetchBudgets(), 16000);                                // 16s delay
      
      if (combinedData.success) {
        toast.success(`Loaded costs for ${combinedData.scopes_queried} subscription(s)`);
      }
      setLastRefreshTime(new Date());
    } catch (error) {
      console.error('Error fetching costs:', error);
      toast.error('Failed to fetch cost data');
      setCostData(null);
    } finally {
      setLoading(false);
    }
  }, [selectedSubscriptions, dateRange, customStartDate, customEndDate, groupBy, JSON.stringify(selectedResourceGroups), JSON.stringify(availableResourceGroups)]);

  // Fetch previous period cost for comparison
  const fetchPreviousPeriodCost = async (currentStart: Date, currentEnd: Date) => {
    try {
      let previousStart: Date;
      let previousEnd: Date;
      
      // Calculate previous period based on selected date range
      if (dateRange === 'this_month') {
        // For "this month", compare with FULL last month
        const now = new Date();
        previousStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1, 0, 0, 0));
        previousEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 0, 23, 59, 59));
        console.log(` Comparing "this month" with full last month`);
      } else if (dateRange === 'last_month') {
        // For "last month", compare with the month before that
        const now = new Date();
        previousStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 2, 1, 0, 0, 0));
        previousEnd = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 0, 23, 59, 59));
        console.log(` Comparing "last month" with the month before`);
      } else {
        // For other date ranges (last 7 days, last 30 days, custom), compare equal duration
        const duration = currentEnd.getTime() - currentStart.getTime();
        previousStart = new Date(currentStart.getTime() - duration);
        previousEnd = new Date(currentEnd.getTime() - duration);
        console.log(` Comparing with equal time period before`);
      }
      
      console.log(` Previous period: ${previousStart.toISOString()} to ${previousEnd.toISOString()}`);
      
      // Build resource group filter (same logic as main fetch)
      const resourceGroupsFilter: { [key: string]: string[] } = {};
      let hasActiveFilter = false;
      
      Object.keys(selectedResourceGroups).forEach(subId => {
        const rgs = selectedResourceGroups[subId] || [];
        const allRgs = availableResourceGroups[subId] || [];
        
        if (rgs.length > 0 && rgs.length < allRgs.length) {
          resourceGroupsFilter[subId] = rgs;
          hasActiveFilter = true;
        }
      });
      
      const response = await apiService.getSubscriptionCosts(
        selectedSubscriptions,
        previousStart.toISOString(),
        previousEnd.toISOString(),
        groupBy,
        false,
        hasActiveFilter ? resourceGroupsFilter : undefined
      );
      
      const data = response?.data || response;
      if (data.success) {
        setPreviousPeriodCost(data.total_cost);
      }
    } catch (error) {
      console.error('Error fetching previous period:', error);
      setPreviousPeriodCost(null);
    }
  };

  // Fetch budget data
  const fetchBudgets = async () => {
    if (selectedSubscriptions.length === 0) {
      return;
    }

    try {
      console.log(` Fetching budgets for ${selectedSubscriptions.length} subscriptions`);
      
      const response = await apiService.getBudgets(selectedSubscriptions);
      const data = response?.data || response;
      
      console.log(' Budget data received:', data);
      
      if (data.success) {
        setBudgetData(data);
      } else {
        console.error('Budget fetch failed:', data);
        setBudgetData(null);
      }
    } catch (error) {
      console.error('Error fetching budgets:', error);
      setBudgetData(null);
    }
  };

  // Load saved view filters
  const handleViewSelected = (view: SavedView) => {
    console.log(' Loading saved view:', view.name);
    console.log(' View filters:', view.filters);
    console.log(' Resource groups in view:', view.filters.resource_groups);
    
    // Set flag to prevent clearing the view when filters change
    setIsLoadingFromView(true);
    
    // Apply all filters from the view
    setSelectedSubscriptions(view.filters.subscriptions);
    setSelectedResourceGroups(view.filters.resource_groups || {});
    setDateRange(view.filters.date_range);
    setCustomStartDate(view.filters.custom_start_date || '');
    setCustomEndDate(view.filters.custom_end_date || '');
    setGroupBy(view.filters.group_by);
    
    // Enable RG filter if the view has specific RG filters (not all RGs)
    const hasSpecificRgFilter = view.filters.resource_groups && 
      Object.keys(view.filters.resource_groups).some(subId => {
        const selectedRgs = view.filters.resource_groups![subId];
        return selectedRgs && selectedRgs.length > 0;
      });
    setEnableResourceGroupFilter(hasSpecificRgFilter || false);
    
    console.log(' Applied resource groups:', view.filters.resource_groups || {});
    console.log(' RG filter enabled:', hasSpecificRgFilter);
    
    // Clear the flag after a short delay
    setTimeout(() => setIsLoadingFromView(false), 100);
    
    toast.success(`Loaded view: ${view.name}`);
  };

  // Handle save view success
  const handleViewSaved = () => {
    // Refresh the view selector by incrementing key
    setViewSelectorKey(prev => prev + 1);
  };

  // Fetch reservation and savings plan analysis
  const fetchReservationAnalysis = async () => {
    if (selectedSubscriptions.length === 0) {
      return;
    }

    try {
      const { startDate, endDate } = getDateRange();
      console.log(` Fetching reservation analysis for ${selectedSubscriptions.length} subscriptions`);
      
      const response = await apiService.getReservationAnalysis(
        selectedSubscriptions,
        startDate.toISOString(),
        endDate.toISOString()
      );
      const data = response?.data || response;
      
      console.log(' Reservation data received:', data);
      
      if (data.success) {
        setReservationData(data);
      } else {
        console.error('Reservation analysis fetch failed:', data);
        setReservationData(null);
      }
    } catch (error) {
      console.error('Error fetching reservation analysis:', error);
      setReservationData(null);
    }
  };

  // Auto-fetch when filters change (with debounce to prevent rapid calls)
  useEffect(() => {
    if (!isAuthenticated || selectedSubscriptions.length === 0) {
      setCostData(null);
      setPreviousPeriodCost(null);
      setReservationData(null);
      setBudgetData(null);
      return;
    }

    // Wait for resource groups to load before fetching costs
    if (loadingResourceGroups) {
      console.log(' Waiting for resource groups to load before fetching costs');
      return;
    }

    // Debounce: wait 500ms after last change before fetching
    const timeoutId = setTimeout(() => {
      console.log(' Auto-fetching after filter change');
      fetchCosts();
    }, 500);

    return () => {
      clearTimeout(timeoutId);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, selectedSubscriptions, groupBy, dateRange, customStartDate, customEndDate, enableResourceGroupFilter, JSON.stringify(selectedResourceGroups), loadingResourceGroups]);

  // Format currency
  const formatCurrency = (amount: number, currency: string = 'SEK') => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount);
  };

  // Calculate percentage
  const calculatePercentage = (part: number, total: number) => {
    return total > 0 ? ((part / total) * 100).toFixed(1) : '0.0';
  };

  // Format resource ID to readable name
  const formatResourceName = (resourceId: string) => {
    // Parse Azure resource ID: /subscriptions/{sub}/resourceGroups/{rg}/providers/{provider}/{type}/{name}
    const parts = resourceId.split('/');
    
    if (parts.length < 9) return resourceId; // Invalid format, return as-is
    
    // Find resourceGroups index
    const rgIndex = parts.findIndex(p => p.toLowerCase() === 'resourcegroups');
    if (rgIndex === -1 || rgIndex + 1 >= parts.length) return resourceId;
    
    const resourceGroup = parts[rgIndex + 1];
    const resourceName = parts[parts.length - 1]; // Last part is the resource name
    
    return `${resourceName} (${resourceGroup})`;
  };

  // Export to CSV
  const exportToCSV = () => {
    if (!costData?.breakdown) {
      toast.error('No data to export');
      return;
    }

    const headers = ['Name', 'Cost', 'Currency', 'Percentage'];
    const rows = costData.breakdown.map(item => {
      let name = item.subscriptionname || item.servicename || item.resourcelocation || item.location || item.resourcegroupname || item.resourceid || 'Unknown';
      
      // Format resource ID if present
      if (item.resourceid && groupBy === 'ResourceId') {
        name = formatResourceName(item.resourceid);
      }
      
      const percentage = calculatePercentage(item.cost, costData.total_cost);
      return [name, item.cost.toFixed(2), costData.currency, `${percentage}%`];
    });

    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `cost_breakdown_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    toast.success('Cost data exported successfully');
  };

  // Get top items from breakdown
  const topBreakdown = useMemo(() => {
    if (!costData?.breakdown) return [];
    
    // Filter by search query first
    let filtered = costData.breakdown;
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(item => {
        const name = (item.subscriptionname || item.servicename || item.resourcelocation || item.location || item.resourcegroupname || item.resourceid || '').toLowerCase();
        return name.includes(query);
      });
    }
    
    // Sort by cost (highest to lowest)
    return [...filtered]
      .sort((a, b) => b.cost - a.cost);
  }, [costData, searchQuery]);

  // Get max cost for bar chart scaling
  const maxCost = useMemo(() => {
    if (!topBreakdown.length) return 0;
    return Math.max(...topBreakdown.map(item => item.cost));
  }, [topBreakdown]);

  // Group daily trend by week for better visualization
  const weeklyTrend = useMemo(() => {
    if (!costData?.daily_trend?.length) return [];
    
    // For now, return daily trend as-is (we can add weekly grouping later if needed)
    return costData.daily_trend.slice(-30); // Show last 30 days max
  }, [costData]);

  if (!isAuthenticated || !user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-slate-900 mb-4">
            Please sign in to view cost analysis
          </h2>
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
            <h1 className="text-[22px] font-bold text-slate-900 tracking-tight">Cost Analysis</h1>
            <p className="mt-1 text-[13px] text-slate-500">
              Comprehensive cost breakdown and trend analysis across your Azure subscriptions
            </p>
          </div>
          <div className="flex flex-col items-start sm:items-end gap-2">
            <div className="flex flex-wrap items-center gap-2">
              {/* Saved Views */}
              <ViewSelector
                key={viewSelectorKey}
                viewType="cost"
                onViewSelected={handleViewSelected}
              />
              <button
                onClick={() => setSaveViewDialogOpen(true)}
                disabled={selectedSubscriptions.length === 0}
                className="btn-primary text-[12.5px]"
                title="Save current filters as a view"
              >
                <BookmarkIcon className="h-4 w-4" />
                <span>Save View</span>
              </button>
              
              {/* Refresh Button */}
              <button
                onClick={fetchCosts}
                disabled={loading || selectedSubscriptions.length === 0}
                className="btn-secondary text-[12.5px] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <svg
                  className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                {loading ? 'Refreshing...' : 'Refresh'}
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
          {/* Filter Header */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <FunnelIcon className="h-4 w-4 text-slate-500" />
              <span className="text-sm font-semibold text-slate-700">Cost Filters</span>
              {selectedSubscriptions.length > 0 && (
                <button
                  onClick={() => handleSubscriptionsChange([])}
                  className="text-xs text-indigo-500 hover:text-indigo-700 font-medium"
                >
                  Clear All
                </button>
              )}
            </div>
            
            {selectedSubscriptions.length > 0 && (
              <span className="text-xs text-slate-500">
                {selectedSubscriptions.length} subscription(s) selected
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {/* Subscription Filter */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <RectangleStackIcon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Subscriptions ({selectedSubscriptions.length}/10)
                </label>
              </div>
              <MultiSearchableSelect
                options={visibleSubscriptions.map(s => ({
                  value: s.subscription_id,
                  label: s.display_name,
                  subtitle: s.subscription_id.slice(0, 13) + '...'
                }))}
                selectedValues={selectedSubscriptions}
                onChange={handleSubscriptionsChange}
                placeholder="Select subscriptions..."
                showSelectAll={true}
                maxSelections={10}
              />
            </div>

            {/* Resource Group Filter Toggle */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <FolderIcon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Resource Groups
                </label>
              </div>
              <button
                type="button"
                onClick={() => {
                  setEnableResourceGroupFilter(!enableResourceGroupFilter);
                  if (enableResourceGroupFilter) {
                    handleResourceGroupsChange({});
                  }
                }}
                className={`w-full px-4 py-2.5 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm font-medium transition-colors ${
                  enableResourceGroupFilter 
                    ? 'bg-indigo-600 text-white border-indigo-600 hover:bg-indigo-700' 
                    : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'
                }`}
              >
                {enableResourceGroupFilter ? '✓ Filter Active' : 'All Groups'}
              </button>
            </div>

            {/* Date Range Filter */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <CalendarIcon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Date Range
                </label>
              </div>
              <select
                value={dateRange}
                onChange={(e) => handleDateRangeChange(e.target.value as any)}
                className="w-full px-4 py-2.5 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white text-sm"
              >
                <option value="this_month">This Month</option>
                <option value="last_month">Last Month</option>
                <option value="last_7_days">Last 7 Days</option>
                <option value="last_30_days">Last 30 Days</option>
                <option value="custom">Custom Range</option>
              </select>
            </div>

            {/* Group By Filter */}
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <Squares2X2Icon className="h-3.5 w-3.5 text-slate-500" />
                <label className="text-xs font-medium text-slate-600">
                  Group By
                </label>
              </div>
              <select
                value={groupBy}
                onChange={(e) => handleGroupByChange(e.target.value as any)}
                className="w-full px-4 py-2.5 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white text-sm"
              >
                <option value="ServiceName">Service</option>
                <option value="Location">Location</option>
                <option value="ResourceGroupName">Resource Group</option>
                <option value="ResourceId">Resource</option>
              </select>
            </div>
          </div>

          {/* Custom Date Range */}
          {dateRange === 'custom' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-slate-600">
                  Start Date
                </label>
                <input
                  type="date"
                  value={customStartDate}
                  onChange={(e) => setCustomStartDate(e.target.value)}
                  className="w-full px-4 py-2.5 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white text-sm"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs font-medium text-slate-600">
                  End Date
                </label>
                <input
                  type="date"
                  value={customEndDate}
                  onChange={(e) => setCustomEndDate(e.target.value)}
                  className="w-full px-4 py-2.5 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent bg-white text-sm"
                />
              </div>
            </div>
          )}

          {/* Resource Group Filter */}
          {selectedSubscriptions.length > 0 && enableResourceGroupFilter && (
            <div className="mt-4 pt-4 border-t border-slate-200">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <FolderIcon className="h-4 w-4 text-slate-500" />
                  <label className="text-xs font-medium text-slate-600">
                    Resource Groups Filter
                  </label>
                </div>
                {Object.keys(availableResourceGroups).length > 0 && (
                  <button
                    onClick={() => {
                      // Select all resource groups for all subscriptions
                      const allSelected: { [key: string]: string[] } = {};
                      Object.keys(availableResourceGroups).forEach(subId => {
                        allSelected[subId] = availableResourceGroups[subId];
                      });
                      handleResourceGroupsChange(allSelected);
                    }}
                    className="text-xs text-indigo-500 hover:text-indigo-700 font-medium"
                  >
                    Select All
                  </button>
                )}
              </div>
              
              {loadingResourceGroups ? (
                <div className="flex items-center gap-2 text-sm text-slate-500 py-2">
                  <svg className="animate-spin h-4 w-4 text-indigo-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                  Loading resource groups...
                </div>
              ) : Object.keys(availableResourceGroups).length === 0 ? (
                <div className="text-sm text-slate-500 py-2">
                  No resource groups found in selected subscriptions.
                </div>
              ) : (
                <div className="space-y-3">
                  {selectedSubscriptions.map(subId => {
                    const sub = subscriptions.find(s => s.subscription_id === subId);
                    const rgs = availableResourceGroups[subId] || [];
                    const selectedRgs = selectedResourceGroups[subId] || [];
                    
                    if (rgs.length === 0) return null;
                    
                    return (
                      <div key={subId} className="bg-slate-50 rounded-lg p-3">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-xs font-medium text-slate-700 truncate flex-1">
                            {sub?.display_name || subId}
                          </span>
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-slate-500">
                              {selectedRgs.length}/{rgs.length}
                            </span>
                            <button
                              onClick={() => {
                                handleResourceGroupsChange({
                                  ...selectedResourceGroups,
                                  [subId]: selectedRgs.length === rgs.length ? [] : rgs
                                });
                              }}
                              className="text-xs text-indigo-500 hover:text-indigo-700 font-medium"
                            >
                              {selectedRgs.length === rgs.length ? 'Clear' : 'All'}
                            </button>
                          </div>
                        </div>
                        <MultiSearchableSelect
                          options={rgs.map(rg => ({
                            value: rg,
                            label: rg,
                            subtitle: undefined
                          }))}
                          selectedValues={selectedRgs}
                          onChange={(newSelection) => {
                            handleResourceGroupsChange({
                              ...selectedResourceGroups,
                              [subId]: newSelection
                            });
                          }}
                          placeholder="Select resource groups..."
                          showSelectAll={false}
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Selected Filters as Tags */}
          {selectedSubscriptions.length > 0 && (
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

                {/* Date Range Tag */}
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-purple-100 text-purple-800 rounded-md text-xs font-medium">
                  <CalendarIcon className="h-3 w-3" />
                  {dateRange === 'this_month' ? 'This Month' : 
                      dateRange === 'last_month' ? 'Last Month' : 
                      dateRange === 'last_7_days' ? 'Last 7 Days' : 
                      dateRange === 'last_30_days' ? 'Last 30 Days' : 
                      'Custom Range'}
                </span>

                {/* Group By Tag */}
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-green-100 text-green-800 rounded-md text-xs font-medium">
                  <Squares2X2Icon className="h-3 w-3" />
                  Group by: {groupBy === 'ServiceName' ? 'Service' : 
                                groupBy === 'Location' ? 'Location' : 
                                groupBy === 'ResourceGroupName' ? 'Resource Group' :
                                'Resource'}
                </span>

                {/* Resource Group Filter Tags */}
                {Object.keys(selectedResourceGroups).map(subId => {
                  const rgs = selectedResourceGroups[subId] || [];
                  const allRgs = availableResourceGroups[subId] || [];
                  const sub = subscriptions.find(s => s.subscription_id === subId);
                  
                  // Show tag only if not all RGs are selected (filter is active)
                  if (rgs.length === 0 || rgs.length === allRgs.length) return null;
                  
                  return (
                    <span
                      key={`rg-${subId}`}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-orange-100 text-orange-800 rounded-md text-xs font-medium"
                    >
                      <FolderIcon className="h-3 w-3" />
                      {sub?.display_name || subId}: {rgs.length} RG{rgs.length !== 1 ? 's' : ''}
                    </span>
                  );
                })}
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

      <div className="space-y-6">
        {/* Loading State */}
        {loading && selectedSubscriptions.length > 0 && (
          <div className="bg-white rounded-lg shadow p-12 text-center">
            <div className="animate-spin rounded-full h-16 w-16 border-2 border-indigo-200 border-t-indigo-600 mx-auto mb-4"></div>
            <p className="text-slate-500">Loading cost data...</p>
          </div>
        )}

        {/* Cost Data Display */}
        {!loading && costData && selectedSubscriptions.length > 0 && (
          <div className="space-y-6">
            {/* Summary Cards */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
              {/* Total Cost Card */}
              <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-sm transition-shadow">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total Cost</h3>
                  <div className="w-10 h-10 bg-blue-50 rounded-lg flex items-center justify-center">
                    <CurrencyDollarIcon className="h-5 w-5 text-blue-600" />
                  </div>
                </div>
                <p className="text-3xl font-bold text-slate-900">
                  {formatCurrency(costData.total_cost, costData.currency)}
                </p>
                <div className="flex items-center justify-between mt-2">
                  <p className="text-sm text-slate-600">
                    {costData.scopes_queried} subscription(s)
                  </p>
                  {previousPeriodCost !== null && (() => {
                    const change = ((costData.total_cost - previousPeriodCost) / previousPeriodCost) * 100;
                    const isIncrease = change > 0;
                    
                    return (
                      <div className={`flex items-center gap-1 text-xs font-semibold ${
                        isIncrease ? 'text-red-600' : 'text-green-600'
                      }`}>
                        {isIncrease ? <ArrowTrendingUpIcon className="h-3.5 w-3.5" /> : <ArrowTrendingDownIcon className="h-3.5 w-3.5" />}
                        <span>{isIncrease ? '+' : ''}{change.toFixed(1)}%</span>
                      </div>
                    );
                  })()}
                </div>
              </div>

              {/* Amortized Cost Card */}
              <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-sm transition-shadow">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Amortized Cost</h3>
                  <div className="w-10 h-10 bg-amber-50 rounded-lg flex items-center justify-center">
                    <CurrencyDollarIcon className="h-5 w-5 text-amber-600" />
                  </div>
                </div>
                <p className="text-3xl font-bold text-slate-900">
                  {formatCurrency(costData.amortized_cost || 0, costData.currency)}
                </p>
                <p className="text-sm text-slate-600 mt-2">
                  With prepaid commitments
                </p>
              </div>

              {/* Average Cost Card */}
              <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-sm transition-shadow">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Avg per Subscription</h3>
                  <div className="w-10 h-10 bg-purple-50 rounded-lg flex items-center justify-center">
                    <ChartBarIcon className="h-5 w-5 text-purple-600" />
                  </div>
                </div>
                <p className="text-3xl font-bold text-slate-900">
                  {formatCurrency(costData.total_cost / (costData.scopes_queried || 1), costData.currency)}
                </p>
                <p className="text-sm text-slate-600 mt-2">
                  Per subscription
                </p>
              </div>

              {/* Services Count */}
              <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 hover:shadow-sm transition-shadow">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                    {groupBy === 'ServiceName' ? 'Services' : groupBy === 'Location' ? 'Locations' : groupBy === 'ResourceId' ? 'Resources' : 'Resource Groups'}
                  </h3>
                  <div className="w-10 h-10 bg-green-50 rounded-lg flex items-center justify-center">
                    <ChartBarIcon className="h-5 w-5 text-green-600" />
                  </div>
                </div>
                <p className="text-3xl font-bold text-slate-900">
                  {costData.breakdown?.length || 0}
                </p>
                <p className="text-sm text-slate-600 mt-2">
                  Different {groupBy === 'ServiceName' ? 'services' : groupBy === 'Location' ? 'locations' : groupBy === 'ResourceId' ? 'resources' : 'groups'}
                </p>
              </div>
            </div>

            {/* Cost Visualization */}
            {costData && (
              <CostVisualization data={costData} showBreakdown={false} />
            )}

            {/* Budget Overview Section */}
            {budgetData?.success && budgetData.budgets.length > 0 && (
              <div className="bg-indigo-50/60 rounded-xl border border-indigo-100 shadow-sm p-6">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2">
                    <CurrencyDollarIcon className="h-6 w-6 text-indigo-600" />
                    <h3 className="text-lg font-semibold text-slate-900">Budget Overview</h3>
                    <span className="text-xs bg-indigo-100 text-indigo-700 px-2 py-1 rounded-full">
                      {budgetData.budget_count} {budgetData.budget_count === 1 ? 'Budget' : 'Budgets'}
                    </span>
                  </div>
                  <div className="text-right">
                    <p className="text-2xl font-bold text-slate-900">
                      {formatCurrency(budgetData.total_budget, budgetData.currency)}
                    </p>
                    <p className="text-xs text-slate-500">Total Budget</p>
                  </div>
                </div>

                {/* Budget Metrics Grid */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                  {/* Current Spend */}
                  <div className="bg-white rounded-lg p-4 shadow-sm">
                    <p className="text-sm text-slate-600 mb-1">Current Spend</p>
                    <p className="text-xl font-semibold text-slate-900">
                      {formatCurrency(budgetData.total_current_spend, budgetData.currency)}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">
                      {((budgetData.total_current_spend / budgetData.total_budget) * 100).toFixed(1)}% of budget
                    </p>
                  </div>

                  {/* Forecasted Spend */}
                  <div className="bg-white rounded-lg p-4 shadow-sm">
                    <p className="text-sm text-slate-600 mb-1">Forecasted Spend</p>
                    <p className="text-xl font-semibold text-slate-900">
                      {formatCurrency(budgetData.total_forecasted_spend, budgetData.currency)}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">
                      {((budgetData.total_forecasted_spend / budgetData.total_budget) * 100).toFixed(1)}% of budget
                    </p>
                  </div>

                  {/* Remaining Budget */}
                  <div className="bg-white rounded-lg p-4 shadow-sm">
                    <p className="text-sm text-slate-600 mb-1">Remaining Budget</p>
                    <p className="text-xl font-semibold text-slate-900">
                      {formatCurrency(budgetData.total_budget - budgetData.total_current_spend, budgetData.currency)}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">
                      {(((budgetData.total_budget - budgetData.total_current_spend) / budgetData.total_budget) * 100).toFixed(1)}% available
                    </p>
                  </div>
                </div>

                {/* Utilization Progress Bar */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="font-medium text-slate-700">Budget Utilization</span>
                    <span className={`font-bold ${
                      budgetData.total_utilization_percent >= 90 ? 'text-red-600' :
                      budgetData.total_utilization_percent >= 70 ? 'text-amber-600' :
                      'text-green-600'
                    }`}>
                      {budgetData.total_utilization_percent.toFixed(1)}%
                    </span>
                  </div>
                  <div className="w-full bg-slate-200 rounded-full h-4 overflow-hidden shadow-inner">
                    <div
                      className={`h-full rounded-full transition-all duration-500 ${
                        budgetData.total_utilization_percent >= 90 ? 'bg-gradient-to-r from-red-500 to-red-600' :
                        budgetData.total_utilization_percent >= 70 ? 'bg-gradient-to-r from-amber-400 to-amber-500' :
                        'bg-gradient-to-r from-green-400 to-green-500'
                      }`}
                      style={{ width: `${Math.min(budgetData.total_utilization_percent, 100)}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-xs text-slate-500">
                    <span>0 {budgetData.currency}</span>
                    <span>{formatCurrency(budgetData.total_budget, budgetData.currency)}</span>
                  </div>
                </div>

                {/* Budget Health Indicator */}
                <div className={`mt-4 p-3 rounded-lg ${
                  budgetData.total_utilization_percent >= 90 ? 'bg-red-50 border border-red-200' :
                  budgetData.total_utilization_percent >= 70 ? 'bg-amber-50 border border-amber-200' :
                  'bg-green-50 border border-green-200'
                }`}>
                  <p className={`text-sm font-medium ${
                    budgetData.total_utilization_percent >= 90 ? 'text-red-800' :
                    budgetData.total_utilization_percent >= 70 ? 'text-amber-800' :
                    'text-green-800'
                  }`}>
                    {budgetData.total_utilization_percent >= 90 ? 'Critical — Budget nearly exhausted' :
                     budgetData.total_utilization_percent >= 70 ? 'Warning — Approaching budget limit' :
                     'Healthy — Within budget targets'}
                  </p>
                </div>

                {/* Spending Velocity */}
                {(() => {
                  // Calculate spending velocity based on CURRENT MONTH only (independent of date filter)
                  const now = new Date();
                  const currentDayOfMonth = now.getDate(); // Days elapsed in current month
                  const totalDaysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
                  const daysLeftInMonth = totalDaysInMonth - currentDayOfMonth;
                  
                  // Daily burn rate based on current month progress
                  const dailyBurnRate = currentDayOfMonth > 0 ? budgetData.total_current_spend / currentDayOfMonth : 0;
                  
                  const remainingBudget = budgetData.total_budget - budgetData.total_current_spend;
                  const daysUntilExhausted = remainingBudget > 0 && dailyBurnRate > 0 ? Math.floor(remainingBudget / dailyBurnRate) : 0;
                  const projectedMonthlySpend = budgetData.total_current_spend + (dailyBurnRate * daysLeftInMonth);
                  
                  return (
                    <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                      <div className="bg-white rounded-lg p-3 shadow-sm">
                        <p className="text-slate-600 mb-1">Daily Burn Rate</p>
                        <p className="text-lg font-semibold text-slate-900">
                          {formatCurrency(dailyBurnRate, budgetData.currency)}/day
                        </p>
                      </div>
                      <div className="bg-white rounded-lg p-3 shadow-sm">
                        <p className="text-slate-600 mb-1">Budget Depletion</p>
                        <p className="text-lg font-semibold text-slate-900">
                          {daysUntilExhausted > 0 ? `~${daysUntilExhausted} days` : 'Exhausted'}
                        </p>
                      </div>
                      <div className="bg-white rounded-lg p-3 shadow-sm">
                        <p className="text-slate-600 mb-1">Month Projection</p>
                        <p className={`text-lg font-semibold ${projectedMonthlySpend > budgetData.total_budget ? 'text-red-600' : 'text-slate-900'}`}>
                          {formatCurrency(projectedMonthlySpend, budgetData.currency)}
                        </p>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* Reservation & Savings Plans Analysis */}
            {reservationData && reservationData.reservation_savings !== 0 && (
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
                <div className="flex items-center gap-2 mb-4">
                  <div className={`p-2 rounded-lg ${
                    reservationData.reservation_savings > 0 ? 'bg-green-50' : 'bg-sas-red-50'
                  }`}>
                    <svg className={`h-6 w-6 ${
                      reservationData.reservation_savings > 0 ? 'text-green-600' : 'text-sas-red-500'
                    }`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                  </div>
                  <h2 className="text-xl font-semibold text-slate-900">
                    {reservationData.reservation_savings > 0 
                      ? 'Reservation & Savings Plans' 
                      : 'Reservation Cost Impact'}
                  </h2>
                </div>

                {/* Savings/Cost Summary */}
                <div className={`bg-slate-50 rounded-lg p-4 mb-4 border ${
                  reservationData.reservation_savings > 0 ? 'border-green-200' : 'border-sas-red-200'
                }`}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium text-slate-600">
                      {reservationData.reservation_savings > 0 ? 'Total Savings' : 'Additional Cost'}
                    </span>
                    <span className={`text-2xl font-bold ${
                      reservationData.reservation_savings > 0 ? 'text-green-600' : 'text-sas-red-500'
                    }`}>
                      {formatCurrency(Math.abs(reservationData.reservation_savings), reservationData.currency)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-500">
                      {reservationData.reservation_savings > 0 ? 'Savings Rate' : 'Cost Increase'}
                    </span>
                    <span className={`text-lg font-semibold ${
                      reservationData.reservation_savings > 0 ? 'text-green-600' : 'text-sas-red-500'
                    }`}>
                      {Math.abs(reservationData.savings_percent).toFixed(1)}%
                    </span>
                  </div>
                </div>

                {/* Cost Comparison */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="bg-white rounded-lg p-4 border border-slate-200">
                    <p className="text-sm text-slate-600 mb-1">Pay-as-you-go Cost</p>
                    <p className="text-xl font-semibold text-slate-900">
                      {formatCurrency(reservationData.actual_cost, reservationData.currency)}
                    </p>
                    <p className="text-xs text-slate-500 mt-1">On-demand pricing</p>
                  </div>
                  <div className={`bg-white rounded-lg p-4 border ${
                    reservationData.reservation_savings > 0 
                      ? 'border-green-200 bg-green-50' 
                      : 'border-amber-200 bg-amber-50'
                  }`}>
                    <p className="text-sm text-slate-600 mb-1">With Reservations</p>
                    <p className={`text-xl font-semibold ${
                      reservationData.reservation_savings > 0 ? 'text-green-700' : 'text-amber-700'
                    }`}>
                      {formatCurrency(reservationData.amortized_cost, reservationData.currency)}
                    </p>
                    <p className={`text-xs mt-1 ${
                      reservationData.reservation_savings > 0 ? 'text-green-600' : 'text-amber-600'
                    }`}>
                      {reservationData.reservation_savings > 0 ? 'Optimized pricing' : 'Includes unused capacity'}
                    </p>
                  </div>
                </div>

                {/* Explanation */}
                <div className={`mt-4 p-3 border rounded-lg ${
                  reservationData.reservation_savings > 0 
                    ? 'bg-indigo-50 border-indigo-200' 
                    : 'bg-slate-100 border-slate-300'
                }`}>
                  <div className="flex gap-2">
                    <svg className={`h-5 w-5 flex-shrink-0 mt-0.5 ${
                      reservationData.reservation_savings > 0 ? 'text-indigo-500' : 'text-slate-600'
                    }`} fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                    </svg>
                    <div className={`text-sm ${
                      reservationData.reservation_savings > 0 ? 'text-indigo-700' : 'text-slate-700'
                    }`}>
                      <p className="font-medium mb-1">
                        {reservationData.reservation_savings > 0 ? 'Reservation Benefits' : 'Cost Allocation Note'}
                      </p>
                      <p className={reservationData.reservation_savings > 0 ? 'text-indigo-700' : 'text-slate-700'}>
                        {reservationData.reservation_savings > 0 ? (
                          <>
                            By committing to reserved capacity, you're saving <strong>{Math.abs(reservationData.savings_percent).toFixed(1)}%</strong> compared to on-demand pricing. 
                            This includes savings from Reserved Instances, Reserved Capacity, and Azure Savings Plans.
                          </>
                        ) : (
                          <>
                            Amortized cost is <strong>{Math.abs(reservationData.savings_percent).toFixed(1)}%</strong> higher than actual cost. 
                            This indicates your subscription is consuming capacity covered by shared reservations or savings plans purchased at a higher billing scope. 
                            The difference represents your allocated share of prepaid usage.
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                </div>

                {/* Reservation Details Table */}
                {reservationData.reservation_details && reservationData.reservation_details.length > 0 && (
                  <div className="mt-4">
                    <h3 className="text-sm font-semibold text-slate-700 mb-3">Unused Reservation Capacity</h3>
                    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
                      <div className="overflow-x-auto">
                        <table className="min-w-full divide-y divide-slate-200">
                          <thead className="bg-slate-50">
                            <tr>
                              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                                Reservation Name
                              </th>
                              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                                Service
                              </th>
                              <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 uppercase tracking-wider">
                                Wasted Cost
                              </th>
                              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 uppercase tracking-wider">
                                Subscriptions
                              </th>
                            </tr>
                          </thead>
                          <tbody className="bg-white divide-y divide-slate-200">
                            {reservationData.reservation_details.map((reservation, index) => (
                              <tr key={index} className="hover:bg-slate-50">
                                <td className="px-4 py-3 text-sm">
                                  <div className="font-medium text-slate-900">{reservation.reservation_name}</div>
                                  <div className="text-xs text-slate-500 mt-0.5">
                                    ID: {reservation.reservation_order_id}
                                  </div>
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-700">
                                  {reservation.sku_name}
                                </td>
                                <td className="px-4 py-3 text-sm text-right">
                                  <span className="font-semibold text-red-600">
                                    {reservation.unused_cost.toFixed(2)} {reservationData.currency}
                                  </span>
                                </td>
                                <td className="px-4 py-3 text-sm text-slate-600">
                                  {reservation.subscriptions.length} subscription{reservation.subscriptions.length > 1 ? 's' : ''}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                    
                    {/* Summary */}
                    <div className="mt-2 text-xs text-slate-600">
                      Showing {reservationData.reservation_details.length} reservation{reservationData.reservation_details.length > 1 ? 's' : ''} with unused capacity, sorted by wasted cost
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Cost Breakdown by Service/Location/Resource Group */}
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xl font-semibold text-slate-900">
                  Costs by {groupBy === 'ServiceName' ? 'Service' : groupBy === 'Location' ? 'Location' : groupBy === 'ResourceGroupName' ? 'Resource Group' : 'Resource'}
                </h2>
                <button
                  onClick={exportToCSV}
                  className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm font-medium"
                >
                  <ArrowDownTrayIcon className="h-4 w-4" />
                  Export CSV
                </button>
              </div>

              {/* Search and Filter */}
              <div className="mb-4">
                <div className="relative">
                  <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-slate-400" />
                  <input
                    type="text"
                    placeholder={`Search ${groupBy === 'ServiceName' ? 'services' : groupBy === 'Location' ? 'locations' : groupBy === 'ResourceId' ? 'resources' : 'resource groups'}...`}
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 border border-slate-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                  />
                </div>
                <div className="mt-2 flex items-center justify-between text-sm text-slate-500">
                  <span>
                    {searchQuery ? `Found ${topBreakdown.length} matching items` : `Showing top ${topBreakdown.length} of ${costData.breakdown?.length || 0} items`}
                  </span>
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="text-blue-600 hover:text-blue-700 font-medium"
                    >
                      Clear search
                    </button>
                  )}
                </div>
              </div>

              {topBreakdown.length > 0 ? (
                <div className="space-y-3">
                  {topBreakdown.map((item, index) => {
                    let name = item.subscriptionname || item.servicename || item.resourcelocation || item.location || item.resourcegroupname || item.resourceid || 'Unknown';
                    
                    // Format resource ID if present
                    if (item.resourceid && groupBy === 'ResourceId') {
                      name = formatResourceName(item.resourceid);
                    }
                    
                    const percentage = calculatePercentage(item.cost, costData.total_cost);
                    const barWidth = (item.cost / maxCost) * 100;

                    return (
                      <div key={index} className="group">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-sm font-medium text-slate-700 truncate flex-1">
                            {name}
                          </span>
                          <div className="flex items-center gap-3">
                            <span className="text-sm text-slate-500">
                              {percentage}%
                            </span>
                            <span className="text-sm font-semibold text-slate-900 min-w-[100px] text-right">
                              {formatCurrency(item.cost, costData.currency)}
                            </span>
                          </div>
                        </div>
                        <div className="w-full bg-slate-200 rounded-full h-3 overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-indigo-500 to-violet-500 rounded-full transition-all duration-500 group-hover:from-indigo-600 group-hover:to-violet-600"
                            style={{ width: `${barWidth}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-8 text-slate-500">
                  No breakdown data available
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      
      {/* Floating AI Assistant */}
      <FloatingAIAssistant />
      
      {/* Save View Dialog */}
      <SaveViewDialog
        isOpen={saveViewDialogOpen}
        onClose={() => setSaveViewDialogOpen(false)}
        currentFilters={{
          subscriptions: selectedSubscriptions,
          resource_groups: selectedResourceGroups,
          date_range: dateRange,
          custom_start_date: customStartDate || null,
          custom_end_date: customEndDate || null,
          group_by: groupBy
        }}
        viewType="cost"
        onSaved={handleViewSaved}
      />
    </div>
  );
}
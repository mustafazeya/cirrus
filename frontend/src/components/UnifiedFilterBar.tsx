'use client';

import React, { useEffect, useMemo, useState } from 'react';
import SearchableSelect from './SearchableSelect';
import { useAppStore, getVisibleSubscriptions } from '../store/appStore';
import { apiService } from '../services/apiService';
import toast from 'react-hot-toast';
import { useIsAuthenticated } from '@azure/msal-react';
import { 
  BookmarkIcon, 
  FunnelIcon, 
  GlobeAltIcon, 
  RectangleStackIcon,
  FolderIcon,
  CubeIcon,
  ServerIcon
} from '@heroicons/react/24/outline';
import ViewSelector from './ViewSelector';
import SaveViewDialog from './SaveViewDialog';
import type { SavedView, SavedViewFilters } from '../types/savedViews';

interface UnifiedFilterBarProps {
  onFilterChange: (filters: {
    subscription?: string;
    resourceGroup?: string;
    resourceType?: string;
    resource?: string;
    globalScope?: boolean;
  }) => void;
  isLoadingFromView?: boolean;
  compact?: boolean;
}

export default function UnifiedFilterBar({ onFilterChange, isLoadingFromView = false, compact = false }: UnifiedFilterBarProps) {
  const isAuthenticated = useIsAuthenticated();
  const { 
    user,
    subscriptions,
    selectedSubscriptionId,
    resourceGroups, 
    resources,
    selectedResourceType,
    selectedResource,
    selectedResourceGroup,
    globalScope,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly,
    setSelectedSubscription,
    setResourceGroups,
    setResources,
    setSelectedResourceType,
    setSelectedResource,
    setSelectedResourceGroup,
    setGlobalScope,
    setLoading,
    setSelectedQueryViewId
  } = useAppStore();

  // Saved views state
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [viewSelectorKey, setViewSelectorKey] = useState(0);

  // Get visible subscriptions based on filter preference
  const visibleSubscriptions = getVisibleSubscriptions(
    subscriptions,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly
  );

  // Dynamically get available resource types from loaded resources
  const availableResourceTypes = useMemo(() => {
    // Filter resources based on current filters
    let filteredForTypes = resources;
    
    if (selectedSubscriptionId) {
      filteredForTypes = filteredForTypes.filter(r => 
        r.id.toLowerCase().includes(selectedSubscriptionId.toLowerCase())
      );
    }
    
    if (selectedResourceGroup) {
      filteredForTypes = filteredForTypes.filter(r => 
        r.resource_group.toLowerCase() === selectedResourceGroup.toLowerCase()
      );
    }

    // Extract unique resource types from filtered resources
    const uniqueTypes = Array.from(new Set(filteredForTypes.map(r => r.type))).sort();
    
    return uniqueTypes;
  }, [resources, selectedSubscriptionId, selectedResourceGroup]);

  // Notify parent when filters change
  useEffect(() => {
    onFilterChange({
      subscription: selectedSubscriptionId || undefined,
      resourceGroup: selectedResourceGroup || undefined,
      resourceType: selectedResourceType || undefined,
      resource: selectedResource || undefined,
      globalScope: globalScope,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSubscriptionId, selectedResourceGroup, selectedResourceType, selectedResource, globalScope]);

  // Load resource groups when subscription changes
  useEffect(() => {
    // Skip if: not authenticated, global scope enabled, or already have resource groups for this subscription
    if (!isAuthenticated || !user || globalScope) return;
    
    if (selectedSubscriptionId && resourceGroups.length === 0) {
      loadResourceGroups(selectedSubscriptionId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, user, selectedSubscriptionId, globalScope]);

  // Load resources when subscription or resource group changes
  useEffect(() => {
    // Skip if: not authenticated, global scope enabled, or no resource group selected
    if (!isAuthenticated || !user || globalScope || !selectedSubscriptionId || !selectedResourceGroup) return;
    
    // Only load if we don't have resources cached
    if (resources.length === 0) {
      loadResources(selectedSubscriptionId, selectedResourceGroup, selectedResourceType || undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, user, selectedSubscriptionId, selectedResourceGroup, selectedResourceType, globalScope]);

  const loadResourceGroups = async (subscriptionId: string) => {
    if (!isAuthenticated || !user) return;
    
    try {
      setLoading(true);
      const response = await apiService.getResourceGroups(subscriptionId);
      setResourceGroups((response as any).resource_groups || []);
    } catch (error) {
      console.error('Failed to load resource groups:', error);
      toast.error('Failed to load resource groups');
    } finally {
      setLoading(false);
    }
  };

  const loadResources = async (subscriptionId: string, resourceGroupName?: string, resourceType?: string) => {
    if (!isAuthenticated || !user) return;
    
    try {
      setLoading(true);
      const response = await apiService.getResources(subscriptionId, resourceGroupName, resourceType);
      setResources((response as any).resources || []);
    } catch (error) {
      console.error('Failed to load resources:', error);
      toast.error('Failed to load resources');
    } finally {
      setLoading(false);
    }
  };

  // Filter resource groups based on selected subscription
  const filteredResourceGroups = useMemo(() => {
    if (!selectedSubscriptionId) return [];
    return resourceGroups.filter(rg => 
      rg.id.includes(selectedSubscriptionId)
    );
  }, [resourceGroups, selectedSubscriptionId]);

  // Filter resources based on selected subscription, resource group, and type
  const filteredResources = useMemo(() => {
    let filtered = resources;
    
    if (selectedSubscriptionId) {
      filtered = filtered.filter(r => 
        r.id.toLowerCase().includes(selectedSubscriptionId.toLowerCase())
      );
    }
    
    if (selectedResourceGroup) {
      filtered = filtered.filter(r => 
        r.resource_group.toLowerCase() === selectedResourceGroup.toLowerCase()
      );
    }

    if (selectedResourceType) {
      filtered = filtered.filter(r => 
        r.type === selectedResourceType
      );
    }
    
    return filtered;
  }, [resources, selectedSubscriptionId, selectedResourceGroup, selectedResourceType]);

  const handleSubscriptionChange = (value: string) => {
    if (!isLoadingFromView) {
      setSelectedQueryViewId(null);
    }
    setSelectedSubscription(value);
    setSelectedResourceGroup(''); // Reset dependent filters
    setSelectedResourceType('');
    setSelectedResource('');
    
    // Load resource groups for new subscription
    if (value) {
      setResourceGroups([]);
      setResources([]);
      loadResourceGroups(value);
    }
  };

  const handleResourceGroupChange = (value: string) => {
    if (!isLoadingFromView) {
      setSelectedQueryViewId(null);
    }
    setSelectedResourceGroup(value);
    setSelectedResource(''); // Reset dependent filter
    setSelectedResourceType(''); // Reset resource type when RG changes

    // Load resources for new resource group
    if (value && selectedSubscriptionId) {
      setResources([]);
      loadResources(selectedSubscriptionId, value, undefined); // Load all types first
    }
  };

  const handleResourceTypeChange = (value: string) => {
    if (!isLoadingFromView) {
      setSelectedQueryViewId(null);
    }
    setSelectedResourceType(value);
    setSelectedResource(''); // Reset dependent filter

    // Reload resources with new type filter
    if (selectedSubscriptionId) {
      setResources([]);
      loadResources(selectedSubscriptionId, selectedResourceGroup || undefined, value || undefined);
    }
  };

  const handleResourceChange = (value: string) => {
    if (!isLoadingFromView) {
      setSelectedQueryViewId(null);
    }
    setSelectedResource(value);
  };

  const handleClearFilters = () => {
    if (!isLoadingFromView) {
      setSelectedQueryViewId(null);
    }
    setGlobalScope(false);
    setSelectedSubscription('');
    setSelectedResourceGroup('');
    setSelectedResourceType('');
    setSelectedResource('');
    setResourceGroups([]);
    setResources([]);
  };

  const handleGlobalScopeToggle = () => {
    if (!isLoadingFromView) {
      setSelectedQueryViewId(null);
    }
    setGlobalScope(!globalScope);
  };

  // Handle saved view selection (called from parent query page)
  const handleViewSelected = (view: SavedView) => {
    console.log(' Loading saved query view:', view.name);
    console.log(' View filters:', view.filters);
    
    // Apply filters from the view
    if (view.filters.subscription_id) {
      handleSubscriptionChange(view.filters.subscription_id);
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

  const hasActiveFilters = globalScope || selectedSubscriptionId || selectedResourceGroup || selectedResourceType || selectedResource;

  const content = (
    <>
      {/* Global Scope Toggle */}
      <div className="flex items-center justify-between mb-4 pb-3 border-b border-gray-200">
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <div className="relative">
                <input
                  type="checkbox"
                  checked={globalScope}
                  onChange={handleGlobalScopeToggle}
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </div>
              <div className="flex items-center gap-2">
                <GlobeAltIcon className="h-4 w-4" />
                <span className="text-sm font-semibold text-gray-700">Global Scope</span>
              </div>
            </label>
            {globalScope && (
              <span className="text-xs text-blue-600 font-medium bg-blue-50 px-2 py-1 rounded">
                All Subscriptions
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!globalScope && (
              <div className="text-xs text-gray-500">
                <span className="hidden sm:inline">Select a scope to begin querying your Azure resources</span>
                <span className="sm:hidden">Select a scope to begin</span>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <FunnelIcon className="h-4 w-4 text-gray-500" />
            <span className="text-sm font-semibold text-gray-700">Scope Filters</span>
            {hasActiveFilters && (
              <button
                onClick={handleClearFilters}
                className="text-xs text-indigo-500 hover:text-indigo-700 font-medium"
              >
                Clear All
              </button>
            )}
          </div>
          {hasActiveFilters && !globalScope && (
            <div className="text-xs text-gray-500">
              {[
                selectedSubscriptionId && <span key="sub" className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">Subscription</span>,
                selectedResourceGroup && <span key="rg" className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">Resource Group</span>,
                selectedResourceType && <span key="rt" className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">Resource Type</span>,
                selectedResource && <span key="r" className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200">Resource</span>,
              ].filter(Boolean)}
            </div>
          )}
        </div>
        
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Subscription Filter */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <RectangleStackIcon className="h-3.5 w-3.5 text-gray-500" />
              <label className="text-xs font-medium text-gray-600">
                Subscription
              </label>
            </div>
            <SearchableSelect
              options={[
                { value: '', label: 'All Subscriptions' },
                ...visibleSubscriptions.map(sub => ({
                  value: sub.subscription_id,
                  label: sub.display_name,
                  subtitle: sub.subscription_id,
                }))
              ]}
              value={selectedSubscriptionId || ''}
              onChange={handleSubscriptionChange}
              placeholder="Select subscription..."
              disabled={globalScope}
            />
          </div>

          {/* Resource Group Filter */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <FolderIcon className="h-3.5 w-3.5 text-gray-500" />
              <label className="text-xs font-medium text-gray-600">
                Resource Group
              </label>
            </div>
            <SearchableSelect
              options={[
                { value: '', label: 'All Resource Groups' },
                ...filteredResourceGroups.map(rg => ({
                  value: rg.name,
                  label: rg.name,
                  subtitle: rg.location,
                }))
              ]}
              value={selectedResourceGroup || ''}
              onChange={handleResourceGroupChange}
              placeholder={globalScope ? "Disabled in global scope" : selectedSubscriptionId ? "Select resource group..." : "Select subscription first"}
              disabled={globalScope || !selectedSubscriptionId}
            />
          </div>

          {/* Resource Type Filter */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <CubeIcon className="h-3.5 w-3.5 text-gray-500" />
              <label className="text-xs font-medium text-gray-600">
                Resource Type {availableResourceTypes.length > 0 && (
                  <span className="text-xs font-normal text-gray-500">({availableResourceTypes.length})</span>
                )}
              </label>
            </div>
            <SearchableSelect
              options={
                availableResourceTypes.length === 0
                  ? [{ value: '', label: 'No resource types available', subtitle: 'Select subscription/resource group first' }]
                  : [
                      { value: '', label: 'All Resource Types' },
                      ...availableResourceTypes.map(type => ({
                        value: type,
                        label: type.split('/')[1],
                        subtitle: type.split('/')[0],
                      }))
                    ]
              }
              value={selectedResourceType || ''}
              onChange={handleResourceTypeChange}
              placeholder={globalScope ? "Disabled in global scope" : availableResourceTypes.length === 0 ? "No resource types available" : "Select resource type..."}
              disabled={globalScope || availableResourceTypes.length === 0}
            />
          </div>

          {/* Resource Filter */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <ServerIcon className="h-3.5 w-3.5 text-gray-500" />
              <label className="text-xs font-medium text-gray-600">
                Resource {filteredResources.length > 0 && (
                  <span className="text-xs font-normal text-gray-500">({filteredResources.length})</span>
                )}
              </label>
            </div>
            <SearchableSelect
              options={
                filteredResources.length === 0
                  ? [{ value: '', label: 'No resources available', subtitle: selectedSubscriptionId ? 'No resources in selected scope' : 'Select subscription first' }]
                  : [
                      { value: '', label: 'All Resources' },
                      ...filteredResources.map(r => ({
                        value: r.name,
                        label: r.name,
                        subtitle: `${r.type.split('/')[1]} • ${r.location || 'No location'}`,
                      }))
                    ]
              }
              value={selectedResource || ''}
              onChange={handleResourceChange}
              placeholder={globalScope ? "Disabled in global scope" : filteredResources.length === 0 ? "No resources available" : "Select resource..."}
              disabled={globalScope || !selectedSubscriptionId || filteredResources.length === 0}
            />
          </div>
        </div>
      
      {/* Save View Dialog */}
      <SaveViewDialog
        isOpen={saveViewDialogOpen}
        onClose={() => setSaveViewDialogOpen(false)}
        currentFilters={getCurrentFilters()}
        viewType="query"
        onSaved={handleViewSaved}
      />
    </>
  );

  if (compact) {
    return <div className="px-0 py-0">{content}</div>;
  }

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm">
      <div className="px-5 py-4">
        {content}
      </div>
    </div>
  );
}

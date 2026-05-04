'use client';

import { useState, useEffect } from 'react';
import { ChevronDownIcon, MagnifyingGlassIcon, BookmarkIcon } from '@heroicons/react/24/outline';
import { useIsAuthenticated } from '@azure/msal-react';
import { useAppStore, getVisibleSubscriptions } from '../store/appStore';
import { apiService } from '../services/apiService';
import toast from 'react-hot-toast';
import SearchableSelect from './SearchableSelect';
import ViewSelector from './ViewSelector';
import SaveViewDialog from './SaveViewDialog';
import type { SavedView, SavedViewFilters } from '../types/savedViews';

interface FilterPanelProps {
  onFiltersChange: (filters: any) => void;
}

export default function FilterPanel({ onFiltersChange }: FilterPanelProps) {
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
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly,
    setSelectedSubscription,
    setResourceGroups,
    setResources,
    setSelectedResourceType,
    setSelectedResource,
    setSelectedResourceGroup,
    setLoading,
    setError
  } = useAppStore();

  const [isExpanded, setIsExpanded] = useState(true);
  
  // Saved views state
  const [saveViewDialogOpen, setSaveViewDialogOpen] = useState(false);
  const [viewSelectorKey, setViewSelectorKey] = useState(0);

  // Get visible subscriptions based on filter preference
  const visibleSubscriptions = getVisibleSubscriptions(
    subscriptions,
    filteredSubscriptionIds,
    useFilteredSubscriptionsOnly
  );

  // Resource types for filtering
  const resourceTypes = [
    'Microsoft.Compute/virtualMachines',
    'Microsoft.Web/sites',
    'Microsoft.Storage/storageAccounts',
    'Microsoft.ContainerService/managedClusters',
    'Microsoft.Sql/servers',
    'Microsoft.Network/virtualNetworks',
    'Microsoft.KeyVault/vaults',
    'Microsoft.Insights/components'
  ];

  // Load resource groups when subscription changes or when component mounts with persisted subscription
  useEffect(() => {
    // Skip if not authenticated or no subscription selected
    if (!isAuthenticated || !user || !selectedSubscriptionId) return;
    
    // Only load if we don't have resource groups cached
    if (resourceGroups.length === 0) {
      loadResourceGroups(selectedSubscriptionId);
    }
  }, [isAuthenticated, user, selectedSubscriptionId]);

  // Load resources when we have persisted resource group selection
  useEffect(() => {
    // Skip if not authenticated or missing required filters
    if (!isAuthenticated || !user || !selectedSubscriptionId || !selectedResourceGroup) return;
    
    // Only load if we don't have resources cached
    if (resources.length === 0) {
      loadResources(selectedSubscriptionId, selectedResourceGroup, selectedResourceType);
    }
  }, [isAuthenticated, user, selectedSubscriptionId, selectedResourceGroup, selectedResourceType]);

  // Notify parent when filters change
  useEffect(() => {
    onFiltersChange({
      subscriptionId: selectedSubscriptionId,
      resourceGroup: selectedResourceGroup,
      resourceType: selectedResourceType,
      resource: selectedResource
    });
  }, [selectedSubscriptionId, selectedResourceGroup, selectedResourceType, selectedResource]);

  const loadResourceGroups = async (subscriptionId: string) => {
    if (!isAuthenticated || !user) return;
    
    try {
      setLoading(true);
      const response = await apiService.getResourceGroups(subscriptionId);
      setResourceGroups((response as any).resource_groups || []);
    } catch (error) {
      console.error('Failed to load resource groups:', error);
      toast.error('Failed to load resource groups');
      setError('Failed to load resource groups');
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
      setError('Failed to load resources');
    } finally {
      setLoading(false);
    }
  };

  const handleSubscriptionChange = (subscriptionId: string) => {
    setSelectedSubscription(subscriptionId);
    setSelectedResourceGroup(''); // Clear dependent filters
    setSelectedResource(''); // Clear resource selection
    
    // Load resource groups for the selected subscription
    if (subscriptionId) {
      loadResourceGroups(subscriptionId);
    } else {
      setResourceGroups([]); // Clear resource groups if no subscription selected
      setResources([]); // Clear resources too
    }
  };

  const handleResourceGroupChange = (resourceGroupName: string) => {
    setSelectedResourceGroup(resourceGroupName);
    setSelectedResource(''); // Clear resource selection when resource group changes
    
    // Load resources for the selected subscription and resource group
    if (selectedSubscriptionId && resourceGroupName) {
      loadResources(selectedSubscriptionId, resourceGroupName, selectedResourceType);
    }
  };

  const clearFilters = () => {
    setSelectedResourceType('');
    setSelectedResource('');
    setSelectedResourceGroup('');
  };

  // Handle saved view selection
  const handleViewSelected = (view: SavedView) => {
    console.log(' Loading saved query view:', view.name);
    console.log(' View filters:', view.filters);
    
    // Apply filters from the view
    if (view.filters.subscription_id) {
      handleSubscriptionChange(view.filters.subscription_id);
    }
    if (view.filters.resource_group) {
      setSelectedResourceGroup(view.filters.resource_group);
    }
    if (view.filters.resource_type) {
      setSelectedResourceType(view.filters.resource_type);
    }
    if (view.filters.resource) {
      setSelectedResource(view.filters.resource);
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
      global_scope: false,
    };
  };

  return (
    <div className="bg-white shadow-sm rounded-lg border border-gray-200">
      <div className="px-4 py-3 border-b border-gray-200">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-medium text-gray-900">Filters</h3>
          <div className="flex items-center space-x-2">
            {/* Saved Views */}
            <ViewSelector
              key={viewSelectorKey}
              viewType="query"
              onViewSelected={handleViewSelected}
            />
            <button
              onClick={() => setSaveViewDialogOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 border border-gray-300 rounded-md transition-colors"
              title="Save current filters as view"
            >
              <BookmarkIcon className="h-4 w-4" />
              Save
            </button>
            <button
              onClick={clearFilters}
              className="text-sm text-azure-600 hover:text-azure-700"
            >
              Clear all
            </button>
            <button
              onClick={() => setIsExpanded(!isExpanded)}
              className="p-1 rounded hover:bg-gray-100"
            >
              <ChevronDownIcon 
                className={`h-5 w-5 transform transition-transform ${
                  isExpanded ? 'rotate-180' : ''
                }`} 
              />
            </button>
          </div>
        </div>
      </div>

      {isExpanded && (
        <div className="p-4 space-y-4">
          {/* Subscription Filter */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Subscription
            </label>
            <SearchableSelect
              options={[
                { value: '', label: 'Select subscription...' },
                ...visibleSubscriptions.map(sub => ({
                  value: sub.subscription_id,
                  label: sub.display_name,
                  subtitle: sub.subscription_id
                }))
              ]}
              value={selectedSubscriptionId || ''}
              onChange={handleSubscriptionChange}
              placeholder="Select subscription..."
              className="w-full"
            />
          </div>

          {/* Resource Group Filter */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Resource Group
            </label>
            <SearchableSelect
              options={[
                { value: '', label: 'All resource groups' },
                ...resourceGroups.map(rg => ({
                  value: rg.name,
                  label: rg.name,
                  subtitle: rg.location
                }))
              ]}
              value={selectedResourceGroup}
              onChange={handleResourceGroupChange}
              placeholder="Select resource group..."
              disabled={!selectedSubscriptionId}
              className="w-full"
            />
          </div>

          {/* Resource Type Filter */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Resource Type
            </label>
            <SearchableSelect
              options={[
                { value: '', label: 'All resource types' },
                ...resourceTypes.map(type => ({
                  value: type,
                  label: type.split('/')[1],
                  subtitle: type.split('/')[0]
                }))
              ]}
              value={selectedResourceType}
              onChange={setSelectedResourceType}
              placeholder="Select resource type..."
              className="w-full"
            />
          </div>

          {/* Resource Filter */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Resource
            </label>
            <SearchableSelect
              options={[
                { value: '', label: 'All resources' },
                ...resources.map(resource => ({
                  value: resource.name,
                  label: resource.name,
                  subtitle: `${resource.type.split('/')[1]} • ${resource.location || 'No location'}`
                }))
              ]}
              value={selectedResource}
              onChange={setSelectedResource}
              placeholder="Select resource..."
              disabled={!selectedSubscriptionId}
              className="w-full"
            />
          </div>

          {/* Active Filters Summary */}
          {(selectedResourceGroup || selectedResourceType || selectedResource) && (
            <div className="pt-3 border-t border-gray-200">
              <div className="text-sm text-gray-600 mb-2">Active filters:</div>
              <div className="flex flex-wrap gap-2">
                {selectedResourceGroup && (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-azure-100 text-azure-800">
                    RG: {selectedResourceGroup}
                    <button
                      onClick={() => setSelectedResourceGroup('')}
                      className="ml-1 text-azure-600 hover:text-azure-900"
                    >
                      ×
                    </button>
                  </span>
                )}
                {selectedResourceType && (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                    Type: {selectedResourceType.split('/')[1]}
                    <button
                      onClick={() => setSelectedResourceType('')}
                      className="ml-1 text-green-600 hover:text-green-900"
                    >
                      ×
                    </button>
                  </span>
                )}
                {selectedResource && (
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                    Resource: {selectedResource}
                    <button
                      onClick={() => setSelectedResource('')}
                      className="ml-1 text-purple-600 hover:text-purple-900"
                    >
                      ×
                    </button>
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
      
      {/* Save View Dialog */}
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
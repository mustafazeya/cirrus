'use client';

import { useState, useEffect } from 'react';
import { 
  ChevronDownIcon, 
  ChevronUpIcon, 
  ShieldCheckIcon,
  GlobeAltIcon,
  ServerIcon,
  FolderIcon,
  CubeIcon,
  InformationCircleIcon
} from '@heroicons/react/24/outline';
import { apiService } from '../services/apiService';
import { useAppStore } from '../store/appStore';

interface UserAccessProps {
  className?: string;
  // Optional: Override scope from props instead of using global store
  scopeOverride?: {
    globalScope?: boolean;
    subscriptionId?: string;
    resourceGroup?: string;
    resourceId?: string;
  };
}

interface RoleAssignment {
  role_name: string;
  role_type: string;
  scope: string;
  scope_level: string;
  inherited?: boolean;
  group_id?: string;
  group_name?: string;
  permissions?: {
    actions: string[];
    not_actions: string[];
    data_actions: string[];
    description?: string;
  };
}

interface AccessInfo {
  scope_type: string;
  scope_name: string;
  role_assignments: RoleAssignment[];
  accessible_subscriptions_count: number;
  user_email: string;
  management_group?: {
    management_group_id: string;
    display_name: string;
    description: string;
  };
  log_analytics_workspace?: {
    workspace_id: string;
    workspace_name: string;
    subscription_id: string;
    resource_group: string;
    description: string;
  };
  subscription_id?: string;
  resource_group?: string;
  resource_id?: string;
}

export default function UserAccess({ className = '', scopeOverride }: UserAccessProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [accessInfo, setAccessInfo] = useState<AccessInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const {
    globalScope: storeGlobalScope,
    selectedSubscriptionId: storeSubscriptionId,
    selectedResourceGroup: storeResourceGroup,
    selectedResource: storeResource
  } = useAppStore();

  // Use override if provided, otherwise use store values
  // Check if property exists in scopeOverride object (not just if it's defined)
  const globalScope = scopeOverride && 'globalScope' in scopeOverride ? scopeOverride.globalScope : storeGlobalScope;
  const selectedSubscriptionId = scopeOverride && 'subscriptionId' in scopeOverride ? scopeOverride.subscriptionId : storeSubscriptionId;
  const selectedResourceGroup = scopeOverride && 'resourceGroup' in scopeOverride ? scopeOverride.resourceGroup : storeResourceGroup;
  const selectedResource = scopeOverride && 'resourceId' in scopeOverride && scopeOverride.resourceId ? { id: scopeOverride.resourceId } : storeResource;

  // Extract resource ID for useEffect dependency (avoid object reference issues)
  const selectedResourceId = typeof selectedResource === 'string' ? selectedResource : selectedResource?.id;

  // Fetch access info when scope changes (even when minimized, to keep data fresh)
  useEffect(() => {
    fetchAccessInfo();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [globalScope, selectedSubscriptionId, selectedResourceGroup, selectedResourceId]);

  const fetchAccessInfo = async () => {
    // Don't fetch if no scope is selected
    if (!globalScope && !selectedSubscriptionId && !selectedResourceGroup && !selectedResource) {
      setAccessInfo(null);
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const scopeContext = {
        global_scope: globalScope,
        subscription_id: selectedSubscriptionId || undefined,
        resource_group: selectedResourceGroup || undefined,
        resource_id: typeof selectedResource === 'string' ? selectedResource : selectedResource?.id || undefined
      };

      const response = await apiService.getUserAccess(scopeContext);
      setAccessInfo(response as AccessInfo);
    } catch (err: any) {
      console.error('Failed to fetch access info:', err);
      setError(err.response?.data?.detail || 'Failed to load access information');
    } finally {
      setIsLoading(false);
    }
  };

  const getScopeIcon = () => {
    if (!accessInfo) return <InformationCircleIcon className="w-5 h-5" />;

    switch (accessInfo.scope_type) {
      case 'global':
        return <GlobeAltIcon className="w-5 h-5 text-blue-500" />;
      case 'resource_group':
        return <FolderIcon className="w-5 h-5 text-green-500" />;
      case 'resource':
        return <CubeIcon className="w-5 h-5 text-purple-500" />;
      case 'subscription':
      default:
        return <ShieldCheckIcon className="w-5 h-5 text-indigo-500" />;
    }
  };

  const getScopeBadgeColor = () => {
    if (!accessInfo) return 'bg-gray-100 text-gray-700';

    switch (accessInfo.scope_type) {
      case 'global':
        return 'bg-blue-100 text-blue-700';
      case 'resource_group':
        return 'bg-green-100 text-green-700';
      case 'resource':
        return 'bg-purple-100 text-purple-700';
      case 'subscription':
      default:
        return 'bg-indigo-100 text-indigo-700';
    }
  };

  const extractScopeInfo = (scope: string): { type: string; details: { managementGroup?: string; subscription?: string; resourceGroup?: string; resource?: string } } => {
    const scopeLower = scope.toLowerCase();
    const parts = scope.split('/').filter(p => p);
    
    const details: { managementGroup?: string; subscription?: string; resourceGroup?: string; resource?: string } = {};
    
    // Management Group
    if (scopeLower.includes('/managementgroups/')) {
      const mgIndex = parts.findIndex(p => p.toLowerCase() === 'managementgroups');
      if (mgIndex >= 0 && mgIndex + 1 < parts.length) {
        details.managementGroup = parts[mgIndex + 1];
      }
      return { type: 'Management Group', details };
    }
    
    // Subscription
    const subIndex = parts.findIndex(p => p.toLowerCase() === 'subscriptions');
    if (subIndex >= 0 && subIndex + 1 < parts.length) {
      details.subscription = parts[subIndex + 1];
    }
    
    // Resource Group
    const rgIndex = parts.findIndex(p => p.toLowerCase() === 'resourcegroups');
    if (rgIndex >= 0 && rgIndex + 1 < parts.length) {
      details.resourceGroup = parts[rgIndex + 1];
    }
    
    // Resource
    const providerIndex = parts.findIndex(p => p.toLowerCase() === 'providers');
    if (providerIndex >= 0 && providerIndex + 2 < parts.length) {
      // Resource name is the last part
      details.resource = parts[parts.length - 1];
    }
    
    // Determine type
    if (details.resource) {
      return { type: 'Resource', details };
    } else if (details.resourceGroup) {
      return { type: 'Resource Group', details };
    } else if (details.subscription) {
      return { type: 'Subscription', details };
    }
    
    return { type: 'Unknown', details };
  };

  return (
    <div className={`bg-white rounded-lg border border-gray-200 shadow-sm ${className}`}>
      {/* Header - Always visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <ShieldCheckIcon className="w-5 h-5 text-gray-600" />
          <span className="font-medium text-gray-900">Your Access</span>
        </div>
        {isExpanded ? (
          <ChevronUpIcon className="w-5 h-5 text-gray-400" />
        ) : (
          <ChevronDownIcon className="w-5 h-5 text-gray-400" />
        )}
      </button>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="px-4 py-3 border-t border-gray-200">
          {isLoading && (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-4">
              <p className="text-sm text-red-700">{error}</p>
            </div>
          )}

          {accessInfo && !isLoading && !error && (
            <div className="space-y-4">
              {/* Scope Information */}
              <div className="bg-gray-50 rounded-lg p-4">
                <div className="flex items-start gap-3">
                  {getScopeIcon()}
                  <div className="flex-1">
                    <h4 className="font-medium text-gray-900 mb-1">Current Scope</h4>
                    <p className="text-sm text-gray-600 mb-2">{accessInfo.scope_name}</p>
                    
                    {/* Show subscription/resource group details when selected */}
                    {accessInfo.subscription_id && (
                      <div className="text-xs text-gray-500 space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">Subscription ID:</span>
                          <span className="font-mono bg-white px-2 py-0.5 rounded">{accessInfo.subscription_id}</span>
                        </div>
                        {accessInfo.resource_group && (
                          <div className="flex items-center gap-2">
                            <span className="font-medium">Resource Group:</span>
                            <span className="font-mono bg-white px-2 py-0.5 rounded">{accessInfo.resource_group}</span>
                          </div>
                        )}
                      </div>
                    )}
                    
                    {/* Accessible Subscriptions Count - only show in global scope */}
                    {accessInfo.scope_type === 'global' && (
                      <div className="flex items-center gap-2 text-xs text-gray-500 mt-2">
                        <span className="flex items-center gap-1.5"><svg className="w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" /></svg>{accessInfo.accessible_subscriptions_count} subscription{accessInfo.accessible_subscriptions_count !== 1 ? 's' : ''} accessible</span>
                      </div>
                    )}

                    {/* Management Group Info (Global Scope) */}
                    {accessInfo.management_group && (
                      <div className="mt-3 pt-3 border-t border-gray-200">
                        <p className="text-xs font-medium text-gray-700 mb-1">Management Group</p>
                        <p className="text-xs text-gray-600 font-mono bg-white px-2 py-1 rounded">
                          {accessInfo.management_group.management_group_id}
                        </p>
                        <p className="text-xs text-gray-500 mt-1">
                          {accessInfo.management_group.description}
                        </p>
                      </div>
                    )}

                  </div>
                </div>
              </div>

              {/* Role Assignments */}
              {accessInfo.role_assignments && accessInfo.role_assignments.length > 0 && (
                <div>
                  <h4 className="font-medium text-gray-900 mb-3 flex items-center gap-2">
                    <ShieldCheckIcon className="w-4 h-4" />
                    Your Roles ({(() => {
                      // Group by role name to get unique count
                      const uniqueRoles = new Set(accessInfo.role_assignments.map(r => r.role_name));
                      return uniqueRoles.size;
                    })()})
                  </h4>
                  <div className="space-y-3">
                    {(() => {
                      // Group roles by role_name
                      const groupedRoles = accessInfo.role_assignments.reduce((acc, role) => {
                        if (!acc[role.role_name]) {
                          acc[role.role_name] = {
                            role_name: role.role_name,
                            role_type: role.role_type,
                            permissions: role.permissions,
                            scopes: [],
                            hasInherited: false,
                            groupName: undefined as string | undefined
                          };
                        }
                        acc[role.role_name].scopes.push({
                          scope: role.scope,
                          scope_level: role.scope_level,
                          inherited: role.inherited || false,
                          group_name: role.group_name
                        });
                        if (role.inherited) {
                          acc[role.role_name].hasInherited = true;
                          // Store first group name found for this role
                          if (role.group_name && !acc[role.role_name].groupName) {
                            acc[role.role_name].groupName = role.group_name;
                          }
                        }
                        return acc;
                      }, {} as Record<string, { role_name: string; role_type: string; permissions?: any; scopes: Array<{ scope: string; scope_level: string; inherited: boolean; group_name?: string }>; hasInherited: boolean; groupName?: string }>);

                      // Convert to array and sort
                      return Object.values(groupedRoles)
                        .sort((a, b) => {
                          // Define role priority (Owner > Contributor > Reader > others)
                          const getRolePriority = (roleName: string) => {
                            const lowerName = roleName.toLowerCase();
                            if (lowerName.includes('owner')) return 0;
                            if (lowerName.includes('contributor')) return 1;
                            if (lowerName.includes('reader')) return 2;
                            return 3;
                          };
                          
                          const priorityA = getRolePriority(a.role_name);
                          const priorityB = getRolePriority(b.role_name);
                          
                          // First sort by priority
                          if (priorityA !== priorityB) {
                            return priorityA - priorityB;
                          }
                          
                          // Then sort alphabetically by role name
                          return a.role_name.localeCompare(b.role_name);
                        })
                        .map((role, index) => {
                          return (
                        <div key={index} className="border border-gray-200 rounded-lg p-4 hover:border-gray-300 transition-colors bg-white">
                          <div className="flex items-start justify-between mb-2">
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <p className="font-semibold text-sm text-gray-900">{role.role_name}</p>
                                {role.hasInherited && role.groupName && (
                                  <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-700 rounded border border-blue-200" title={`Inherited from group: ${role.groupName}`}>
                                    via {role.groupName}
                                  </span>
                                )}
                                {role.hasInherited && !role.groupName && (
                                  <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-700 rounded border border-blue-200">
                                    via group
                                  </span>
                                )}
                              </div>
                              <p className="text-xs text-gray-500 mt-0.5">
                                {role.scopes.length} scope{role.scopes.length !== 1 ? 's' : ''}
                              </p>
                            </div>
                          </div>
                          
                          {role.permissions && role.permissions.description && (
                            <p className="text-xs text-gray-600 mb-3 leading-relaxed">{role.permissions.description}</p>
                          )}
                          
                          {/* Scopes */}
                          <div className="mt-3 pt-3 border-t border-gray-200 space-y-4">
                            {role.scopes.map((scopeItem, scopeIndex) => {
                              const scopeInfo = extractScopeInfo(scopeItem.scope);
                              return (
                                <div key={scopeIndex}>
                                  {role.scopes.length > 1 && (
                                    <div className="flex items-center justify-between mb-2">
                                      <p className="text-xs font-medium text-gray-700">
                                        Scope {scopeIndex + 1} - {scopeItem.scope_level}
                                      </p>
                                      {scopeItem.inherited && scopeItem.group_name && (
                                        <span className="text-xs text-blue-600" title={`Group: ${scopeItem.group_name}`}>
                                          Inherited via {scopeItem.group_name}
                                        </span>
                                      )}
                                      {scopeItem.inherited && !scopeItem.group_name && (
                                        <span className="text-xs text-blue-600">Inherited via group</span>
                                      )}
                                    </div>
                                  )}
                                  <div className="space-y-2">
                                    {scopeInfo.details.managementGroup && (
                                      <div className="grid grid-cols-[120px_1fr] gap-2 items-start">
                                        <span className="text-xs text-gray-500">Mgmt Group:</span>
                                        <span className="text-xs text-gray-900 font-mono break-all">
                                          {scopeInfo.details.managementGroup}
                                        </span>
                                      </div>
                                    )}
                                    {scopeInfo.details.subscription && (
                                      <div className="grid grid-cols-[120px_1fr] gap-2 items-start">
                                        <span className="text-xs text-gray-500">Subscription:</span>
                                        <span className="text-xs text-gray-900 font-mono break-all">
                                          {scopeInfo.details.subscription}
                                        </span>
                                      </div>
                                    )}
                                    {scopeInfo.details.resourceGroup && (
                                      <div className="grid grid-cols-[120px_1fr] gap-2 items-start">
                                        <span className="text-xs text-gray-500">Resource Group:</span>
                                        <span className="text-xs text-gray-900 font-mono break-all">
                                          {scopeInfo.details.resourceGroup}
                                        </span>
                                      </div>
                                    )}
                                    {scopeInfo.details.resource && (
                                      <div className="grid grid-cols-[120px_1fr] gap-2 items-start">
                                        <span className="text-xs text-gray-500">Resource:</span>
                                        <span className="text-xs text-gray-900 font-mono break-all">
                                          {scopeInfo.details.resource}
                                        </span>
                                      </div>
                                    )}
                                  </div>
                                  {scopeIndex < role.scopes.length - 1 && (
                                    <div className="mt-3 border-b border-gray-100"></div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      );
                    });
                  })()}
                  </div>
                </div>
              )}

              {/* No Roles Message */}
              {accessInfo.role_assignments && accessInfo.role_assignments.length === 0 && (
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                  <p className="text-sm text-yellow-700">
                    No specific role assignments found at this scope level. You may have inherited permissions from a parent scope.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

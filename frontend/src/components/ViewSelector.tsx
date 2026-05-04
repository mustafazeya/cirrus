/**
 * View Selector Component
 * 
 * Enterprise-grade dropdown to select and manage saved views
 * Features: Search, categorization, actions, status indicators
 */

'use client';

import { useState, useEffect, useMemo } from 'react';
import { Menu, Transition } from '@headlessui/react';
import { 
  BookmarkIcon, 
  ChevronDownIcon,
  StarIcon,
  TrashIcon,
  MagnifyingGlassIcon,
  ClockIcon,
  FunnelIcon,
  RectangleStackIcon,
  FolderIcon,
  CalendarIcon,
  Squares2X2Icon,
  CubeIcon,
  ServerIcon,
  GlobeAltIcon,
  MapPinIcon
} from '@heroicons/react/24/outline';
import { 
  StarIcon as StarIconSolid, 
  BookmarkIcon as BookmarkIconSolid 
} from '@heroicons/react/24/solid';
import toast from 'react-hot-toast';
import { apiService } from '../services/apiService';
import { useAppStore } from '../store/appStore';
import type { SavedView } from '../types/savedViews';

interface ViewSelectorProps {
  viewType: 'cost' | 'dashboard' | 'query';
  onViewSelected: (view: SavedView) => void;
  onRefresh?: () => void;
}

export default function ViewSelector({ viewType, onViewSelected, onRefresh }: ViewSelectorProps) {
  const [views, setViews] = useState<SavedView[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  
  // Get subscriptions from store to look up names
  const subscriptions = useAppStore((state) => state.subscriptions);
  
  // Get persisted selected view ID from store based on view type
  const selectedViewId = useAppStore((state) => {
    switch (viewType) {
      case 'cost': return state.selectedCostViewId;
      case 'query': return state.selectedQueryViewId;
      case 'dashboard': return state.selectedDashboardViewId;
      default: return null;
    }
  });
  
  // Get setter for selected view ID from store
  const setSelectedViewId = useAppStore((state) => {
    switch (viewType) {
      case 'cost': return state.setSelectedCostViewId;
      case 'query': return state.setSelectedQueryViewId;
      case 'dashboard': return state.setSelectedDashboardViewId;
      default: return () => {};
    }
  });
  
  // Find the selected view object from the ID
  const selectedView = useMemo(() => {
    return views.find(v => v.id === selectedViewId) || null;
  }, [views, selectedViewId]);

  // Filter views based on search query
  const filteredViews = useMemo(() => {
    if (!searchQuery.trim()) return views;
    
    const query = searchQuery.toLowerCase();
    return views.filter(view => 
      view.name.toLowerCase().includes(query) ||
      view.description?.toLowerCase().includes(query)
    );
  }, [views, searchQuery]);

  const loadViews = async () => {
    try {
      const response = await apiService.listSavedViews();
      if (response.success) {
        const filteredViews = response.views.filter((v: SavedView) => v.view_type === viewType);
        setViews(filteredViews);
        
        // Only auto-select default view if no view is currently selected
        if (!selectedViewId) {
          const defaultView = filteredViews.find((v: SavedView) => v.is_default);
          if (defaultView) {
            setSelectedViewId(defaultView.id);
            onViewSelected(defaultView);
          }
        }
      }
    } catch (error) {
      console.error('Failed to load saved views:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadViews();
  }, [viewType]);

  useEffect(() => {
    if (onRefresh) {
      onRefresh();
    }
  }, [onRefresh]);

  const handleSelectView = (view: SavedView) => {
    setSelectedViewId(view.id);
    onViewSelected(view);
    toast.success(`Loaded view: ${view.name}`);
  };

  const handleDeleteView = async (viewId: string, viewName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    
    if (!confirm(`Delete view "${viewName}"?`)) {
      return;
    }

    try {
      await apiService.deleteSavedView(viewId);
      toast.success('View deleted');
      await loadViews();
      if (selectedViewId === viewId) {
        setSelectedViewId(null);
      }
    } catch (error) {
      console.error('Failed to delete view:', error);
      toast.error('Failed to delete view');
    }
  };

  const handleSetDefault = async (viewId: string, viewName: string, e: React.MouseEvent) => {
    e.stopPropagation();

    try {
      await apiService.updateSavedView(viewId, { is_default: true });
      toast.success(`"${viewName}" set as default`);
      await loadViews();
    } catch (error) {
      console.error('Failed to set default:', error);
      toast.error('Failed to set default');
    }
  };

  if (loading) {
    return <div className="h-[30px] w-28 rounded-lg bg-slate-100 animate-pulse" />;
  }

  if (views.length === 0) {
    return (
      <span className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[11.5px] font-medium text-slate-400 border border-dashed border-slate-200 rounded-lg select-none">
        <BookmarkIcon className="h-3.5 w-3.5" />
        No saved views
      </span>
    );
  }

  return (
    <Menu as="div" className="relative inline-block text-left">
      {({ open }) => (
        <>
          <Menu.Button
            className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-[12.5px] font-medium rounded-lg border transition-all focus:outline-none focus:ring-2 focus:ring-indigo-500 ${
              selectedView
                ? 'bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100'
                : 'bg-white text-slate-600 border-gray-200 hover:bg-slate-50 shadow-sm'
            }`}
            title={selectedView ? `View: ${selectedView.name}` : 'Select a saved view'}
          >
            {selectedView
              ? <BookmarkIconSolid className="h-3.5 w-3.5 text-indigo-500 flex-shrink-0" />
              : <BookmarkIcon className="h-3.5 w-3.5 text-slate-400 flex-shrink-0" />
            }
            <span className="hidden sm:inline truncate max-w-[120px]">
              {selectedView ? selectedView.name : 'Views'}
            </span>
            <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[10px] font-semibold rounded-full bg-slate-200/80 text-slate-500 flex-shrink-0">
              {views.length}
            </span>
            <ChevronDownIcon className={`h-3.5 w-3.5 text-slate-400 flex-shrink-0 transition-transform duration-150 ${open ? 'rotate-180' : ''}`} />
          </Menu.Button>

          <Transition
            show={open}
            enter="transition ease-out duration-100"
            enterFrom="transform opacity-0 scale-95"
            enterTo="transform opacity-100 scale-100"
            leave="transition ease-in duration-75"
            leaveFrom="transform opacity-100 scale-100"
            leaveTo="transform opacity-0 scale-95"
          >
            <Menu.Items
              static
              className="absolute right-0 z-50 mt-1.5 w-[380px] bg-white rounded-xl shadow-xl border border-gray-200 focus:outline-none overflow-hidden"
            >
              {/* Search (show when 4+ views) */}
              {views.length >= 4 && (
                <div className="px-3 pt-3 pb-2 border-b border-gray-100">
                  <div className="relative">
                    <MagnifyingGlassIcon className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-400" />
                    <input
                      type="text"
                      placeholder="Search views…"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
                      autoFocus
                      onClick={(e) => e.stopPropagation()}
                    />
                  </div>
                </div>
              )}

              <div className="overflow-y-auto max-h-[380px] p-2 space-y-0.5">
                {filteredViews.length === 0 ? (
                  <div className="text-center py-8 text-slate-400">
                    <MagnifyingGlassIcon className="h-8 w-8 mx-auto mb-2 opacity-30" />
                    <p className="text-xs">No views match</p>
                  </div>
                ) : (
                  filteredViews.map((view) => (
                    <Menu.Item key={view.id}>
                      {({ active }) => (
                        <div
                          className={`
                            relative rounded-lg px-3 py-2.5 cursor-pointer group transition-all duration-150 border-l-2
                            ${selectedView?.id === view.id
                              ? 'bg-indigo-50 border-indigo-400'
                              : active
                                ? 'bg-slate-50 border-transparent'
                                : 'hover:bg-slate-50 border-transparent'
                            }
                          `}
                          onClick={() => handleSelectView(view)}
                        >

                          <div className="flex items-start justify-between gap-3">
                            {/* View content */}
                            <div className="flex-1 min-w-0">
                              {/* Title row */}
                              <div className="flex items-center gap-1.5 mb-0.5">
                                <p className={`text-[12.5px] font-semibold truncate ${selectedView?.id === view.id ? 'text-indigo-700' : 'text-slate-800'}`}>
                                  {view.name}
                                </p>
                                {view.is_default && (
                                  <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-amber-50 text-amber-600 rounded text-[9.5px] font-medium border border-amber-100 flex-shrink-0">
                                    <StarIconSolid className="h-2.5 w-2.5" />
                                    Default
                                  </span>
                                )}
                              </div>
                              
                              {view.description && (
                                <p className="text-[11px] text-slate-500 mb-1.5 line-clamp-1">{view.description}</p>
                              )}
                              
                              <div className="flex flex-wrap items-center gap-1">
                                {renderFilterTags(view)}
                              </div>
                            </div>
                            
                            <div className="flex items-center gap-0.5 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity mt-0.5">
                              {!view.is_default && (
                                <button
                                  onClick={(e) => handleSetDefault(view.id, view.name, e)}
                                  className="p-1 text-slate-300 hover:text-amber-500 hover:bg-amber-50 rounded transition-colors"
                                  title="Set as default"
                                >
                                  <StarIcon className="h-3.5 w-3.5" />
                                </button>
                              )}
                              <button
                                onClick={(e) => handleDeleteView(view.id, view.name, e)}
                                className="p-1 text-slate-300 hover:text-red-500 hover:bg-red-50 rounded transition-colors"
                                title="Delete"
                              >
                                <TrashIcon className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          </div>
                        </div>
                      )}
                    </Menu.Item>
                  ))
                )}
              </div>


            </Menu.Items>
          </Transition>
        </>
      )}
    </Menu>
  );

  // Helper function to render filter tags
  function renderFilterTags(view: SavedView) {
    const tags: JSX.Element[] = [];
    
    // Cost view filters
    if (view.view_type === 'cost' && view.filters.subscriptions) {
      tags.push(
        <span key="subs" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium">
          <RectangleStackIcon className="h-2.5 w-2.5" />
          {view.filters.subscriptions.length} sub{view.filters.subscriptions.length !== 1 ? 's' : ''}
        </span>
      );
      
      if (view.filters.resource_groups && Object.keys(view.filters.resource_groups).length > 0) {
        tags.push(
          <span key="rgs" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium">
            <FolderIcon className="h-2.5 w-2.5" />
            RG filter
          </span>
        );
      }
      
      if (view.filters.date_range) {
        tags.push(
          <span key="date" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-indigo-50 text-indigo-600 rounded text-[10px] font-medium">
            <CalendarIcon className="h-2.5 w-2.5" />
            {view.filters.date_range.replace(/_/g, ' ')}
          </span>
        );
      }
      
      if (view.filters.group_by) {
        tags.push(
          <span key="group" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium">
            <Squares2X2Icon className="h-2.5 w-2.5" />
            {view.filters.group_by.replace('Name', '')}
          </span>
        );
      }
    }
    
    // Query view filters
    if (view.view_type === 'query') {
      if (view.filters.subscription_id) {
        const subName = subscriptions.find(s => s.subscription_id === view.filters.subscription_id)?.display_name;
        tags.push(
          <span key="sub" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium truncate max-w-[140px]">
            <RectangleStackIcon className="h-2.5 w-2.5" />
            {subName?.split(' ')[0] || 'Sub'}
          </span>
        );
      }
      
      if (view.filters.resource_group) {
        tags.push(
          <span key="rg" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium truncate max-w-[110px]">
            <FolderIcon className="h-2.5 w-2.5" />
            {view.filters.resource_group}
          </span>
        );
      }
      
      if (view.filters.resource_type) {
        tags.push(
          <span key="type" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium truncate max-w-[100px]">
            <CubeIcon className="h-2.5 w-2.5" />
            {view.filters.resource_type.split('/').pop()}
          </span>
        );
      }
      
      if (view.filters.resource) {
        tags.push(
          <span key="resource" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium truncate max-w-[110px]">
            <ServerIcon className="h-2.5 w-2.5" />
            {view.filters.resource}
          </span>
        );
      }
      
      if (view.filters.global_scope) {
        tags.push(
          <span key="global" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-indigo-50 text-indigo-600 rounded text-[10px] font-medium">
            <GlobeAltIcon className="h-2.5 w-2.5" />
            Global
          </span>
        );
      }
    }
    
    // Dashboard view filters
    if (view.view_type === 'dashboard') {
      if (view.filters.subscriptions && view.filters.subscriptions.length > 0) {
        tags.push(
          <span key="subs" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium">
            <RectangleStackIcon className="h-2.5 w-2.5" />
            {view.filters.subscriptions.length} sub{view.filters.subscriptions.length !== 1 ? 's' : ''}
          </span>
        );
      }
      
      if (view.filters.locations && view.filters.locations.length > 0) {
        tags.push(
          <span key="locs" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium">
            <MapPinIcon className="h-2.5 w-2.5" />
            {view.filters.locations.length} region{view.filters.locations.length !== 1 ? 's' : ''}
          </span>
        );
      }
      
      if (view.filters.search_query) {
        tags.push(
          <span key="search" className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[10px] font-medium truncate max-w-[130px]">
            <MagnifyingGlassIcon className="h-2.5 w-2.5" />
            "{view.filters.search_query.substring(0, 15)}{view.filters.search_query.length > 15 ? '…' : ''}"
          </span>
        );
      }
    }
    
    // If no tags, show "No filters"
    if (tags.length === 0) {
      tags.push(
        <span key="none" className="text-[10px] text-slate-400 italic">No filters</span>
      );
    }
    
    return tags;
  }
}

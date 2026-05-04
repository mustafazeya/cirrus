/**
 * Save View Dialog Component
 * 
 * Modal dialog for saving current filters as a reusable view
 */

'use client';

import { useState, useEffect } from 'react';
import { Dialog } from '@headlessui/react';
import { XMarkIcon, BookmarkIcon, RectangleStackIcon, FolderIcon, CubeIcon, ServerIcon, GlobeAltIcon, CalendarIcon, Squares2X2Icon, MapPinIcon } from '@heroicons/react/24/outline';
import toast from 'react-hot-toast';
import { apiService } from '../services/apiService';
import { useAppStore } from '../store/appStore';
import type { SavedViewFilters } from '../types/savedViews';

interface SaveViewDialogProps {
  isOpen: boolean;
  onClose: () => void;
  currentFilters: SavedViewFilters;
  viewType: 'cost' | 'dashboard' | 'query';
  onSaved?: () => void;
}

export default function SaveViewDialog({
  isOpen,
  onClose,
  currentFilters,
  viewType,
  onSaved
}: SaveViewDialogProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isDefault, setIsDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  
  // Get subscriptions from store to look up names
  const subscriptions = useAppStore((state) => state.subscriptions);

  // Reset form each time dialog opens
  useEffect(() => {
    if (isOpen) {
      setName('');
      setDescription('');
      setIsDefault(false);
    }
  }, [isOpen]);

  const handleSave = async () => {
    if (!name.trim()) {
      toast.error('Please enter a view name');
      return;
    }

    console.log(' Saving view with filters:', currentFilters);
    console.log(' Resource groups being saved:', currentFilters.resource_groups);

    setSaving(true);
    try {
      await apiService.createSavedView({
        name: name.trim(),
        description: description.trim() || null,
        view_type: viewType,
        filters: currentFilters,
        is_default: isDefault,
        is_shared: false
      });

      toast.success(`View “${name}” saved!`);
      setName('');
      setDescription('');
      setIsDefault(false);
      onClose();
      if (onSaved) onSaved();
    } catch (error) {
      console.error('Failed to save view:', error);
      toast.error('Failed to save view');
    } finally {
      setSaving(false);
    }
  };

  // Build filter chips for the preview section
  const buildFilterChips = () => {
    const chips: { icon: JSX.Element; label: string }[] = [];
    if (viewType === 'query') {
      if (currentFilters.global_scope) {
        chips.push({ icon: <GlobeAltIcon className="h-3 w-3" />, label: 'Global scope' });
      } else if (currentFilters.subscription_id) {
        const sub = subscriptions.find(s => s.subscription_id === currentFilters.subscription_id);
        chips.push({ icon: <RectangleStackIcon className="h-3 w-3" />, label: sub?.display_name || currentFilters.subscription_id });
      } else {
        chips.push({ icon: <RectangleStackIcon className="h-3 w-3" />, label: 'No subscription' });
      }
      if (currentFilters.resource_group) chips.push({ icon: <FolderIcon className="h-3 w-3" />, label: currentFilters.resource_group });
      if (currentFilters.resource_type) chips.push({ icon: <CubeIcon className="h-3 w-3" />, label: currentFilters.resource_type.split('/').pop() || '' });
      if (currentFilters.resource) chips.push({ icon: <ServerIcon className="h-3 w-3" />, label: currentFilters.resource });
    }
    if (viewType === 'cost' && currentFilters.subscriptions) {
      chips.push({ icon: <RectangleStackIcon className="h-3 w-3" />, label: `${currentFilters.subscriptions.length} subscription${currentFilters.subscriptions.length !== 1 ? 's' : ''}` });
      if (currentFilters.resource_groups && Object.keys(currentFilters.resource_groups).length > 0)
        chips.push({ icon: <FolderIcon className="h-3 w-3" />, label: 'RG filter active' });
      if (currentFilters.date_range) chips.push({ icon: <CalendarIcon className="h-3 w-3" />, label: currentFilters.date_range.replace(/_/g, ' ') });
      if (currentFilters.group_by) chips.push({ icon: <Squares2X2Icon className="h-3 w-3" />, label: `Group: ${currentFilters.group_by.replace('Name', '')}` });
    }
    if (viewType === 'dashboard' && currentFilters.subscriptions) {
      chips.push({ icon: <RectangleStackIcon className="h-3 w-3" />, label: `${currentFilters.subscriptions.length} subscription${currentFilters.subscriptions.length !== 1 ? 's' : ''}` });
      if (currentFilters.locations?.length) chips.push({ icon: <MapPinIcon className="h-3 w-3" />, label: `${currentFilters.locations.length} region${currentFilters.locations.length !== 1 ? 's' : ''}` });
      if (currentFilters.search_query) chips.push({ icon: <FolderIcon className="h-3 w-3" />, label: `“${currentFilters.search_query}”` });
    }
    return chips;
  };
  const filterChips = buildFilterChips();

  return (
    <Dialog open={isOpen} onClose={onClose} className="relative z-50">
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/20 backdrop-blur-[2px]" aria-hidden="true" />

      {/* Dialog panel */}
      <div className="fixed inset-0 flex items-center justify-center p-4">
        <Dialog.Panel className="mx-auto max-w-md w-full bg-white rounded-2xl shadow-2xl border border-gray-100">
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center flex-shrink-0">
                <BookmarkIcon className="h-4 w-4 text-indigo-600" />
              </div>
              <Dialog.Title className="text-sm font-semibold text-slate-900">Save View</Dialog.Title>
            </div>
            <button onClick={onClose} className="p-1.5 hover:bg-slate-100 rounded-lg transition-colors">
              <XMarkIcon className="h-4 w-4 text-slate-400" />
            </button>
          </div>

          {/* Body */}
          <div className="px-5 py-4 space-y-4">
            <div>
              <label htmlFor="view-name" className="block text-xs font-semibold text-slate-700 mb-1.5">
                Name <span className="text-red-400">*</span>
              </label>
              <input
                id="view-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && name.trim()) handleSave(); }}
                placeholder="e.g., Production — Monthly Review"
                maxLength={100}
                autoFocus
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
              />
              <p className="mt-1 text-[10.5px] text-slate-400 text-right">{name.length}/100</p>
            </div>

            <div>
              <label htmlFor="view-description" className="block text-xs font-semibold text-slate-700 mb-1.5">
                Description
              </label>
              <textarea
                id="view-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Optional description…"
                maxLength={500}
                rows={2}
                className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none resize-none"
              />
            </div>

            {/* Filter preview */}
            <div>
              <p className="text-[10.5px] font-semibold text-slate-400 uppercase tracking-wide mb-2">Filters being saved</p>
              {filterChips.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {filterChips.map((chip, i) => (
                    <span key={i} className="inline-flex items-center gap-1 px-2 py-1 bg-slate-100 text-slate-700 rounded-md text-xs font-medium">
                      {chip.icon}
                      {chip.label}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-slate-400 italic border border-dashed border-slate-200 rounded-lg px-3 py-2 text-center">
                  No active filters — this view has no scope restriction.
                </p>
              )}
            </div>

            {/* Default toggle */}
            <button
              type="button"
              onClick={() => setIsDefault(!isDefault)}
              className="w-full flex items-center justify-between gap-3 px-3 py-3 rounded-xl bg-slate-50 border border-slate-100 hover:bg-slate-100/60 transition-colors text-left"
            >
              <div>
                <p className="text-xs font-semibold text-slate-700">Set as default view</p>
                <p className="text-[10.5px] text-slate-400 mt-0.5">Load automatically when you open this page</p>
              </div>
              <div className={`relative flex-shrink-0 w-9 h-5 rounded-full transition-colors ${isDefault ? 'bg-indigo-600' : 'bg-gray-200'}`}>
                <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow-sm transition-transform ${isDefault ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </div>
            </button>
          </div>

          {/* Footer */}
          <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-100">
            <button
              onClick={onClose}
              disabled={saving}
              className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving || !name.trim()}
              className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed rounded-lg transition-colors flex items-center gap-1.5"
            >
              {saving ? (
                <>
                  <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  Saving…
                </>
              ) : (
                <>
                  <BookmarkIcon className="h-3.5 w-3.5" />
                  Save View
                </>
              )}
            </button>
          </div>
        </Dialog.Panel>
      </div>
    </Dialog>
  );
}

'use client';

import { useState, useEffect } from 'react';
import { 
  MagnifyingGlassIcon, 
  XMarkIcon,
  BookmarkIcon,
  GlobeAltIcon,
  LockClosedIcon,
  CogIcon,
  FunnelIcon,
  SparklesIcon,
  ArrowDownTrayIcon,
  FireIcon
} from '@heroicons/react/24/outline';
import { BookmarkIcon as BookmarkSolidIcon } from '@heroicons/react/24/solid';
import { useMsal } from '@azure/msal-react';
import { apiService } from '../services/apiService';
import { useAppStore } from '../store/appStore';
import toast from 'react-hot-toast';
import type { QueryTemplateListItem, TemplateCategory, QueryType } from '../types/templates';
import { TEMPLATE_CATEGORIES_WITH_ALL, getCategoryIcon } from '../constants/templateCategories';

interface TemplateBrowserProps {
  onSelectTemplate: (template: QueryTemplateListItem) => void;
  onClose: () => void;
  currentQuery?: string; // To enable "Save as Template"
  onSaveAsTemplate?: () => void;
}

export default function TemplateBrowser({ 
  onSelectTemplate, 
  onClose,
  currentQuery,
  onSaveAsTemplate
}: TemplateBrowserProps) {
  const { user } = useAppStore();
  const { accounts } = useMsal();
  const [templates, setTemplates] = useState<QueryTemplateListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<TemplateCategory | 'all'>('all');
  const [visibilityFilter, setVisibilityFilter] = useState<'all' | 'system' | 'public' | 'private'>('all');
  const [isAdmin, setIsAdmin] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [confirmDeleteName, setConfirmDeleteName] = useState('');

  const categories = TEMPLATE_CATEGORIES_WITH_ALL;

  // Check admin status from Azure AD App Roles in the MSAL ID token
  useEffect(() => {
    const roles = (accounts?.[0]?.idTokenClaims as { roles?: string[] })?.roles ?? [];
    setIsAdmin(roles.includes('Admin'));
  }, [user, accounts]);

  const loadTemplates = async (bustCache: boolean = false) => {
    try {
      setLoading(true);
      const response = await apiService.getTemplates(true, bustCache);
      setTemplates(response.templates || []);
    } catch (error) {
      console.error('Failed to load templates:', error);
      toast.error('Failed to load templates');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTemplates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filterTemplates = () => {
    const sourceTemplates = templates;
    let filtered = [...sourceTemplates];

    // Search filter
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(t => 
        t.name.toLowerCase().includes(query) ||
        t.description.toLowerCase().includes(query) ||
        t.tags.some(tag => tag.toLowerCase().includes(query))
      );
    }

    // Category filter
    if (selectedCategory !== 'all') {
      filtered = filtered.filter(t => t.category === selectedCategory);
    }

    // Visibility filter
    if (visibilityFilter === 'system') {
      filtered = filtered.filter(t => t.is_system);
    } else if (visibilityFilter === 'public') {
      filtered = filtered.filter(t => t.is_public && !t.is_system);
    } else if (visibilityFilter === 'private') {
      filtered = filtered.filter(t => !t.is_public && !t.is_system);
    }
    // 'all' shows everything

    // Sort: System templates first, then by usage count, then by name
    filtered.sort((a, b) => {
      if (a.is_system && !b.is_system) return -1;
      if (!a.is_system && b.is_system) return 1;
      if (a.use_count !== b.use_count) return b.use_count - a.use_count;
      return a.name.localeCompare(b.name);
    });

    return filtered;
  };

  // Compute filtered templates directly instead of storing in state
  const filteredTemplates = filterTemplates();

  const handleSelectTemplate = (template: QueryTemplateListItem) => {
    onSelectTemplate(template);
    toast.success(`Using template: ${template.name}`);
  };

  const handleDeleteTemplate = (templateId: string, templateName: string) => {
    setConfirmDeleteId(templateId);
    setConfirmDeleteName(templateName);
  };

  const handleConfirmDelete = async () => {
    if (!confirmDeleteId) return;
    const templateId = confirmDeleteId;
    setConfirmDeleteId(null);

    // Store original templates for rollback on error
    const originalTemplates = [...templates];
    
    try {
      // Optimistic update: Remove template from UI immediately
      setTemplates(prevTemplates => prevTemplates.filter(t => t.id !== templateId));
      
      // Perform actual deletion
      await apiService.deleteTemplate(templateId);
      toast.success('Template deleted successfully');
      
      // Force refresh with cache-busting to ensure we have latest data
      // This handles edge cases with CDN/cache delays
      await loadTemplates(true);
    } catch (error: any) {
      console.error('Failed to delete template:', error);
      // Rollback optimistic update on error
      setTemplates(originalTemplates);
      const errorMessage = error?.response?.data?.detail || 'Failed to delete template';
      toast.error(errorMessage);
    }
  };

  const handleImportTemplate = async (template: QueryTemplateListItem) => {
    try {
      await apiService.createTemplate({
        name: `${template.name} (imported)`,
        description: template.description,
        query_type: template.query_type,
        query: template.query,
        category: template.category,
        tags: [...template.tags, 'imported']
      });
      toast.success(`Imported: ${template.name}`);
      loadTemplates();
    } catch (error) {
      console.error('Failed to import template:', error);
      toast.error('Failed to import template');
    }
  };

  const getQueryTypeLabel = (type: QueryType) => {
    switch (type) {
      case 'natural_language': return 'Natural Language';
      case 'kql': return 'KQL';
      case 'resource_graph': return 'Resource Graph';
      case 'cost_analysis': return 'Cost Analysis';
      case 'metrics': return 'Metrics';
      default: return type;
    }
  };

  const systemTemplates = filteredTemplates.filter(t => t.is_system);
  const publicTemplates = filteredTemplates.filter(t => t.is_public && !t.is_system);
  const privateTemplates = filteredTemplates.filter(t => !t.is_public && !t.is_system);

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-2 sm:p-4">
      <div className="relative bg-white rounded-2xl shadow-2xl border border-gray-100 max-w-5xl w-full max-h-[94vh] sm:max-h-[90vh] flex flex-col">

        {/* Confirm Delete overlay */}
        {confirmDeleteId && (
          <div className="absolute inset-0 bg-black/40 flex items-center justify-center rounded-2xl z-10">
            <div className="bg-white rounded-xl shadow-2xl p-6 max-w-sm w-full mx-4 border border-gray-100">
              <h3 className="text-sm font-semibold text-slate-900 mb-1">Delete template?</h3>
              <p className="text-sm text-slate-600 mb-5">
                <span className="font-medium">“{confirmDeleteName}”</span> will be permanently removed.
              </p>
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setConfirmDeleteId(null)}
                  className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmDelete}
                  className="px-4 py-2 text-sm font-semibold text-white bg-red-600 hover:bg-red-700 rounded-lg transition-colors"
                >
                  Delete
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Header */}
        <div className="flex-shrink-0 border-b border-gray-100">
          <div className="flex items-center justify-between px-5 py-4">
            <div className="flex items-center gap-3 min-w-0 flex-1">
              <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center flex-shrink-0">
                <BookmarkSolidIcon className="h-4 w-4 text-indigo-600" />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-slate-900">AI Templates</h2>
                <p className="text-[11px] text-slate-400 hidden sm:block">{templates.length} template{templates.length !== 1 ? 's' : ''} available</p>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {currentQuery && onSaveAsTemplate && (
                <button
                  onClick={onSaveAsTemplate}
                  className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-xs font-semibold transition-colors"
                >
                  <BookmarkIcon className="h-3.5 w-3.5" />
                  Save as Template
                </button>
              )}
              <button
                onClick={onClose}
                className="p-1.5 hover:bg-slate-100 rounded-lg transition-colors"
              >
                <XMarkIcon className="h-4 w-4 text-slate-400" />
              </button>
            </div>
          </div>
        </div>

        {/* Filters */}
        <div className="flex-shrink-0 px-5 py-3 border-b border-gray-100 bg-slate-50/50 space-y-3">
          {/* Search */}
          <div className="relative">
            <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400" />
            <input
              type="text"
              placeholder="Search templates…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-9 pr-4 py-2 text-sm bg-white border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
            />
          </div>

          {/* Category chips + visibility toggle */}
          <div className="flex flex-wrap items-center gap-2">
            {/* Category pills */}
            <div className="flex items-center gap-1.5 flex-wrap flex-1">
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setSelectedCategory(cat as TemplateCategory | 'all')}
                  className={`flex-shrink-0 px-2.5 py-1 rounded-full text-xs font-medium transition-all ${
                    selectedCategory === cat
                      ? 'bg-indigo-600 text-white shadow-sm'
                      : 'bg-white border border-gray-200 text-slate-600 hover:border-indigo-300 hover:text-indigo-600'
                  }`}
                >
                  {cat === 'all' ? 'All' : (() => { const Icon = getCategoryIcon(cat as TemplateCategory); return <><Icon className="w-3 h-3 inline mr-1" />{cat}</>; })()}
                </button>
              ))}
            </div>

            {/* Visibility toggle pills */}
            <div className="flex items-center bg-slate-100 rounded-lg p-0.5 gap-0.5 flex-shrink-0">
              {(['all', 'system', 'public', 'private'] as const).map(v => (
                <button
                  key={v}
                  onClick={() => setVisibilityFilter(v)}
                  className={`px-2.5 py-1 text-xs font-medium rounded-md transition-all capitalize ${
                    visibilityFilter === v
                      ? 'bg-white text-slate-700 shadow-sm'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {v}
                </button>
              ))}
            </div>

            <span className="text-xs text-slate-400 flex-shrink-0">
              {filteredTemplates.length} result{filteredTemplates.length !== 1 ? 's' : ''}
            </span>
          </div>
        </div>

        {/* Template List */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {loading ? (
            <div className="flex items-center justify-center h-64">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
            </div>
          ) : filteredTemplates.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-slate-400">
              <BookmarkIcon className="h-12 w-12 mb-3 opacity-30" />
              <p className="text-sm font-medium text-slate-500">No templates found</p>
              <p className="text-xs text-center mt-1 max-w-xs">
                Try adjusting your filters or save a query as a new template
              </p>
            </div>
          ) : (
            <div className="space-y-6">
              {/* System Templates */}
              {systemTemplates.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3 flex items-center gap-2">
                    <CogIcon className="h-3.5 w-3.5" />
                    System ({systemTemplates.length})
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {systemTemplates.map(template => (
                      <TemplateCard
                        key={template.id}
                        template={template}
                        onSelect={handleSelectTemplate}
                        onDelete={handleDeleteTemplate}
                        isAdmin={isAdmin}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Public Templates */}
              {publicTemplates.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3 flex items-center gap-2">
                    <GlobeAltIcon className="h-3.5 w-3.5" />
                    Team shared ({publicTemplates.length})
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {publicTemplates.map(template => (
                      <TemplateCard
                        key={template.id}
                        template={template}
                        onSelect={handleSelectTemplate}
                        onDelete={handleDeleteTemplate}
                        isAdmin={isAdmin}
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Private Templates */}
              {privateTemplates.length > 0 && (
                <div>
                  <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3 flex items-center gap-2">
                    <LockClosedIcon className="h-3.5 w-3.5" />
                    My templates ({privateTemplates.length})
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {privateTemplates.map(template => (
                      <TemplateCard
                        key={template.id}
                        template={template}
                        onSelect={handleSelectTemplate}
                        onDelete={handleDeleteTemplate}
                        isAdmin={isAdmin}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Template Card Component
interface TemplateCardProps {
  template: QueryTemplateListItem;
  onSelect: (template: QueryTemplateListItem) => void;
  onDelete: (id: string, name: string) => void;
  isAdmin: boolean;
}

function TemplateCard({ template, onSelect, onDelete, isAdmin }: TemplateCardProps) {
  const getVisibilityIcon = () => {
    if (template.is_system) return <CogIcon className="h-4 w-4 text-blue-600" />;
    if (template.is_public) return <GlobeAltIcon className="h-4 w-4 text-green-600" />;
    return <LockClosedIcon className="h-4 w-4 text-gray-600" />;
  };

  // Determine if delete button should be shown:
  // - System templates cannot be deleted by anyone
  // - Private templates can be deleted by owner (handled by backend)
  // - Public templates can be deleted by owner or admin
  const canDelete = !template.is_system && (!template.is_public || isAdmin);

  return (
    <div className="border border-gray-200 rounded-xl p-4 hover:shadow-md hover:border-indigo-200 transition-all bg-white group">
      {/* Header */}
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="w-6 h-6 flex items-center justify-center bg-indigo-50 rounded-md flex-shrink-0">{(() => { const Icon = getCategoryIcon(template.category); return <Icon className="w-3.5 h-3.5 text-indigo-500" />; })()}</span>
          <h4 className="text-[12.5px] font-semibold text-slate-900 line-clamp-1">{template.name}</h4>
        </div>
        {getVisibilityIcon()}
      </div>

      {/* Description */}
      <p className="text-xs text-slate-500 mb-3 line-clamp-2 min-h-[32px]">
        {template.description}
      </p>

      {/* Metadata */}
      <div className="flex items-center gap-1.5 mb-3 flex-wrap">
        <span className="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-[10px] font-medium capitalize">{template.category}</span>
        <span className="bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded text-[10px] font-medium">
          {template.query_type === 'natural_language' ? 'Natural' : template.query_type}
        </span>
        {template.use_count > 0 && (
          <span className="flex items-center gap-0.5 text-[10px] text-slate-400">
            <SparklesIcon className="h-2.5 w-2.5" />
            {template.use_count}
          </span>
        )}
      </div>

      {/* Tags */}
      {template.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {template.tags.slice(0, 3).map(tag => (
            <span key={tag} className="text-[10px] bg-slate-50 text-slate-500 border border-slate-200 px-1.5 py-0.5 rounded">
              {tag}
            </span>
          ))}
          {template.tags.length > 3 && (
            <span className="text-[10px] text-slate-400">+{template.tags.length - 3}</span>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-1.5">
        <button
          onClick={() => onSelect(template)}
          className="flex-1 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold transition-colors"
        >
          Use Template
        </button>
        {canDelete && (
          <button
            onClick={() => onDelete(template.id, template.name)}
            className="px-3 py-1.5 bg-red-50 text-red-600 rounded-lg hover:bg-red-100 text-xs font-medium transition-colors border border-red-100"
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}

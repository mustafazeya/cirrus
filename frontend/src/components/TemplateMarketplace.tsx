'use client';

import { useState, useEffect } from 'react';
import { 
  MagnifyingGlassIcon,
  XMarkIcon,
  GlobeAltIcon,
  FireIcon,
  ClockIcon,
  StarIcon,
  ArrowDownTrayIcon,
  FunnelIcon
} from '@heroicons/react/24/outline';
import { StarIcon as StarSolidIcon } from '@heroicons/react/24/solid';
import { apiService } from '../services/apiService';
import toast from 'react-hot-toast';
import type { QueryTemplateListItem, TemplateCategory } from '../types/templates';
import { TEMPLATE_CATEGORIES_WITH_ALL, getCategoryIcon } from '../constants/templateCategories';

interface TemplateMarketplaceProps {
  onClose: () => void;
  onImportTemplate: (template: QueryTemplateListItem) => void;
}

export default function TemplateMarketplace({ onClose, onImportTemplate }: TemplateMarketplaceProps) {
  const [templates, setTemplates] = useState<QueryTemplateListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [sortBy, setSortBy] = useState<'popular' | 'trending' | 'recent'>('popular');
  const [selectedCategory, setSelectedCategory] = useState<TemplateCategory | 'all'>('all');
  const [searchQuery, setSearchQuery] = useState('');

  const categories = TEMPLATE_CATEGORIES_WITH_ALL;

  useEffect(() => {
    loadMarketplace();
  }, [sortBy, selectedCategory]);

  const loadMarketplace = async () => {
    try {
      setLoading(true);
      const params: any = {
        sort: sortBy,
        limit: 50
      };
      
      if (selectedCategory !== 'all') {
        params.category = selectedCategory;
      }

      const response = await apiService.request({
        method: 'GET',
        url: '/api/templates/marketplace',
        params
      });

      setTemplates(response.templates || []);
    } catch (error) {
      console.error('Failed to load marketplace:', error);
      toast.error('Failed to load marketplace');
    } finally {
      setLoading(false);
    }
  };

  const handleImport = async (template: QueryTemplateListItem) => {
    try {
      // Create a copy in user's templates
      await apiService.createTemplate({
        name: `${template.name} (imported)`,
        description: template.description,
        query_type: template.query_type,
        query: template.query,
        category: template.category,
        tags: [...template.tags, 'imported']
      });

      toast.success(`Imported: ${template.name}`);
      onImportTemplate(template);
    } catch (error) {
      console.error('Failed to import template:', error);
      toast.error('Failed to import template');
    }
  };

  const handleExport = async (templateId: string, templateName: string) => {
    try {
      const response = await apiService.request({
        method: 'GET',
        url: `/api/templates/export/${templateId}`
      });

      // Download as JSON file
      const blob = new Blob([JSON.stringify(response, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${templateName.replace(/\s+/g, '_')}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      toast.success('Template exported');
    } catch (error) {
      console.error('Failed to export template:', error);
      toast.error('Failed to export template');
    }
  };

  const filteredTemplates = templates.filter(t => {
    if (!searchQuery) return true;
    const query = searchQuery.toLowerCase();
    return (
      t.name.toLowerCase().includes(query) ||
      t.description.toLowerCase().includes(query) ||
      t.tags.some(tag => tag.toLowerCase().includes(query))
    );
  });

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-2xl max-w-6xl w-full max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b bg-gradient-to-r from-blue-600 to-indigo-600">
          <div className="flex items-center gap-3">
            <GlobeAltIcon className="h-6 w-6 text-white" />
            <div>
              <h2 className="text-2xl font-bold text-white">Template Marketplace</h2>
              <p className="text-sm text-blue-100">Discover and import community templates</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-white/20 rounded-lg transition-colors"
          >
            <XMarkIcon className="h-6 w-6 text-white" />
          </button>
        </div>

        {/* Filters */}
        <div className="p-4 border-b bg-gray-50 space-y-3">
          {/* Search */}
          <div className="relative">
            <MagnifyingGlassIcon className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-gray-400" />
            <input
              type="text"
              placeholder="Search templates..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>

          {/* Sort and Filter Row */}
          <div className="flex flex-wrap gap-3 items-center">
            {/* Sort buttons */}
            <div className="flex items-center gap-2">
              <FunnelIcon className="h-4 w-4 text-gray-500" />
              <button
                onClick={() => setSortBy('popular')}
                className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                  sortBy === 'popular'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-300'
                }`}
              >
                <FireIcon className="h-4 w-4 inline mr-1" />
                Popular
              </button>
              <button
                onClick={() => setSortBy('trending')}
                className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${
                  sortBy === 'trending'
                    ? 'bg-blue-600 text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-100 border border-gray-300'
                }`}
              >
                <StarIcon className="h-4 w-4 inline mr-1" />
                Trending
              </button>
            </div>

            {/* Category filter */}
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value as TemplateCategory | 'all')}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500"
            >
              <option value="all">All Categories</option>
              {categories.filter(c => c !== 'all').map(cat => (
                <option key={cat} value={cat}>
                  {getCategoryIcon(cat as TemplateCategory)} {cat}
                </option>
              ))}
            </select>

            <span className="text-sm text-gray-500">
              {filteredTemplates.length} templates
            </span>
          </div>
        </div>

        {/* Templates Grid */}
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <div className="flex items-center justify-center h-64">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
            </div>
          ) : filteredTemplates.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-64 text-gray-500">
              <GlobeAltIcon className="h-16 w-16 mb-4" />
              <p className="text-lg font-medium">No templates found</p>
              <p className="text-sm">Try adjusting your filters</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {filteredTemplates.map((template) => (
                <div
                  key={template.id}
                  className="border border-gray-200 rounded-lg p-4 hover:shadow-lg transition-shadow bg-white"
                >
                  {/* Header */}
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2 flex-1">
                      <span className="text-2xl">{getCategoryIcon(template.category)}</span>
                      <h4 className="font-semibold text-gray-900 line-clamp-1">{template.name}</h4>
                    </div>
                    {template.is_system && (
                      <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded">System</span>
                    )}
                  </div>

                  {/* Description */}
                  <p className="text-sm text-gray-600 mb-3 line-clamp-2 min-h-[40px]">
                    {template.description}
                  </p>

                  {/* Metadata */}
                  <div className="flex items-center gap-2 mb-3 text-xs text-gray-500">
                    <span className="bg-gray-100 px-2 py-1 rounded capitalize">{template.category}</span>
                    <span className="flex items-center gap-1">
                      <FireIcon className="h-3 w-3" />
                      {template.use_count} uses
                    </span>
                  </div>

                  {/* Tags */}
                  {template.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-3">
                      {template.tags.slice(0, 3).map(tag => (
                        <span key={tag} className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded">
                          {tag}
                        </span>
                      ))}
                      {template.tags.length > 3 && (
                        <span className="text-xs text-gray-500">+{template.tags.length - 3}</span>
                      )}
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleImport(template)}
                      className="flex-1 px-3 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium transition-colors flex items-center justify-center gap-2"
                    >
                      <ArrowDownTrayIcon className="h-4 w-4" />
                      Import
                    </button>
                    <button
                      onClick={() => handleExport(template.id, template.name)}
                      className="px-3 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 text-sm font-medium transition-colors"
                      title="Export as JSON"
                    >
                      <ArrowDownTrayIcon className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

'use client';

import { useState } from 'react';
import { XMarkIcon } from '@heroicons/react/24/outline';
import { apiService } from '../services/apiService';
import toast from 'react-hot-toast';
import type { CreateTemplateRequest, TemplateCategory, QueryType } from '../types/templates';
import { TEMPLATE_CATEGORIES, getCategoryIcon } from '../constants/templateCategories';

interface SaveTemplateModalProps {
  query: string;
  queryType?: QueryType;
  onClose: () => void;
  onSaved: () => void;
}

export default function SaveTemplateModal({ 
  query, 
  queryType = 'natural_language',
  onClose, 
  onSaved 
}: SaveTemplateModalProps) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<TemplateCategory>('custom');
  const [tags, setTags] = useState('');
  const [isPublic, setIsPublic] = useState(false);
  const [saving, setSaving] = useState(false);

  const categories = TEMPLATE_CATEGORIES;

  const handleSave = async () => {
    if (!name.trim()) {
      toast.error('Please enter a template name');
      return;
    }

    if (!description.trim()) {
      toast.error('Please enter a description');
      return;
    }

    try {
      setSaving(true);

      const templateData: CreateTemplateRequest = {
        name: name.trim(),
        description: description.trim(),
        query_type: queryType,
        query: query,
        category,
        tags: tags.split(',').map(t => t.trim()).filter(t => t.length > 0),
        is_public: isPublic
      };

      await apiService.createTemplate(templateData);
      if (isPublic) {
        toast.success('Template saved and shared with the team');
      } else {
        toast.success('Template saved successfully!');
      }
      
      onSaved();
      onClose();
    } catch (error) {
      console.error('Failed to save template:', error);
      toast.error('Failed to save template');
    } finally {
      setSaving(false);
    }
  };


  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-2 sm:p-4">
      <div className="bg-white rounded-2xl shadow-2xl border border-gray-100 max-w-lg w-full max-h-[94vh] sm:max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-50 flex items-center justify-center flex-shrink-0">
              <XMarkIcon className="h-4 w-4 text-indigo-600 hidden" />
              <span className="text-base">&#128278;</span>
            </div>
            <h2 className="text-sm font-semibold text-slate-900">Save as Template</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <XMarkIcon className="h-4 w-4 text-slate-400" />
          </button>
        </div>

        {/* Form */}
        <div className="px-5 py-4 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5">
              Name <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., Monthly Cost Report"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
              maxLength={100}
              autoFocus
            />
            <p className="text-[10.5px] text-slate-400 mt-1 text-right">{name.length}/100</p>
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5">
              Description <span className="text-red-400">*</span>
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Describe what this template does…"
              rows={2}
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none resize-none"
              maxLength={500}
            />
            <p className="text-[10.5px] text-slate-400 mt-1 text-right">{description.length}/500</p>
          </div>

          {/* Category */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-2">Category</label>
            <div className="flex flex-wrap gap-1.5">
              {categories.map(cat => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setCategory(cat)}
                  className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-all ${
                    category === cat
                      ? 'bg-indigo-600 text-white border-indigo-600 shadow-sm'
                      : 'bg-white text-slate-600 border-gray-200 hover:border-indigo-300 hover:text-indigo-600'
                  }`}
                >
                  {(() => { const Icon = getCategoryIcon(cat); return <><Icon className="w-3 h-3 inline mr-1" />{cat}</>; })()}
                </button>
              ))}
            </div>
          </div>

          {/* Tags */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5">Tags</label>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="monthly, cost, analysis (comma-separated)"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent outline-none"
            />
          </div>

          {/* Share with Team toggle */}
          <button
            type="button"
            onClick={() => setIsPublic(!isPublic)}
            className="w-full flex items-center justify-between gap-3 px-3 py-3 rounded-xl bg-slate-50 border border-slate-100 hover:bg-slate-100/60 transition-colors text-left"
          >
            <div>
              <p className="text-xs font-semibold text-slate-700">
                Share with team
                {isPublic && <span className="ml-2 text-[10px] bg-green-100 text-green-700 px-1.5 py-0.5 rounded">Public</span>}
              </p>
              <p className="text-[10.5px] text-slate-400 mt-0.5">
                {isPublic ? 'Visible to everyone in your organisation' : 'Only visible to you'}
              </p>
            </div>
            <div className={`relative flex-shrink-0 w-9 h-5 rounded-full transition-colors ${isPublic ? 'bg-indigo-600' : 'bg-gray-200'}`}>
              <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow-sm transition-transform ${isPublic ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
            </div>
          </button>

          {/* Query Preview */}
          <div>
            <label className="block text-xs font-semibold text-slate-700 mb-1.5">Query Preview</label>
            <div className="bg-slate-50 border border-gray-200 rounded-lg p-3">
              <p className="text-xs text-slate-700 font-mono line-clamp-3 leading-relaxed">{query}</p>
            </div>
            <p className="text-[10.5px] text-slate-400 mt-1">
              Type: <span className="font-medium">{queryType}</span>
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 rounded-lg transition-colors"
            disabled={saving}
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !name.trim() || !description.trim()}
            className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400 disabled:cursor-not-allowed rounded-lg transition-colors flex items-center gap-1.5"
          >
            {saving ? (
              <>
                <div className="animate-spin rounded-full h-3.5 w-3.5 border-b-2 border-white"></div>
                Saving…
              </>
            ) : (
              'Save Template'
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

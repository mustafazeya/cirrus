/**
 * Template Categories - Centralized constants and utilities
 * Single source of truth for template categories
 */
import type React from 'react';
import {
  CurrencyDollarIcon,
  ShieldCheckIcon,
  ChartBarIcon,
  ServerStackIcon,
  DocumentTextIcon,
  ScaleIcon,
  StarIcon,
  DocumentIcon,
} from '@heroicons/react/24/outline';
import type { TemplateCategory } from '../types/templates';

/**
 * All available template categories
 */
export const TEMPLATE_CATEGORIES: TemplateCategory[] = [
  'cost',
  'security',
  'performance',
  'resources',
  'logs',
  'governance',
  'custom'
];

/**
 * Template categories with 'all' option for filtering
 */
export const TEMPLATE_CATEGORIES_WITH_ALL: (TemplateCategory | 'all')[] = [
  'all',
  ...TEMPLATE_CATEGORIES
];

/**
 * Get the Heroicon component for a template category
 */
export function getCategoryIcon(category: TemplateCategory | string): React.ComponentType<{ className?: string }> {
  switch (category) {
    case 'cost':        return CurrencyDollarIcon;
    case 'security':    return ShieldCheckIcon;
    case 'performance': return ChartBarIcon;
    case 'resources':   return ServerStackIcon;
    case 'logs':        return DocumentTextIcon;
    case 'governance':  return ScaleIcon;
    case 'custom':      return StarIcon;
    default:            return DocumentIcon;
  }
}

/**
 * Get a human-readable label for a category
 */
export function getCategoryLabel(category: TemplateCategory | 'all'): string {
  if (category === 'all') return 'All Categories';
  return category.charAt(0).toUpperCase() + category.slice(1);
}

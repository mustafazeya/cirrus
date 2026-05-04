/**
 * Template Categories - Centralized constants and utilities
 * Single source of truth for template categories
 */
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
 * Get the emoji icon for a template category
 */
export function getCategoryIcon(category: TemplateCategory | string): string {
  switch (category) {
    case 'cost':
      return '💰';
    case 'security':
      return '🔒';
    case 'performance':
      return '📈';
    case 'resources':
      return '🗄️';
    case 'logs':
      return '📋';
    case 'governance':
      return '⚖️';
    case 'custom':
      return '⭐';
    default:
      return '📄';
  }
}

/**
 * Get a human-readable label for a category
 */
export function getCategoryLabel(category: TemplateCategory | 'all'): string {
  if (category === 'all') return 'All Categories';
  return category.charAt(0).toUpperCase() + category.slice(1);
}

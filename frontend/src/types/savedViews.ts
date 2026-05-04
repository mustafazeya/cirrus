/**
 * Saved Views Types
 * 
 * Type definitions for saved filter configurations (views)
 */

export interface SavedViewFilters {
  // Cost page filters
  subscriptions?: string[];
  resource_groups?: { [subscriptionId: string]: string[] } | null;
  date_range?: 'this_month' | 'last_month' | 'last_7_days' | 'last_30_days' | 'custom';
  custom_start_date?: string | null;
  custom_end_date?: string | null;
  group_by?: 'ServiceName' | 'Location' | 'ResourceGroupName' | 'ResourceId';
  
  // Query page filters
  subscription_id?: string | null;
  resource_group?: string | null;
  resource_type?: string | null;
  resource?: string | null;
  global_scope?: boolean;
  
  // Dashboard page filters
  locations?: string[];
  search_query?: string;

  // Home page watchlist card
  card_metric?: 'cost' | 'health' | 'security' | 'compliance' | 'changes';
  card_size?: 'sm' | 'md' | 'lg';
}

export interface SavedView {
  id: string;
  name: string;
  description?: string | null;
  owner_id: string;
  view_type: 'cost' | 'dashboard' | 'query' | 'home';
  filters: SavedViewFilters;
  is_default: boolean;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateSavedViewRequest {
  name: string;
  description?: string | null;
  view_type: 'cost' | 'dashboard' | 'query' | 'home';
  filters: SavedViewFilters;
  is_default?: boolean;
  is_shared?: boolean;
}

export interface UpdateSavedViewRequest {
  name?: string;
  description?: string | null;
  filters?: SavedViewFilters;
  is_default?: boolean;
  is_shared?: boolean;
}

export interface SavedViewListResponse {
  success: boolean;
  views: SavedView[];
  total_count: number;
}

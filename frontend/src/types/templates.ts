/**
 * Query Template Types
 * Type definitions for the Query Templates feature
 */

export type QueryType = 'natural_language' | 'kql' | 'resource_graph' | 'cost_analysis' | 'metrics';
export type TemplateCategory = 'cost' | 'security' | 'performance' | 'resources' | 'logs' | 'governance' | 'custom';

export interface QueryTemplate {
  id: string;
  user_id: string;
  name: string;
  description: string;
  query_type: QueryType;
  query: string;
  parameters: Record<string, any>;
  category: TemplateCategory;
  tags: string[];
  is_public: boolean;
  is_system: boolean;
  use_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
  created_by_name: string | null;
}

export interface QueryTemplateListItem {
  id: string;
  name: string;
  description: string;
  query_type: QueryType;
  query: string; // The actual query text
  category: TemplateCategory;
  tags: string[];
  is_public: boolean;
  is_system: boolean;
  use_count: number;
  created_at: string;
  created_by_name: string | null;
  user_id: string | null; // Only present for public templates
}

export interface CreateTemplateRequest {
  name: string;
  description: string;
  query_type: QueryType;
  query: string;
  parameters?: Record<string, any>;
  category?: TemplateCategory;
  tags?: string[];
  is_public?: boolean;
}

export interface UpdateTemplateRequest {
  name?: string;
  description?: string;
  query?: string;
  parameters?: Record<string, any>;
  category?: TemplateCategory;
  tags?: string[];
}

export interface ExecuteTemplateRequest {
  template_id: string;
  parameter_values?: Record<string, any>;
  context?: {
    subscription?: string;
    resource_group?: string;
    resource_id?: string;
    workspace_id?: string;
  };
}

export interface TemplateSearchFilters {
  query?: string; // Search in name/description
  query_type?: QueryType;
  category?: TemplateCategory;
  tags?: string[];
  is_public?: boolean;
}

export interface TemplatesResponse {
  templates: QueryTemplateListItem[];
  count: number;
}

export interface ExecuteTemplateResponse {
  template: QueryTemplateListItem;
  result: any;
  query_executed: string;
}

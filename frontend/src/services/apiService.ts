import axios, { AxiosInstance, AxiosRequestConfig } from 'axios';
import { API_CONFIG } from '../config/authConfig';
import type { TemplateCategory } from '../types/templates';
import { trackException, trackEvent } from '../utils/appInsights';
import type {
  FirewallSubscription,
  FirewallPolicy,
  AzureFirewall,
  PolicySummary,
  RulesResult,
  SearchResult,
  OverlapResult,
  OrphanResult,
  FirewallDiagnosticInfo,
  RuleLogDetail,
  TrafficIntelligenceResult,
  LogSearchResult,
  PacketWalkResult,
  FirewallConfiguration,
  FwMetricResult,
  SavedKqlTemplate,
  KqlResult,
  KqlJobStatus,
} from '../types/firewall';

// Types
export interface ApiResponse<T = any> {
  data?: T;
  message?: string;
  status?: number;
  success?: boolean;
  error?: string;
  query_type?: string;
  [key: string]: any; // Allow additional dynamic properties from backend
}

export interface QueryRequest {
  query: string;
  subscription_ids?: string[];
}

export interface KQLQueryRequest {
  query: string;
  workspace_id: string;
}

class ApiService {
  private client: AxiosInstance;
  private idToken: string | null = null; // ID token for role checking (admin access)
  private managementToken: string | null = null; // Token for Azure Management API
  private logAnalyticsToken: string | null = null; // Token for Log Analytics API
  private graphToken: string | null = null; // Token for Microsoft Graph API
  private tokenRefreshCallback: (() => Promise<void>) | null = null; // Callback to refresh tokens
  private tokenRefreshPromise: Promise<void> | null = null; // Prevents concurrent refresh attempts

  constructor() {
    this.client = axios.create({
      baseURL: API_CONFIG.baseURL,
      timeout: API_CONFIG.timeout,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Request interceptor to add auth token based on endpoint
    this.client.interceptors.request.use(
      (config) => {
        // Record start time for duration tracking in App Insights
        (config as any).metadata = { startTime: Date.now() };

        // Determine which token to use based on the endpoint
        const url = config.url || '';
        
        // For AI query endpoints, send management token in Authorization header only.
        // LA token is injected into the JSON body to avoid Envoy/Cloudflare rejecting
        // requests where two large JWTs (users with many AD groups) exceed header size limits.
        if (url.includes('/api/ai/query') || url.includes('/api/ai/root-cause')) {
          if (this.managementToken) {
            config.headers.Authorization = `Bearer ${this.managementToken}`;
          }
          if (this.logAnalyticsToken && config.data) {
            try {
              const body = typeof config.data === 'string' ? JSON.parse(config.data) : { ...config.data };
              body.la_token = this.logAnalyticsToken;
              config.data = typeof config.data === 'string' ? JSON.stringify(body) : body;
            } catch {
              // body parse failed — fall back to header so KQL still works
              config.headers['X-LA-Token'] = this.logAnalyticsToken;
            }
          }
        } 
        // For admin and firewall endpoints, send Management token + ID token (for role checking)
        else if (url.includes('/api/admin') || url.includes('/api/firewall')) {
          if (this.managementToken) {
            config.headers.Authorization = `Bearer ${this.managementToken}`;
          }
          if (this.idToken) {
            config.headers['X-ID-Token'] = this.idToken;
          }
        }
        // Use Management token for all other endpoints (subscriptions, resources, etc.)
        else {
          if (this.managementToken) {
            config.headers.Authorization = `Bearer ${this.managementToken}`;
          }
        }
        
        return config;
      },
      (error) => {
        return Promise.reject(error);
      }
    );

    // Response interceptor for error handling with automatic token refresh
    this.client.interceptors.response.use(
      (response) => {
        // Track successful response duration for slow-call analysis
        const duration = Date.now() - ((response.config as any).metadata?.startTime ?? Date.now());
        trackEvent('api_request_success', {
          url: response.config.url,
          method: response.config.method?.toUpperCase(),
          status: response.status,
          duration_ms: duration,
        });
        return response;
      },
      async (error) => {
        const originalRequest = error.config;
        const duration = Date.now() - ((originalRequest as any)?.metadata?.startTime ?? Date.now());

        if (error.response?.status === 401 && !originalRequest._retry) {
          console.log(' 401 error - attempting token refresh...');
          
          // Check if it's a token audience error
          const errorMessage = error.response?.data?.detail || '';
          if (errorMessage.includes('Invalid token audience') || errorMessage.includes('wrong audience')) {
            console.error(' Token audience error - need to refresh with correct scope');
            trackException(new Error('Token audience error'), {
              url: originalRequest.url,
              errorMessage,
            });
            this.managementToken = null;
            this.logAnalyticsToken = null;
            this.graphToken = null;
            this.idToken = null;
            return Promise.reject(error);
          }
          
          // Mark request as retried to prevent infinite loops
          originalRequest._retry = true;
          
          // Try to refresh tokens using deduplicated refresh handler
          try {
            await this.handleTokenRefresh();
            console.log(' Tokens refreshed - retrying request');
            
            // Retry the original request with new tokens
            return this.client(originalRequest);
          } catch (refreshError) {
            console.error(' Token refresh failed:', refreshError);
            trackException(refreshError instanceof Error ? refreshError : new Error('Token refresh failed'), {
              url: originalRequest.url,
              status: error.response?.status,
            });
            this.idToken = null;
            this.managementToken = null;
            this.logAnalyticsToken = null;
            this.graphToken = null;
            return Promise.reject(error);
          }
        }

        // Track all non-retried API errors globally (4xx, 5xx, network failures)
        if (!originalRequest._retry) {
          const status = error.response?.status;
          const errMessage = error.response?.data?.detail || error.message || 'unknown';
          trackException(new Error(`API error ${status ?? 'network'}: ${originalRequest?.url}`), {
            url: originalRequest?.url,
            method: originalRequest?.method?.toUpperCase(),
            status,
            duration_ms: duration,
            error_detail: errMessage,
          });
        }

        return Promise.reject(error);
      }
    );
  }

  // Register a callback for token refresh
  setTokenRefreshCallback(callback: () => Promise<void>) {
    this.tokenRefreshCallback = callback;
  }

  // Handle token refresh with deduplication to prevent concurrent refresh attempts
  private async handleTokenRefresh(): Promise<void> {
    // If refresh already in progress, wait for it instead of starting a new one
    if (this.tokenRefreshPromise) {
      console.log(' Token refresh already in progress, waiting...');
      return this.tokenRefreshPromise;
    }

    // Start new refresh and store the promise
    this.tokenRefreshPromise = (async () => {
      try {
        if (!this.tokenRefreshCallback) {
          throw new Error('Token refresh callback not registered');
        }
        await this.tokenRefreshCallback();
      } finally {
        // Clear the promise after completion (success or failure)
        this.tokenRefreshPromise = null;
      }
    })();

    return this.tokenRefreshPromise;
  }

  // Public method to manually trigger token refresh
  async refreshTokens(): Promise<void> {
    return this.handleTokenRefresh();
  }

  setIdToken(token: string) {
    // Set ID token for role checking (admin access)
    this.idToken = token;
  }

  setAccessToken(token: string) {
    // Set as management token by default (most common use case)
    this.managementToken = token;
  }

  setLogAnalyticsToken(token: string) {
    // Set token specifically for Log Analytics queries
    // Empty string clears the token
    this.logAnalyticsToken = token || null;
  }

  setGraphToken(token: string) {
    // Set token specifically for Microsoft Graph API
    this.graphToken = token;
  }

  clearAccessToken() {
    this.idToken = null;
    this.managementToken = null;
    this.logAnalyticsToken = null;
    this.graphToken = null;
  }

  // Clear cached profile photo
  clearUserPhotoCache() {
    try {
      localStorage.removeItem('user_profile_photo');
      localStorage.removeItem('user_profile_photo_timestamp');
    } catch (error) {
      console.warn('Error clearing photo cache:', error);
    }
  }

  // Health check
  async healthCheck(): Promise<ApiResponse> {
    const response = await this.client.get('/api/health');
    return response.data;
  }

  // Authentication
  async getCurrentUser(): Promise<ApiResponse> {
    const response = await this.client.get('/api/auth/me');
    return response.data;
  }

  // Get user access information based on current scope
  async getUserAccess(scopeContext: {
    global_scope?: boolean;
    cloudflare_mode?: boolean;
    subscription_id?: string;
    resource_group?: string;
    resource_id?: string;
  }): Promise<ApiResponse> {
    const response = await this.client.post('/api/user/access', scopeContext);
    return response.data;
  }

  // Fetch user profile photo from Microsoft Graph API with 24-hour caching
  async getUserPhoto(): Promise<string | null> {
    if (!this.graphToken) {
      console.warn('No Graph API token available for fetching user photo');
      return null;
    }

    // Check cache first
    const cacheKey = 'user_profile_photo';
    const cacheTimestampKey = 'user_profile_photo_timestamp';
    const cacheExpiryHours = 24;
    
    try {
      const cachedPhoto = localStorage.getItem(cacheKey);
      const cachedTimestamp = localStorage.getItem(cacheTimestampKey);
      
      if (cachedPhoto && cachedTimestamp) {
        const cacheAge = Date.now() - parseInt(cachedTimestamp);
        const cacheExpiryMs = cacheExpiryHours * 60 * 60 * 1000; // 24 hours in ms
        
        if (cacheAge < cacheExpiryMs) {
          return cachedPhoto;
        }
      }
    } catch (error) {
      console.warn('Error reading photo cache:', error);
    }

    try {
      const response = await axios.get(
        'https://graph.microsoft.com/v1.0/me/photo/$value',
        {
          headers: {
            Authorization: `Bearer ${this.graphToken}`,
          },
          responseType: 'blob', // Important: get the photo as a blob
        }
      );

      // Convert blob to base64 data URL
      const blob = response.data;
      const photoUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      
      // Cache the photo and timestamp
      try {
        localStorage.setItem(cacheKey, photoUrl);
        localStorage.setItem(cacheTimestampKey, Date.now().toString());
      } catch (error) {
        console.warn('Error caching photo:', error);
      }
      
      return photoUrl;
    } catch (error: any) {
      // 404 means user has no profile photo, which is not an error
      if (error.response?.status === 404) {
        console.log('User does not have a profile photo in Azure AD');
        // Cache the null result to avoid repeated 404 requests
        try {
          localStorage.setItem(cacheKey, 'null');
          localStorage.setItem(cacheTimestampKey, Date.now().toString());
        } catch (cacheError) {
          console.warn('Error caching null photo result:', cacheError);
        }
        return null;
      }
      console.error('Error fetching user photo:', error);
      return null;
    }
  }

  // Subscriptions
  async getSubscriptions(): Promise<ApiResponse> {
    const response = await this.client.get('/api/subscriptions?page_size=1000');
    return response.data;
  }

  // Resource Groups
  async getResourceGroups(subscriptionId: string): Promise<ApiResponse> {
    const response = await this.client.get(`/api/subscriptions/${subscriptionId}/resource-groups`);
    return response.data;
  }

  // Batch Resource Groups - get resource groups for multiple subscriptions
  async getResourceGroupsBatch(subscriptionIds: string[]): Promise<ApiResponse> {
    const response = await this.client.post('/api/resource-groups/batch', subscriptionIds);
    return response.data;
  }

  // Resources
  async getResources(
    subscriptionId: string,
    resourceGroup?: string,
    resourceType?: string
  ): Promise<ApiResponse> {
    const params = new URLSearchParams();
    if (resourceGroup) params.append('resource_group', resourceGroup);
    if (resourceType) params.append('resource_type', resourceType);
    
    const query = params.toString() ? `?${params.toString()}` : '';
    const response = await this.client.get(`/api/subscriptions/${subscriptionId}/resources${query}`);
    return response.data;
  }

  // Get Log Analytics Workspaces
  async getWorkspaces(): Promise<ApiResponse> {
    const response = await this.client.get('/api/ai/workspaces');
    return response.data;
  }

  // Cache Management
  async getCacheStats(): Promise<ApiResponse> {
    const response = await this.client.get('/api/ai/cache/stats');
    return response.data;
  }

  async clearCache(): Promise<ApiResponse> {
    const response = await this.client.delete('/api/ai/cache/clear');
    return response.data;
  }

  // Cost Analysis (legacy single subscription)
  async getCostAnalysis(
    subscriptionId: string,
    timePeriod: string = 'ThisMonth'
  ): Promise<ApiResponse> {
    const response = await this.client.get(
      `/api/subscriptions/${subscriptionId}/costs?time_period=${timePeriod}`
    );
    return response.data;
  }

  // Advanced Cost Analysis (multi-subscription)
  async getSubscriptionCosts(
    subscriptionIds: string[],
    startDate: string,
    endDate: string,
    groupBy: string | null = 'ServiceName',
    includeReservations: boolean = false,
    resourceGroups?: { [key: string]: string[] }
  ): Promise<ApiResponse> {
    const requestBody: any = {
      subscription_ids: subscriptionIds,
      start_date: startDate,
      end_date: endDate,
      include_reservations: includeReservations
    };
    
    // Handle group_by:
    // - If null or empty string: explicitly send null for daily time series
    // - If valid string: send the dimension name
    if (!groupBy || groupBy.trim() === '') {
      requestBody.group_by = null;  // Explicitly null for daily time series
    } else {
      requestBody.group_by = groupBy;
    }
    
    // Add resource groups if provided
    if (resourceGroups && Object.keys(resourceGroups).length > 0) {
      requestBody.resource_groups = resourceGroups;
    }
    
    const response = await this.client.post('/api/costs/subscriptions', requestBody);
    trackEvent('cost_analysis_requested', {
      subscription_count: subscriptionIds.length,
      start_date: startDate,
      end_date: endDate,
      group_by: groupBy ?? 'none',
      include_reservations: includeReservations,
      has_resource_group_filter: !!(resourceGroups && Object.keys(resourceGroups).length > 0),
    });
    return response.data;
  }

  // OPTIMIZED: Get both ActualCost and AmortizedCost in a single API call
  async getSubscriptionCostsWithBothMetrics(
    subscriptionIds: string[],
    startDate: string,
    endDate: string,
    groupBy: string = 'ServiceName',
    resourceGroups?: { [key: string]: string[] }
  ): Promise<ApiResponse> {
    const requestBody: any = {
      subscription_ids: subscriptionIds,
      start_date: startDate,
      end_date: endDate,
      group_by: groupBy,
      return_both_metrics: true  // NEW: Returns both actual and amortized costs
    };
    
    // Add resource groups if provided
    if (resourceGroups && Object.keys(resourceGroups).length > 0) {
      requestBody.resource_groups = resourceGroups;
    }
    
    const response = await this.client.post('/api/costs/subscriptions', requestBody);
    return response.data;
  }

  // Get budgets for subscriptions
  async getBudgets(subscriptionIds: string[]): Promise<ApiResponse> {
    const response = await this.client.post('/api/costs/budgets', {
      subscription_ids: subscriptionIds
    });
    return response.data;
  }

  // Get reservation and savings plan analysis
  async getReservationAnalysis(
    subscriptionIds: string[],
    startDate: string,
    endDate: string
  ): Promise<ApiResponse> {
    const response = await this.client.post('/api/costs/reservations', {
      subscription_ids: subscriptionIds,
      start_date: startDate,
      end_date: endDate
    });
    return response.data;
  }

  // Governance APIs
  
  // Get policy compliance across subscriptions
  async getPolicyCompliance(subscriptionIds: string[]): Promise<ApiResponse> {
    const response = await this.client.get('/api/governance/policy-compliance', {
      params: {
        subscription_ids: subscriptionIds.join(',')
      }
    });
    return response.data;
  }

  // Get Microsoft Defender security score across subscriptions
  async getSecurityScore(subscriptionIds: string[]): Promise<ApiResponse> {
    const response = await this.client.get('/api/governance/security-score', {
      params: {
        subscription_ids: subscriptionIds.join(',')
      }
    });
    return response.data;
  }

  // Get resource health status across subscriptions
  async getResourceHealth(subscriptionIds: string[]): Promise<ApiResponse> {
    const response = await this.client.get('/api/governance/resource-health', {
      params: {
        subscription_ids: subscriptionIds.join(',')
      }
    });
    return response.data;
  }

  // Get Azure Resource changes (Change Analysis)
  async getChangeAnalysis(subscriptionIds: string[], hours: number = 24): Promise<ApiResponse> {
    const response = await this.client.get('/api/governance/change-analysis', {
      params: {
        subscription_ids: subscriptionIds.join(','),
        hours
      }
    });
    return response.data;
  }

  // Run AI Root Cause Analysis
  async getRootCauseAnalysis(request: {
    incident_summary: string;
    subscription_ids: string[];
    subscription_id?: string;
    resource_id?: string;
    resource_group?: string;
    hours?: number;
    custom_start?: string;
    custom_end?: string;
    include_external_status?: boolean;
  }): Promise<ApiResponse> {
    trackEvent('rca_submitted', {
      subscription_count: request.subscription_ids.length,
      hours: request.hours ?? 24,
      has_resource_id: !!request.resource_id,
      has_resource_group: !!request.resource_group,
      include_external_status: request.include_external_status ?? false,
    });
    const response = await this.client.post('/api/ai/root-cause', request);
    return response.data;
  }

  // ============================================================================
  // Query Template Methods
  // ============================================================================

  // Get all templates (user's private + public templates)
  async getTemplates(includePublic: boolean = true, bustCache: boolean = false): Promise<ApiResponse> {
    const params: any = { include_public: includePublic };
    
    // Add cache-busting timestamp to force fresh data (bypasses Cloudflare/CDN cache)
    if (bustCache) {
      params._t = Date.now();
    }
    
    const response = await this.client.get('/api/templates', { params });
    return response.data;
  }

  // Create a new template
  async createTemplate(templateData: {
    name: string;
    description: string;
    query_type: 'natural_language' | 'kql' | 'resource_graph' | 'cost_analysis' | 'metrics';
    query: string;
    parameters?: Record<string, any>;
    category?: TemplateCategory;
    tags?: string[];
  }): Promise<ApiResponse> {
    const response = await this.client.post('/api/templates', templateData);
    return response.data;
  }

  // Get a specific template by ID
  async getTemplate(templateId: string): Promise<ApiResponse> {
    const response = await this.client.get(`/api/templates/${templateId}`);
    return response.data;
  }

  // Update an existing template
  async updateTemplate(templateId: string, updates: {
    name?: string;
    description?: string;
    query?: string;
    parameters?: Record<string, any>;
    category?: TemplateCategory;
    tags?: string[];
  }): Promise<ApiResponse> {
    const response = await this.client.put(`/api/templates/${templateId}`, updates);
    return response.data;
  }

  // Delete a template
  async deleteTemplate(templateId: string): Promise<ApiResponse> {
    const response = await this.client.delete(`/api/templates/${templateId}`);
    return response.data;
  }

  // Search templates with filters
  async searchTemplates(filters: {
    query?: string;
    query_type?: 'natural_language' | 'kql' | 'resource_graph' | 'cost_analysis' | 'metrics';
    category?: 'cost' | 'security' | 'performance' | 'resources' | 'logs' | 'custom';
    tags?: string[];
    is_public?: boolean;
  }): Promise<ApiResponse> {
    const response = await this.client.post('/api/templates/search', filters);
    return response.data;
  }

  // Execute a template with parameter substitution
  async executeTemplate(templateId: string, params: {
    parameter_values?: Record<string, any>;
    context?: {
      subscription?: string;
      resource_group?: string;
      resource_id?: string;
      workspace_id?: string;
    };
  }): Promise<ApiResponse> {
    trackEvent('template_executed', {
      template_id: templateId,
      has_subscription: !!params.context?.subscription,
      has_resource_group: !!params.context?.resource_group,
      parameter_count: Object.keys(params.parameter_values ?? {}).length,
    });
    const response = await this.client.post(`/api/templates/${templateId}/execute`, {
      template_id: templateId,
      ...params
    });
    return response.data;
  }

  // ============================================================================
  // AI-Powered Template Features
  // ============================================================================

  // Get AI-powered template suggestions based on query
  async getTemplateSuggestions(query: string, limit: number = 3): Promise<ApiResponse> {
    const response = await this.client.get('/api/templates/suggestions', {
      params: { query, limit }
    });
    return response.data;
  }

  // Get recently used templates
  async getRecentTemplates(limit: number = 5): Promise<ApiResponse> {
    const response = await this.client.get('/api/templates/recent', {
      params: { limit }
    });
    return response.data;
  }

  // ============================================================================
  // Template Marketplace
  // ============================================================================

  // Browse marketplace templates
  async getMarketplaceTemplates(params: {
    category?: string;
    sort?: 'popular' | 'trending' | 'recent';
    limit?: number;
  } = {}): Promise<ApiResponse> {
    const response = await this.client.get('/api/templates/marketplace', { params });
    return response.data;
  }

  // Rate a template
  async rateTemplate(templateId: string, rating: number): Promise<ApiResponse> {
    const response = await this.client.post(`/api/templates/${templateId}/rate`, null, {
      params: { rating }
    });
    return response.data;
  }

  // Submit thumbs feedback on an AI message
  async submitMessageFeedback(params: {
    conversation_id: string;
    message_id: string;
    feedback: 'positive' | 'negative';
    correction?: string;
  }): Promise<ApiResponse> {
    const response = await this.client.post('/api/ai/feedback', params);
    return response.data;
  }

  // Import a template
  async importTemplate(templateData: {
    name: string;
    description: string;
    query_type: string;
    query: string;
    category?: string;
    tags?: string[];
  }): Promise<ApiResponse> {
    const response = await this.client.post('/api/templates/import', templateData);
    return response.data;
  }

  // Export a template
  async exportTemplate(templateId: string): Promise<ApiResponse> {
    const response = await this.client.get(`/api/templates/export/${templateId}`);
    return response.data;
  }

  // ============================================================================
  // SAVED VIEWS API
  // ============================================================================

  // Create a new saved view
  async createSavedView(data: {
    name: string;
    description?: string | null;
    view_type: 'cost' | 'dashboard' | 'query' | 'home';
    filters: any;
    is_default?: boolean;
    is_shared?: boolean;
  }): Promise<ApiResponse> {
    const response = await this.client.post('/api/saved-views', data);
    return response.data;
  }

  // List all saved views for the current user
  async listSavedViews(): Promise<ApiResponse> {
    const response = await this.client.get('/api/saved-views');
    return response.data;
  }

  // Get user's default saved view
  async getDefaultView(): Promise<ApiResponse> {
    const response = await this.client.get('/api/saved-views/default');
    return response.data;
  }

  // Get a specific saved view by ID
  async getSavedView(viewId: string): Promise<ApiResponse> {
    const response = await this.client.get(`/api/saved-views/${viewId}`);
    return response.data;
  }

  // Update an existing saved view
  async updateSavedView(viewId: string, data: {
    name?: string;
    description?: string | null;
    filters?: any;
    is_default?: boolean;
    is_shared?: boolean;
  }): Promise<ApiResponse> {
    const response = await this.client.put(`/api/saved-views/${viewId}`, data);
    return response.data;
  }

  // Delete a saved view
  async deleteSavedView(viewId: string): Promise<ApiResponse> {
    const response = await this.client.delete(`/api/saved-views/${viewId}`);
    return response.data;
  }

  // Generic request method
  async request<T = any>(config: AxiosRequestConfig): Promise<T> {
    const response = await this.client.request(config);
    return response.data;
  }

  // ── Azure Firewall Analysis ────────────────────────────────────────────────

  async firewallListSubscriptions(hasPolicies = true): Promise<{ subscriptions: FirewallSubscription[] }> {
    const response = await this.client.get('/api/firewall/subscriptions', {
      params: hasPolicies ? { has_policies: true } : undefined,
    });
    return response.data;
  }

  async firewallGetDiagnosticInfo(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
  ): Promise<FirewallDiagnosticInfo> {
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/diagnostic-info`,
    );
    return response.data;
  }

  async firewallListFirewalls(subscriptionId: string): Promise<{ firewalls: AzureFirewall[] }> {
    const response = await this.client.get(`/api/firewall/subscriptions/${subscriptionId}/firewalls`);
    return response.data;
  }

  async firewallListPolicies(subscriptionId: string): Promise<{ policies: FirewallPolicy[] }> {
    const response = await this.client.get(`/api/firewall/subscriptions/${subscriptionId}/policies`);
    return response.data;
  }

  async firewallGetSummary(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
  ): Promise<PolicySummary> {
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/summary`,
    );
    return response.data;
  }

  async firewallGetRules(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    ruleTypes?: string,
  ): Promise<RulesResult> {
    const params = new URLSearchParams();
    if (ruleTypes) params.append('rule_types', ruleTypes);
    const qs = params.toString() ? `?${params}` : '';
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/rules${qs}`,
    );
    return response.data;
  }

  async firewallSearchRules(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    filters: {
      source?: string;
      destination?: string;
      port?: string;
      protocol?: string;
      rule_types?: string;
      action?: string;
      name_filter?: string;
    },
  ): Promise<SearchResult> {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => { if (v) params.append(k, v); });
    const qs = params.toString() ? `?${params}` : '';
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/search${qs}`,
    );
    return response.data;
  }

  async firewallSearchRulesAdvanced(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    groups: { conditions: { field: string; operator: string; value: string }[]; intra_connector: string; inter_connector: string }[],
  ): Promise<SearchResult> {
    const response = await this.client.post(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/search/advanced`,
      { groups },
    );
    return response.data;
  }


  async firewallGetTrafficIntelligence(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    workspaceId: string,
    days: number,
    ruleTypes?: string,
    startTime?: string,
    endTime?: string,
  ): Promise<TrafficIntelligenceResult> {
    const params = new URLSearchParams({ workspace_id: workspaceId, days: String(days) });
    if (ruleTypes) params.append('rule_types', ruleTypes);
    if (startTime) params.append('start_time', startTime);
    if (endTime)   params.append('end_time', endTime);
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/traffic/intelligence?${params}`,
    );
    return response.data;
  }

  async firewallSearchLogs(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    body: {
      workspace_id: string;
      groups: { conditions: { field: string; operator: string; value: string }[]; intra_connector: string; inter_connector: string }[];
      start_time: string;
      end_time: string;
      limit: number;
    },
  ): Promise<LogSearchResult> {
    const response = await this.client.post(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/logs/search`,
      body,
    );
    return response.data;
  }

  // Returns the auth headers needed for a firewall endpoint (used by native fetch streaming calls)
  getFirewallHeaders(): Record<string, string> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.managementToken) headers['Authorization'] = `Bearer ${this.managementToken}`;
    if (this.idToken) headers['X-ID-Token'] = this.idToken;
    return headers;
  }

  async firewallExecuteKql(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    body: { workspace_id: string; kql: string; start_time: string; end_time: string; limit: number },
    signal?: AbortSignal,
  ): Promise<KqlResult> {
    const response = await this.client.post(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/logs/kql`,
      body,
      { signal },
    );
    return response.data;
  }

  async firewallKqlJobStatus(jobId: string, signal?: AbortSignal): Promise<KqlJobStatus> {
    const response = await this.client.get(`/api/firewall/kql-jobs/${jobId}`, { signal });
    return response.data;
  }

  async firewallNlToKql(naturalQuery: string): Promise<{ kql: string }> {
    const response = await this.client.post('/api/firewall/logs/nl-to-kql', { natural_query: naturalQuery });
    return response.data;
  }

  async firewallKqlTemplatesList(): Promise<{ templates: SavedKqlTemplate[] }> {
    const response = await this.client.get('/api/firewall/kql-templates');
    return response.data;
  }

  async firewallKqlTemplatesSave(body: { name: string; description: string; kql: string }): Promise<SavedKqlTemplate> {
    const response = await this.client.post('/api/firewall/kql-templates', body);
    return response.data;
  }

  async firewallKqlTemplatesDelete(templateId: string): Promise<void> {
    await this.client.delete(`/api/firewall/kql-templates/${templateId}`);
  }

  async firewallKqlFavouritesList(): Promise<{ favourite_ids: string[] }> {
    const response = await this.client.get('/api/firewall/kql-templates/favourites');
    return response.data;
  }

  async firewallKqlFavouritesToggle(templateId: string): Promise<void> {
    await this.client.post('/api/firewall/kql-templates/favourites/toggle', { template_id: templateId });
  }

  async firewallDetectOverlaps(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    ruleTypes?: string,
  ): Promise<OverlapResult> {
    const params = new URLSearchParams();
    if (ruleTypes) params.append('rule_types', ruleTypes);
    const qs = params.toString() ? `?${params}` : '';
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/overlaps${qs}`,
    );
    return response.data;
  }

  async firewallDetectOrphans(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    workspaceId: string,
    days: number,
    ruleTypes?: string,
  ): Promise<OrphanResult> {
    const params = new URLSearchParams({ workspace_id: workspaceId, days: String(days) });
    if (ruleTypes) params.append('rule_types', ruleTypes);
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/orphans?${params}`,
    );
    return response.data;
  }

  async firewallGetRuleLogDetail(
    workspaceId: string,
    ruleName: string,
    days: number,
  ): Promise<RuleLogDetail> {
    const params = new URLSearchParams({
      workspace_id: workspaceId,
      rule_name: ruleName,
      days: String(days),
    });
    const response = await this.client.get(`/api/firewall/rule-log-detail?${params}`);
    return response.data;
  }

  async firewallGetAzureMetrics(
    subscriptionId: string,
    resourceGroup: string,
    firewallName: string,
    metric: string,
    aggregation: string,
    interval: string,
    timespanStart: string,
    timespanEnd: string,
    splitBy = '',
  ): Promise<FwMetricResult> {
    const params = new URLSearchParams({
      metric,
      aggregation,
      interval,
      timespan_start: timespanStart,
      timespan_end: timespanEnd,
      ...(splitBy ? { split_by: splitBy } : {}),
    });
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${firewallName}/azure-metrics?${params}`,
    );
    return response.data;
  }

  async firewallGetConfiguration(
    subscriptionId: string,
    resourceGroup: string,
    firewallName: string,
  ): Promise<FirewallConfiguration> {
    const response = await this.client.get(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${firewallName}/configuration`,
    );
    return response.data;
  }

  async firewallPacketWalk(
    subscriptionId: string,
    resourceGroup: string,
    policyName: string,
    body: { source: string; destination: string; port: string; protocol: string; subscription_ids: string[] },
  ): Promise<PacketWalkResult> {
    const response = await this.client.post(
      `/api/firewall/${subscriptionId}/${resourceGroup}/${policyName}/packet-walk`,
      body,
    );
    return response.data;
  }
}

// Create singleton instance
export const apiService = new ApiService();

// Hook for using API service with error handling
export const useApiService = () => {
  const executeWithErrorHandling = async <T>(
    apiCall: () => Promise<T>,
    onError?: (error: any) => void
  ): Promise<T | null> => {
    try {
      return await apiCall();
    } catch (error) {
      console.error('API call failed:', error);
      if (onError) {
        onError(error);
      }
      return null;
    }
  };

  return {
    apiService,
    executeWithErrorHandling,
  };
};
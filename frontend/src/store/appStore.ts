import { create } from 'zustand';
import type { FirewallContext } from '../types/firewall';

// localStorage utilities
const getStoredValue = <T>(key: string, defaultValue: T): T => {
  if (typeof window === 'undefined') return defaultValue;
  try {
    const item = window.localStorage.getItem(key);
    return item ? JSON.parse(item) : defaultValue;
  } catch (error) {
    console.warn(`Error reading localStorage key "${key}":`, error);
    return defaultValue;
  }
};

const setStoredValue = <T>(key: string, value: T): void => {
  if (typeof window === 'undefined') return;
  try {
    // Special handling for user object - don't store photoUrl as it's stored separately
    let valueToStore = value;
    if (key === 'user' && value && typeof value === 'object' && 'photoUrl' in value) {
      // Strip photoUrl from user object before storing
      // Photo is stored separately in 'user_profile_photo' by apiService
      const { photoUrl, ...userWithoutPhoto } = value as any;
      valueToStore = userWithoutPhoto as T;
    }
    
    const serialized = JSON.stringify(valueToStore);
    
    // Check size before storing (5MB is typical localStorage limit)
    // We'll limit individual items to 1MB to be safe
    const sizeInBytes = new Blob([serialized]).size;
    const sizeInMB = sizeInBytes / (1024 * 1024);
    
    if (sizeInMB > 1) {
      console.warn(`localStorage key "${key}" is ${sizeInMB.toFixed(2)}MB - pruning data`);
      
      // Special handling for large data structures
      if (key === 'conversationSessions') {
        // Prune message content from old sessions
        const sessions = value as any[];
        const prunedSessions = sessions.map((session, index) => {
          // Keep full data for the 2 most recent sessions, prune older ones
          if (index < sessions.length - 2) {
            return {
              ...session,
              messages: session.messages.map((msg: any) => ({
                ...msg,
                // Keep only essential fields, truncate content
                content: msg.content?.slice(0, 200) || '',
                query_result: undefined // Remove large query results
              }))
            };
          }
          return session;
        });
        window.localStorage.setItem(key, JSON.stringify(prunedSessions));
        return;
      } else if (key === 'queryHistory') {
        // Keep only last 20 queries instead of 50
        const history = (value as any[]).slice(0, 20).map(entry => ({
          ...entry,
          // Remove large result data, keep only summary
          results: entry.results ? {
            query_type: entry.results.query_type,
            row_count: entry.results.row_count
          } : null
        }));
        window.localStorage.setItem(key, JSON.stringify(history));
        return;
      } else if (key === 'queryResults') {
        // Don't store large query results
        console.warn(`Skipping storage of large queryResults (${sizeInMB.toFixed(2)}MB)`);
        return;
      }
    }
    
    window.localStorage.setItem(key, serialized);
  } catch (error: any) {
    // Handle quota exceeded errors
    if (error.name === 'QuotaExceededError' || error.code === 22) {
      console.error(`localStorage quota exceeded for key "${key}". Attempting cleanup...`);
      
      // Try to free up space
      try {
        // Remove least critical data first
        if (key !== 'user' && key !== 'selectedSubscriptionId') {
          // For non-critical keys, just skip storing
          console.warn(`Skipping storage for non-critical key: ${key}`);
          
          // Clean up old data to free space
          const keysToCleanup = ['queryResults', 'queryHistory', 'conversationSessions'];
          for (const cleanupKey of keysToCleanup) {
            if (cleanupKey !== key) {
              try {
                window.localStorage.removeItem(cleanupKey);
                console.log(`Removed ${cleanupKey} to free space`);
              } catch (e) {
                console.warn(`Failed to remove ${cleanupKey}:`, e);
              }
            }
          }
          
          // Try storing again after cleanup
          try {
            window.localStorage.setItem(key, JSON.stringify(value));
            console.log(`Successfully stored ${key} after cleanup`);
          } catch (retryError) {
            console.error(`Still failed to store ${key} after cleanup:`, retryError);
          }
        }
      } catch (cleanupError) {
        console.error(`Cleanup failed:`, cleanupError);
      }
    } else {
      console.error(`Error writing localStorage key "${key}":`, error);
    }
  }
};

// Helper to get cached user with photo
const getStoredUser = () => {
  const user = getStoredValue('user', null);
  if (!user) return null;
  
  // Load photoUrl from separate cache (stored by apiService.getUserPhoto())
  // This prevents the user object from exceeding 1MB localStorage limit
  try {
    const cachedPhoto = localStorage.getItem('user_profile_photo');
    const cachedTimestamp = localStorage.getItem('user_profile_photo_timestamp');
    
    if (cachedPhoto && cachedPhoto !== 'null' && cachedTimestamp) {
      // Check if cache is still valid (24 hours)
      const cacheAge = Date.now() - parseInt(cachedTimestamp);
      const cacheExpiryMs = 24 * 60 * 60 * 1000; // 24 hours
      
      if (cacheAge < cacheExpiryMs) {
        user.photoUrl = cachedPhoto;
      }
    }
  } catch (error) {
    // Silently fail
  }
  
  return user;
};

// Helper function to filter enabled subscriptions
// Only includes subscriptions with state: 'Enabled' (case-insensitive)
// Excludes: 'Disabled', 'Suspended', 'PastDue', 'Warned', etc.
export const filterEnabledSubscriptions = (subscriptions: any[]): Subscription[] => {
  return subscriptions.filter((sub: any) => 
    sub.state && sub.state.toLowerCase() === 'enabled'
  );
};

// Helper function to get visible subscriptions based on user preference
export const getVisibleSubscriptions = (
  allSubscriptions: Subscription[],
  filteredIds: string[],
  useFilteredOnly: boolean
): Subscription[] => {
  if (!useFilteredOnly || filteredIds.length === 0) {
    return allSubscriptions;
  }
  return allSubscriptions.filter(sub => filteredIds.includes(sub.subscription_id));
};

// Types
export interface Subscription {
  subscription_id: string;
  display_name: string;
  state: string;
  tenant_id: string;
}

export interface ResourceGroup {
  name: string;
  location: string;
  id: string;
  tags: Record<string, string>;
  provisioning_state: string;
}

export interface AzureResource {
  id: string;
  name: string;
  type: string;
  location: string;
  resource_group: string;
  tags: Record<string, string>;
  sku?: any;
  kind?: string;
  properties?: Record<string, any>;
}

// Conversation session interface
export interface ConversationSession {
  id: string;
  title: string;
  messages: Array<{
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    message_type: 'user' | 'ai' | 'error' | 'loading' | 'rca_result';
    timestamp: string;
    query_result?: any;
    actions?: Array<{ label: string; query: string }>;
  }>;
  conversationId: string | null;
  createdAt: number;
  updatedAt: number;
  messageCount?: number; // Total message count from backend (for sessions not yet loaded)
}

export interface QueryResult {
  query_type: string;
  results?: any[]; // Optional for cost queries
  row_count?: number; // Optional for cost queries
  cost_data?: any; // Cost query data (daily_trend, breakdown, etc.)
  explanation?: string;
  original_query?: string;
  structured_query?: any;
}

// Workspace interface
export interface Workspace {
  workspace_id: string;
  name: string;
  resource_group: string;
  subscription_id: string;
  subscription_name?: string;
  location?: string;
}

// Store interface
interface AppStore {
  // User state
  user: any | null;
  isAuthenticated: boolean;
  
  // Azure data
  subscriptions: Subscription[];
  selectedSubscriptionId: string | null;
  selectedSubscriptions: string[]; // Shared multi-subscription filter for dashboard and costs
  resourceGroups: ResourceGroup[];
  resources: AzureResource[];
  workspaces: Workspace[];
  
  // Query state - separate for each mode
  naturalLanguageQuery: string;
  kqlQuery: string;
  queryResults: QueryResult | null;
  queryHistory: Array<{
    query: string;
    type: 'natural' | 'kql';
    results: QueryResult | null;
    timestamp: number;
  }>;
  
  // Conversation sessions (max 5)
  conversationSessions: ConversationSession[];
  activeSessionId: string | null;
  
  isLoading: boolean;
  tokensReady: boolean;
  firewallCtx: FirewallContext | null;
  isLoadingConversations: boolean; // Track if loading conversations from backend
  error: string | null;
  
  // UI state
  selectedResourceType: string;
  selectedResource: string;
  selectedResourceGroup: string;
  selectedWorkspaceId: string;
  globalScope: boolean;
  messageLayoutStyle: 'bubble' | 'fullwidth'; // User preference for message display
  filteredSubscriptionIds: string[]; // User-selected subscriptions to show in filters
  useFilteredSubscriptionsOnly: boolean; // Preference to use filtered subscriptions
  rcaFeatureEnabled: boolean; // User preference to enable RCA feature
  
  // Selected saved views (persisted per view type)
  selectedCostViewId: string | null;
  selectedQueryViewId: string | null;
  selectedDashboardViewId: string | null;
  
  // Actions
  setUser: (user: any) => void;
  setSubscriptions: (subscriptions: Subscription[]) => void;
  setSelectedSubscription: (subscriptionId: string) => void;
  setSelectedSubscriptions: (subscriptionIds: string[]) => void;
  setResourceGroups: (resourceGroups: ResourceGroup[]) => void;
  setResources: (resources: AzureResource[]) => void;
  setWorkspaces: (workspaces: Workspace[]) => void;
  setNaturalLanguageQuery: (query: string) => void;
  setKqlQuery: (query: string) => void;
  setQueryResults: (results: QueryResult | null) => void;
  addToQueryHistory: (query: string, type: 'natural' | 'kql', results: QueryResult | null) => void;
  clearQueryHistory: () => void;
  rehydrateQueryHistory: () => void;
  
  // Conversation session actions
  createNewSession: () => string;
  setActiveSession: (sessionId: string) => void;
  updateSessionMessages: (sessionId: string, messages: ConversationSession['messages']) => void;
  updateSessionTitle: (sessionId: string, title: string) => void;
  updateSessionConversationId: (sessionId: string, conversationId: string) => void;
  deleteSession: (sessionId: string) => void;
  getActiveSession: () => ConversationSession | null;
  loadConversationsFromBackend: () => Promise<void>; // Load from Redis/backend
  setConversationSessions: (sessions: ConversationSession[]) => void; // Set sessions from API
  
  setLoading: (loading: boolean) => void;
  setTokensReady: (ready: boolean) => void;
  setFirewallCtx: (ctx: FirewallContext | null) => void;
  setError: (error: string | null) => void;
  setSelectedResourceType: (type: string) => void;
  setSelectedResource: (resource: string) => void;
  setSelectedResourceGroup: (resourceGroup: string) => void;
  setSelectedWorkspaceId: (workspaceId: string) => void;
  setGlobalScope: (globalScope: boolean) => void;
  setMessageLayoutStyle: (style: 'bubble' | 'fullwidth') => void;
  setFilteredSubscriptionIds: (ids: string[]) => void;
  setUseFilteredSubscriptionsOnly: (use: boolean) => void;
  setRcaFeatureEnabled: (enabled: boolean) => void;
  setSelectedCostViewId: (viewId: string | null) => void;
  setSelectedQueryViewId: (viewId: string | null) => void;
  setSelectedDashboardViewId: (viewId: string | null) => void;
  clearError: () => void;
  reset: () => void;
}

// Initial state
const initialState = {
  user: getStoredUser(),
  isAuthenticated: false,
  subscriptions: [],
  selectedSubscriptionId: getStoredValue('selectedSubscriptionId', null),
  selectedSubscriptions: getStoredValue<string[]>('selectedSubscriptions', []),
  resourceGroups: [],
  resources: [],
  workspaces: [],
  naturalLanguageQuery: getStoredValue('naturalLanguageQuery', ''),
  kqlQuery: getStoredValue('kqlQuery', ''),
  queryResults: getStoredValue('queryResults', null),
  queryHistory: getStoredValue<Array<{query: string; type: 'natural' | 'kql'; results: QueryResult | null; timestamp: number}>>('queryHistory', []),
  conversationSessions: [], // Load from API, not localStorage
  activeSessionId: null,
  isLoading: false,
  tokensReady: false,
  firewallCtx: getStoredValue<FirewallContext | null>('firewallCtx', null),
  isLoadingConversations: false, // Track conversation loading state
  error: null,
  selectedResourceType: getStoredValue('selectedResourceType', ''),
  selectedResource: getStoredValue('selectedResource', ''),
  selectedResourceGroup: getStoredValue('selectedResourceGroup', ''),
  selectedWorkspaceId: getStoredValue('selectedWorkspaceId', ''),
  globalScope: getStoredValue('globalScope', false),
  messageLayoutStyle: getStoredValue<'bubble' | 'fullwidth'>('messageLayoutStyle', 'fullwidth'),
  filteredSubscriptionIds: getStoredValue<string[]>('filteredSubscriptionIds', []),
  useFilteredSubscriptionsOnly: getStoredValue<boolean>('useFilteredSubscriptionsOnly', false),
  rcaFeatureEnabled: getStoredValue<boolean>('rcaFeatureEnabled', false),
  selectedCostViewId: getStoredValue<string | null>('selectedCostViewId', null),
  selectedQueryViewId: getStoredValue<string | null>('selectedQueryViewId', null),
  selectedDashboardViewId: getStoredValue<string | null>('selectedDashboardViewId', null),
};

// Create store
export const useAppStore = create<AppStore>((set, get) => ({
  ...initialState,
  
  setUser: (user) => {
    setStoredValue('user', user);
    set({ 
      user, 
      isAuthenticated: !!user 
    });
  },
  
  setSubscriptions: (subscriptions) => set({ 
    subscriptions,
    // Auto-select first subscription if none selected
    selectedSubscriptionId: get().selectedSubscriptionId || 
      (subscriptions.length > 0 ? subscriptions[0].subscription_id : null)
  }),
  
  setSelectedSubscription: (subscriptionId) => {
    const currentSubscriptionId = get().selectedSubscriptionId;
    setStoredValue('selectedSubscriptionId', subscriptionId);
    
    // Only clear dependent data if subscription actually changed
    if (currentSubscriptionId !== subscriptionId) {
      set({ 
        selectedSubscriptionId: subscriptionId,
        // Clear dependent data when subscription changes
        resourceGroups: [],
        resources: [],
        selectedResourceGroup: '',
      });
      setStoredValue('selectedResourceGroup', '');
    } else {
      set({ selectedSubscriptionId: subscriptionId });
    }
  },
  
  setSelectedSubscriptions: (subscriptionIds) => {
    setStoredValue('selectedSubscriptions', subscriptionIds);
    set({ selectedSubscriptions: subscriptionIds });
  },
  
  setResourceGroups: (resourceGroups) => set({ resourceGroups }),
  
  setResources: (resources) => set({ resources }),
  
  setWorkspaces: (workspaces) => set({ workspaces }),
  
  setNaturalLanguageQuery: (query) => {
    setStoredValue('naturalLanguageQuery', query);
    set({ naturalLanguageQuery: query });
  },
  
  setKqlQuery: (query) => {
    setStoredValue('kqlQuery', query);
    set({ kqlQuery: query });
  },
  setQueryResults: (results) => {
    setStoredValue('queryResults', results);
    set({ queryResults: results });
  },
  
  addToQueryHistory: (query, type, results) => {
    const currentHistory = get().queryHistory;
    
    // Prune results to store only essential data
    const prunedResults = results ? {
      query_type: results.query_type,
      row_count: results.row_count,
      explanation: results.explanation?.slice(0, 200), // Limit explanation length
      // Don't store full results array or cost_data to save space
    } : null;
    
    const newEntry = {
      query,
      type,
      results: prunedResults,
      timestamp: Date.now()
    };
    
    // Keep only last 20 queries (reduced from 50)
    const updatedHistory = [newEntry, ...currentHistory].slice(0, 20);
    setStoredValue('queryHistory', updatedHistory);
    set({ queryHistory: updatedHistory });
  },
  
  clearQueryHistory: () => {
    setStoredValue('queryHistory', []);
    set({ queryHistory: [] });
  },

  rehydrateQueryHistory: () => {
    if (typeof window === 'undefined') return;
    try {
      const raw = window.localStorage.getItem('queryHistory');
      if (raw) set({ queryHistory: JSON.parse(raw) });
    } catch {}
  },
  
  createNewSession: () => {
    const now = Date.now();
    const sessionId = `session-${now}`;
    const newSession: ConversationSession = {
      id: sessionId,
      title: `Chat ${now}`,
      messages: [],
      conversationId: null,
      createdAt: now,
      updatedAt: now,
    };
    
    const currentSessions = get().conversationSessions;
    let updatedSessions = [...currentSessions, newSession];
    
    // Keep only last 5 sessions (delete oldest if we have more than 5)
    if (updatedSessions.length > 5) {
      updatedSessions = updatedSessions.slice(-5);
    }
    
    // Only store in Zustand state, NOT localStorage (syncs via backend/Redis)
    set({ 
      conversationSessions: updatedSessions,
      activeSessionId: sessionId 
    });
    
    return sessionId;
  },
  
  setActiveSession: (sessionId) => {
    set({ activeSessionId: sessionId });
  },
  
  updateSessionMessages: (sessionId, messages) => {
    const sessions = get().conversationSessions;
    
    const updatedSessions = sessions.map(session => {
      if (session.id !== sessionId) return session;
      
      // Determine if we should auto-generate title
      // Only auto-generate if:
      // 1. Title starts with 'Chat ' (default generated title), OR
      // 2. Session has conversationId (loaded from backend) and messageCount > 0 (already has a title)
      const shouldKeepExistingTitle = session.conversationId && (session.messageCount ?? 0) > 0;
      const shouldAutoGenerateTitle = session.title.startsWith('Chat ') && messages.length > 0 && messages[0].role === 'user';
      
      let newTitle = session.title;
      if (!shouldKeepExistingTitle && shouldAutoGenerateTitle) {
        newTitle = messages[0].content.slice(0, 50) + (messages[0].content.length > 50 ? '...' : '');
      }
      
      return { 
        ...session, 
        messages, 
        updatedAt: Date.now(),
        title: newTitle
      };
    });
    
    // Only store in Zustand state, NOT localStorage
    set({ conversationSessions: updatedSessions });
  },
  
  updateSessionTitle: (sessionId, title) => {
    const sessions = get().conversationSessions;
    const updatedSessions = sessions.map(session => 
      session.id === sessionId ? { ...session, title, updatedAt: Date.now() } : session
    );
    
    // Only store in Zustand state, NOT localStorage
    set({ conversationSessions: updatedSessions });
  },
  
  updateSessionConversationId: (sessionId, conversationId) => {
    const sessions = get().conversationSessions;
    const updatedSessions = sessions.map(session => 
      session.id === sessionId ? { ...session, conversationId, updatedAt: Date.now() } : session
    );
    
    // Only store in Zustand state, NOT localStorage
    set({ conversationSessions: updatedSessions });
  },
  
  deleteSession: (sessionId) => {
    const sessions = get().conversationSessions;
    const updatedSessions = sessions.filter(s => s.id !== sessionId);
    
    // If we deleted the active session, set active to the most recent session
    const currentActiveId = get().activeSessionId;
    let newActiveId = currentActiveId;
    
    if (currentActiveId === sessionId) {
      newActiveId = updatedSessions.length > 0 ? updatedSessions[updatedSessions.length - 1].id : null;
    }
    
    // Only store in Zustand state, NOT localStorage
    set({ 
      conversationSessions: updatedSessions,
      activeSessionId: newActiveId 
    });
  },
  
  loadConversationsFromBackend: async () => {
    set({ isLoadingConversations: true });
    try {
      console.log(' Loading conversations from backend...');
      
      // Use apiService which has correct baseURL configured
      const { apiService } = await import('../services/apiService');
      
      console.log(' Request details:', {
        url: '/api/ai/conversations',
        hasManagementToken: !!apiService['managementToken'],
        hasIdToken: !!apiService['idToken']
      });
      
      const data = await apiService.request({
        method: 'GET',
        url: '/api/ai/conversations',
      });
      
      const conversations = data.conversations || [];
      console.log(` Loaded ${conversations.length} conversations from backend`, conversations);
        
        // Map backend conversations to frontend session format
        const sessions: ConversationSession[] = conversations.map((conv: any) => {
          // Use preview (first message) for title, fallback to last message if needed
          const firstMessage = conv.preview || conv.last_message?.content || `Chat ${conv.id.slice(0, 8)}`;
          const title = firstMessage.length > 50 ? firstMessage.slice(0, 50) + '...' : firstMessage;
          
          return {
            id: `session-${conv.id}`, // Prefix to avoid ID collision
            title: title,
            messages: [], // Messages will be loaded when session is opened
            conversationId: conv.id, // Store backend conversation ID
            createdAt: new Date(conv.created_at).getTime(),
            updatedAt: new Date(conv.updated_at).getTime(),
            messageCount: conv.message_count || 0, // Store count from backend
          };
        });
        
        console.log(' Mapped to sessions:', sessions);
        set({ conversationSessions: sessions, isLoadingConversations: false });
        
        // Set first session as active if none selected
        if (sessions.length > 0 && !get().activeSessionId) {
          console.log(` Setting active session: ${sessions[0].id}`);
          set({ activeSessionId: sessions[0].id });
        }
    } catch (error) {
      console.error(' Failed to load conversations from backend:', error);
      set({ isLoadingConversations: false });
    }
  },
  
  setConversationSessions: (sessions) => {
    set({ conversationSessions: sessions });
  },
  
  getActiveSession: () => {
    const { conversationSessions, activeSessionId } = get();
    return conversationSessions.find(s => s.id === activeSessionId) || null;
  },
  
  setLoading: (loading) => set({ isLoading: loading }),
  setTokensReady: (ready) => set({ tokensReady: ready }),
  setFirewallCtx: (ctx) => {
    setStoredValue('firewallCtx', ctx);
    set({ firewallCtx: ctx });
  },
  
  setError: (error) => set({ error }),
  
  setSelectedResourceType: (type) => {
    setStoredValue('selectedResourceType', type);
    set({ selectedResourceType: type });
  },
  
  setSelectedResource: (resource) => {
    setStoredValue('selectedResource', resource);
    set({ selectedResource: resource });
  },
  
  setSelectedResourceGroup: (resourceGroup) => {
    setStoredValue('selectedResourceGroup', resourceGroup);
    set({ selectedResourceGroup: resourceGroup });
  },
  
  setSelectedWorkspaceId: (workspaceId) => {
    setStoredValue('selectedWorkspaceId', workspaceId);
    set({ selectedWorkspaceId: workspaceId });
  },
  
  setGlobalScope: (globalScope) => {
    setStoredValue('globalScope', globalScope);
    set({
      globalScope,
      // Clear subscription-specific filters when enabling global scope
      ...(globalScope ? {
        selectedSubscriptionId: null,
        selectedResourceGroup: '',
        selectedResourceType: '',
        selectedResource: '',
      } : {})
    });
    if (globalScope) {
      setStoredValue('selectedSubscriptionId', null);
      setStoredValue('selectedResourceGroup', '');
      setStoredValue('selectedResourceType', '');
      setStoredValue('selectedResource', '');
    }
  },
  
  setMessageLayoutStyle: (style) => {
    setStoredValue('messageLayoutStyle', style);
    set({ messageLayoutStyle: style });
  },
  
  setFilteredSubscriptionIds: (ids) => {
    setStoredValue('filteredSubscriptionIds', ids);
    set({ filteredSubscriptionIds: ids });
  },
  
  setUseFilteredSubscriptionsOnly: (use) => {
    setStoredValue('useFilteredSubscriptionsOnly', use);
    set({ useFilteredSubscriptionsOnly: use });
  },
  
  setRcaFeatureEnabled: (enabled) => {
    setStoredValue('rcaFeatureEnabled', enabled);
    set({ rcaFeatureEnabled: enabled });
  },
  
  setSelectedCostViewId: (viewId) => {
    setStoredValue('selectedCostViewId', viewId);
    set({ selectedCostViewId: viewId });
  },
  
  setSelectedQueryViewId: (viewId) => {
    setStoredValue('selectedQueryViewId', viewId);
    set({ selectedQueryViewId: viewId });
  },
  
  setSelectedDashboardViewId: (viewId) => {
    setStoredValue('selectedDashboardViewId', viewId);
    set({ selectedDashboardViewId: viewId });
  },
  
  clearError: () => set({ error: null }),
  
  reset: () => {
    // Clear photo cache on reset/logout
    try {
      localStorage.removeItem('user_profile_photo');
      localStorage.removeItem('user_profile_photo_timestamp');
    } catch (error) {
      console.warn('Error clearing photo cache on reset:', error);
    }
    set(initialState);
  },
}));
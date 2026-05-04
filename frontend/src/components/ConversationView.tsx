'use client';

import React, { useState, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ChatMessage from './ChatMessage';
import CirrusAvatar from './CirrusAvatar';
import { apiService } from '../services/apiService';
import toast from 'react-hot-toast';
import { trackEvent, trackException } from '../utils/appInsights';

import { useAppStore } from '../store/appStore';
import TemplateBrowser from './TemplateBrowser';
import SaveTemplateModal from './SaveTemplateModal';
import TemplateSuggestions from './TemplateSuggestions';
import RcaResultCard from './RcaResultCard';
import type { QueryTemplateListItem } from '../types/templates';
import { BookmarkIcon, FolderOpenIcon, PlusCircleIcon } from '@heroicons/react/24/outline';

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  message_type: 'user' | 'ai' | 'error' | 'loading' | 'rca_result';
  timestamp: string;
  query_result?: any;
  actions?: Array<{ label: string; query: string }>;
}

// Inline markdown renderer for RCA card content — bold, code, inline text only.
// Defined at module scope so React never sees a new component type on re-renders.
const RcaMd = ({ children }: { children: string }) => (
  <ReactMarkdown
    remarkPlugins={[remarkGfm]}
    components={{
      p: ({ node, ...props }: any) => <span {...props} className="leading-relaxed" />,
      ul: ({ node, ...props }: any) => <ul {...props} className="list-none space-y-1.5 mt-1" />,
      li: ({ node, ...props }: any) => <li {...props} className="leading-relaxed text-gray-700" />,
      strong: ({ node, ...props }: any) => <strong {...props} className="font-semibold text-gray-900" />,
      code: ({ node, inline, ...props }: any) => (
        inline ? <code {...props} className="px-1 py-0.5 bg-gray-100 dark:bg-gray-700 rounded-md text-xs font-mono text-gray-800 dark:text-gray-200" /> : <code {...props} />
      ),
      h1: ({ node, ...props }: any) => <strong {...props} />,
      h2: ({ node, ...props }: any) => <strong {...props} />,
      h3: ({ node, ...props }: any) => <strong {...props} />,
    }}
  >{children}</ReactMarkdown>
);

interface ConversationViewProps {
  selectedFilters: {
    subscription?: string;
    resourceGroup?: string;
    resourceType?: string;
    resource?: string;
    globalScope?: boolean;
  };
  isMaximized?: boolean;
  onToggleMaximize?: () => void;
  initialQuery?: string;
  autoExecute?: boolean;
}

export default function ConversationView({ 
  selectedFilters, 
  isMaximized = false, 
  onToggleMaximize,
  initialQuery,
  autoExecute = false
}: ConversationViewProps) {
  // Get session management from store
  const { 
    conversationSessions,
    activeSessionId,
    isLoadingConversations,
    createNewSession,
    setActiveSession,
    updateSessionMessages,
    updateSessionConversationId,
    getActiveSession,
    addToQueryHistory,
    loadConversationsFromBackend
  } = useAppStore();
  
  // Load conversations from backend AFTER auth is ready
  useEffect(() => {
    // Don't load if auth is not complete
    // Wait a moment for AuthWrapper to set tokens
    const timer = setTimeout(() => {
      loadConversationsFromBackend();
    }, 500); // Small delay to ensure auth completes first
    
    return () => clearTimeout(timer);
  }, [loadConversationsFromBackend]);
  
  // Initialize session if needed (only after loading is complete and no conversations exist)
  useEffect(() => {
    console.log(' Check init:', { isLoadingConversations, conversationSessions: conversationSessions.length, activeSessionId });
    
    // Only create new session if:
    // 1. Not currently loading from backend
    // 2. No active session
    // 3. No conversations exist
    if (!isLoadingConversations && !activeSessionId && conversationSessions.length === 0) {
      console.log(' Creating new empty session');
      createNewSession();
    }
  }, [isLoadingConversations, conversationSessions.length, activeSessionId, createNewSession]);
  
  // Get active session
  const activeSession = getActiveSession();
  
  // Initialize messages from active session
  const [messages, setMessages] = useState<Message[]>(activeSession?.messages || []);
  const [conversationId, setConversationId] = useState<string | null>(activeSession?.conversationId || null);
  
  // Sync messages with active session when it changes
  useEffect(() => {
    console.log(' Active session changed:', activeSessionId, 'Previous:', previousSessionIdRef.current);
    
    // Save messages from the previous session before switching
    if (previousSessionIdRef.current && previousSessionIdRef.current !== activeSessionId) {
      console.log(' Saving messages for previous session:', previousSessionIdRef.current);
      updateSessionMessages(previousSessionIdRef.current, messages);
    }
    
    // Load messages from the new active session
    if (activeSession) {
      console.log(' Active session:', activeSession.id, 'ConversationID:', activeSession.conversationId, 'Messages:', activeSession.messages.length);
      
      // If session has a conversationId but no messages, load from backend
      if (activeSession.conversationId && activeSession.messages.length === 0) {
        console.log(' Fetching conversation from backend:', activeSession.conversationId);
        
        // Fetch full conversation from backend
        const loadConversation = async () => {
          try {
            const response = await apiService.request({
              method: 'GET',
              url: `/api/ai/conversations/${activeSession.conversationId}`,
            });
            
            console.log(' Loaded conversation from backend:', response);
            
            if (response && response.messages) {
              const loadedMessages = response.messages.map((msg: any) => ({
                id: msg.id,
                role: msg.role,
                content: msg.content,
                message_type: msg.message_type,
                timestamp: msg.timestamp,
                query_result: msg.query_result,
                actions: msg.actions,
              }));
              
              console.log(` Setting ${loadedMessages.length} messages`);
              setMessages(loadedMessages);
              // Update session with loaded messages
              updateSessionMessages(activeSessionId!, loadedMessages);
            }
          } catch (error) {
            console.error(' Failed to load conversation from backend:', error);
            // Fallback to empty messages
            setMessages([]);
          }
        };
        
        loadConversation();
      } else {
        // Use messages from session
        console.log(' Using messages from session cache');
        setMessages(activeSession.messages);
      }
      
      setConversationId(activeSession.conversationId);
    } else {
      // If no active session, reset to empty
      console.log(' No active session');
      setMessages([]);
      setConversationId(null);
    }
    
    // Update the previous session ref
    previousSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);
  
  // DO NOT auto-save on every message change - it causes race conditions
  // Messages are saved:
  // 1. When switching sessions (in the useEffect above)
  // 2. When adding new messages (in handleSendMessage)
  // 3. When creating new conversation (in handleNewConversation)
  
  // Save conversation ID when it changes
  useEffect(() => {
    if (activeSessionId && conversationId) {
      updateSessionConversationId(activeSessionId, conversationId);
    }
  }, [conversationId, activeSessionId]);
  
  const [inputValue, setInputValue] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);

  const [showHistory, setShowHistory] = useState(false);
  const [showTemplateBrowser, setShowTemplateBrowser] = useState(false);
  const [showSaveTemplateModal, setShowSaveTemplateModal] = useState(false);
  const [rcaHours, setRcaHours] = useState(24);
  const [rcaUseCustomTime, setRcaUseCustomTime] = useState(false);
  const [rcaCustomStart, setRcaCustomStart] = useState('');
  const [rcaCustomEnd, setRcaCustomEnd] = useState('');
  const [rcaLoading, setRcaLoading] = useState(false);
  const [rcaResult, setRcaResult] = useState<any | null>(null);
  const [userTemplates, setUserTemplates] = useState<QueryTemplateListItem[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const loadingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const hasAutoExecuted = useRef(false);
  const previousSessionIdRef = useRef<string | null>(activeSessionId);
  const isAnyLoading = isLoading || rcaLoading;
  const isComposerLoading = isLoading;

  const toLocalDateTimeInput = (value: Date) => {
    const offsetMs = value.getTimezoneOffset() * 60000;
    return new Date(value.getTime() - offsetMs).toISOString().slice(0, 16);
  };

  const nowLocalInput = toLocalDateTimeInput(new Date());
  const minCustomStartInput = toLocalDateTimeInput(new Date(Date.now() - 92 * 24 * 60 * 60 * 1000));
  
  // Load user templates to check if query already exists
  useEffect(() => {
    const loadUserTemplates = async () => {
      try {
        const response = await apiService.getTemplates(true); // Load ALL templates (user + public + system)
        setUserTemplates(response.templates || []);
      } catch (error) {
        console.error('Failed to load templates:', error);
      }
    };
    loadUserTemplates();
  }, []);
  
  // Reload templates after saving a new one
  const reloadTemplates = async () => {
    try {
      const response = await apiService.getTemplates(true); // Load ALL templates
      setUserTemplates(response.templates || []);
    } catch (error) {
      console.error('Failed to reload templates:', error);
    }
  };
  
  // Check if current query already exists in templates
  const isQueryAlreadySaved = () => {
    if (!inputValue || !inputValue.trim()) return false;
    const trimmedQuery = inputValue.trim().toLowerCase();
    return userTemplates.some(template => 
      template.query.trim().toLowerCase() === trimmedQuery
    );
  };

  // Get resources from store to find full resource ID
  const resources = useAppStore((state) => state.resources);
  const selectedSubscriptions = useAppStore((state) => state.selectedSubscriptions);
  const rcaFeatureEnabled = false; // RCA is now always-on via natural language; no explicit mode needed

  const getSelectedResourceId = () => {
    if (!selectedFilters.resource || !resources || resources.length === 0) {
      return undefined;
    }

    const selectedResourceObj = resources.find((resource) => resource.name === selectedFilters.resource);
    if (!selectedResourceObj) {
      console.warn(` Could not find full resource ID for '${selectedFilters.resource}' in resources array`);
      return undefined;
    }

    return selectedResourceObj.id;
  };

  const getRcaSubscriptionScope = () => {
    if (selectedFilters.globalScope && selectedSubscriptions.length > 0) {
      return selectedSubscriptions;
    }

    if (selectedFilters.subscription) {
      return [selectedFilters.subscription];
    }

    return [];
  };

  // Handle initial query and auto-execute
  useEffect(() => {
    if (initialQuery) {
      // If initialQuery changed, reset the flag
      const queryChanged = inputValue !== initialQuery;
      if (queryChanged) {
        hasAutoExecuted.current = false;
      }
      
      if (!hasAutoExecuted.current) {
        setInputValue(initialQuery);
        hasAutoExecuted.current = true;
        
        // Auto-execute if requested
        if (autoExecute) {
          // Small delay to ensure component is fully mounted
          setTimeout(() => {
            handleSendMessage(initialQuery);
          }, 100);
        }
      }
    }
  }, [initialQuery, autoExecute]);

  // Auto-scroll to bottom when new messages arrive (not on initial empty mount)
  useEffect(() => {
    if (messages.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);



  const handleSendMessage = async (messageText?: string) => {
    const textToSend = messageText || inputValue;
    if (!textToSend.trim() || isLoading) return;

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: textToSend,
      message_type: 'user',
      timestamp: new Date().toISOString(),
    };

    // Add user message immediately
    setMessages((prev) => [...prev, userMessage]);
    setInputValue(''); // Clear input immediately to stop template suggestions
    setIsLoading(true);

    // Add loading indicator — content is intentionally empty;
    // the ThinkingBubble component in ChatMessage handles its own
    // elapsed-time-based phase display with no hardcoded strings.
    const loadingMessage: Message = {
      id: `loading-${Date.now()}`,
      role: 'assistant',
      content: '',
      message_type: 'loading',
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, loadingMessage]);

    // Record to history immediately on send
    addToQueryHistory(userMessage.content, 'natural', null);

    try {
      const fullResourceId = getSelectedResourceId();
      if (fullResourceId) {
        console.log(` Found full resource ID for '${selectedFilters.resource}': ${fullResourceId}`);
      }

      const requestData = {
        query: userMessage.content,
        conversation_id: conversationId,
        context: {
          subscription: selectedFilters.subscription,
          resource_group: selectedFilters.resourceGroup,
          resource_type: selectedFilters.resourceType,
          resource_id: fullResourceId, // Use full resource ID, not just name
          globalScope: selectedFilters.globalScope || false,
        },
      };

      console.log(' Sending conversation query:', requestData);

      const queryStart = Date.now();
      trackEvent('ai_query_submitted', {
        query_length: userMessage.content.length,
        has_subscription: !!selectedFilters.subscription,
        has_resource_group: !!selectedFilters.resourceGroup,
        has_resource: !!selectedFilters.resource,
        global_scope: selectedFilters.globalScope ?? false,
        conversation_id: conversationId ?? 'new',
      });

      const response = await apiService.request({
        method: 'POST',
        url: '/api/ai/query',
        data: requestData,
      });

      trackEvent('ai_query_completed', {
        duration_ms: Date.now() - queryStart,
        message_type: response.message_type,
        conversation_id: response.conversation_id,
      });

      console.log(' Received response:', response);
      console.log('Response fields:', {
        message_id: response.message_id,
        conversation_id: response.conversation_id,
        content: response.content,
        message_type: response.message_type,
        timestamp: response.timestamp
      });

      // Remove loading message
      setMessages((prev) => prev.filter((m) => m.id !== loadingMessage.id));

      // Update conversation ID if new
      if (response.conversation_id && !conversationId) {
        setConversationId(response.conversation_id);
        // CRITICAL: Update the session's conversationId so it persists in Redis
        if (activeSessionId) {
          updateSessionConversationId(activeSessionId, response.conversation_id);
        }
      }

      // Add AI response
      const aiMessage: Message = {
        id: response.message_id,
        role: 'assistant',
        content: response.content,
        message_type: response.message_type,
        timestamp: response.timestamp,
        query_result: response.query_result,
        actions: response.actions,
      };

      setMessages((prev) => [...prev, aiMessage]);
      
      // If the AI detected an incident/RCA intent, run inline and render in chat thread
      if (response.rca_trigger) {
        const loadingId = `rca-loading-${Date.now()}`;
        setMessages(prev => [...prev, {
          id: loadingId,
          role: 'assistant',
          content: '',
          message_type: 'loading',
          timestamp: new Date().toISOString(),
        }]);
        try {
          const subscriptionScope = getRcaSubscriptionScope();
          const rcaResult = await apiService.getRootCauseAnalysis({
            incident_summary: userMessage.content,
            subscription_ids: subscriptionScope,
            resource_id: getSelectedResourceId(),
            resource_group: selectedFilters.resourceGroup,
            hours: (response.rca_start && response.rca_end) ? undefined
              : (typeof response.rca_hours === 'number' && response.rca_hours > 0
                  ? Math.min(response.rca_hours, 24) : 24),
            custom_start: response.rca_start || undefined,
            custom_end: response.rca_end || undefined,
            include_external_status: true,
          });
          setMessages(prev => prev
            .filter(m => m.id !== loadingId)
            .concat({
              id: `rca-result-${Date.now()}`,
              role: 'assistant',
              content: '',
              message_type: 'rca_result',
              timestamp: new Date().toISOString(),
              query_result: rcaResult,
            })
          );
        } catch {
          setMessages(prev => prev.filter(m => m.id !== loadingId));
          setMessages(prev => [...prev, {
            id: `error-${Date.now()}`,
            role: 'assistant',
            content: 'The root cause analysis could not be completed. The service may be temporarily unavailable — please try again in a moment.',
            message_type: 'error',
            timestamp: new Date().toISOString(),
          }]);
        }
      }
      
      // Save updated messages to store after successful response
      if (activeSessionId) {
        // We need to save with the updated messages array
        updateSessionMessages(activeSessionId, [...messages, userMessage, aiMessage]);
      }
    } catch (error: any) {
      // Remove loading message
      setMessages((prev) => prev.filter((m) => m.id !== loadingMessage.id));

      console.error('Conversation query error:', error);
      trackException(error instanceof Error ? error : new Error('AI query failed'), {
        url: '/api/ai/query',
        status: error.response?.status,
        has_subscription: !!selectedFilters.subscription,
      });

      // Translate technical error codes into calm, actionable human language.
      // Never expose raw backend strings — they carry no value for the user.
      const status: number | undefined = error.response?.status;
      const isNetworkError = !error.response && (error.code === 'ERR_NETWORK' || error.message?.toLowerCase().includes('network'));
      const isTimeout = error.code === 'ECONNABORTED' || error.message?.toLowerCase().includes('timeout');

      let errorContent: string;
      if (isTimeout) {
        errorContent = 'This request took too long to complete. Your query may be too broad — try narrowing the scope or time range and ask again.';
      } else if (isNetworkError) {
        errorContent = "I couldn't reach the service right now. Check your connection and try again.";
      } else if (status === 401 || status === 403) {
        errorContent = 'Your session may have expired. Refreshing the page should restore access.';
      } else if (status === 429) {
        errorContent = 'Too many requests are being processed right now. Wait a few seconds and try again.';
      } else if (status && status >= 500) {
        errorContent = 'The service encountered an unexpected issue. This is usually temporary — try again in a moment.';
      } else {
        errorContent = "I wasn't able to complete that request. Try rephrasing your question or narrowing the scope.";
      }

      const errorMessage: Message = {
        id: `error-${Date.now()}`,
        role: 'assistant',
        content: errorContent,
        message_type: 'error',
        timestamp: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, errorMessage]);
      // No toast — the in-chat message is the signal; a simultaneous toast is noise.

      if (activeSessionId) {
        updateSessionMessages(activeSessionId, [...messages, userMessage, loadingMessage, errorMessage]);
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleActionClick = (query: string) => {
    setInputValue(query);
    inputRef.current?.focus();
  };

  const handleRerunQuery = (query: string) => {
    handleSendMessage(query);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Template handlers
  const handleSelectTemplate = (template: QueryTemplateListItem) => {
    console.log(' Template selected:', template.name, 'Query:', template.query);
    setInputValue(template.query);
    setShowTemplateBrowser(false);
    toast.success(`Using template: ${template.name}`);
    
    // Focus the input after a brief delay
    setTimeout(() => {
      if (inputRef.current) {
        inputRef.current.focus();
      }
    }, 100);
  };

  const handleTemplatesSaved = () => {
    toast.success('Template saved! You can find it in the template browser.');
    reloadTemplates(); // Reload templates so the button disappears
  };

  const handleNewConversation = () => {
    // Don't create a new session if current one is empty (button should be disabled)
    if (messages.length === 0) {
      return;
    }
    
    // Save current session before creating new one
    if (activeSessionId && messages.length > 0) {
      updateSessionMessages(activeSessionId, messages);
    }
    
    // Create a new session - this will update activeSessionId in the store
    createNewSession();
    
    // IMPORTANT: Don't clear messages here - let the useEffect handle it when activeSessionId changes
    // This prevents the race condition where we save empty messages
    setConversationId(null);
    setInputValue('');
    
    toast.success('Started new conversation');
  };

  const handleRunRca = async (incidentTextOverride?: string) => {
    const incidentText = (incidentTextOverride || inputValue).trim();
    if (!incidentText) {
      toast.error('Add an incident summary to run root cause analysis');
      return;
    }

    const subscriptionScope = getRcaSubscriptionScope();
    if (subscriptionScope.length === 0) {
      toast.error('Select a subscription or enable Global Scope before running RCA');
      return;
    }

    if (rcaUseCustomTime) {
      if (!rcaCustomStart || !rcaCustomEnd) {
        toast.error('Choose both start and end time for custom RCA window');
        return;
      }

      const start = new Date(rcaCustomStart);
      const end = new Date(rcaCustomEnd);
      if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
        toast.error('Invalid custom time range');
        return;
      }

      if (end <= start) {
        toast.error('End time must be later than start time');
        return;
      }

      const windowHours = (end.getTime() - start.getTime()) / (1000 * 3600);
      if (windowHours > 24) {
        toast.error('RCA window cannot exceed 24 hours — pick a narrower range');
        return;
      }

      if (end > new Date()) {
        toast.error('Custom end time cannot be in the future');
        return;
      }
    }

    try {
      setRcaLoading(true);
      setRcaResult(null);

      const result = await apiService.getRootCauseAnalysis({
        incident_summary: incidentText,
        subscription_ids: subscriptionScope,
        resource_id: getSelectedResourceId(),
        resource_group: selectedFilters.resourceGroup,
        hours: rcaUseCustomTime ? undefined : rcaHours,
        custom_start: rcaUseCustomTime ? new Date(rcaCustomStart).toISOString() : undefined,
        custom_end: rcaUseCustomTime ? new Date(rcaCustomEnd).toISOString() : undefined,
        include_external_status: true,
      });

      setRcaResult(result);
      setInputValue('');
      toast.success('Root cause analysis completed');
    } catch (error) {
      console.error('RCA request failed:', error);
      toast.error('Failed to run root cause analysis');
    } finally {
      setRcaLoading(false);
    }
  };

  return (
    <div className={`flex flex-col bg-white rounded-2xl border border-slate-200 overflow-hidden transition-all duration-300 ${
      isMaximized ? 'fixed inset-0 z-50 rounded-none' : 'h-full'
    }`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 sm:px-6 py-3 sm:py-4 bg-white border-b border-slate-200">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0">
          <div className="flex-shrink-0 w-8 h-8 rounded-lg overflow-hidden">
            <CirrusAvatar size="sm" />
          </div>
          <div className="min-w-0">
            <h2 className="text-base sm:text-sm font-semibold text-slate-900 truncate">CIRRUS AI</h2>
            <p className="text-xs text-slate-400 hidden sm:block">Ask anything about your cloud infrastructure</p>
          </div>
        </div>
        <div className="flex items-center gap-1 sm:gap-2 flex-shrink-0">
          <button
            onClick={handleNewConversation}
            disabled={messages.length === 0}
            className={`px-2 sm:px-3 py-1.5 text-xs sm:text-sm font-medium rounded-lg transition-colors touch-manipulation ${
              messages.length === 0 
                ? 'bg-slate-100 text-slate-400 cursor-not-allowed' 
                : 'bg-slate-100 hover:bg-slate-200 active:bg-slate-300 text-slate-600'
            }`}
            title={messages.length === 0 ? 'Already on a fresh conversation' : 'Start a new conversation'}
          >
            <span className="hidden sm:inline">New Chat</span>
            <span className="sm:hidden">New</span>
          </button>

          {/* RCA Button removed — RCA is now always available via natural language */}
          
          {/* Templates Button */}
          <button
            onClick={() => setShowTemplateBrowser(true)}
            className="px-2 sm:px-3 py-1.5 text-xs sm:text-sm font-medium bg-slate-100 hover:bg-slate-200 active:bg-slate-300 text-slate-600 rounded-lg transition-colors flex items-center gap-1 sm:gap-2 touch-manipulation"
            title="Browse query templates"
          >
            <FolderOpenIcon className="w-4 h-4" />
            <span className="hidden sm:inline">Templates</span>
          </button>

          {/* Save Template Button - Only show if there's a query AND it's not already saved */}
          {inputValue && inputValue.trim() && !isQueryAlreadySaved() && (
            <button
              onClick={() => setShowSaveTemplateModal(true)}
              className="px-2 sm:px-3 py-1.5 text-xs sm:text-sm font-medium bg-emerald-50 hover:bg-emerald-100 active:bg-emerald-200 text-emerald-700 border border-emerald-200 rounded-lg transition-colors flex items-center gap-1 sm:gap-2 touch-manipulation"
              title="Save current query as template"
            >
              <PlusCircleIcon className="w-4 h-4" />
              <span className="hidden xs:inline">Save</span>
            </button>
          )}


          {/* History Dropdown Button - Show if ANY session has messages */}
          {conversationSessions.some(s => (s.messageCount && s.messageCount > 0) || s.messages.length > 0) && (
            <div className="relative">
              <button
                onClick={() => setShowHistory(!showHistory)}
                className="px-2 sm:px-3 py-1.5 text-xs sm:text-sm font-medium bg-slate-100 hover:bg-slate-200 active:bg-slate-300 text-slate-600 rounded-lg transition-colors flex items-center gap-1 sm:gap-2 touch-manipulation"
              >
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="hidden sm:inline">History</span>
              </button>
              
              {/* History Dropdown */}
              {showHistory && (
                <>
                  {/* Backdrop to close dropdown */}
                  <div 
                    className="fixed inset-0 z-[100]" 
                    onClick={() => setShowHistory(false)}
                  />
                  
                  {/* Dropdown menu */}
                  <div className="absolute right-0 mt-2 w-80 max-w-[calc(100vw-2rem)] bg-white rounded-lg shadow-xl border border-gray-200 py-2 z-[110]">
                    <div className="px-3 sm:px-4 py-2 border-b border-gray-200">
                      <h3 className="text-xs sm:text-sm font-semibold text-gray-900">Recent Conversations</h3>
                      <p className="text-xs text-gray-500 mt-0.5 hidden sm:block">Switch between your recent chats</p>
                    </div>
                    
                    <div className="max-h-[60vh] sm:max-h-96 overflow-y-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
                      {conversationSessions
                        .filter(s => (s.messageCount && s.messageCount > 0) || s.messages.length > 0)
                        .map((session, index) => {
                          // Get the most recent user message for display
                          const lastUserMessage = [...session.messages]
                            .reverse()
                            .find(m => m.role === 'user');
                          const displayTitle = lastUserMessage 
                            ? lastUserMessage.content.slice(0, 50) + (lastUserMessage.content.length > 50 ? '...' : '')
                            : session.title;
                          
                          return (
                            <button
                              key={session.id}
                              onClick={() => {
                                setActiveSession(session.id);
                                setShowHistory(false);
                              }}
                              className={`w-full text-left px-3 sm:px-4 py-2 sm:py-3 hover:bg-gray-50 transition-colors border-l-4 ${
                                session.id === activeSessionId
                                  ? 'border-indigo-500 bg-indigo-50'
                                  : 'border-transparent'
                              }`}
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2 mb-1">
                                    <span className={`flex-shrink-0 w-5 h-5 sm:w-6 sm:h-6 rounded-full flex items-center justify-center text-xs font-bold ${
                                      session.id === activeSessionId
                                        ? 'bg-indigo-500 text-white'
                                        : 'bg-gray-200 text-gray-600'
                                    }`}>
                                      {index + 1}
                                    </span>
                                    <span className={`text-xs sm:text-sm font-medium truncate ${
                                      session.id === activeSessionId ? 'text-indigo-700 font-semibold' : 'text-slate-800'
                                    }`}>
                                      {displayTitle}
                                    </span>
                                  </div>
                                  <p className="text-xs text-gray-500 ml-7 sm:ml-8">
                                    {(session.messageCount || session.messages.length)} message{(session.messageCount || session.messages.length) !== 1 ? 's' : ''}
                                  </p>
                                  <p className="text-xs text-gray-400 ml-7 sm:ml-8 hidden sm:block">
                                    {new Date(session.updatedAt).toLocaleString()}
                                  </p>
                                </div>
                                
                                {session.id === activeSessionId && (
                                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor" className="w-4 h-4 sm:w-5 sm:h-5 text-indigo-500 flex-shrink-0">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                                  </svg>
                                )}
                              </div>
                            </button>
                          );
                        })}
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
          
          {onToggleMaximize && (
            <button
              onClick={onToggleMaximize}
              className="px-3 py-1.5 text-sm font-medium bg-white/20 hover:bg-white/30 text-white rounded-lg transition-colors backdrop-blur-sm"
              title={isMaximized ? 'Minimize' : 'Maximize'}
            >
              {isMaximized ? (
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 9V4.5M9 9H4.5M9 9L3.75 3.75M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 9h4.5M15 9V4.5M15 9l5.25-5.25M15 15h4.5M15 15v4.5m0-4.5l5.25 5.25" />
                </svg>
              ) : (
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
                </svg>
              )}
            </button>
          )}
        </div>
      </div>

      {/* Messages Area */}
      <div className="flex-1 overflow-y-auto bg-white dark:bg-gray-900">

        {/* Scope notice — shown only before first message, no scope set */}
        {messages.length === 0 && !selectedFilters.globalScope && !selectedFilters.subscription && (
          <div className="px-4 sm:px-6 pt-4">
            <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-50 border border-slate-200 max-w-4xl mx-auto">
              <svg className="w-4 h-4 text-slate-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.75}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" />
              </svg>
              <p className="text-[13px] text-slate-500 leading-snug">
                <span className="font-medium text-slate-700">No scope selected.</span>
                {' '}Select a subscription or enable Global Scope to query your Azure resources.
                <span className="text-slate-400"> General questions can still be answered.</span>
              </p>
            </div>
          </div>
        )}
        
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center p-8">
            
            <div className="w-12 h-12 rounded-xl overflow-hidden mb-5 shadow-lg shadow-indigo-200">
              <CirrusAvatar size="lg" />
            </div>
            <h3 className="text-base font-semibold mb-2 text-slate-900">
              How can I help you today?
            </h3>
            <p className="text-gray-600 mb-6 max-w-md">
              Ask anything about your Azure infrastructure: resources, costs, logs, metrics, and more.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-3 w-full max-w-2xl px-4 sm:px-0">
              {[
                    'What can you do?',
                    'Show me defender security compliance',
                    'Show me my costs for last month',
                    'What are my most expensive resources?',
                    'Show me activity logs for last 1 week',
                    'List all my virtual machines with Public IP',
              ].map((example, idx) => (
                <button
                  key={idx}
                  onClick={() => setInputValue(example)}
                  className="p-2 sm:p-3 text-left text-xs sm:text-sm bg-white border rounded-xl transition-all active:scale-95 text-slate-600 hover:text-slate-900 hover:bg-slate-50 border-slate-200 hover:border-slate-300"
                >
                  <svg className="inline w-3.5 h-3.5 text-indigo-400 mr-1.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>{example}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {messages.length > 0 && (
          <>
            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                id={message.id}
                role={message.role}
                content={message.content}
                messageType={message.message_type}
                timestamp={message.timestamp}
                queryResult={message.query_result}
                actions={message.actions}
                onActionClick={handleActionClick}
                onRerun={handleRerunQuery}
              />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-200 p-2 sm:p-4 bg-white space-y-2 sm:space-y-3">
        {/* AI Template Suggestions */}
        {inputValue && inputValue.trim().length >= 3 && !isAnyLoading && (
          <TemplateSuggestions
            query={inputValue}
            onSelectTemplate={handleSelectTemplate}
          />
        )}
        
        <div className="flex gap-1 sm:gap-2">
          <textarea
            ref={inputRef}
            value={inputValue || ''}
            onChange={(e) => setInputValue(e.target.value || '')}
            onKeyDown={handleKeyDown}
              placeholder="Ask CIRRUS about your infrastructure..."
            className="flex-1 resize-none rounded-xl border border-slate-200 px-3 sm:px-4 py-2 sm:py-3 text-sm focus:border-indigo-400 focus:ring-2 focus:ring-indigo-400/20 outline-none transition-all shadow-sm bg-slate-50 focus:bg-white"
            rows={2}
            disabled={isAnyLoading}
          />
          <button
            onClick={() => handleSendMessage()}
            disabled={!inputValue || !inputValue.trim() || isAnyLoading}
            className="px-3 sm:px-6 py-2 disabled:bg-slate-200 disabled:cursor-not-allowed text-white font-medium rounded-xl transition-colors flex items-center gap-2 active:scale-95 min-w-[60px] sm:min-w-[80px] bg-indigo-600 hover:bg-indigo-700 shadow-sm"
          >
              {isComposerLoading ? (
              <>
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Thinking...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
                Send
              </>
            )}
          </button>
        </div>
        <p className="mt-1 sm:mt-2 text-xs text-gray-500 px-1">
          <span className="flex items-center gap-1.5"><svg className="w-3 h-3 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg><span className="hidden sm:inline">CIRRUS AI remembers conversation context, so ask follow-up questions naturally</span><span className="sm:hidden">Conversation context is retained</span></span>
        </p>
      </div>

      {/* Template Browser Modal */}
      {showTemplateBrowser && (
        <TemplateBrowser
          onSelectTemplate={handleSelectTemplate}
          onClose={() => setShowTemplateBrowser(false)}
          currentQuery={inputValue && inputValue.trim() ? inputValue.trim() : undefined}
          onSaveAsTemplate={() => {
            setShowTemplateBrowser(false);
            setShowSaveTemplateModal(true);
          }}
        />
      )}

      {/* Save Template Modal */}
      {showSaveTemplateModal && (
        <SaveTemplateModal
          query={inputValue || ''}
          queryType="natural_language"
          onClose={() => setShowSaveTemplateModal(false)}
          onSaved={handleTemplatesSaved}
        />
      )}
    </div>
  );
}

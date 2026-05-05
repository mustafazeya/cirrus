'use client';

import { useState, useEffect } from 'react';
import { useIsAuthenticated } from '@azure/msal-react';
import { apiService } from '../../services/apiService';
import { 
  ChartBarIcon, 
  CurrencyDollarIcon, 
  ClockIcon,
  ArrowPathIcon,
  CheckCircleIcon,
  XCircleIcon,
  ExclamationTriangleIcon,
  UserGroupIcon,
  DocumentTextIcon,
  ShieldCheckIcon
} from '@heroicons/react/24/outline';

type Tab = 'overview' | 'metrics' | 'audit' | 'prefetch' | 'cache';

interface CacheStats {
  success: boolean;
  stats: {
    advanced_query_cache: {
      total_entries: number;
      hit_rate: number;
      total_size_mb?: number;
    };
    prefetch_cache: {
      subscriptions_count: number;
      resource_types_count: number;
      last_updated: string | null;
      scheduler_running: boolean;
    };
    metrics_cache: {
      total_requests: number;
      total_errors: number;
      active_users: number;
      endpoints_tracked: number;
      using_redis: boolean;
    };
    template_cache: {
      total_templates: number;
      user_templates: number;
      public_templates: number;
      system_templates: number;
    };
    cost_cache?: {
      total_entries: number;
      cache_hits: number;
      cache_misses: number;
      hit_rate: number;
      storage: string;
    };
    arg_table_mappings_cache?: {
      mappings_count: number;
      age_hours: number | null;
      ttl_hours: number;
      status: string;
      source?: string;
      last_updated?: string;
    };
    la_table_schemas_cache?: {
      table_count: number;
      age_days: number | null;
      ttl_days: number;
      status: string;
      last_updated?: string;
      storage: string;
    };
  };
  timestamp: string;
}

interface SystemOverview {
  api_metrics: {
    requests_24h: number;
    avg_response_time_ms: number;
    error_rate: number;
  };
  users: {
    active_now: number;
    last_24h: number;
    last_7d: number;
  };
  performance: {
    total_requests: number;
    endpoints_tracked: number;
    response_times: {
      p50: number;
      p95: number;
      p99: number;
    };
  };
  security: {
    failed_auth_count: number;
    permission_denials: number;
  };
  system_health: {
    cache: string;
    scheduler: boolean;
  };
}

interface ApiMetrics {
  total_requests: number;
  avg_response_time_ms: number;
  error_rate: number;
  requests_per_hour: number;
  top_endpoints: Array<{
    endpoint: string;
    count: number;
    avg_time_ms: number;
  }>;
}

interface AuditLog {
  timestamp: string;
  action: string;
  user_email: string;
  resource: string;
  status: string;
  details: any;
}

interface PrefetchStatus {
  prefetch_enabled: boolean;
  scheduler_status: {
    scheduler_running: boolean;
    next_run_time: string | null;
  };
  last_prefetch_time: string | null;
  cached_data: {
    subscriptions_count: number;
    resource_types_count: number;
  };
}

interface PrefetchData {
  subscriptions: {
    count: number;
    sample: Array<{
      subscription_id: string;
      display_name: string;
      state: string;
    }>;
  };
  resource_types: {
    count: number;
    sample: string[];
  };
}

export default function AdminDashboard() {
  const isAuthenticated = useIsAuthenticated();
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [overview, setOverview] = useState<SystemOverview | null>(null);
  const [apiMetrics, setApiMetrics] = useState<ApiMetrics | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [filteredAuditLogs, setFilteredAuditLogs] = useState<AuditLog[]>([]);
  const [status, setStatus] = useState<PrefetchStatus | null>(null);
  const [data, setData] = useState<PrefetchData | null>(null);
  const [cacheStats, setCacheStats] = useState<CacheStats | null>(null);
  const [health, setHealth] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auditSearchTerm, setAuditSearchTerm] = useState('');
  const [metricsTimeRange, setMetricsTimeRange] = useState<24 | 168 | 720>(24);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [clearingCache, setClearingCache] = useState<Set<string>>(new Set());

  const fetchStatus = async () => {
    try {
      const response = await apiService.request({
        url: '/api/admin/prefetch/status',
        method: 'GET'
      });
      
      setStatus(response);
      setError(null);
    } catch (error: any) {
      console.error('Failed to fetch status:', error);
      if (error.response?.status === 403) {
        setError('Access denied. Admin privileges required.');
      } else {
        setError(error instanceof Error ? error.message : 'Failed to fetch status');
      }
    }
  };

  const fetchData = async () => {
    try {
      const response = await apiService.request({
        url: '/api/admin/prefetch/data',
        method: 'GET'
      });
      
      setData(response);
      setError(null);
    } catch (error: any) {
      console.error('Failed to fetch data:', error);
      if (error.response?.status === 403) {
        setError('Access denied. Admin privileges required.');
      } else {
        setError(error instanceof Error ? error.message : 'Failed to fetch data');
      }
    }
  };

  const triggerPrefetch = async () => {
    setTriggering(true);
    try {
      const result = await apiService.request({
        url: '/api/admin/prefetch/trigger',
        method: 'POST'
      });
      
      console.log('Prefetch triggered:', result);
      setError(null);
      
      // Refresh status after triggering
      setTimeout(() => {
        fetchStatus();
        fetchData();
      }, 2000);
    } catch (error: any) {
      console.error('Failed to trigger prefetch:', error);
      if (error.response?.status === 403) {
        setError('Access denied. Admin privileges required.');
      } else {
        setError(error instanceof Error ? error.message : 'Failed to trigger prefetch');
      }
    } finally {
      setTriggering(false);
    }
  };

  const fetchHealth = async () => {
    try {
      const response = await apiService.request({
        url: '/api/health',
        method: 'GET'
      });
      setHealth(response);
    } catch (error: any) {
      console.error('Failed to fetch health:', error);
    }
  };

  const fetchOverview = async () => {
    try {
      const response = await apiService.request({
        url: '/api/admin/system/overview',
        method: 'GET'
      });
      
      setOverview(response.overview);
      // Fetch health and status for system health card
      await Promise.all([fetchHealth(), fetchStatus()]);
      setError(null);
    } catch (error: any) {
      console.error('Failed to fetch overview:', error);
      if (error.response?.status === 403) {
        setError('Access denied. Admin privileges required.');
      }
    }
  };

  const fetchApiMetrics = async () => {
    try {
      const response = await apiService.request({
        url: `/api/admin/metrics/api?hours=${metricsTimeRange}`,
        method: 'GET'
      });
      
      if (response.success) {
        setApiMetrics(response.metrics);
      }
    } catch (error) {
      console.error('Failed to fetch API metrics:', error);
    }
  };

  const fetchAuditLogs = async () => {
    try {
      const response = await apiService.request({
        url: '/api/admin/audit/logs?hours=24&limit=1000',
        method: 'GET'
      });
      
      if (response.success) {
        setAuditLogs(response.logs);
        setFilteredAuditLogs(response.logs);
      }
    } catch (error) {
      console.error('Failed to fetch audit logs:', error);
    }
  };

  const clearCache = async (cacheType: 'all' | 'query' | 'prefetch' | 'metrics' | 'templates' | 'cost' | 'arg_mappings' | 'la_tables' = 'all') => {
    console.log('Clearing cache:', cacheType);
    
    // Add to clearing set
    const cachesToClear = cacheType === 'all' ? ['all', 'query', 'prefetch', 'metrics', 'templates', 'cost', 'arg_mappings', 'la_tables'] : [cacheType];
    setClearingCache(prev => new Set([...Array.from(prev), ...cachesToClear]));
    
    try {
      const response = await apiService.request({
        url: '/api/admin/cache/clear',
        method: 'POST',
        data: { cache_type: cacheType }
      });
      
      console.log('Clear cache response:', response);
      
      // Add small delay to ensure backend finishes clearing before fetching stats
      await new Promise(resolve => setTimeout(resolve, 500));
    } catch (error: any) {
      console.error('Failed to clear cache:', error);
      console.error('Error details:', error.response?.data || error.message);
    } finally {
      // Always refresh stats after clearing (or attempting to clear)
      await Promise.all([fetchOverview(), fetchCacheStats()]);
      
      // Remove from clearing set
      setClearingCache(prev => {
        const newSet = new Set(prev);
        cachesToClear.forEach(cache => newSet.delete(cache));
        return newSet;
      });
    }
  };

  const fetchCacheStats = async () => {
    try {
      // Add cache-busting timestamp to prevent browser/CDN caching
      const timestamp = Date.now();
      const response = await apiService.request({
        url: `/api/admin/cache/stats?_=${timestamp}`,
        method: 'GET',
        headers: {
          'Cache-Control': 'no-cache, no-store, must-revalidate',
          'Pragma': 'no-cache'
        }
      });
      
      if (response.success) {
        setCacheStats(response);
      }
    } catch (error) {
      console.error('Failed to fetch cache stats:', error);
    }
  };

  const refreshAll = async () => {
    setLoading(true);
    await Promise.all([
      fetchOverview(),
      fetchApiMetrics(),
      fetchAuditLogs(),
      fetchStatus(),
      fetchData(),
      fetchCacheStats()
    ]);
    setLoading(false);
    setLastUpdated(new Date());
  };

  useEffect(() => {
    refreshAll();
    // Auto-refresh every minute
    const interval = setInterval(refreshAll, 60000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Refetch metrics when time range changes
    if (activeTab === 'metrics') {
      fetchApiMetrics();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metricsTimeRange, activeTab]);

  useEffect(() => {
    // Filter audit logs based on search term
    if (auditSearchTerm.trim() === '') {
      setFilteredAuditLogs(auditLogs);
    } else {
      const term = auditSearchTerm.toLowerCase();
      const filtered = auditLogs.filter(log => 
        log.user_email?.toLowerCase().includes(term) ||
        (typeof log.action === 'string' && log.action.toLowerCase().includes(term)) ||
        (typeof log.details === 'string' && log.details.toLowerCase().includes(term))
      );
      setFilteredAuditLogs(filtered);
    }
  }, [auditSearchTerm, auditLogs]);

  const formatTime = (timeString: string | null) => {
    if (!timeString) return 'Never';
    return new Date(timeString).toLocaleString('sv-SE', { timeZone: 'Europe/Stockholm' });
  };

  const exportAuditLogs = () => {
    const csvContent = [
      ['Timestamp', 'User', 'Action', 'Details', 'Status'],
      ...filteredAuditLogs.map(log => [
        new Date(log.timestamp).toISOString(),
        log.user_email || 'System',
        typeof log.action === 'string' ? log.action : JSON.stringify(log.action),
        log.details ? (typeof log.details === 'string' ? log.details : JSON.stringify(log.details)) : '-',
        log.status
      ])
    ].map(row => row.map(cell => `"${cell}"`).join(',')).join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit_logs_${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  };

  if (error && error.includes('Access denied')) {
    return (
      <div className="max-w-7xl mx-auto py-16 px-4 sm:px-6 lg:px-8 text-center">
        <div className="w-16 h-16 mx-auto bg-red-100 rounded-full flex items-center justify-center mb-4">
          <ShieldCheckIcon className="w-8 h-8 text-red-500" />
        </div>
        <h2 className="text-xl font-semibold text-slate-900 mb-2">Access Denied</h2>
        <p className="text-slate-500 text-sm">You need the <span className="font-semibold">Admin</span> role to access this page.</p>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
      <div className="mb-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-[22px] font-bold text-slate-900 tracking-tight">Admin Dashboard</h1>
            <p className="mt-1 text-xs text-slate-500">
              {lastUpdated
                ? `Last updated: ${lastUpdated.toLocaleTimeString('sv-SE', { timeZone: 'Europe/Stockholm' })}`
                : 'System management and operations'}
            </p>
          </div>
          <button
            onClick={refreshAll}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-300 text-slate-700 rounded-md hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-sm font-medium self-start sm:self-auto"
          >
            <ArrowPathIcon className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            {loading ? 'Refreshing...' : 'Refresh All'}
          </button>
        </div>
      </div>

      {error && !error.includes('Access denied') && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg mb-6">
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Tab Navigation */}
      <div className="mb-6 border-b border-slate-200">
        <nav className="-mb-px flex overflow-x-auto gap-1 sm:gap-0 sm:space-x-8">
          {[
            { id: 'overview', label: 'Overview' },
            { id: 'metrics', label: 'API Metrics' },
            { id: 'audit', label: 'Audit Logs' },
            { id: 'prefetch', label: 'Prefetch' },
            { id: 'cache', label: 'Cache' }
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`py-4 px-3 sm:px-1 border-b-2 font-medium text-sm whitespace-nowrap flex-shrink-0 ${
                activeTab === tab.id
                  ? 'border-indigo-500 text-indigo-600'
                  : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Overview Tab */}
      {activeTab === 'overview' && overview && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {/* API Usage */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">API Usage (24h)</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Total Requests:</span>
                  <span className="text-base font-medium text-slate-900">{overview.api_metrics.requests_24h}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Error Rate:</span>
                  <span className={`text-base font-medium ${overview.api_metrics.error_rate > 5 ? 'text-red-600' : 'text-green-600'}`}>
                    {overview.api_metrics.error_rate.toFixed(1)}%
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Avg Response:</span>
                  <span className="text-base font-medium text-slate-900">{overview.api_metrics.avg_response_time_ms.toFixed(0)}ms</span>
                </div>
              </div>
            </div>

            {/* Active Users */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">Active Users</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Active Now:</span>
                  <span className="text-base font-medium text-slate-900">{overview.users.active_now}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Last 24h:</span>
                  <span className="text-base font-medium text-slate-900">{overview.users.last_24h}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Last 7d:</span>
                  <span className="text-base font-medium text-slate-900">{overview.users.last_7d}</span>
                </div>
              </div>
            </div>

            {/* Performance */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">Performance</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">P50:</span>
                  <span className="text-base font-medium text-slate-900">{overview.performance.response_times?.p50?.toFixed(0) || 0}ms</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">P95:</span>
                  <span className="text-base font-medium text-slate-900">{overview.performance.response_times?.p95?.toFixed(0) || 0}ms</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">P99:</span>
                  <span className={`text-base font-medium ${(overview.performance.response_times?.p99 || 0) > 2000 ? 'text-red-600' : 'text-slate-900'}`}>
                    {overview.performance.response_times?.p99?.toFixed(0) || 0}ms
                  </span>
                </div>
              </div>
            </div>

            {/* Security */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">Security (24h)</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Failed Auth:</span>
                  <span className={`text-base font-medium ${overview.security.failed_auth_count > 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {overview.security.failed_auth_count}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Permission Denials:</span>
                  <span className="text-base font-medium text-slate-900">{overview.security.permission_denials}</span>
                </div>
              </div>
            </div>

            {/* System Health */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">System Health</h3>
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Backend:</span>
                  <span className="text-sm font-medium text-green-600">Online</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Cache:</span>
                  <span className={health?.redis_connected ? 'text-sm font-medium text-green-600' : 'text-sm font-medium text-yellow-600'}>
                    {health?.redis_connected ? 'Redis' : 'Disconnected'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-sm text-slate-600">Scheduler:</span>
                  <span className={status?.scheduler_status?.scheduler_running ? 'text-sm font-medium text-green-600' : 'text-sm font-medium text-red-600'}>
                    {status?.scheduler_status?.scheduler_running ? 'Running' : 'Stopped'}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Metrics Tab */}
      {activeTab === 'metrics' && (
        <div className="space-y-6">
          {apiMetrics ? (
          <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
            <h2 className="text-lg font-semibold mb-6 text-slate-900">
              API Metrics (Last 24 Hours)
            </h2>
            
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
              <div className="p-4 bg-indigo-50 rounded-lg">
                <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Requests</div>
                <div className="text-lg font-semibold text-slate-900 mt-1">{apiMetrics.total_requests}</div>
              </div>
              <div className="p-4 bg-red-50 rounded-lg">
                <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Error Rate</div>
                <div className="text-lg font-semibold text-red-600 mt-1">{apiMetrics.error_rate.toFixed(1)}%</div>
              </div>
              <div className="p-4 bg-yellow-50 rounded-lg">
                <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Requests/Hour</div>
                <div className="text-lg font-semibold text-slate-900 mt-1">{apiMetrics.requests_per_hour.toFixed(0)}</div>
              </div>
              <div className="p-4 bg-green-50 rounded-lg">
                <div className="text-xs font-medium text-slate-500 uppercase tracking-wider">Avg Response</div>
                <div className="text-lg font-semibold text-slate-900 mt-1">{apiMetrics.avg_response_time_ms.toFixed(0)}ms</div>
              </div>
            </div>

            <h3 className="text-sm font-semibold text-slate-900 mb-3">Top Endpoints</h3>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Endpoint</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Requests</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Avg Time</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-slate-200">
                  {apiMetrics.top_endpoints.map((endpoint, index) => (
                    <tr key={index}>
                      <td className="px-6 py-4 text-sm font-mono text-slate-600">{endpoint.endpoint}</td>
                      <td className="px-6 py-4 text-sm">{endpoint.count}</td>
                      <td className="px-6 py-4 text-sm">{endpoint.avg_time_ms.toFixed(0)}ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          ) : (
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm text-center text-slate-500">
              Loading metrics...
            </div>
          )}
        </div>
      )}

      {/* Audit Tab */}
      {activeTab === 'audit' && (
        <div className="space-y-6">
          <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-lg font-semibold text-slate-900">Audit Logs (Last 24 Hours)</h2>
              <button
                onClick={exportAuditLogs}
                disabled={filteredAuditLogs.length === 0}
                className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-300 text-slate-700 rounded-md hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-sm font-medium"
              >
                <DocumentTextIcon className="h-4 w-4" />
                Export CSV
              </button>
            </div>
            
            {/* Search Filter */}
            <div className="mb-4">
              <input
                type="text"
                placeholder="Search logs by user, action, or details..."
                value={auditSearchTerm}
                onChange={(e) => setAuditSearchTerm(e.target.value)}
                className="w-full px-4 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
              />
              {auditSearchTerm && (
                <p className="text-sm text-slate-500 mt-2">
                  Showing {filteredAuditLogs.length} of {auditLogs.length} logs
                </p>
              )}
            </div>
            
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Timestamp</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">User</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Action</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Details</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-slate-500 uppercase">Status</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-slate-200">
                  {filteredAuditLogs.map((log, index) => (
                    <tr key={index}>
                      <td className="px-6 py-4 text-sm whitespace-nowrap">
                        {new Date(log.timestamp).toLocaleString('sv-SE', { timeZone: 'Europe/Stockholm' })}
                      </td>
                      <td className="px-6 py-4 text-sm">{log.user_email || 'System'}</td>
                      <td className="px-6 py-4 text-sm">
                        {typeof log.action === 'string' ? log.action : JSON.stringify(log.action)}
                      </td>
                      <td className="px-6 py-4 text-sm text-slate-600">
                        {log.details 
                          ? (typeof log.details === 'string' 
                              ? log.details 
                              : JSON.stringify(log.details))
                          : '-'}
                      </td>
                      <td className="px-6 py-4 text-sm">
                        <span className={`px-2 py-1 rounded-md text-xs font-medium ${
                          log.status === 'success' 
                            ? 'bg-green-100 text-green-800' 
                            : 'bg-red-100 text-red-800'
                        }`}>
                          {log.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            
            {filteredAuditLogs.length === 0 && auditSearchTerm && (
              <div className="text-center py-8 text-slate-500">No logs match your search</div>
            )}
            {auditLogs.length === 0 && !auditSearchTerm && (
              <div className="text-center py-8 text-slate-500">No audit logs available</div>
            )}
          </div>
        </div>
      )}

      {/* Prefetch Tab */}
      {activeTab === 'prefetch' && (
        <div className="space-y-6">
          <div className="flex justify-end mb-4">
            <button
              onClick={triggerPrefetch}
              disabled={triggering}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-sm font-medium"
            >
              {triggering ? 'Triggering...' : 'Trigger Prefetch'}
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            {/* Status Overview */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">
                Prefetch Status
              </h2>
              {status ? (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-600">Enabled:</span>
                    <span className={status.prefetch_enabled ? 'text-sm font-medium text-green-600' : 'text-sm font-medium text-red-600'}>
                      {status.prefetch_enabled ? 'Yes' : 'No'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-600">Scheduler:</span>
                    <span className={status.scheduler_status?.scheduler_running ? 'text-sm font-medium text-green-600' : 'text-sm font-medium text-red-600'}>
                      {status.scheduler_status?.scheduler_running ? 'Running' : 'Stopped'}
                    </span>
                  </div>
                  <div className="text-sm text-slate-600">
                    <div>Last Run: {formatTime(status.last_prefetch_time)}</div>
                    <div>Next Run: {formatTime(status.scheduler_status?.next_run_time)}</div>
                  </div>
                </div>
              ) : (
                <div>Loading...</div>
              )}
            </div>

            {/* Cached Data Counts */}
            <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
              <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">
                Cached Data
              </h2>
              {status?.cached_data ? (
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Subscriptions:</span>
                    <span className="text-sm font-semibold text-slate-900">{status.cached_data.subscriptions_count}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Resource Types:</span>
                    <span className="text-sm font-semibold text-slate-900">{status.cached_data.resource_types_count}</span>
                  </div>
                </div>
              ) : (
                <div>Loading...</div>
              )}
            </div>
          </div>

          {/* Sample Data */}
          {data && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">Sample Subscriptions ({data.subscriptions.count} total)</h2>
                <div className="space-y-2">
                  {data.subscriptions.sample.map((sub, index) => (
                    <div key={index} className="p-3 bg-slate-50 rounded-md text-sm">
                      <div className="font-medium">{sub.display_name}</div>
                      <div className="text-slate-600 font-mono text-xs">{sub.subscription_id}</div>
                      <div className="text-slate-600">State: {sub.state}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4">Sample Resource Types ({data.resource_types.count} total)</h2>
                <div className="space-y-2">
                  {data.resource_types.sample.map((type, index) => (
                    <div key={index} className="p-3 bg-slate-50 rounded-md text-sm font-mono">
                      {type}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Cache Tab */}
      {activeTab === 'cache' && (
        <div className="space-y-6">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold text-slate-900">Cache Management</h2>
            <button
              onClick={() => clearCache('all')}
              disabled={clearingCache.has('all')}
              className="px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed text-sm font-medium transition-colors"
            >
              {clearingCache.has('all') ? 'Clearing...' : 'Clear All Cache'}
            </button>
          </div>

          {/* Cache Statistics */}
          {cacheStats?.stats && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-6">
              {/* Advanced Query Cache Stats */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">
                      <ChartBarIcon className="h-4 w-4 inline mr-1.5 text-indigo-500" />
                      AI Query Cache
                    </h3>
                    <p className="text-xs text-slate-500 mt-1">Cached KQL/Resource Graph queries</p>
                  </div>
                  <button
                    onClick={() => clearCache('query')}
                    disabled={clearingCache.has('query') || clearingCache.has('all')}
                    className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                    title="Clear Query Cache only"
                  >
                    {clearingCache.has('query') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                  </button>
                </div>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Total Entries:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.advanced_query_cache?.total_entries || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Hit Rate:</span>
                    <span className="text-sm font-semibold text-slate-900">
                      {!cacheStats.stats.advanced_query_cache?.hit_rate || isNaN(cacheStats.stats.advanced_query_cache.hit_rate) || !isFinite(cacheStats.stats.advanced_query_cache.hit_rate)
                        ? '0.0%'
                        : `${(cacheStats.stats.advanced_query_cache.hit_rate * 100).toFixed(1)}%`}
                    </span>
                  </div>
                  {cacheStats.stats.advanced_query_cache?.total_size_mb !== undefined && (
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Size:</span>
                      <span className="text-sm font-semibold text-slate-900">{(cacheStats.stats.advanced_query_cache.total_size_mb * 1024).toFixed(2)} KB</span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Storage:</span>
                    <span className="text-sm font-semibold text-green-600">
                      Redis
                    </span>
                  </div>
                </div>
              </div>

              {/* Prefetch Cache Stats */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">
                      <ArrowPathIcon className="h-4 w-4 inline mr-1.5 text-green-500" />
                      Subscription & Resource Cache
                    </h3>
                    <p className="text-xs text-slate-500 mt-1">Pre-loaded Azure data</p>
                  </div>
                  <button
                    onClick={() => clearCache('prefetch')}
                    disabled={clearingCache.has('prefetch') || clearingCache.has('all')}
                    className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                    title="Clear Prefetch Cache only"
                  >
                    {clearingCache.has('prefetch') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                  </button>
                </div>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Subscriptions:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.prefetch_cache?.subscriptions_count || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Resource Types:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.prefetch_cache?.resource_types_count || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Scheduler:</span>
                    <span className={`text-sm font-semibold ${cacheStats.stats.prefetch_cache?.scheduler_running ? 'text-green-600' : 'text-red-600'}`}>
                      {cacheStats.stats.prefetch_cache?.scheduler_running ? 'Running' : 'Stopped'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Storage:</span>
                    <span className="text-sm font-semibold text-green-600">Redis</span>
                  </div>
                  {cacheStats.stats.prefetch_cache?.last_updated && (
                    <div className="text-sm text-slate-500 mt-2">
                      Last Updated: {new Date(cacheStats.stats.prefetch_cache.last_updated).toLocaleString('sv-SE', { timeZone: 'Europe/Stockholm' })}
                    </div>
                  )}
                </div>
              </div>

              {/* Metrics Cache Stats */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">
                      <ChartBarIcon className="h-4 w-4 inline mr-1.5 text-purple-500" />
                      API Metrics Storage
                    </h3>
                    <p className="text-xs text-slate-500 mt-1">Request tracking & analytics</p>
                  </div>
                  <button
                    onClick={() => clearCache('metrics')}
                    disabled={clearingCache.has('metrics') || clearingCache.has('all')}
                    className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                    title="Clear Metrics Cache only"
                  >
                    {clearingCache.has('metrics') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                  </button>
                </div>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Requests Tracked:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.metrics_cache?.total_requests || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Errors Logged:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.metrics_cache?.total_errors || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Active Users:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.metrics_cache?.active_users || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Storage:</span>
                    <span className={`text-sm font-semibold ${cacheStats.stats.metrics_cache?.using_redis ? 'text-green-600' : 'text-yellow-600'}`}>
                      {cacheStats.stats.metrics_cache?.using_redis ? 'Redis' : 'Memory'}
                    </span>
                  </div>
                </div>
              </div>
              
              {/* Template Cache Stats */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">
                      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4 inline mr-1.5 text-blue-500">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0111.186 0z" />
                      </svg>
                      Query Templates
                    </h3>
                    <p className="text-xs text-slate-500 mt-1">Saved query templates in Redis</p>
                  </div>
                  <button
                    onClick={() => clearCache('templates')}
                    disabled={clearingCache.has('templates') || clearingCache.has('all')}
                    className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                    title="Clear Template Cache only"
                  >
                    {clearingCache.has('templates') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                  </button>
                </div>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Total Templates:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.template_cache?.total_templates || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">System:</span>
                    <span className="text-sm font-semibold text-indigo-600">{cacheStats.stats.template_cache?.system_templates || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Public:</span>
                    <span className="text-sm font-semibold text-green-600">{cacheStats.stats.template_cache?.public_templates || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">User Private:</span>
                    <span className="text-sm font-semibold text-slate-600">{cacheStats.stats.template_cache?.user_templates || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Storage:</span>
                    <span className="text-sm font-semibold text-green-600">Redis</span>
                  </div>
                </div>
              </div>

              {/* Cost Cache Stats */}
              <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900">
                      <CurrencyDollarIcon className="h-4 w-4 inline mr-1.5 text-green-500" />
                      Cost Analysis Cache
                    </h3>
                    <p className="text-xs text-slate-500 mt-1">Azure Cost Management API responses</p>
                  </div>
                  <button
                    onClick={() => clearCache('cost')}
                    disabled={clearingCache.has('cost') || clearingCache.has('all')}
                    className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                    title="Clear Cost Cache only - Forces fresh queries to Azure Cost Management API"
                  >
                    {clearingCache.has('cost') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                  </button>
                </div>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Total Entries:</span>
                    <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.cost_cache?.total_entries || 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Hit Rate:</span>
                    <span className="text-sm font-semibold text-indigo-600">{cacheStats.stats.cost_cache?.hit_rate?.toFixed(1) || 0}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-sm text-slate-600">Storage:</span>
                    <span className="text-sm font-semibold text-green-600">{cacheStats.stats.cost_cache?.storage || 'Redis'}</span>
                  </div>
                </div>
              </div>

              {/* ARG Table Mappings Cache Stats */}
              {cacheStats?.stats?.arg_table_mappings_cache && (
                <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                  <div className="flex justify-between items-start mb-4">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-900">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4 inline mr-1.5 text-purple-500">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
                        </svg>
                        ARG Table Mappings
                      </h3>
                      <p className="text-xs text-slate-500 mt-1">Azure Resource Graph table mappings from docs</p>
                    </div>
                    <button
                      onClick={() => clearCache('arg_mappings')}
                      disabled={clearingCache.has('arg_mappings') || clearingCache.has('all')}
                      className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                      title="Clear ARG Mappings Cache and refresh from docs"
                    >
                      {clearingCache.has('arg_mappings') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                    </button>
                  </div>
                  <div className="space-y-3">
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Provider Mappings:</span>
                      <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.arg_table_mappings_cache.mappings_count}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Cache Age:</span>
                      <span className={`text-sm font-semibold ${cacheStats.stats.arg_table_mappings_cache.status === 'fresh' ? 'text-green-600' : 'text-orange-600'}`}>
                        {cacheStats.stats.arg_table_mappings_cache.age_hours !== null 
                          ? `${Math.round(cacheStats.stats.arg_table_mappings_cache.age_hours / 24)}d` 
                          : 'N/A'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">TTL:</span>
                      <span className="text-sm font-semibold text-slate-600">
                        {Math.round(cacheStats.stats.arg_table_mappings_cache.ttl_hours / 24)} days
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Status:</span>
                      <span className={`text-sm font-semibold ${cacheStats.stats.arg_table_mappings_cache.status === 'fresh' ? 'text-green-600' : 'text-orange-600'}`}>
                        {cacheStats.stats.arg_table_mappings_cache.status === 'fresh' ? 'Fresh' : 'Stale'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Storage:</span>
                      <span className="text-sm font-semibold text-green-600">Redis</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Log Analytics Table Schemas Cache Stats */}
              {cacheStats?.stats?.la_table_schemas_cache && (
                <div className="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                  <div className="flex justify-between items-start mb-4">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-900">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-4 w-4 inline mr-1.5 text-blue-500">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M20.25 6.375c0 2.278-3.694 4.125-8.25 4.125S3.75 8.653 3.75 6.375m16.5 0c0-2.278-3.694-4.125-8.25-4.125S3.75 4.097 3.75 6.375m16.5 0v11.25c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125V6.375m16.5 0v3.75m-16.5-3.75v3.75m16.5 0v3.75C20.25 16.153 16.556 18 12 18s-8.25-1.847-8.25-4.125v-3.75m16.5 0c0 2.278-3.694 4.125-8.25 4.125s-8.25-1.847-8.25-4.125" />
                        </svg>
                        Log Analytics Table Schemas
                      </h3>
                      <p className="text-xs text-slate-500 mt-1">Column schemas from Microsoft Learn</p>
                    </div>
                    <button
                      onClick={() => clearCache('la_tables')}
                      disabled={clearingCache.has('la_tables') || clearingCache.has('all')}
                      className="px-3 py-1 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed min-w-[90px] transition-colors"
                      title="Clear LA Table Schemas Cache and refresh from Microsoft Learn"
                    >
                      {clearingCache.has('la_tables') || clearingCache.has('all') ? 'Clearing...' : 'Clear'}
                    </button>
                  </div>
                  <div className="space-y-3">
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Tables Cached:</span>
                      <span className="text-sm font-semibold text-slate-900">{cacheStats.stats.la_table_schemas_cache.table_count}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Cache Age:</span>
                      <span className={`text-sm font-semibold ${cacheStats.stats.la_table_schemas_cache.status === 'fresh' ? 'text-green-600' : 'text-orange-600'}`}>
                        {cacheStats.stats.la_table_schemas_cache.age_days !== null 
                          ? `${cacheStats.stats.la_table_schemas_cache.age_days}d` 
                          : 'N/A'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">TTL:</span>
                      <span className="text-sm font-semibold text-slate-600">
                        {cacheStats.stats.la_table_schemas_cache.ttl_days} days
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Status:</span>
                      <span className={`text-sm font-semibold ${cacheStats.stats.la_table_schemas_cache.status === 'fresh' ? 'text-green-600' : 'text-orange-600'}`}>
                        {cacheStats.stats.la_table_schemas_cache.status === 'fresh' ? 'Fresh' : 'Stale'}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-sm text-slate-600">Storage:</span>
                      <span className="text-sm font-semibold text-green-600">{cacheStats.stats.la_table_schemas_cache.storage}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}


        </div>
      )}
    </div>
  );
}
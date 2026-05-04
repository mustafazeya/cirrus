'use client';

import { useEffect, useState } from 'react';
import { useMsal, useIsAuthenticated } from '@azure/msal-react';
import { useRouter } from 'next/navigation';
import { loginRequest } from '../config/authConfig';
import { useAppStore, filterEnabledSubscriptions } from '../store/appStore';
import { apiService } from '../services/apiService';
import toast, { Toaster } from 'react-hot-toast';
import FloatingAIAssistant from '../components/FloatingAIAssistant';
import PinnedCards from '../components/PinnedCards';
import CirrusAvatar from '../components/CirrusAvatar';
import { 
  ChartBarIcon, 
  CurrencyDollarIcon, 
  ChatBubbleLeftRightIcon,
  SparklesIcon,
  ClockIcon,
  CodeBracketIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';

export default function HomePage() {
  const { instance, accounts, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const router = useRouter();

  
  const {
    user,
    subscriptions,
    isLoading,
    error,
    setUser,
    setSubscriptions,
    setLoading,
    setError,
    clearError,
    queryHistory: storeHistory,
  } = useAppStore();

  // Read queryHistory directly from localStorage on mount — guards against SSR returning []
  const [localHistory, setLocalHistory] = useState<typeof storeHistory>(() => {
    if (typeof window === 'undefined') return [];
    try {
      const raw = window.localStorage.getItem('queryHistory');
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  });

  // Always prefer whichever list has the more recent first entry
  const newestStore = storeHistory[0]?.timestamp ?? 0;
  const newestLocal = localHistory[0]?.timestamp ?? 0;
  const queryHistory = newestStore >= newestLocal ? storeHistory : localHistory;

  // Sync localHistory when the store receives a newer entry (query submitted in same session)
  useEffect(() => {
    if (newestStore > newestLocal) setLocalHistory(storeHistory);
  }, [newestStore]);

  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    if (isAuthenticated && accounts.length > 0) {
      initializeApp();
    }
  }, [isAuthenticated, accounts]);

  const initializeApp = async () => {
    try {
      setLoading(true);
      clearError();

      // Get access token
      const tokenResponse = await instance.acquireTokenSilent({
        ...loginRequest,
        account: accounts[0],
      });

      // Set token in API service
      apiService.setAccessToken(tokenResponse.accessToken);

      // Get user info
      const userResponse = await apiService.getCurrentUser();
      setUser(userResponse);

      // Get subscriptions
      const subscriptionsResponse = await apiService.getSubscriptions();
      const allSubscriptions = (subscriptionsResponse as any).data || [];
      
      // Filter out disabled subscriptions
      const enabledSubscriptions = filterEnabledSubscriptions(allSubscriptions);
      setSubscriptions(enabledSubscriptions);

    } catch (error: any) {
      console.error('App initialization failed:', error);
      
      // Check if this is an interaction required error (consent needed)
      const isInteractionRequired = 
        error?.errorCode === 'interaction_required' ||
        error?.errorCode === 'consent_required' ||
        error?.errorCode === 'login_required' ||
        error?.name === 'InteractionRequiredAuthError' ||
        error?.errorMessage?.includes('AADSTS65001') || // User/admin consent required
        error?.errorMessage?.includes('AADSTS50058') || // Silent sign-in failed
        error?.errorMessage?.includes('AADSTS50076'); // MFA required
      
      if (isInteractionRequired) {
        try {
          apiService.clearAccessToken();
          await instance.acquireTokenRedirect({
            ...loginRequest,
            account: accounts[0],
            // Do NOT force prompt:'consent' — user already consented.
            // Entra ID will only show a prompt if genuinely needed (new permissions, expired session).
            // Hardcoding prompt:'consent' causes a full re-consent screen on every new deployment.
          });
          return; // Don't show error, we're redirecting
        } catch (redirectError) {
          console.error('Auth redirect failed:', redirectError);
          toast.error('Authentication required. Please sign in again.');
        }
      } else {
        toast.error('Failed to initialize application');
      }
      
      setError('Failed to initialize application');
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async () => {
    try {
      if (!instance) throw new Error('MSAL instance not initialized');
      if (!process.env.NEXT_PUBLIC_AZURE_CLIENT_ID) throw new Error('Azure Client ID not configured');
      await instance.loginPopup(loginRequest);
    } catch (error) {
      console.error('Login failed:', error);
      toast.error(`Login failed: ${error.message}`);
    }
  };

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      router.push(`/ai?q=${encodeURIComponent(searchQuery)}`);
    }
  };

  if (!isAuthenticated) {
    return (
      /* fixed inset-0 covers the sidebar + mobile topbar completely */
      <div className="fixed inset-0 z-[100] flex bg-white overflow-hidden">

        {/* ── Left panel: brand story (desktop only) ── */}
        <div
          className="hidden lg:flex lg:w-[46%] xl:w-[42%] flex-col justify-between px-16 py-14 relative overflow-hidden flex-shrink-0"
          style={{ background: 'linear-gradient(145deg, #0f172a 0%, #1e1b4b 60%, #0f172a 100%)' }}
        >
          {/* Dot grid overlay */}
          <div className="absolute inset-0 opacity-[0.06]" style={{
            backgroundImage: 'radial-gradient(#818cf8 1px, transparent 1px)',
            backgroundSize: '28px 28px',
          }} />
          {/* Glow blobs */}
          <div className="absolute -top-20 -left-16 w-[420px] h-[420px] bg-indigo-600/25 rounded-full blur-[80px] pointer-events-none" />
          <div className="absolute -bottom-16 -right-10 w-[300px] h-[300px] bg-violet-500/20 rounded-full blur-[70px] pointer-events-none" />
          <div className="absolute top-[45%] left-[55%] w-[200px] h-[200px] bg-blue-500/10 rounded-full blur-[60px] pointer-events-none" />

          {/* Top: brand mark */}
          <div className="relative z-10 flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl overflow-hidden shadow-lg shadow-indigo-900/40">
              <CirrusAvatar size="sm" />
            </div>
            <span className="text-white font-bold text-[15px] tracking-tight">CIRRUS</span>
          </div>

          {/* Middle: headline + features */}
          <div className="relative z-10 space-y-10">
            <div>
              <p className="text-[10.5px] font-semibold uppercase tracking-[0.18em] text-indigo-400 mb-4">
                Azure Cloud Intelligence
              </p>
              <h1 className="text-[34px] font-bold text-white leading-[1.18] tracking-tight">
                Your Azure cloud,<br />
                <span className="text-indigo-400">intelligently</span><br />
                managed.
              </h1>
              <p className="text-slate-400 text-[13.5px] leading-relaxed mt-5 max-w-[300px]">
                AI-powered observability, cost analysis, and resource intelligence across all your Azure subscriptions.
              </p>
            </div>

            <div className="space-y-3">
              {[
                { icon: SparklesIcon,            text: 'Natural language queries for any resource' },
                { icon: ChartBarIcon,            text: 'Unified view across all subscriptions'     },
                { icon: CurrencyDollarIcon,      text: 'Real-time cost analysis & optimization'    },
                { icon: ChatBubbleLeftRightIcon, text: 'Root cause analysis for incidents'          },
              ].map(({ icon: Icon, text }) => (
                <div key={text} className="flex items-center gap-3">
                  <div className="w-7 h-7 rounded-lg bg-white/[0.07] border border-white/[0.08] flex items-center justify-center flex-shrink-0">
                    <Icon className="w-3.5 h-3.5 text-indigo-300" />
                  </div>
                  <span className="text-slate-300 text-[13px] leading-snug">{text}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Bottom: left blank for clean look */}
          <div />
        </div>

        {/* ── Right panel: sign-in form ── */}
        <div className="flex-1 flex flex-col items-center justify-center px-6 sm:px-12 bg-white relative overflow-y-auto">
          {/* Very subtle indigo top glow */}
          <div
            className="absolute inset-x-0 top-0 h-64 pointer-events-none"
            style={{ background: 'radial-gradient(ellipse 80% 50% at 50% 0%, #eef2ff 0%, transparent 100%)' }}
          />

          <div className="relative w-full max-w-[360px] py-16">

            {/* Mobile logo */}
            <div className="flex items-center gap-3 mb-10 lg:hidden">
              <div className="w-8 h-8 rounded-xl overflow-hidden shadow-md shadow-indigo-200">
                <CirrusAvatar size="sm" />
              </div>
              <span className="font-bold text-slate-900 text-[15px] tracking-tight">CIRRUS</span>
            </div>

            {/* Heading */}
            <h2 className="text-[26px] font-bold text-slate-900 tracking-tight leading-tight">
              Welcome back
            </h2>
            <p className="text-[13.5px] text-slate-500 mt-2 mb-8 leading-relaxed">
              Sign in with your Microsoft work account to access your cloud environment.
            </p>

            {/* Microsoft sign-in button */}
            <button
              onClick={handleLogin}
              className="w-full flex items-center gap-4 px-5 py-4 bg-white border border-slate-200 rounded-xl shadow-sm hover:shadow-md hover:border-slate-300 active:scale-[0.99] transition-all duration-150 group"
            >
              <svg className="w-5 h-5 flex-shrink-0" viewBox="0 0 21 21" fill="none">
                <path d="M10.039 0H.996v9.043h9.043V0Z"          fill="#F25022"/>
                <path d="M21 0h-9.043v9.043H21V0Z"               fill="#7FBA00"/>
                <path d="M10.039 10.957H.996V20h9.043v-9.043Z"   fill="#00A4EF"/>
                <path d="M21 10.957h-9.043V20H21v-9.043Z"        fill="#FFB900"/>
              </svg>
              <span className="flex-1 text-left text-[14px] font-semibold text-slate-700 group-hover:text-slate-900 transition-colors">
                Continue with Microsoft
              </span>
              <svg className="w-4 h-4 text-slate-300 group-hover:text-indigo-400 transition-colors flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </button>

            {/* Security note */}
            <div className="mt-8 flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-slate-300 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
              <p className="text-[11.5px] text-slate-400 leading-relaxed">
                Authenticated via Microsoft Entra ID &middot; Role-based access enforced
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  /* First name extraction helper */
  const firstName = user?.name
    ? (user.name.includes(',') ? user.name.split(',')[1].trim().split(' ')[0] : user.name.split(' ')[0])
    : 'there';

  const longDate = new Date().toLocaleDateString('en-GB', {
    weekday: 'long', day: 'numeric', month: 'long',
  });

  const timeAgo = (ts: number) => {
    const diff = Math.floor((Date.now() - ts) / 1000);
    if (diff < 60)    return 'just now';
    if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  };

  const suggestedQueries = [
    { label: 'Show all resources',          desc: 'All resources across subscriptions', q: 'Show me all resources' },
    { label: 'Most expensive resources',    desc: 'Cost breakdown by resource',         q: 'What are my most expensive resources' },
    { label: 'Activity logs — last 7 days', desc: 'Recent changes and operations',       q: 'Show me activity logs from last 7 days' },
    { label: 'Security recommendations',    desc: 'Top Defender for Cloud findings',    q: 'What are my top security recommendations' },
    { label: 'Virtual machines by region',  desc: 'VM inventory with status',           q: 'List all virtual machines by region' },
    { label: 'Failed deployments',          desc: 'Recent deployment errors',           q: 'Show me failed deployments in the last 24 hours' },
  ];

  return (
    <div className="page-container">
      <Toaster position="top-right" toastOptions={{ style: { fontSize: '13px' } }} />

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" />
            <p className="text-sm text-slate-500">Loading your environment…</p>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl p-4 mb-6">
          <svg className="h-4 w-4 text-red-500 mt-0.5 flex-shrink-0" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
          </svg>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-red-800">{error}</p>
          </div>
          <button onClick={clearError} className="text-red-400 hover:text-red-600 touch-target-small">
            <XMarkIcon className="h-4 w-4" />
          </button>
        </div>
      )}

      {!isLoading && !error && (
        <div className="space-y-7">

          {/* ── Page header ── */}
          <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-widest text-slate-400 mb-0.5">{longDate}</p>
              <h1 className="text-[22px] font-bold text-slate-900 tracking-tight leading-tight">
                Good {new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 17 ? 'afternoon' : 'evening'}, {firstName}
              </h1>
              <p className="text-[13px] text-slate-500 mt-0.5">
                {subscriptions.length} subscription{subscriptions.length !== 1 ? 's' : ''} connected
              </p>
            </div>
            <button
              onClick={() => router.push('/ai')}
              className="btn-secondary text-[13px] flex-shrink-0 py-2.5"
            >
              <SparklesIcon className="h-3.5 w-3.5" />
              Open AI Chat
            </button>
          </div>

          {/* ── AI search bar ── */}
          <form onSubmit={handleSearchSubmit} className="flex gap-2.5">
            <div className="relative flex-1">
              <SparklesIcon className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-indigo-400 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="Ask anything about your Azure environment…"
                className="w-full pl-10 pr-4 py-3 bg-white border border-slate-200 rounded-xl text-[13.5px] text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-400/60 focus:border-indigo-400 shadow-sm transition-all"
              />
            </div>
            <button
              type="submit"
              className="flex items-center gap-2 px-5 py-3 bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white rounded-xl text-[13.5px] font-semibold transition-colors shadow-sm flex-shrink-0"
            >
              <SparklesIcon className="h-4 w-4" />
              <span className="hidden sm:inline">Ask AI</span>
              <span className="sm:hidden">Ask</span>
            </button>
          </form>

          {/* ── Queries grid ── */}
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">

            {/* Recent / history */}
            <div className="lg:col-span-3 bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
              <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
                <div className="flex items-center gap-2">
                  <ClockIcon className="h-3.5 w-3.5 text-slate-400" />
                  <span className="text-[11.5px] font-semibold uppercase tracking-widest text-slate-500">
                    {queryHistory.length > 0 ? 'Recently asked' : 'Quick start'}
                  </span>
                </div>
              </div>
              <div className="divide-y divide-slate-50">
                {(queryHistory.length > 0 ? queryHistory.slice(0, 6) : suggestedQueries.slice(0, 6)).map((entry: any, i) => {
                  const q     = queryHistory.length > 0 ? entry.query   : entry.q;
                  const label = queryHistory.length > 0 ? entry.query   : entry.label;
                  const sub   = queryHistory.length > 0 ? timeAgo(entry.timestamp) : entry.desc;
                  const isKql = entry.type === 'kql';
                  return (
                    <button
                      key={i}
                      onClick={() => router.push(`/ai?q=${encodeURIComponent(q)}&execute=true`)}
                      className="w-full text-left px-5 py-3.5 hover:bg-slate-50 active:bg-slate-100 transition-colors flex items-center gap-3.5 group"
                    >
                      <span className={`flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center ${isKql ? 'bg-amber-50 text-amber-500' : 'bg-indigo-50 text-indigo-500'}`}>
                        {isKql
                          ? <CodeBracketIcon className="w-3.5 h-3.5" />
                          : <SparklesIcon    className="w-3.5 h-3.5" />}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-[13px] font-medium text-slate-800 truncate leading-snug">{label}</p>
                        <p className="text-[11.5px] text-slate-400 mt-0.5 truncate">{sub}</p>
                      </div>
                      <span className="flex-shrink-0 text-[11px] text-indigo-500 font-medium sm:opacity-0 sm:group-hover:opacity-100 transition-opacity pr-0.5">
                        Run →
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Suggested / featured prompts */}
            <div className="lg:col-span-2 bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
              <div className="flex items-center gap-2 px-5 py-4 border-b border-slate-100">
                <SparklesIcon className="h-3.5 w-3.5 text-indigo-400" />
                <span className="text-[11.5px] font-semibold uppercase tracking-widest text-slate-500">Suggested</span>
              </div>
              <div className="divide-y divide-slate-50">
                {suggestedQueries.slice(0, 6).map(({ label, desc, q }) => (
                  <button
                    key={q}
                    onClick={() => router.push(`/ai?q=${encodeURIComponent(q)}`)}
                    className="w-full text-left px-5 py-3.5 hover:bg-indigo-50/60 active:bg-indigo-50 transition-colors group"
                  >
                    <p className="text-[13px] font-medium text-slate-800 leading-snug group-hover:text-indigo-700 transition-colors">{label}</p>
                    <p className="text-[11.5px] text-slate-400 mt-0.5">{desc}</p>
                  </button>
                ))}
              </div>
            </div>

          </div>

          {/* ── Watchlist ── */}
          <PinnedCards subscriptions={subscriptions} />

        </div>
      )}

      <FloatingAIAssistant />
    </div>
  );
}


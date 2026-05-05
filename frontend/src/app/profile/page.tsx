'use client';

import { useEffect, useState } from 'react';
import { useIsAuthenticated, useMsal } from '@azure/msal-react';
import { ArrowPathIcon } from '@heroicons/react/24/outline';
import { useAppStore } from '../../store/appStore';
import { apiService } from '../../services/apiService';

export default function ProfilePage() {
  const isAuthenticated = useIsAuthenticated();
  const { instance, accounts } = useMsal();
  const { 
    user, 
    subscriptions, 
    setUser, 
    messageLayoutStyle, 
    setMessageLayoutStyle,
    filteredSubscriptionIds,
    setFilteredSubscriptionIds,
    useFilteredSubscriptionsOnly,
    setUseFilteredSubscriptionsOnly,
  } = useAppStore();
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshingPhoto, setIsRefreshingPhoto] = useState(false);
  const [showAllSubscriptions, setShowAllSubscriptions] = useState(false);
  const [subscriptionsExpanded, setSubscriptionsExpanded] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    // Fetch fresh user data when page loads
    const fetchUserData = async () => {
      if (isAuthenticated && accounts.length > 0) {
        try {
          const account = accounts[0];
          
          // Get fresh tokens
          const tokenResponse = await instance.acquireTokenSilent({
            scopes: ["https://management.azure.com/user_impersonation"],
            account: account,
          });

          let profileTokenResponse;
          try {
            profileTokenResponse = await instance.acquireTokenSilent({
              scopes: ["openid", "profile", "email"],
              account: account,
            });
          } catch (e) {
            // profile token is optional
          }

          // Get Graph token for photo
          let graphToken = null;
          try {
            const graphTokenResponse = await instance.acquireTokenSilent({
              scopes: ["User.Read"],
              account: account,
            });
            graphToken = graphTokenResponse.accessToken;
            apiService.setGraphToken(graphToken);
          } catch (e: any) {
            // If consent is required, try interactive flow
            if (e?.errorCode === 'consent_required' || 
                e?.errorCode === 'interaction_required' ||
                e?.errorMessage?.includes('AADSTS65001')) {
              try {
                const graphTokenResponse = await instance.acquireTokenPopup({
                  scopes: ["User.Read"],
                  account: account,
                });
                graphToken = graphTokenResponse.accessToken;
                apiService.setGraphToken(graphToken);
              } catch (popupError) {
                // Silently fail
              }
            }
          }

          // Extract user info
          const tokenClaims = profileTokenResponse?.idTokenClaims || tokenResponse?.idTokenClaims || {};
          const email = account.username || 
                       account.idTokenClaims?.email || 
                       account.idTokenClaims?.preferred_username || 
                       account.idTokenClaims?.upn || 
                       tokenClaims?.email ||
                       tokenClaims?.preferred_username ||
                       tokenClaims?.upn ||
                       '';
          
          // Fetch photo (cached or fresh)
          let photoUrl = null;
          if (graphToken) {
            try {
              photoUrl = await apiService.getUserPhoto();
            } catch (e) {
              console.log('Could not fetch photo');
            }
          }
          
          const userInfo = {
            id: account.idTokenClaims?.oid || account.localAccountId?.split('.')[0] || account.homeAccountId?.split('.')[0] || '',
            name: account.name || '',
            email: email,
            username: account.username || '',
            tenantId: account.tenantId || '',
            photoUrl: photoUrl,
          };
          
          setUser(userInfo);
        } catch (error) {
          console.error('Failed to fetch user data:', error);
        }
      }
      setIsLoading(false);
    };

    fetchUserData();
  }, [isAuthenticated, accounts, instance, setUser]);

  const refreshPhoto = async () => {
    if (!isAuthenticated || accounts.length === 0) return;
    
    setIsRefreshingPhoto(true);
    try {
      const account = accounts[0];
      const graphTokenResponse = await instance.acquireTokenSilent({
        scopes: ["User.Read"],
        account: account,
      });
      
      apiService.setGraphToken(graphTokenResponse.accessToken);
      apiService.clearUserPhotoCache(); // Clear cache to force fresh fetch
      
      const photoUrl = await apiService.getUserPhoto();
      setUser({ ...user, photoUrl });
    } catch (error) {
      console.error('Failed to refresh photo:', error);
    } finally {
      setIsRefreshingPhoto(false);
    }
  };

  if (!isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <h2 className="text-xl font-semibold text-slate-900 mb-4">
            Please sign in to view your profile
          </h2>
        </div>
      </div>
    );
  }

  if (isLoading || !user) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-2 border-indigo-200 border-t-indigo-600 mx-auto mb-4"></div>
          <p className="text-slate-600">Loading profile...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto py-6 px-4 sm:px-6 lg:px-8">
        <div className="mb-6">
          <h1 className="text-[22px] font-bold text-slate-900 tracking-tight">Profile</h1>
          <p className="mt-1 text-slate-500">
            Manage your account information and preferences
          </p>
        </div>

        <div className="bg-white border border-slate-200 rounded-xl shadow-sm">
          <div className="px-6 py-6">
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-6">
              Account Information
            </h2>
            
            <div className="space-y-6">
              <div>
                <label className="block text-sm font-medium text-slate-700">
                  Profile Photo
                </label>
                <div className="mt-2 flex items-end">
                  <div className="w-16 h-16 rounded-full overflow-hidden bg-slate-200">
                    {user.photoUrl ? (
                      <img
                        src={user.photoUrl}
                        alt={user.name || 'User'}
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-slate-400">
                        <svg className="w-8 h-8" fill="currentColor" viewBox="0 0 20 20">
                          <path fillRule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clipRule="evenodd" />
                        </svg>
                      </div>
                    )}
                  </div>
                  <button
                    onClick={refreshPhoto}
                    disabled={isRefreshingPhoto}
                    className="p-1.5 text-indigo-600 hover:text-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
                    title="Refresh Photo"
                  >
                    <ArrowPathIcon className={`h-5 w-5 ${isRefreshingPhoto ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700">
                  Display Name
                </label>
                <div className="mt-1 text-sm text-slate-900">
                  {user.name || 'Not provided'}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700">
                  Email Address
                </label>
                <div className="mt-1 text-sm text-slate-900">
                  {user.email || 'Not provided'}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700">
                  User ID
                </label>
                <div className="mt-1 text-sm text-slate-900 font-mono">
                  {user.id || 'Not provided'}
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700">
                  Tenant ID
                </label>
                <div className="mt-1 text-sm text-slate-900 font-mono">
                  {user.tenantId || 'Not provided'}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-6 bg-white border border-slate-200 rounded-xl shadow-sm">
          <div className="px-6 py-6">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Azure Subscriptions
                </h2>
                <p className="text-sm text-slate-500 mt-1">
                  {subscriptions.filter(s => s.state === 'Enabled').length} Active subscription{subscriptions.filter(s => s.state === 'Enabled').length !== 1 ? 's' : ''}
                  {useFilteredSubscriptionsOnly && ` • ${filteredSubscriptionIds.length} selected for filtering`}
                </p>
              </div>
              {subscriptions.length > 0 && (
                <button
                  onClick={() => setSubscriptionsExpanded(!subscriptionsExpanded)}
                  className="text-sm text-indigo-600 hover:text-indigo-700 font-medium"
                >
                  {subscriptionsExpanded ? 'Hide' : 'Manage'}
                </button>
              )}
            </div>
            
            {subscriptionsExpanded && subscriptions.length > 0 && (
              <>
                <div className="mb-4 space-y-3">
                  <input
                    type="text"
                    placeholder="Search subscriptions..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
                  />
                  <div className="flex items-center gap-4">
                    <button
                      onClick={() => {
                        const enabledSubs = subscriptions
                          .filter(s => s.state === 'Enabled')
                          .map(s => s.subscription_id);
                        setFilteredSubscriptionIds(enabledSubs);
                      }}
                      className="text-sm text-indigo-600 hover:text-indigo-700"
                    >
                      Select All Enabled
                    </button>
                    <button
                      onClick={() => setFilteredSubscriptionIds([])}
                      className="text-sm text-red-600 hover:text-red-700"
                    >
                      Clear Selection
                    </button>
                  </div>
                </div>
                <div className="max-h-96 overflow-y-auto space-y-2 pr-2">
                  {subscriptions
                  .filter(s => s.state === 'Enabled')
                  .filter(sub => 
                    searchTerm === '' || 
                    sub.display_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                    sub.subscription_id.toLowerCase().includes(searchTerm.toLowerCase())
                  )
                  .map((subscription) => {
                    const isSelected = filteredSubscriptionIds.includes(subscription.subscription_id);
                    return (
                      <div
                        key={subscription.subscription_id}
                        onClick={() => {
                          if (isSelected) {
                            setFilteredSubscriptionIds(
                              filteredSubscriptionIds.filter(id => id !== subscription.subscription_id)
                            );
                          } else {
                            setFilteredSubscriptionIds([...filteredSubscriptionIds, subscription.subscription_id]);
                          }
                        }}
                        className={`border rounded-lg p-3 cursor-pointer hover:bg-slate-50 transition-colors ${
                          isSelected
                            ? 'border-indigo-500 bg-indigo-50'
                            : 'border-slate-200'
                        }`}
                      >
                        <div className="flex justify-between items-start gap-3">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => {}}
                            className="mt-1 h-4 w-4 text-indigo-600 focus:ring-indigo-500 border-slate-300 rounded"
                          />
                          <div className="flex-1 min-w-0">
                            <h3 className="text-sm font-medium text-slate-900 truncate">
                              {subscription.display_name}
                            </h3>
                            <p className="text-xs text-slate-500 font-mono truncate mt-1">
                              {subscription.subscription_id}
                            </p>
                          </div>
                          <span
                            className={`flex-shrink-0 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                              subscription.state === 'Enabled'
                                ? 'bg-green-100 text-green-800'
                                : subscription.state === 'Disabled'
                                ? 'bg-red-100 text-red-800'
                                : 'bg-yellow-100 text-yellow-800'
                            }`}
                          >
                            {subscription.state}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}
            
            {!subscriptionsExpanded && subscriptions.length > 0 && (
              <p className="text-sm text-slate-500">
                Click "Manage" to select which subscriptions to show in filters
              </p>
            )}
            
            {subscriptions.length === 0 && (
              <p className="text-slate-500">No subscriptions found.</p>
            )}
          </div>
        </div>

        <div className="mt-6 bg-white border border-slate-200 rounded-xl shadow-sm">
          <div className="px-4 sm:px-6 py-5 sm:py-6">
            <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-4 sm:mb-6">
              Preferences
            </h2>
            
            <div className="space-y-6">
              {/* Subscription Filter Preference */}
              <div className="pb-6 border-b border-slate-200">
                <div className="flex items-center justify-between">
                  <div>
                    <label className="block text-sm font-medium text-slate-900">
                      Subscription Filtering
                    </label>
                    <p className="text-sm text-slate-500 mt-1">
                      {useFilteredSubscriptionsOnly
                        ? `Showing ${filteredSubscriptionIds.length} selected subscription${filteredSubscriptionIds.length !== 1 ? 's' : ''} in filters`
                        : 'Showing all subscriptions in filters'
                      }
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setUseFilteredSubscriptionsOnly(!useFilteredSubscriptionsOnly)}
                    className={`touch-target-small relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:ring-offset-2 ${
                      useFilteredSubscriptionsOnly ? 'bg-indigo-600' : 'bg-slate-200'
                    }`}
                    role="switch"
                    aria-checked={useFilteredSubscriptionsOnly}
                  >
                    <span
                      aria-hidden="true"
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                        useFilteredSubscriptionsOnly ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                <div className="mt-3 flex items-center gap-4 text-xs text-slate-500">
                  <span className={!useFilteredSubscriptionsOnly ? 'font-semibold text-slate-900' : ''}>
                    All Subscriptions
                  </span>
                  <span className={useFilteredSubscriptionsOnly ? 'font-semibold text-slate-900' : ''}>
                    Filtered Only
                  </span>
                </div>
                {useFilteredSubscriptionsOnly && filteredSubscriptionIds.length === 0 && (
                  <p className="mt-3 text-xs text-amber-600">
                    No subscriptions selected. Please select subscriptions above to filter.
                  </p>
                )}
                <p className="mt-3 text-xs text-slate-500">
                  Controls which subscriptions appear in filters on Query, Cost, and Dashboard pages.
                </p>
              </div>

              {/* Message Layout Style Preference */}
              <div>
                <div className="flex items-center justify-between">
                  <div>
                    <label className="block text-sm font-medium text-slate-900">
                      Message Display Style
                    </label>
                    <p className="text-sm text-slate-500 mt-1">
                      {messageLayoutStyle === 'bubble' 
                        ? 'Compact chat bubbles for both user and AI messages'
                        : 'User messages stay as bubbles, AI responses spread full-width for better readability'
                      }
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setMessageLayoutStyle(messageLayoutStyle === 'bubble' ? 'fullwidth' : 'bubble')}
                    className={`touch-target-small relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-indigo-600 focus:ring-offset-2 ${
                      messageLayoutStyle === 'fullwidth' ? 'bg-indigo-600' : 'bg-slate-200'
                    }`}
                    role="switch"
                    aria-checked={messageLayoutStyle === 'fullwidth'}
                  >
                    <span
                      aria-hidden="true"
                      className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                        messageLayoutStyle === 'fullwidth' ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
                <div className="mt-3 flex items-center gap-4 text-xs text-slate-500">
                  <span className={messageLayoutStyle === 'bubble' ? 'font-semibold text-slate-900' : ''}>
                    Bubble Style
                  </span>
                  <span className={messageLayoutStyle === 'fullwidth' ? 'font-semibold text-slate-900' : ''}>
                    Full Width
                  </span>
                </div>
                <p className="mt-3 text-xs text-slate-500">
                  Changes take effect immediately in all conversations.
                </p>
              </div>
            </div>
          </div>
        </div>
    </div>
  );
}
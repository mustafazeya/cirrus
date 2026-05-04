'use client';

import { useEffect, useState } from 'react';
import { useIsAuthenticated, useMsal } from '@azure/msal-react';
import { InteractionStatus } from '@azure/msal-browser';
import { useAppStore, filterEnabledSubscriptions } from '../store/appStore';
import { apiService } from '../services/apiService';
import { setUserContext, clearUserContext } from '../utils/appInsights';

interface AuthWrapperProps {
  children: React.ReactNode;
}

export default function AuthWrapper({ children }: AuthWrapperProps) {
  const isAuthenticated = useIsAuthenticated();
  const { instance, accounts, inProgress } = useMsal();
  const [isInitializing, setIsInitializing] = useState(true);
  const { 
    user, 
    subscriptions, 
    setUser, 
    setSubscriptions, 
    setLoading,
    setTokensReady,
    setError, 
    clearError
  } = useAppStore();

  useEffect(() => {
    // CRITICAL: Don't initialize while MSAL is processing redirect
    // This prevents race conditions on mobile Safari after consent redirect
    if (inProgress !== InteractionStatus.None) {
      console.log('MSAL interaction in progress:', inProgress);
      return;
    }
    
    // Initialize authentication state when component mounts or auth state changes
    if (isAuthenticated && accounts.length > 0) {
      // Only initialize if not already done (prevents infinite loops)
      if (isInitializing) {
        console.log(' Initializing auth state...');
        initializeAuthState();
      }
    } else if (!isAuthenticated && accounts.length === 0 && !isInitializing) {
      // ONLY clear user data on actual logout (no accounts at all)
      // Don't clear during token refresh or when user data exists in cache
      if (user) {
        console.log('User logged out - clearing state');
        setUser(null);
        setSubscriptions([]);
        apiService.clearAccessToken();
        clearUserContext();
      }
      setIsInitializing(false);
    }
  }, [isAuthenticated, accounts, inProgress, isInitializing]);

  // Proactive token refresh - refresh tokens before they expire
  useEffect(() => {
    if (!isAuthenticated || accounts.length === 0) return;

    console.log(' Setting up proactive token refresh (every 50 minutes)');
    
    // Refresh tokens every 50 minutes (Azure AD tokens expire after 60 minutes)
    const refreshInterval = setInterval(async () => {
      console.log(' Proactive token refresh triggered');
      try {
        const account = accounts[0];
        if (!account) return;

        // Refresh management token
        const tokenResponse = await instance.acquireTokenSilent({
          scopes: ["https://management.azure.com/user_impersonation"],
          account: account,
          forceRefresh: true, // Force new token from Azure AD
        });
        apiService.setAccessToken(tokenResponse.accessToken);
        console.log(' Management token proactively refreshed');

        // Refresh ID token if available
        if (tokenResponse.idToken) {
          apiService.setIdToken(tokenResponse.idToken);
        }

        // Refresh Log Analytics token
        try {
          const laTokenResponse = await instance.acquireTokenSilent({
            scopes: ["https://api.loganalytics.io/.default"],
            account: account,
            forceRefresh: true,
          });
          apiService.setLogAnalyticsToken(laTokenResponse.accessToken);
          console.log(' LA token proactively refreshed');
        } catch (laError) {
          console.warn(' Could not proactively refresh LA token:', laError);
          apiService.setLogAnalyticsToken('');
        }

        // Refresh Graph token
        try {
          const graphTokenResponse = await instance.acquireTokenSilent({
            scopes: ["User.Read"],
            account: account,
            forceRefresh: true,
          });
          apiService.setGraphToken(graphTokenResponse.accessToken);
          console.log(' Graph token proactively refreshed');
        } catch (graphError) {
          console.warn(' Could not proactively refresh Graph token:', graphError);
        }

      } catch (error) {
        console.error(' Proactive token refresh failed:', error);
        // Don't clear tokens on proactive refresh failure
        // Let reactive refresh (on 401) handle it
        // IMPORTANT: App state (filters, globalScope, Cloudflare) is preserved
        // State is managed by Zustand store and persists in localStorage
      }
    }, 50 * 60 * 1000); // 50 minutes

    // Cleanup interval on unmount or auth state change
    return () => {
      console.log(' Clearing proactive token refresh interval');
      clearInterval(refreshInterval);
    };
  }, [isAuthenticated, accounts, instance]);

  const fetchUserPhotoIfMissing = async () => {
    if (!accounts.length) return;
    
    try {
      const account = accounts[0];
      
      // Get Microsoft Graph token for user profile photo
      const graphTokenResponse = await instance.acquireTokenSilent({
        scopes: ["User.Read"],
        account: account,
      });
      
      apiService.setGraphToken(graphTokenResponse.accessToken);
      
      // Fetch photo from Graph API (will use cache if available and valid)
      const photoUrl = await apiService.getUserPhoto();
      
      if (photoUrl) {
        setUser({ ...user, photoUrl });
      }
    } catch (error: any) {
      // Photo fetch is optional - don't block user experience if it fails
      if (error?.errorCode !== 'consent_required' && 
          error?.errorCode !== 'interaction_required' &&
          !error?.errorMessage?.includes('AADSTS65001')) {
        console.warn('Could not fetch user photo:', error);
      }
    }
  };

  const initializeAuthState = async () => {
    try {
      setLoading(true);
      clearError();

      const account = accounts[0];
      if (account) {
        // Register token refresh callback for automatic retry on 401
        apiService.setTokenRefreshCallback(async () => {
          console.log(' Token refresh callback triggered');
          const tokenResponse = await instance.acquireTokenSilent({
            scopes: ["https://management.azure.com/user_impersonation"],
            account: account,
            forceRefresh: true,
          });
          apiService.setAccessToken(tokenResponse.accessToken);
          
          if (tokenResponse.idToken) {
            apiService.setIdToken(tokenResponse.idToken);
          }
          
          // Also refresh Log Analytics token
          try {
            const laTokenResponse = await instance.acquireTokenSilent({
              scopes: ["https://api.loganalytics.io/.default"],
              account: account,
              forceRefresh: true,
            });
            apiService.setLogAnalyticsToken(laTokenResponse.accessToken);
          } catch (err) {
            console.warn('Could not refresh Log Analytics token, clearing it:', err);
            // Clear LA token if refresh fails to prevent using stale token
            apiService.setLogAnalyticsToken('');
          }
        });
        
        // Clear redirect flag on successful init
        sessionStorage.removeItem('msal_redirect_in_progress');
        
        // Now get each token individually (should be silent after initial login consent)
        const tokenResponse = await instance.acquireTokenSilent({
          scopes: ["https://management.azure.com/user_impersonation"],
          account: account,
        });

        console.log(' Acquired Management API token with audience:', 
          tokenResponse.accessToken ? 'token present' : 'no token');
        console.log(' Token scopes:', tokenResponse.scopes);
        console.log(' ID Token present:', tokenResponse.idToken ? 'yes' : 'no');

        // Also try to get a token with profile scope to get user info
        let profileTokenResponse;
        try {
          profileTokenResponse = await instance.acquireTokenSilent({
            scopes: ["openid", "profile", "email"],
            account: account,
          });
        } catch (profileError) {
          console.log('Could not get profile token, using account info:', profileError);
        }

        // Set the management token in the API service
        apiService.setAccessToken(tokenResponse.accessToken);
        setTokensReady(true);
        
        // Set ID token for role checking (admin access) if available
        if (tokenResponse.idToken) {
          apiService.setIdToken(tokenResponse.idToken);
          console.log(' ID token set for role checking');
        }
        
        console.log(' Management API token set in API service');

        // Get Log Analytics token for KQL queries
        try {
          const logAnalyticsTokenResponse = await instance.acquireTokenSilent({
            scopes: ["https://api.loganalytics.io/.default"],
            account: account,
          });
          apiService.setLogAnalyticsToken(logAnalyticsTokenResponse.accessToken);
        } catch (laTokenError) {
          console.warn('Could not get Log Analytics token, clearing it:', laTokenError);
          // Clear LA token if acquisition fails to prevent using stale token
          apiService.setLogAnalyticsToken('');
        }

        // Get Microsoft Graph token for user profile photo
        // Using only User.Read scope which doesn't require admin consent
        let graphToken = null;
        try {
          const graphTokenResponse = await instance.acquireTokenSilent({
            scopes: ["User.Read"],
            account: account,
          });
          graphToken = graphTokenResponse.accessToken;
          apiService.setGraphToken(graphToken);
        } catch (graphTokenError: any) {
          // Graph token should already be consented during initial login
          // If it fails, photo fetch will be skipped (non-critical)
          console.warn('Graph token not available, photo fetch may fail:', graphTokenError);
        }

        // Extract email from different possible sources, including token claims
        const tokenClaims = profileTokenResponse?.idTokenClaims || tokenResponse?.idTokenClaims || {};
        const email = account.username || 
                     account.idTokenClaims?.email || 
                     account.idTokenClaims?.preferred_username || 
                     account.idTokenClaims?.upn || 
                     tokenClaims?.email ||
                     tokenClaims?.preferred_username ||
                     tokenClaims?.upn ||
                     '';
        
        const userId = account.idTokenClaims?.oid || account.localAccountId?.split('.')[0] || account.homeAccountId?.split('.')[0] || '';
        
        // Create user info without photoUrl first
        const userInfo = {
          id: userId,
          name: account.name || '',
          email: email,
          username: account.username || '',
          tenantId: account.tenantId || '',
        };
        setUser(userInfo);

        // Identify the user in App Insights — unlocks Users / Sessions /
        // User Flows / Retention blades in the portal.
        // OID is a stable, opaque Azure AD identifier (non-PII).
        setUserContext(userId, account.tenantId);
        
        // Fetch photo asynchronously if we have a graph token
        if (graphToken) {
          try {
            const photoUrl = await apiService.getUserPhoto();
            if (photoUrl) {
              setUser({ ...userInfo, photoUrl });
            }
          } catch (photoError) {
            // Silently fail - photo is optional
          }
        }

        // Fetch subscriptions if not already loaded (load first page with larger size)
        // Check if we have cached subscriptions and they're not too old (5 minutes)
        const now = Date.now();
        const subscriptionsCacheKey = `subscriptions_cache_timestamp_${userId}`;
        const lastFetchTime = localStorage.getItem(subscriptionsCacheKey);
        const cacheAge = lastFetchTime ? now - parseInt(lastFetchTime) : Infinity;
        const CACHE_DURATION = 5 * 60 * 1000; // 5 minutes
        
        if (subscriptions.length === 0 || cacheAge > CACHE_DURATION) {
          console.log(`Fetching subscriptions (cache age: ${Math.round(cacheAge / 1000)}s)`);
          const response = await apiService.getSubscriptions();
          if (response.data) {
            // Handle new paginated response format
            const allSubscriptions = response.data.data || response.data || [];
            
            // Filter out disabled subscriptions
            const enabledSubscriptions = filterEnabledSubscriptions(allSubscriptions);
            
            setSubscriptions(enabledSubscriptions);
            
            // Update cache timestamp
            localStorage.setItem(subscriptionsCacheKey, now.toString());
            console.log(`Loaded ${enabledSubscriptions.length} subscriptions`);
          }
        } else {
          console.log(`Using cached subscriptions (${subscriptions.length} items, age: ${Math.round(cacheAge / 1000)}s)`);
        }
      }
    } catch (error: any) {
      console.error('Failed to initialize auth state:', error);
      
      // Check if this is an interaction required error (consent needed, token expired, etc.)
      const isInteractionRequired = 
        error?.errorCode === 'interaction_required' ||
        error?.errorCode === 'consent_required' ||
        error?.errorCode === 'login_required' ||
        error?.name === 'InteractionRequiredAuthError' ||
        error?.errorMessage?.includes('AADSTS65001') || // User/admin consent required
        error?.errorMessage?.includes('AADSTS50058') || // Silent sign-in failed
        error?.errorMessage?.includes('AADSTS50076'); // MFA required
      
      if (isInteractionRequired) {
        console.log(' Interaction required - redirecting to login for consent...');
        
        // Prevent infinite redirect loops - check if we just came back from redirect
        const justRedirected = sessionStorage.getItem('msal_redirect_in_progress');
        if (justRedirected) {
          console.error(' Already attempted redirect, but still failing. Not retrying.');
          sessionStorage.removeItem('msal_redirect_in_progress');
          setError('Authentication failed after consent. Please try logging in again.');
          return;
        }
        
        try {
          // Mark that we're about to redirect
          sessionStorage.setItem('msal_redirect_in_progress', 'true');
          
          // Clear any stale tokens
          apiService.clearAccessToken();
          
          // Redirect to interactive login to get user consent with ALL scopes at once
          await instance.acquireTokenRedirect({
            scopes: [
              "https://management.azure.com/user_impersonation",
              "https://api.loganalytics.io/.default", 
              "User.Read",
              "openid",
              "profile",
              "email"
            ],
            account: accounts[0],
            prompt: 'consent', // Force consent screen for all scopes
          });
          return; // Don't show error, we're redirecting
        } catch (redirectError) {
          console.error('Token refresh redirect failed:', redirectError);
          sessionStorage.removeItem('msal_redirect_in_progress');
          setError('Authentication consent required. Please sign in again.');
        }
      } else {
        setError('Failed to initialize authentication');
      }
    } finally {
      setLoading(false);
      setIsInitializing(false);
    }
  };

  // While MSAL is resolving its startup / redirect flow, render only a white
  // screen — no sidebar, no navbar, no layout.  This prevents the brief flash
  // of the authenticated shell before the login page takes over.
  // Startup takes ~50-150 ms; HandleRedirect completes in one tick.
  if (
    inProgress === InteractionStatus.Startup ||
    inProgress === InteractionStatus.HandleRedirect
  ) {
    return <div style={{ position: 'fixed', inset: 0, background: '#fff' }} />;
  }

  return <>{children}</>;
}
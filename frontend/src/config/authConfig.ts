// MSAL configuration
const AZURE_CLIENT_ID = process.env.NEXT_PUBLIC_AZURE_CLIENT_ID || '';
const AZURE_TENANT_ID = process.env.NEXT_PUBLIC_AZURE_TENANT_ID || '';

// Dynamically determine redirect URI based on current domain
const getRedirectUri = () => {
  if (typeof window !== 'undefined') {
    return window.location.origin;
  }
  return process.env.NEXT_PUBLIC_AZURE_REDIRECT_URI || 'http://localhost:3000';
};

export const msalConfig = {
  auth: {
    clientId: AZURE_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${AZURE_TENANT_ID}`,
    redirectUri: getRedirectUri(),
    postLogoutRedirectUri: getRedirectUri(),
    navigateToLoginRequestUrl: false, // Set to false for SPA apps
  },
  cache: {
    // Use localStorage for persistent auth across sessions and tabs
    // The inProgress check in AuthWrapper prevents race conditions on mobile
    cacheLocation: "localStorage",
    // CRITICAL: Store auth state in cookies for mobile Safari compatibility
    // This prevents state loss during redirect on mobile browsers
    storeAuthStateInCookie: true,
  },
  system: {
    loggerOptions: {
      loggerCallback: (level, message, containsPii) => {
        if (containsPii) {
          return;
        }
        console.log('MSAL:', message);
      },
    },
    // Increase timeouts for mobile networks
    windowHashTimeout: 9000,
    iframeHashTimeout: 9000,
    loadFrameTimeout: 9000,
  }
};

// Scopes for API access
export const loginRequest = {
  scopes: [
    "https://management.azure.com/user_impersonation" // Azure Management API scope - PRIMARY scope for login
  ]
};

// Additional scopes for Microsoft Graph (acquired separately after login)
export const graphRequest = {
  scopes: [
    "User.Read", // Microsoft Graph API - user profile and photo
    "openid",
    "profile",
    "email"
  ]
};

// Additional scope for Log Analytics queries
export const logAnalyticsRequest = {
  scopes: ["https://api.loganalytics.io/Data.Read"]
};

// Graph API scopes and endpoints
export const graphConfig = {
  graphMeEndpoint: "https://graph.microsoft.com/v1.0/me",
  graphMePhotoEndpoint: "https://graph.microsoft.com/v1.0/me/photo/$value"
};

// API configuration
export const API_CONFIG = {
  baseURL: process.env.NEXT_PUBLIC_API_URL!,
  timeout: 300000, // 300 seconds (5 minutes) - AI queries with complex analysis need more time
};
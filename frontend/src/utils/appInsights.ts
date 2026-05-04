// Application Insights configuration for frontend telemetry
import { ApplicationInsights } from '@microsoft/applicationinsights-web';

let appInsights: ApplicationInsights | null = null;

const environment = process.env.NEXT_PUBLIC_ENVIRONMENT ?? 'local';

// Friendly display names for Next.js routes — used in "Most visited pages" / User Flows portal blades
const PAGE_NAMES: Record<string, string> = {
  '/': 'Home',
  '/dashboard': 'Dashboard',
  '/costs': 'Cost Analysis',
  '/ai': 'AI',
  '/profile': 'Profile',
  '/admin': 'Admin',
};

const getPageName = (path: string): string => {
  const clean = path.split('?')[0].replace(/\/$/, '') || '/';
  return PAGE_NAMES[clean] ?? clean;
};

export const initializeAppInsights = () => {
  const connectionString = process.env.NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING;

  if (!connectionString) {
    console.warn('Application Insights connection string not configured');
    return null;
  }

  if (appInsights) {
    return appInsights;
  }

  try {
    appInsights = new ApplicationInsights({
      config: {
        connectionString: connectionString,
        enableAutoRouteTracking: true,
        enableRequestHeaderTracking: true,
        enableResponseHeaderTracking: true,
        enableAjaxErrorStatusText: true,
        enableAjaxPerfTracking: true,
        maxAjaxCallsPerView: 20,
        disableFetchTracking: false,
        disableAjaxTracking: false,
        disableExceptionTracking: false,
        enableCorsCorrelation: true,
        enableDebug: false,
        loggingLevelConsole: 0,
        // Required for Users / Sessions / User Flows blades to work in portal
        enableSessionStorageBuffer: true,
        namePrefix: 'cloudex',
      }
    });

    appInsights.loadAppInsights();

    // Tag every telemetry item with environment, role, and page name
    appInsights.addTelemetryInitializer((item) => {
      item.tags = item.tags ?? {};
      item.tags['ai.cloud.role'] = `cloudex-frontend-${environment}`;
      item.tags['ai.cloud.roleInstance'] = `cloudex-frontend-${environment}`;

      item.baseData = item.baseData ?? {};
      item.baseData['properties'] = item.baseData['properties'] ?? {};
      item.baseData['properties']['environment'] = environment;

      // Enrich page views with friendly name + browser performance timings
      if (item.baseType === 'PageviewData' || item.baseType === 'PageviewPerformanceData') {
        const path = typeof window !== 'undefined' ? window.location.pathname : '';
        item.baseData['name'] = getPageName(path);

        if (typeof window !== 'undefined' && window.performance?.timing) {
          const t = window.performance.timing;
          const nav = t.navigationStart;
          if (nav > 0) {
            item.baseData['properties']['perf_dns_ms'] = t.domainLookupEnd - t.domainLookupStart;
            item.baseData['properties']['perf_connect_ms'] = t.connectEnd - t.connectStart;
            item.baseData['properties']['perf_ttfb_ms'] = t.responseStart - nav;
            item.baseData['properties']['perf_dom_interactive_ms'] = t.domInteractive - nav;
            item.baseData['properties']['perf_dom_complete_ms'] = t.domComplete - nav;
          }
        }
      }
    });

    appInsights.trackPageView({ name: getPageName(typeof window !== 'undefined' ? window.location.pathname : '/') });

    if (environment === 'local') {
      console.log(`[AppInsights] Initialized — streaming to cloudex-shared-insights as cloudex-frontend-local`);
    } else {
      console.log(`[AppInsights] Initialized (environment: ${environment})`);
    }
    return appInsights;
  } catch (error) {
    console.error('Failed to initialize Application Insights:', error);
    return null;
  }
};

/**
 * Call immediately after successful login.
 * Enables Users / Sessions / User Flows / Retention blades in the portal
 * and lets you count unique authenticated users (not just anonymous sessions).
 * userId = Azure AD OID (stable, opaque, non-PII identifier).
 * accountId = optional secondary grouping (e.g. tenantId).
 */
export const setUserContext = (userId: string, accountId?: string) => {
  if (appInsights) {
    // storeInCookie=false — uses session storage only, avoiding GDPR cookie concerns
    appInsights.setAuthenticatedUserContext(userId, accountId, false);
  }
};

/** Call on logout to clear the authenticated user context. */
export const clearUserContext = () => {
  if (appInsights) {
    appInsights.clearAuthenticatedUserContext();
  }
};

export const getAppInsights = (): ApplicationInsights | null => {
  return appInsights;
};

// Track custom events
export const trackEvent = (name: string, properties?: { [key: string]: any }) => {
  if (appInsights) {
    appInsights.trackEvent({ name, properties });
  }
};

// Track exceptions
export const trackException = (error: Error, properties?: { [key: string]: any }) => {
  if (appInsights) {
    appInsights.trackException({ exception: error, properties });
  }
};

// Track traces / logs
export const trackTrace = (message: string, severityLevel?: number, properties?: { [key: string]: any }) => {
  if (appInsights) {
    appInsights.trackTrace({ message, severityLevel, properties });
  }
};

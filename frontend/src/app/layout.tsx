'use client';

import { MsalProvider } from '@azure/msal-react';
import { PublicClientApplication } from '@azure/msal-browser';
import { msalConfig } from '../config/authConfig';
import Navbar from '../components/Navbar';
import AuthWrapper from '../components/AuthWrapper';
import './globals.css';
import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { initializeAppInsights } from '../utils/appInsights';

// Create MSAL instance
const msalInstance = new PublicClientApplication(msalConfig);

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname();

  // Initialize Application Insights on mount
  useEffect(() => {
    initializeAppInsights();
  }, []);

  // Scroll app-main to top on every route change (window.scrollTo doesn't work
  // when the scroll container is the .app-main element, not the window itself)
  useEffect(() => {
    document.querySelector('.app-main')?.scrollTo({ top: 0 });
  }, [pathname]);

  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="theme-color" content="#6366f1" />
        <meta name="description" content="CIRRUS - Intelligent Azure Cloud Management Platform powered by AI" />
        <link rel="icon" type="image/x-icon" href="/favicon.ico?v=6" />
        <link rel="icon" type="image/svg+xml" href="/favicon.svg?v=6" />
        <link rel="apple-touch-icon" href="/favicon.svg?v=6" />
        <title>CIRRUS - Azure Cloud Intelligence</title>
      </head>
      <body className="antialiased">
        <MsalProvider instance={msalInstance}>
          <AuthWrapper>
            {/* app-layout: sidebar (lg) + scrollable content — see globals.css */}
            <div className="app-layout">
              <Navbar />
              <div className="app-content">
                <main className="app-main pt-14 lg:pt-0">
                  {children}
                </main>
              </div>
            </div>
          </AuthWrapper>
        </MsalProvider>
      </body>
    </html>
  );
}
// ── Production console gate ─────────────────────────────────────────
// Silence debug/log noise in production builds. Keep warn + error so
// real problems are still visible in packaged-app DevTools.
if (import.meta.env.PROD) {
  const noop = () => {};
  console.log = noop;
  console.debug = noop;
  // console.warn and console.error are intentionally preserved
}

import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import './styles/global.css';

// ── Global renderer error handlers ──────────────────────────────────
// Catch uncaught JS errors that slip past React
window.onerror = (message, source, lineno, colno, error) => {
  console.error('[Global] Uncaught error:', { message, source, lineno, colno, error });
  try {
    window.eve?.sessionHealth?.recordError?.('window.onerror', String(message));
  } catch {
    // IPC may not be ready yet
  }
};

// Catch unhandled promise rejections in renderer
window.onunhandledrejection = (event: PromiseRejectionEvent) => {
  console.error('[Global] Unhandled rejection:', event.reason);
  try {
    const msg = event.reason instanceof Error ? event.reason.message : String(event.reason);
    window.eve?.sessionHealth?.recordError?.('unhandledrejection', msg);
  } catch {
    // IPC may not be ready yet
  }
};

// ── Render ───────────────────────────────────────────────────────────
const root = createRoot(document.getElementById('root')!);
root.render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);

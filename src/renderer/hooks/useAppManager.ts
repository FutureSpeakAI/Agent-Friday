/**
 * useAppManager.ts — Centralized app open/close state
 *
 * Replaces the 6 individual boolean useState calls in App.tsx
 * with a single Set-based state manager for all 27 registered apps.
 */

import { useState, useCallback } from 'react';

export interface AppManager {
  /** Set of currently open app IDs */
  openApps: Set<string>;
  /** Open an app by ID */
  openApp: (id: string) => void;
  /** Close an app by ID */
  closeApp: (id: string) => void;
  /** Toggle an app open/closed */
  toggleApp: (id: string) => void;
  /** Check if an app is currently open */
  isOpen: (id: string) => boolean;
  /** Close all apps */
  closeAll: () => void;
}

export function useAppManager(): AppManager {
  const [openApps, setOpenApps] = useState<Set<string>>(new Set());

  const openApp = useCallback((id: string) => {
    setOpenApps((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    window.eve.telemetry.appLaunched(id);
  }, []);

  const closeApp = useCallback((id: string) => {
    setOpenApps((prev) => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const toggleApp = useCallback((id: string) => {
    setOpenApps((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const isOpen = useCallback((id: string) => openApps.has(id), [openApps]);

  const closeAll = useCallback(() => setOpenApps(new Set()), []);

  return { openApps, openApp, closeApp, toggleApp, isOpen, closeAll };
}

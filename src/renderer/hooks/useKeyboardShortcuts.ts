import { useEffect } from 'react';
import { useAppStore } from '../store';

/**
 * Keyboard shortcut handler extracted from App.tsx.
 * Handles Ctrl+K (quick actions), Ctrl+Shift+D/M/A/P/C (app toggles),
 * and Space (mic toggle in voice mode).
 */
export function useKeyboardShortcuts(
  appManager: {
    toggleApp: (id: string) => void;
  },
  geminiLive: {
    isListening: boolean;
    isConnected: boolean;
    startListening: () => void;
    stopListening: () => void;
    resetIdleActivity: () => void;
  },
) {
  const voiceMode = useAppStore((s) => s.voiceMode);
  const setShowQuickActions = useAppStore((s) => s.setShowQuickActions);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl+K toggles quick actions palette
      if (e.ctrlKey && e.code === 'KeyK') {
        e.preventDefault();
        setShowQuickActions((s) => !s);
        return;
      }

      // Ctrl+Shift+D toggles command center dashboard
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyD') {
        e.preventDefault();
        appManager.toggleApp('dashboard');
        return;
      }

      // Ctrl+Shift+M toggles memory explorer
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyM') {
        e.preventDefault();
        appManager.toggleApp('memory');
        return;
      }

      // Ctrl+Shift+A toggles agent dashboard
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyA') {
        e.preventDefault();
        appManager.toggleApp('agents');
        return;
      }

      // Ctrl+Shift+P toggles superpowers panel
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyP') {
        e.preventDefault();
        appManager.toggleApp('superpowers');
        return;
      }

      // Ctrl+Shift+C toggles calendar
      if (e.ctrlKey && e.shiftKey && e.code === 'KeyC') {
        e.preventDefault();
        appManager.toggleApp('calendar');
        return;
      }

      // Space toggles mic — only in voice mode, only from body (not while typing)
      if (voiceMode && e.code === 'Space' && e.target === document.body) {
        e.preventDefault();
        geminiLive.resetIdleActivity();
        if (geminiLive.isListening) {
          geminiLive.stopListening();
        } else if (geminiLive.isConnected) {
          geminiLive.startListening();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [voiceMode, geminiLive.isListening, geminiLive.isConnected, geminiLive.startListening, geminiLive.stopListening, geminiLive.resetIdleActivity, appManager, setShowQuickActions]);
}

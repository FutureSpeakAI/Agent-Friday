/**
 * AppShell.tsx — Reusable overlay wrapper for all Agent Friday apps
 *
 * Provides the standard glass-panel overlay with:
 *   - Dark backdrop with blur
 *   - Glass panel with header (icon + title + close button)
 *   - Scrollable content area
 *   - Escape key to close
 *   - Click-outside to close
 */

import { useEffect, useRef, useCallback } from 'react';

export interface AppShellProps {
  visible: boolean;
  onClose: () => void;
  title: string;
  icon: string;
  width?: number;
  maxHeightVh?: number;
  children: React.ReactNode;
}

export default function AppShell({
  visible,
  onClose,
  title,
  icon,
  width = 800,
  maxHeightVh = 85,
  children,
}: AppShellProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  // Escape key handler
  useEffect(() => {
    if (!visible) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [visible, onClose]);

  // Auto-focus panel on mount
  useEffect(() => {
    if (visible && panelRef.current) {
      panelRef.current.focus();
    }
  }, [visible]);

  // Click-outside handler
  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!visible) return null;

  return (
    <div style={s.backdrop} onClick={handleBackdropClick}>
      <div
        ref={panelRef}
        tabIndex={-1}
        style={{
          ...s.panel,
          width,
          maxWidth: '95vw',
          maxHeight: `${maxHeightVh}vh`,
        }}
      >
        {/* Header */}
        <div style={s.header}>
          <div style={s.headerLeft}>
            <span style={s.headerIcon}>{icon}</span>
            <span style={s.headerTitle}>{title}</span>
          </div>
          <button style={s.closeBtn} onClick={onClose} title="Close">
            ✕
          </button>
        </div>

        {/* Scrollable content */}
        <div style={s.content}>{children}</div>
      </div>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0, 0, 0, 0.7)',
    backdropFilter: 'blur(12px)',
    WebkitBackdropFilter: 'blur(12px)',
    zIndex: 100,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    animation: 'fadeIn 0.2s ease',
  },
  panel: {
    background: 'rgba(12, 12, 20, 0.98)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 20,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    boxShadow:
      '0 20px 60px rgba(0, 0, 0, 0.6), 0 0 40px rgba(0, 240, 255, 0.04)',
    outline: 'none',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '16px 20px 12px 20px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    flexShrink: 0,
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  headerIcon: {
    fontSize: 20,
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: '#ffffff',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  closeBtn: {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#888',
    fontSize: 13,
    width: 32,
    height: 32,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.15s',
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    overflowX: 'hidden',
    padding: '16px 20px 20px 20px',
  },
};

import React, { useState, useEffect, useCallback } from 'react';

interface FileModification {
  id: string;
  path: string;
  action: string;
  size: number;
  timestamp: number;
}

const MAX_TOASTS = 5;
const TOAST_LIFETIME = 8000; // ms before auto-dismiss

function formatPath(fullPath: string): { dir: string; file: string } {
  const parts = fullPath.replace(/\\/g, '/').split('/');
  const file = parts.pop() || fullPath;
  const dir = parts.length > 2
    ? '…/' + parts.slice(-2).join('/')
    : parts.join('/');
  return { dir, file };
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileToast() {
  const [toasts, setToasts] = useState<FileModification[]>([]);

  useEffect(() => {
    if (!window.eve?.onFileModified) return;

    const cleanup = window.eve.onFileModified((data) => {
      const mod: FileModification = {
        id: `${data.timestamp}-${Math.random().toString(36).slice(2, 6)}`,
        ...data,
      };
      setToasts((prev) => [...prev.slice(-(MAX_TOASTS - 1)), mod]);
    });

    return cleanup;
  }, []);

  // Auto-dismiss toasts
  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setTimeout(() => {
      setToasts((prev) => prev.slice(1));
    }, TOAST_LIFETIME);
    return () => clearTimeout(timer);
  }, [toasts]);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const handleClick = useCallback((filePath: string) => {
    try {
      window.eve.shell.showInFolder(filePath);
    } catch {
      // fallback — ignore if fails
    }
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div style={styles.container}>
      {toasts.map((toast) => {
        const { dir, file } = formatPath(toast.path);
        return (
          <div
            key={toast.id}
            style={styles.toast}
            className="hover-lift"
          >
            <div style={styles.iconCol}>
              <span style={styles.icon}>📄</span>
            </div>
            <div style={styles.body}>
              <div style={styles.actionLabel}>
                File {toast.action === 'write' ? 'Modified' : 'Changed'}
              </div>
              <button
                onClick={() => handleClick(toast.path)}
                style={styles.pathBtn}
                title={`Open in Explorer: ${toast.path}`}
              >
                <span style={styles.fileName}>{file}</span>
                <span style={styles.dirPath}>{dir}</span>
              </button>
              <div style={styles.meta}>
                <span style={styles.metaSize}>{formatSize(toast.size)}</span>
                <span style={styles.metaDot}>·</span>
                <span style={styles.metaAction}>Click to open in Explorer</span>
              </div>
            </div>
            <button
              onClick={() => dismissToast(toast.id)}
              style={styles.dismissBtn}
              title="Dismiss"
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    top: 80,
    right: 20,
    zIndex: 45,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    maxWidth: 320,
    pointerEvents: 'auto',
  },
  toast: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 14px',
    background: 'rgba(10, 10, 18, 0.92)',
    backdropFilter: 'blur(16px)',
    border: '1px solid rgba(168, 85, 247, 0.2)',
    borderRadius: 10,
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)',
    animation: 'slideInRight 0.3s ease-out',
  },
  iconCol: {
    width: 28,
    height: 28,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  icon: {
    fontSize: 16,
  },
  body: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    minWidth: 0,
  },
  actionLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: '#a855f7',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
  },
  pathBtn: {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6,
    padding: '6px 10px',
    cursor: 'pointer',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    textAlign: 'left' as const,
    transition: 'background 0.15s, border-color 0.15s',
  },
  fileName: {
    fontSize: 13,
    fontWeight: 600,
    color: '#00f0ff',
    fontFamily: "'JetBrains Mono', monospace",
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  dirPath: {
    fontSize: 10,
    color: '#666680',
    fontFamily: "'JetBrains Mono', monospace",
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  meta: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 10,
    color: '#555568',
  },
  metaSize: {
    fontFamily: "'JetBrains Mono', monospace",
  },
  metaDot: {
    color: '#333345',
  },
  metaAction: {
    color: '#555568',
    fontStyle: 'italic',
  },
  dismissBtn: {
    background: 'none',
    border: 'none',
    color: '#555568',
    fontSize: 16,
    cursor: 'pointer',
    padding: '0 2px',
    lineHeight: 1,
    flexShrink: 0,
    transition: 'color 0.15s',
  },
};

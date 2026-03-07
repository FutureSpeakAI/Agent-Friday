/**
 * FridayFiles.tsx — File browser app for Agent Friday
 *
 * IPC: window.eve.files?.listDirectory(path), .open(path), .showInFolder(path)
 * Graceful fallback if backend not available.
 */

import React, { useState, useEffect, useCallback } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

interface FilesProps {
  visible: boolean;
  onClose: () => void;
}

interface FileEntry {
  name: string;
  isDirectory: boolean;
  size: number;
  modifiedAt: string;
}

const QUICK_ACCESS = [
  { label: 'Desktop', icon: '🖥️', path: '~/Desktop' },
  { label: 'Documents', icon: '📄', path: '~/Documents' },
  { label: 'Downloads', icon: '⬇️', path: '~/Downloads' },
  { label: 'Home', icon: '🏠', path: '~' },
];

const FILE_ICONS: Record<string, string> = {
  folder: '📁',
  image: '🖼️',
  video: '🎬',
  audio: '🎵',
  code: '💻',
  document: '📄',
  archive: '📦',
  default: '📎',
};

function getFileIcon(name: string, isDir: boolean): string {
  if (isDir) return FILE_ICONS.folder;
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp'].includes(ext)) return FILE_ICONS.image;
  if (['mp4', 'avi', 'mov', 'mkv', 'webm'].includes(ext)) return FILE_ICONS.video;
  if (['mp3', 'wav', 'ogg', 'flac', 'aac'].includes(ext)) return FILE_ICONS.audio;
  if (['ts', 'tsx', 'js', 'jsx', 'py', 'rs', 'go', 'java', 'css', 'html', 'json'].includes(ext)) return FILE_ICONS.code;
  if (['pdf', 'doc', 'docx', 'txt', 'md', 'rtf', 'xls', 'xlsx'].includes(ext)) return FILE_ICONS.document;
  if (['zip', 'tar', 'gz', 'rar', '7z'].includes(ext)) return FILE_ICONS.archive;
  return FILE_ICONS.default;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatDate(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return dateStr;
  }
}

export default function FridayFiles({ visible, onClose }: FilesProps) {
  const [currentPath, setCurrentPath] = useState('~');
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendAvailable, setBackendAvailable] = useState(true);

  const navigate = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await (window as any).eve?.files?.listDirectory(path);
      if (!result) {
        setBackendAvailable(false);
        setLoading(false);
        return;
      }
      const sorted = [...result].sort((a: FileEntry, b: FileEntry) => {
        if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      setEntries(sorted);
      setCurrentPath(path);
      setBackendAvailable(true);
    } catch {
      setBackendAvailable(false);
      setError('File system backend not available');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (visible) navigate(currentPath);
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleOpen = useCallback(async (entry: FileEntry) => {
    if (entry.isDirectory) {
      const sep = currentPath.includes('\\') ? '\\' : '/';
      const newPath = currentPath === '~'
        ? `~${sep}${entry.name}`
        : `${currentPath}${sep}${entry.name}`;
      navigate(newPath);
    } else {
      try {
        const fullPath = currentPath === '~'
          ? `~/${entry.name}`
          : `${currentPath}/${entry.name}`;
        await (window as any).eve?.files?.open(fullPath);
      } catch {
        // Silently fail
      }
    }
  }, [currentPath, navigate]);

  const handleShowInFolder = useCallback(async (entry: FileEntry) => {
    try {
      const fullPath = `${currentPath}/${entry.name}`;
      await (window as any).eve?.files?.showInFolder(fullPath);
    } catch {
      // Silently fail
    }
  }, [currentPath]);

  const goUp = useCallback(() => {
    const parts = currentPath.replace(/\\/g, '/').split('/');
    if (parts.length > 1) {
      parts.pop();
      navigate(parts.join('/') || '~');
    }
  }, [currentPath, navigate]);

  const breadcrumbs = currentPath.replace(/\\/g, '/').split('/').filter(Boolean);

  return (
    <AppShell visible={visible} onClose={onClose} title="Files" icon="📁" width={820}>
      <ContextBar appId="friday-files" />
      {!backendAvailable ? (
        <div style={s.placeholder}>
          <div style={s.placeholderIcon}>📁</div>
          <div style={s.placeholderTitle}>Backend Coming Soon</div>
          <div style={s.placeholderMsg}>
            File browsing requires the Electron IPC backend (window.eve.files).
            This feature will be available once the backend is connected.
          </div>
          {/* Quick access still shown */}
          <div style={s.quickRow}>
            {QUICK_ACCESS.map((qa) => (
              <button key={qa.path} style={s.quickBtn} onClick={() => navigate(qa.path)}>
                <span>{qa.icon}</span>
                <span style={s.quickLabel}>{qa.label}</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <>
          {/* Quick Access */}
          <div style={s.quickRow}>
            {QUICK_ACCESS.map((qa) => (
              <button key={qa.path} style={s.quickBtn} onClick={() => navigate(qa.path)}>
                <span>{qa.icon}</span>
                <span style={s.quickLabel}>{qa.label}</span>
              </button>
            ))}
          </div>

          {/* Breadcrumbs */}
          <div style={s.breadcrumbs}>
            <button style={s.breadBtn} onClick={goUp} title="Go up">⬆️</button>
            {breadcrumbs.map((seg, i) => (
              <React.Fragment key={i}>
                <span style={s.breadSep}>/</span>
                <button
                  style={s.breadBtn}
                  onClick={() => navigate(breadcrumbs.slice(0, i + 1).join('/'))}
                >
                  {seg}
                </button>
              </React.Fragment>
            ))}
          </div>

          {/* File List */}
          {loading ? (
            <div style={s.center}>
              <span style={s.loadingText}>Loading...</span>
            </div>
          ) : error ? (
            <div style={s.center}>
              <span style={{ color: '#ef4444', fontSize: 13 }}>{error}</span>
            </div>
          ) : (
            <div style={s.fileList}>
              {entries.length === 0 && (
                <div style={s.emptyDir}>This directory is empty</div>
              )}
              {entries.map((entry) => (
                <div
                  key={entry.name}
                  style={s.fileRow}
                  onClick={() => handleOpen(entry)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    handleShowInFolder(entry);
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.06)';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.background = 'transparent';
                  }}
                >
                  <span style={s.fileIcon}>{getFileIcon(entry.name, entry.isDirectory)}</span>
                  <span style={s.fileName}>{entry.name}</span>
                  <span style={s.fileSize}>
                    {entry.isDirectory ? '--' : formatSize(entry.size)}
                  </span>
                  <span style={s.fileDate}>{formatDate(entry.modifiedAt)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  placeholder: {
    textAlign: 'center',
    padding: 32,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 12,
  },
  placeholderIcon: { fontSize: 48 },
  placeholderTitle: { color: '#F8FAFC', fontSize: 18, fontWeight: 700 },
  placeholderMsg: { color: '#8888a0', fontSize: 13, lineHeight: 1.6, maxWidth: 400 },
  quickRow: {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap',
  },
  quickBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '8px 14px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    color: '#F8FAFC',
    fontSize: 13,
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  quickLabel: { fontSize: 12 },
  breadcrumbs: {
    display: 'flex',
    alignItems: 'center',
    gap: 2,
    padding: '6px 12px',
    background: 'rgba(0,0,0,0.3)',
    borderRadius: 8,
    overflowX: 'auto',
  },
  breadBtn: {
    background: 'none',
    border: 'none',
    color: '#00f0ff',
    fontSize: 12,
    cursor: 'pointer',
    padding: '2px 4px',
    borderRadius: 4,
    fontFamily: "'JetBrains Mono', monospace",
  },
  breadSep: { color: '#4a4a62', fontSize: 12 },
  center: {
    display: 'flex',
    justifyContent: 'center',
    padding: 32,
  },
  loadingText: { color: '#8888a0', fontSize: 13 },
  fileList: {
    display: 'flex',
    flexDirection: 'column',
    borderRadius: 10,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)',
    maxHeight: 380,
    overflowY: 'auto',
  },
  emptyDir: {
    color: '#4a4a62',
    fontSize: 13,
    fontStyle: 'italic',
    textAlign: 'center',
    padding: 24,
  },
  fileRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 14px',
    cursor: 'pointer',
    transition: 'background 0.12s',
    borderBottom: '1px solid rgba(255,255,255,0.03)',
  },
  fileIcon: { fontSize: 16, flexShrink: 0 },
  fileName: {
    flex: 1,
    color: '#F8FAFC',
    fontSize: 13,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  fileSize: {
    color: '#8888a0',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    minWidth: 70,
    textAlign: 'right',
  },
  fileDate: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    minWidth: 90,
    textAlign: 'right',
  },
};

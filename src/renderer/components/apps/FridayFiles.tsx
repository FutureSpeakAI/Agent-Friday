/**
 * FridayFiles.tsx — Full-featured file manager for Agent Friday.
 *
 * Features:
 *   - Browse directories with breadcrumb navigation
 *   - Rename, delete (trash), copy, move files and folders
 *   - Create new files and folders
 *   - File preview (text files)
 *   - Search via eve.fileSearch
 *   - Right-click context menu
 *   - Quick access sidebar
 *   - Show/hide hidden files
 *   - Copy path to clipboard
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
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
  createdAt?: string;
  extension?: string;
}

interface ContextMenuState {
  visible: boolean;
  x: number;
  y: number;
  entry: FileEntry | null;
}

const eve = () => (window as any).eve;

const QUICK_ACCESS = [
  { label: 'Desktop', icon: '🖥️', path: '~/Desktop' },
  { label: 'Documents', icon: '📄', path: '~/Documents' },
  { label: 'Downloads', icon: '⬇️', path: '~/Downloads' },
  { label: 'Pictures', icon: '🖼️', path: '~/Pictures' },
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
  executable: '⚙️',
  default: '📎',
};

function getFileIcon(name: string, isDir: boolean): string {
  if (isDir) return FILE_ICONS.folder;
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'ico'].includes(ext)) return FILE_ICONS.image;
  if (['mp4', 'avi', 'mov', 'mkv', 'webm'].includes(ext)) return FILE_ICONS.video;
  if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a'].includes(ext)) return FILE_ICONS.audio;
  if (['ts', 'tsx', 'js', 'jsx', 'py', 'rs', 'go', 'java', 'css', 'html', 'json', 'yaml', 'yml', 'toml', 'sh', 'bat', 'ps1', 'c', 'cpp', 'h', 'rb', 'php'].includes(ext)) return FILE_ICONS.code;
  if (['pdf', 'doc', 'docx', 'txt', 'md', 'rtf', 'xls', 'xlsx', 'pptx', 'csv'].includes(ext)) return FILE_ICONS.document;
  if (['zip', 'tar', 'gz', 'rar', '7z', 'bz2', 'xz'].includes(ext)) return FILE_ICONS.archive;
  if (['exe', 'msi', 'app', 'dmg', 'deb', 'rpm'].includes(ext)) return FILE_ICONS.executable;
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

function buildFullPath(currentPath: string, name: string): string {
  const sep = currentPath.includes('\\') ? '\\' : '/';
  return currentPath === '~' ? `~/${name}` : `${currentPath}${sep}${name}`;
}

const isTextFile = (name: string): boolean => {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return ['txt', 'md', 'json', 'js', 'ts', 'tsx', 'jsx', 'py', 'rs', 'go', 'java', 'css', 'html',
    'xml', 'yaml', 'yml', 'toml', 'sh', 'bat', 'ps1', 'c', 'cpp', 'h', 'rb', 'php', 'sql',
    'csv', 'log', 'cfg', 'ini', 'conf', 'env', 'gitignore', 'editorconfig', 'prettierrc'].includes(ext);
};

export default function FridayFiles({ visible, onClose }: FilesProps) {
  const [currentPath, setCurrentPath] = useState('~');
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendAvailable, setBackendAvailable] = useState(true);
  const [showHidden, setShowHidden] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState>({ visible: false, x: 0, y: 0, entry: null });
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [creating, setCreating] = useState<'file' | 'folder' | null>(null);
  const [createValue, setCreateValue] = useState('');
  const [preview, setPreview] = useState<{ name: string; content: string } | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<FileEntry[] | null>(null);
  const [clipboard, setClipboard] = useState<{ path: string; mode: 'copy' | 'cut' } | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const renameInputRef = useRef<HTMLInputElement>(null);
  const createInputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // Show a brief status message
  const flash = useCallback((msg: string) => {
    setStatusMessage(msg);
    setTimeout(() => setStatusMessage(null), 2500);
  }, []);

  // ── Navigate ────────────────────────────────────────────────
  const navigate = useCallback(async (path: string) => {
    setLoading(true);
    setError(null);
    setSearchResults(null);
    setSearchQuery('');
    setPreview(null);
    setSelected(null);
    setRenaming(null);
    setCreating(null);
    try {
      const result = await eve()?.files?.listDirectory(path, showHidden);
      if (!result) {
        setBackendAvailable(false);
        setLoading(false);
        return;
      }
      setEntries(result);
      setCurrentPath(path);
      setBackendAvailable(true);
    } catch {
      setBackendAvailable(false);
      setError('File system backend not available');
    }
    setLoading(false);
  }, [showHidden]);

  // Refresh current directory
  const refresh = useCallback(() => navigate(currentPath), [navigate, currentPath]);

  useEffect(() => {
    if (visible) navigate(currentPath);
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps — refresh on visibility toggle only

  // Re-list when showHidden changes
  useEffect(() => {
    if (visible && backendAvailable) refresh();
  }, [showHidden]); // eslint-disable-line react-hooks/exhaustive-deps — re-list when hidden-file toggle changes

  // Close context menu on click anywhere
  useEffect(() => {
    const handler = () => setContextMenu(prev => ({ ...prev, visible: false }));
    document.addEventListener('click', handler);
    return () => document.removeEventListener('click', handler);
  }, []);

  // ── Actions ─────────────────────────────────────────────────
  const handleOpen = useCallback(async (entry: FileEntry) => {
    if (entry.isDirectory) {
      navigate(buildFullPath(currentPath, entry.name));
    } else {
      try {
        await eve()?.files?.open(buildFullPath(currentPath, entry.name));
      } catch { /* silently fail */ }
    }
  }, [currentPath, navigate]);

  const handleShowInFolder = useCallback(async (entry: FileEntry) => {
    try {
      await eve()?.files?.showInFolder(buildFullPath(currentPath, entry.name));
    } catch { /* silently fail */ }
  }, [currentPath]);

  const handleDelete = useCallback(async (entry: FileEntry) => {
    const fullPath = buildFullPath(currentPath, entry.name);
    try {
      await eve()?.files?.delete(fullPath, true);
      flash(`Moved "${entry.name}" to trash`);
      refresh();
    } catch (e: any) {
      flash(`Delete failed: ${e?.message || 'unknown error'}`);
    }
  }, [currentPath, refresh, flash]);

  const handleRenameStart = useCallback((entry: FileEntry) => {
    setRenaming(entry.name);
    setRenameValue(entry.name);
    setTimeout(() => renameInputRef.current?.select(), 50);
  }, []);

  const handleRenameSubmit = useCallback(async () => {
    if (!renaming || !renameValue.trim() || renameValue === renaming) {
      setRenaming(null);
      return;
    }
    const fullPath = buildFullPath(currentPath, renaming);
    try {
      await eve()?.files?.rename(fullPath, renameValue.trim());
      flash(`Renamed to "${renameValue.trim()}"`);
      refresh();
    } catch (e: any) {
      flash(`Rename failed: ${e?.message || 'unknown error'}`);
    }
    setRenaming(null);
  }, [renaming, renameValue, currentPath, refresh, flash]);

  const handleCreateSubmit = useCallback(async () => {
    if (!creating || !createValue.trim()) {
      setCreating(null);
      return;
    }
    try {
      if (creating === 'folder') {
        await eve()?.files?.createFolder(currentPath, createValue.trim());
        flash(`Created folder "${createValue.trim()}"`);
      } else {
        await eve()?.files?.createFile(currentPath, createValue.trim());
        flash(`Created file "${createValue.trim()}"`);
      }
      refresh();
    } catch (e: any) {
      flash(`Create failed: ${e?.message || 'unknown error'}`);
    }
    setCreating(null);
    setCreateValue('');
  }, [creating, createValue, currentPath, refresh, flash]);

  const handleCopyPath = useCallback((entry: FileEntry) => {
    const fullPath = buildFullPath(currentPath, entry.name);
    eve()?.files?.copyPath(fullPath);
    flash('Path copied to clipboard');
  }, [currentPath, flash]);

  const handleCut = useCallback((entry: FileEntry) => {
    setClipboard({ path: buildFullPath(currentPath, entry.name), mode: 'cut' });
    flash(`"${entry.name}" cut to clipboard`);
  }, [currentPath, flash]);

  const handleCopy = useCallback((entry: FileEntry) => {
    setClipboard({ path: buildFullPath(currentPath, entry.name), mode: 'copy' });
    flash(`"${entry.name}" copied to clipboard`);
  }, [currentPath, flash]);

  const handlePaste = useCallback(async () => {
    if (!clipboard) return;
    try {
      if (clipboard.mode === 'copy') {
        await eve()?.files?.copy(clipboard.path, currentPath);
        flash('Pasted (copied)');
      } else {
        await eve()?.files?.move(clipboard.path, currentPath);
        flash('Pasted (moved)');
        setClipboard(null);
      }
      refresh();
    } catch (e: any) {
      flash(`Paste failed: ${e?.message || 'unknown error'}`);
    }
  }, [clipboard, currentPath, refresh, flash]);

  const handlePreview = useCallback(async (entry: FileEntry) => {
    if (entry.isDirectory || !isTextFile(entry.name)) return;
    try {
      const content = await eve()?.files?.readText(buildFullPath(currentPath, entry.name));
      setPreview({ name: entry.name, content: content || '' });
    } catch {
      setPreview({ name: entry.name, content: '[Could not read file]' });
    }
  }, [currentPath]);

  // ── Search ──────────────────────────────────────────────────
  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) {
      setSearchResults(null);
      return;
    }
    setLoading(true);
    try {
      const results = await eve()?.fileSearch?.search({
        query: searchQuery.trim(),
        searchPath: currentPath,
        mode: 'filename',
        maxResults: 50,
      });
      if (results && Array.isArray(results)) {
        setSearchResults(results.map((r: any) => ({
          name: r.name || r.path?.split(/[/\\]/).pop() || 'unknown',
          isDirectory: r.isDirectory || false,
          size: r.size || 0,
          modifiedAt: r.modifiedAt || new Date().toISOString(),
        })));
      } else {
        setSearchResults([]);
      }
    } catch {
      setSearchResults([]);
    }
    setLoading(false);
  }, [searchQuery, currentPath]);

  const goUp = useCallback(() => {
    const parts = currentPath.replace(/\\/g, '/').split('/');
    if (parts.length > 1) {
      parts.pop();
      navigate(parts.join('/') || '~');
    }
  }, [currentPath, navigate]);

  // ── Context Menu ────────────────────────────────────────────
  const handleContextMenu = useCallback((e: React.MouseEvent, entry: FileEntry) => {
    e.preventDefault();
    e.stopPropagation();
    setSelected(entry.name);
    // Position relative to container
    const rect = containerRef.current?.getBoundingClientRect();
    setContextMenu({
      visible: true,
      x: e.clientX - (rect?.left || 0),
      y: e.clientY - (rect?.top || 0),
      entry,
    });
  }, []);

  const handleBgContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const rect = containerRef.current?.getBoundingClientRect();
    setContextMenu({
      visible: true,
      x: e.clientX - (rect?.left || 0),
      y: e.clientY - (rect?.top || 0),
      entry: null,
    });
  }, []);

  const breadcrumbs = currentPath.replace(/\\/g, '/').split('/').filter(Boolean);
  const displayEntries = searchResults || entries;

  return (
    <AppShell visible={visible} onClose={onClose} title="Files" icon="📁" width={880}>
      <ContextBar appId="friday-files" />

      {!backendAvailable ? (
        <div style={s.placeholder}>
          <div style={s.placeholderIcon}>📁</div>
          <div style={s.placeholderTitle}>File System Unavailable</div>
          <div style={s.placeholderMsg}>
            File browsing requires the Electron IPC backend.
          </div>
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
        <div ref={containerRef} style={{ position: 'relative', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {/* Toolbar */}
          <div style={s.toolbar}>
            <div style={s.toolbarLeft}>
              {QUICK_ACCESS.map((qa) => (
                <button key={qa.path} style={s.quickBtn} onClick={() => navigate(qa.path)} title={qa.label}>
                  <span>{qa.icon}</span>
                  <span style={s.quickLabel}>{qa.label}</span>
                </button>
              ))}
            </div>
            <div style={s.toolbarRight}>
              <button style={s.toolBtn} onClick={() => { setCreating('folder'); setCreateValue(''); setTimeout(() => createInputRef.current?.focus(), 50); }} title="New folder">
                📁+
              </button>
              <button style={s.toolBtn} onClick={() => { setCreating('file'); setCreateValue(''); setTimeout(() => createInputRef.current?.focus(), 50); }} title="New file">
                📄+
              </button>
              {clipboard && (
                <button style={{ ...s.toolBtn, color: '#00f0ff' }} onClick={handlePaste} title="Paste">
                  📋
                </button>
              )}
              <button style={s.toolBtn} onClick={() => setShowHidden(h => !h)} title={showHidden ? 'Hide hidden files' : 'Show hidden files'}>
                {showHidden ? '👁️' : '👁️‍🗨️'}
              </button>
              <button style={s.toolBtn} onClick={refresh} title="Refresh">🔄</button>
            </div>
          </div>

          {/* Search bar */}
          <div style={s.searchRow}>
            <input
              style={s.searchInput}
              placeholder="Search files..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); if (e.key === 'Escape') { setSearchQuery(''); setSearchResults(null); } }}
            />
            {searchQuery && (
              <button style={s.searchClear} onClick={() => { setSearchQuery(''); setSearchResults(null); }}>✕</button>
            )}
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

          {/* Status message */}
          {statusMessage && (
            <div style={s.statusBar}>{statusMessage}</div>
          )}

          {/* Create input row */}
          {creating && (
            <div style={s.createRow}>
              <span>{creating === 'folder' ? '📁' : '📄'}</span>
              <input
                ref={createInputRef}
                style={s.inlineInput}
                value={createValue}
                placeholder={creating === 'folder' ? 'Folder name...' : 'File name...'}
                onChange={(e) => setCreateValue(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleCreateSubmit(); if (e.key === 'Escape') setCreating(null); }}
                onBlur={handleCreateSubmit}
              />
            </div>
          )}

          {/* File List */}
          {loading ? (
            <div style={s.center}><span style={s.loadingText}>Loading...</span></div>
          ) : error ? (
            <div style={s.center}><span style={{ color: '#ef4444', fontSize: 13 }}>{error}</span></div>
          ) : (
            <div style={s.fileList} onContextMenu={handleBgContextMenu}>
              {searchResults !== null && (
                <div style={s.searchHeader}>
                  {searchResults.length} result{searchResults.length !== 1 ? 's' : ''} for "{searchQuery}"
                </div>
              )}
              {displayEntries.length === 0 && (
                <div style={s.emptyDir}>
                  {searchResults !== null ? 'No files found' : 'This directory is empty'}
                </div>
              )}
              {displayEntries.map((entry) => (
                <div
                  key={entry.name}
                  style={{
                    ...s.fileRow,
                    background: selected === entry.name ? 'rgba(0, 240, 255, 0.08)' : 'transparent',
                  }}
                  onClick={() => { setSelected(entry.name); setPreview(null); }}
                  onDoubleClick={() => handleOpen(entry)}
                  onContextMenu={(e) => handleContextMenu(e, entry)}
                  onMouseEnter={(e) => {
                    if (selected !== entry.name) (e.currentTarget as HTMLElement).style.background = 'rgba(255,255,255,0.04)';
                  }}
                  onMouseLeave={(e) => {
                    if (selected !== entry.name) (e.currentTarget as HTMLElement).style.background = 'transparent';
                  }}
                >
                  <span style={s.fileIcon}>{getFileIcon(entry.name, entry.isDirectory)}</span>
                  {renaming === entry.name ? (
                    <input
                      ref={renameInputRef}
                      style={s.inlineInput}
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleRenameSubmit(); if (e.key === 'Escape') setRenaming(null); }}
                      onBlur={handleRenameSubmit}
                      onClick={(e) => e.stopPropagation()}
                    />
                  ) : (
                    <span style={s.fileName}>{entry.name}</span>
                  )}
                  <span style={s.fileSize}>
                    {entry.isDirectory ? '--' : formatSize(entry.size)}
                  </span>
                  <span style={s.fileDate}>{formatDate(entry.modifiedAt)}</span>
                </div>
              ))}
            </div>
          )}

          {/* Preview panel */}
          {preview && (
            <div style={s.previewPanel}>
              <div style={s.previewHeader}>
                <span style={s.previewTitle}>{preview.name}</span>
                <button style={s.previewClose} onClick={() => setPreview(null)}>✕</button>
              </div>
              <pre style={s.previewContent}>{preview.content}</pre>
            </div>
          )}

          {/* Footer */}
          <div style={s.footer}>
            <span>{displayEntries.length} item{displayEntries.length !== 1 ? 's' : ''}</span>
            {selected && <span style={s.footerSelected}>Selected: {selected}</span>}
          </div>

          {/* Context Menu */}
          {contextMenu.visible && (
            <div style={{ ...s.contextMenu, top: contextMenu.y, left: contextMenu.x }}>
              {contextMenu.entry ? (
                <>
                  <button style={s.ctxItem} onClick={() => { handleOpen(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    {contextMenu.entry.isDirectory ? '📂 Open' : '▶️ Open'}
                  </button>
                  {!contextMenu.entry.isDirectory && isTextFile(contextMenu.entry.name) && (
                    <button style={s.ctxItem} onClick={() => { handlePreview(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                      👁️ Preview
                    </button>
                  )}
                  <button style={s.ctxItem} onClick={() => { handleShowInFolder(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    📍 Show in Explorer
                  </button>
                  <div style={s.ctxSep} />
                  <button style={s.ctxItem} onClick={() => { handleRenameStart(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    ✏️ Rename
                  </button>
                  <button style={s.ctxItem} onClick={() => { handleCopy(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    📋 Copy
                  </button>
                  <button style={s.ctxItem} onClick={() => { handleCut(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    ✂️ Cut
                  </button>
                  <button style={s.ctxItem} onClick={() => { handleCopyPath(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    🔗 Copy Path
                  </button>
                  <div style={s.ctxSep} />
                  <button style={{ ...s.ctxItem, color: '#ef4444' }} onClick={() => { handleDelete(contextMenu.entry!); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    🗑️ Move to Trash
                  </button>
                </>
              ) : (
                <>
                  {clipboard && (
                    <button style={s.ctxItem} onClick={() => { handlePaste(); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                      📋 Paste
                    </button>
                  )}
                  <button style={s.ctxItem} onClick={() => { setCreating('folder'); setCreateValue(''); setContextMenu(cm => ({ ...cm, visible: false })); setTimeout(() => createInputRef.current?.focus(), 50); }}>
                    📁 New Folder
                  </button>
                  <button style={s.ctxItem} onClick={() => { setCreating('file'); setCreateValue(''); setContextMenu(cm => ({ ...cm, visible: false })); setTimeout(() => createInputRef.current?.focus(), 50); }}>
                    📄 New File
                  </button>
                  <div style={s.ctxSep} />
                  <button style={s.ctxItem} onClick={() => { refresh(); setContextMenu(cm => ({ ...cm, visible: false })); }}>
                    🔄 Refresh
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  placeholder: {
    textAlign: 'center', padding: 32,
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12,
  },
  placeholderIcon: { fontSize: 48 },
  placeholderTitle: { color: '#F8FAFC', fontSize: 18, fontWeight: 700 },
  placeholderMsg: { color: '#8888a0', fontSize: 13, lineHeight: 1.6, maxWidth: 400 },

  toolbar: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    gap: 8, flexWrap: 'wrap',
  },
  toolbarLeft: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  toolbarRight: { display: 'flex', gap: 4, alignItems: 'center' },
  toolBtn: {
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6, color: '#ccc', fontSize: 14, padding: '4px 8px',
    cursor: 'pointer', transition: 'background 0.15s',
  },

  quickRow: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  quickBtn: {
    display: 'flex', alignItems: 'center', gap: 5,
    padding: '6px 12px', background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)', borderRadius: 8,
    color: '#F8FAFC', fontSize: 12, cursor: 'pointer', transition: 'background 0.15s',
  },
  quickLabel: { fontSize: 11 },

  searchRow: { display: 'flex', alignItems: 'center', position: 'relative' as const },
  searchInput: {
    flex: 1, padding: '7px 30px 7px 12px',
    background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 8, color: '#F8FAFC', fontSize: 13, outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
  },
  searchClear: {
    position: 'absolute' as const, right: 8, top: '50%', transform: 'translateY(-50%)',
    background: 'none', border: 'none', color: '#666', cursor: 'pointer', fontSize: 13,
  },

  breadcrumbs: {
    display: 'flex', alignItems: 'center', gap: 2,
    padding: '6px 12px', background: 'rgba(0,0,0,0.3)', borderRadius: 8, overflowX: 'auto',
  },
  breadBtn: {
    background: 'none', border: 'none', color: '#00f0ff', fontSize: 12,
    cursor: 'pointer', padding: '2px 4px', borderRadius: 4,
    fontFamily: "'JetBrains Mono', monospace",
  },
  breadSep: { color: '#4a4a62', fontSize: 12 },

  statusBar: {
    padding: '4px 12px', background: 'rgba(0, 240, 255, 0.06)',
    borderRadius: 6, color: '#00f0ff', fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
  },

  createRow: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '6px 14px', background: 'rgba(0,240,255,0.04)',
    border: '1px solid rgba(0,240,255,0.15)', borderRadius: 8,
  },

  inlineInput: {
    flex: 1, padding: '4px 8px',
    background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 4, color: '#F8FAFC', fontSize: 13, outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
  },

  center: { display: 'flex', justifyContent: 'center', padding: 32 },
  loadingText: { color: '#8888a0', fontSize: 13 },

  searchHeader: {
    padding: '6px 14px', color: '#8888a0', fontSize: 12,
    borderBottom: '1px solid rgba(255,255,255,0.05)',
    fontFamily: "'JetBrains Mono', monospace",
  },

  fileList: {
    display: 'flex', flexDirection: 'column', borderRadius: 10, overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)', maxHeight: 340, overflowY: 'auto',
  },
  emptyDir: {
    color: '#4a4a62', fontSize: 13, fontStyle: 'italic', textAlign: 'center', padding: 24,
  },
  fileRow: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '7px 14px', cursor: 'pointer', transition: 'background 0.12s',
    borderBottom: '1px solid rgba(255,255,255,0.03)', userSelect: 'none' as const,
  },
  fileIcon: { fontSize: 16, flexShrink: 0 },
  fileName: {
    flex: 1, color: '#F8FAFC', fontSize: 13,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  },
  fileSize: {
    color: '#8888a0', fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace", minWidth: 70, textAlign: 'right',
  },
  fileDate: {
    color: '#4a4a62', fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace", minWidth: 90, textAlign: 'right',
  },

  previewPanel: {
    border: '1px solid rgba(255,255,255,0.08)', borderRadius: 10,
    background: 'rgba(0,0,0,0.3)', maxHeight: 200, overflow: 'hidden',
    display: 'flex', flexDirection: 'column',
  },
  previewHeader: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '6px 12px', borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  previewTitle: { color: '#00f0ff', fontSize: 12, fontFamily: "'JetBrains Mono', monospace" },
  previewClose: {
    background: 'none', border: 'none', color: '#666', cursor: 'pointer', fontSize: 14,
  },
  previewContent: {
    padding: '8px 12px', color: '#ccc', fontSize: 11, overflow: 'auto', flex: 1, margin: 0,
    fontFamily: "'JetBrains Mono', monospace", whiteSpace: 'pre-wrap', wordBreak: 'break-all',
    maxHeight: 160,
  },

  footer: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '4px 8px', color: '#4a4a62', fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
  },
  footerSelected: { color: '#00f0ff' },

  contextMenu: {
    position: 'absolute' as const, zIndex: 1000,
    background: 'rgba(20, 20, 35, 0.97)', border: '1px solid rgba(255,255,255,0.12)',
    borderRadius: 10, padding: '4px 0', minWidth: 180,
    boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
    backdropFilter: 'blur(20px)',
  },
  ctxItem: {
    display: 'flex', alignItems: 'center', gap: 8, width: '100%',
    padding: '7px 14px', background: 'none', border: 'none',
    color: '#F8FAFC', fontSize: 13, cursor: 'pointer', textAlign: 'left' as const,
    transition: 'background 0.12s',
  },
  ctxSep: {
    height: 1, margin: '4px 8px', background: 'rgba(255,255,255,0.06)',
  },
};

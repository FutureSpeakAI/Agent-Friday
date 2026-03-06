/**
 * FridayDocs.tsx — Agent Friday Document Manager
 *
 * Browse, search, and ingest documents into the knowledge base.
 * Full document viewer with metadata display.
 *
 * IPC: window.eve.documents.*
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

interface DocEntry {
  id: string;
  title: string;
  type?: string;
  size?: number;
  createdAt?: string;
  updatedAt?: string;
  tags?: string[];
  excerpt?: string;
  source?: string;
}

interface DocDetail extends DocEntry {
  content: string;
  metadata?: Record<string, any>;
}

interface Props {
  visible: boolean;
  onClose: () => void;
}

export default function FridayDocs({ visible, onClose }: Props) {
  const [docs, setDocs] = useState<DocEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<DocDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadDocs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await (window as any).eve.documents.list();
      setDocs(Array.isArray(result) ? result : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load documents');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) {
      loadDocs();
      setSelected(null);
      setSearch('');
    }
  }, [visible, loadDocs]);

  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      loadDocs();
      return;
    }
    setSearching(true);
    setError(null);
    try {
      const result = await (window as any).eve.documents.search(query.trim());
      setDocs(Array.isArray(result) ? result : []);
    } catch (err: any) {
      setError(err?.message || 'Search failed');
    } finally {
      setSearching(false);
    }
  }, [loadDocs]);

  const onSearchChange = (value: string) => {
    setSearch(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => handleSearch(value), 400);
  };

  const handleSelectDoc = async (doc: DocEntry) => {
    setLoadingDetail(true);
    setError(null);
    try {
      const detail = await (window as any).eve.documents.get(doc.id);
      setSelected(detail || { ...doc, content: '' });
    } catch (err: any) {
      setError(err?.message || 'Failed to load document');
      setSelected({ ...doc, content: '(Failed to load content)' });
    } finally {
      setLoadingDetail(false);
    }
  };

  const handleIngest = async () => {
    setIngesting(true);
    setError(null);
    try {
      const docs = (window as any).eve.documents;
      // pickAndIngest combines native file dialog + ingestion in one call
      if (docs?.pickAndIngest) {
        const result = await docs.pickAndIngest();
        if (!result) {
          setIngesting(false);
          return;
        }
      } else {
        // Fallback: prompt for path + ingestFile
        const filePath = prompt('Enter file path to ingest:');
        if (!filePath) {
          setIngesting(false);
          return;
        }
        await docs.ingestFile(filePath);
      }
      await loadDocs();
    } catch (err: any) {
      setError(err?.message || 'Failed to ingest document');
    } finally {
      setIngesting(false);
    }
  };

  const formatSize = (bytes?: number): string => {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getTypeIcon = (type?: string): string => {
    switch (type?.toLowerCase()) {
      case 'pdf': return '📕';
      case 'markdown': case 'md': return '📝';
      case 'text': case 'txt': return '📃';
      case 'code': return '💻';
      case 'html': return '🌐';
      default: return '📄';
    }
  };

  return (
    <AppShell visible={visible} onClose={onClose} title="Documents" icon="📄" width={1000}>
      {loading && !searching ? (
        <div style={s.center}>
          <span style={s.spinner}>⟳</span>
          <span style={s.secondaryText}>Loading documents...</span>
        </div>
      ) : (
        <div style={s.layout}>
          {/* ── Left: Document List ── */}
          <div style={s.sidebar}>
            <div style={s.searchRow}>
              <input
                type="text"
                placeholder="Search documents..."
                value={search}
                onChange={(e) => onSearchChange(e.target.value)}
                style={s.searchInput}
              />
              {searching && <span style={s.searchSpinner}>⟳</span>}
            </div>

            <button
              style={s.ingestBtn}
              onClick={handleIngest}
              disabled={ingesting}
            >
              {ingesting ? 'Ingesting...' : '+ Ingest File'}
            </button>

            <div style={s.listScroll}>
              {docs.length === 0 ? (
                <div style={s.emptyState}>
                  {search ? 'No results found' : 'No documents in knowledge base'}
                </div>
              ) : (
                docs.map((doc) => (
                  <div
                    key={doc.id}
                    style={{
                      ...s.docItem,
                      ...(selected?.id === doc.id ? s.docItemActive : {}),
                    }}
                    onClick={() => handleSelectDoc(doc)}
                  >
                    <span style={s.docIcon}>{getTypeIcon(doc.type)}</span>
                    <div style={s.docInfo}>
                      <div style={s.docTitle}>{doc.title}</div>
                      <div style={s.docMeta}>
                        {doc.type && <span>{doc.type.toUpperCase()}</span>}
                        {doc.size && <span>{formatSize(doc.size)}</span>}
                        {doc.updatedAt && (
                          <span>{new Date(doc.updatedAt).toLocaleDateString()}</span>
                        )}
                      </div>
                      {doc.excerpt && (
                        <div style={s.docExcerpt}>
                          {doc.excerpt.slice(0, 80)}
                          {doc.excerpt.length > 80 ? '...' : ''}
                        </div>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
            <div style={s.docCount}>
              {docs.length} document{docs.length !== 1 ? 's' : ''}
            </div>
          </div>

          {/* ── Right: Detail View ── */}
          <div style={s.detail}>
            {error && (
              <div style={s.errorBar}>
                <span>{error}</span>
                <button style={s.dismissBtn} onClick={() => setError(null)}>
                  Dismiss
                </button>
              </div>
            )}

            {loadingDetail ? (
              <div style={s.center}>
                <span style={s.spinner}>⟳</span>
                <span style={s.secondaryText}>Loading document...</span>
              </div>
            ) : !selected ? (
              <div style={s.center}>
                <span style={s.mutedText}>
                  Select a document to view its contents
                </span>
              </div>
            ) : (
              <>
                {/* Document Header */}
                <div style={s.detailHeader}>
                  <span style={{ fontSize: 24 }}>{getTypeIcon(selected.type)}</span>
                  <div style={{ flex: 1 }}>
                    <div style={s.detailTitle}>{selected.title}</div>
                    <div style={s.detailMeta}>
                      {selected.type && (
                        <span style={s.metaPill}>{selected.type.toUpperCase()}</span>
                      )}
                      {selected.size && (
                        <span style={s.secondaryText}>{formatSize(selected.size)}</span>
                      )}
                      {selected.source && (
                        <span style={s.secondaryText}>Source: {selected.source}</span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Tags */}
                {selected.tags && selected.tags.length > 0 && (
                  <div style={s.tagsRow}>
                    {selected.tags.map((t) => (
                      <span key={t} style={s.tag}>{t}</span>
                    ))}
                  </div>
                )}

                {/* Metadata */}
                {selected.metadata && Object.keys(selected.metadata).length > 0 && (
                  <div style={s.metadataCard}>
                    <div style={s.sectionTitle}>Metadata</div>
                    <div style={s.metaGrid}>
                      {Object.entries(selected.metadata).map(([k, v]) => (
                        <div key={k} style={s.metaEntry}>
                          <span style={s.metaKey}>{k}</span>
                          <span style={s.metaValue}>{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Content */}
                <div style={s.contentCard}>
                  <div style={s.sectionTitle}>Content</div>
                  <pre style={s.contentPre}>
                    {selected.content || '(No content available)'}
                  </pre>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </AppShell>
  );
}

/* ── Styles ─────────────────────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  layout: {
    display: 'flex',
    gap: 16,
    minHeight: 440,
    flex: 1,
  },
  sidebar: {
    width: 300,
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    borderRight: '1px solid rgba(255,255,255,0.07)',
    paddingRight: 16,
  },
  searchRow: {
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
  },
  searchInput: {
    flex: 1,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
  },
  searchSpinner: {
    position: 'absolute',
    right: 10,
    color: '#00f0ff',
    fontSize: 14,
    animation: 'spin 1s linear infinite',
  },
  ingestBtn: {
    background: 'rgba(138,43,226,0.12)',
    border: '1px solid rgba(138,43,226,0.3)',
    borderRadius: 8,
    padding: '7px 12px',
    color: '#8A2BE2',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    textAlign: 'center',
  },
  listScroll: {
    flex: 1,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  docItem: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '8px 10px',
    borderRadius: 8,
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  docItemActive: {
    background: 'rgba(0,240,255,0.08)',
    border: '1px solid rgba(0,240,255,0.3)',
  },
  docIcon: { fontSize: 18, flexShrink: 0, marginTop: 1 },
  docInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    overflow: 'hidden',
  },
  docTitle: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  docMeta: {
    display: 'flex',
    gap: 8,
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  docExcerpt: {
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.3,
  },
  docCount: {
    fontSize: 11,
    color: '#4a4a62',
    textAlign: 'center',
    padding: '4px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  detail: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    overflowY: 'auto',
  },
  errorBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 8,
    padding: '6px 12px',
    color: '#ef4444',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  dismissBtn: {
    background: 'none',
    border: 'none',
    color: '#ef4444',
    cursor: 'pointer',
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  detailHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 12,
    padding: '4px 0',
  },
  detailTitle: {
    color: '#F8FAFC',
    fontSize: 17,
    fontWeight: 700,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 4,
  },
  detailMeta: {
    display: 'flex',
    gap: 10,
    alignItems: 'center',
  },
  metaPill: {
    fontSize: 10,
    fontWeight: 700,
    background: 'rgba(0,240,255,0.1)',
    color: '#00f0ff',
    padding: '2px 8px',
    borderRadius: 4,
    fontFamily: "'JetBrains Mono', monospace",
  },
  tagsRow: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap',
  },
  tag: {
    fontSize: 11,
    color: '#8A2BE2',
    background: 'rgba(138,43,226,0.12)',
    padding: '2px 8px',
    borderRadius: 4,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  metadataCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: 12,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  sectionTitle: {
    color: '#00f0ff',
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.05em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  metaGrid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 6,
  },
  metaEntry: {
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  metaKey: {
    fontSize: 10,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
  },
  metaValue: {
    fontSize: 12,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  contentCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    flex: 1,
  },
  contentPre: {
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    lineHeight: 1.6,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    margin: 0,
    maxHeight: 400,
    overflowY: 'auto',
    padding: 10,
    background: 'rgba(0,0,0,0.2)',
    borderRadius: 8,
  },
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    padding: 40,
    flex: 1,
  },
  spinner: {
    fontSize: 28,
    color: '#00f0ff',
    animation: 'spin 1s linear infinite',
  },
  secondaryText: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  mutedText: {
    color: '#4a4a62',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  emptyState: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: 24,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
};

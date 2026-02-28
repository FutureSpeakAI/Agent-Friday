import React, { useState, useEffect, useCallback, useRef } from 'react';

interface MemoryExplorerProps {
  visible: boolean;
  onClose: () => void;
}

type MemoryTab = 'long-term' | 'observations' | 'episodes' | 'search';

interface LongTermEntry {
  id: string;
  fact: string;
  category: string;
  confirmed: boolean;
  source: string;
}

interface MediumTermEntry {
  id: string;
  observation: string;
  category: string;
  confidence: number;
  occurrences: number;
}

interface EpisodeEntry {
  id: string;
  summary: string;
  startTime: number;
  endTime: number;
  durationSeconds: number;
  topics: string[];
  emotionalTone: string;
  keyDecisions: string[];
  turnCount: number;
}

interface SearchResult {
  type: 'memory' | 'observation' | 'episode';
  id: string;
  text: string;
  score: number;
  category?: string;
  timestamp?: number;
}

const CATEGORY_COLORS: Record<string, string> = {
  personal: '#818cf8',
  work: '#f59e0b',
  preference: '#22c55e',
  relationship: '#ec4899',
  health: '#ef4444',
  hobby: '#a78bfa',
  goal: '#00f0ff',
  routine: '#6ee7b7',
  technical: '#38bdf8',
  other: '#666680',
};

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function MemoryExplorer({ visible, onClose }: MemoryExplorerProps) {
  const [tab, setTab] = useState<MemoryTab>('long-term');
  const [longTerm, setLongTerm] = useState<LongTermEntry[]>([]);
  const [mediumTerm, setMediumTerm] = useState<MediumTermEntry[]>([]);
  const [episodes, setEpisodes] = useState<EpisodeEntry[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [filterCategory, setFilterCategory] = useState<string | null>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [lt, mt, ep] = await Promise.allSettled([
        window.eve.memory.getLongTerm(),
        window.eve.memory.getMediumTerm(),
        window.eve.episodic.search(''),
      ]);
      if (lt.status === 'fulfilled') setLongTerm(lt.value);
      if (mt.status === 'fulfilled') setMediumTerm(mt.value);
      if (ep.status === 'fulfilled') setEpisodes(ep.value);
    } catch {
      // partial loads fine
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    loadData();
  }, [visible, loadData]);

  useEffect(() => {
    if (visible) setTimeout(() => overlayRef.current?.focus(), 50);
  }, [visible]);

  // Focus search input when switching to search tab
  useEffect(() => {
    if (tab === 'search') {
      setTimeout(() => searchInputRef.current?.focus(), 100);
    }
  }, [tab]);

  // Debounced semantic search
  const performSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setSearchResults([]);
      return;
    }
    setIsSearching(true);
    try {
      const [memResults, episodeResults] = await Promise.allSettled([
        window.eve.search.query(q),
        window.eve.episodic.search(q),
      ]);

      const results: SearchResult[] = [];

      if (memResults.status === 'fulfilled') {
        for (const m of memResults.value) {
          results.push({
            type: (m.type as SearchResult['type']) || 'memory',
            id: m.id,
            text: m.text || '',
            score: m.score || 0.5,
            category: (m.meta as any)?.category,
          });
        }
      }
      if (episodeResults.status === 'fulfilled') {
        for (const ep of episodeResults.value) {
          results.push({
            type: 'episode',
            id: ep.id,
            text: ep.summary,
            score: 0.4,
            timestamp: ep.startTime,
          });
        }
      }

      // Sort by score
      results.sort((a, b) => b.score - a.score);
      setSearchResults(results);
    } catch {
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, []);

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const q = e.target.value;
    setSearchQuery(q);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => performSearch(q), 400);
  }, [performSearch]);

  // Derive categories from long-term
  const allCategories = [...new Set(longTerm.map((m) => m.category))].sort();

  // Filtered long-term
  const filteredLT = filterCategory
    ? longTerm.filter((m) => m.category === filterCategory)
    : longTerm;

  if (!visible) return null;

  const tabs: { key: MemoryTab; label: string; count: number }[] = [
    { key: 'long-term', label: 'Memories', count: longTerm.length },
    { key: 'observations', label: 'Observations', count: mediumTerm.length },
    { key: 'episodes', label: 'Episodes', count: episodes.length },
    { key: 'search', label: 'Search', count: searchResults.length },
  ];

  return (
    <div
      ref={overlayRef}
      style={styles.overlay}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      tabIndex={-1}
    >
      <div style={styles.panel}>
        {/* Header */}
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <span style={styles.headerIcon}>🧠</span>
            <span style={styles.headerTitle}>Memory Explorer</span>
            <span style={styles.headerSubtitle}>
              {longTerm.length} memories · {mediumTerm.length} observations · {episodes.length} episodes
            </span>
          </div>
          <button onClick={onClose} style={styles.closeBtn}>✕</button>
        </div>

        {/* Tabs */}
        <div style={styles.tabs}>
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                ...styles.tab,
                ...(tab === t.key ? styles.tabActive : {}),
              }}
            >
              {t.label}
              <span style={styles.tabCount}>{t.count}</span>
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={styles.content}>
          {/* Long-term memories */}
          {tab === 'long-term' && (
            <div style={styles.section}>
              {/* Category filter chips */}
              {allCategories.length > 1 && (
                <div style={styles.filterRow}>
                  <button
                    onClick={() => setFilterCategory(null)}
                    style={{
                      ...styles.filterChip,
                      ...(filterCategory === null ? styles.filterChipActive : {}),
                    }}
                  >
                    All
                  </button>
                  {allCategories.map((cat) => (
                    <button
                      key={cat}
                      onClick={() => setFilterCategory(cat === filterCategory ? null : cat)}
                      style={{
                        ...styles.filterChip,
                        ...(filterCategory === cat ? styles.filterChipActive : {}),
                        borderColor: CATEGORY_COLORS[cat] || '#333',
                      }}
                    >
                      {cat}
                    </button>
                  ))}
                </div>
              )}

              {filteredLT.length === 0 ? (
                <div style={styles.empty}>No memories yet — talk to EVE to build your profile</div>
              ) : (
                <div style={styles.entryList}>
                  {filteredLT.map((m) => (
                    <div key={m.id} style={styles.entry}>
                      <span
                        style={{
                          ...styles.categoryDot,
                          background: CATEGORY_COLORS[m.category] || '#666',
                        }}
                      />
                      <div style={styles.entryBody}>
                        <span style={styles.entryText}>{m.fact}</span>
                        <div style={styles.entryMeta}>
                          <span
                            style={{
                              ...styles.categoryBadge,
                              color: CATEGORY_COLORS[m.category] || '#666',
                            }}
                          >
                            {m.category}
                          </span>
                          {m.confirmed && <span style={styles.confirmedBadge}>confirmed</span>}
                          <span style={styles.sourceBadge}>{m.source}</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Observations */}
          {tab === 'observations' && (
            <div style={styles.section}>
              {mediumTerm.length === 0 ? (
                <div style={styles.empty}>No observations yet — EVE builds these over time</div>
              ) : (
                <div style={styles.entryList}>
                  {mediumTerm.map((o) => (
                    <div key={o.id} style={styles.entry}>
                      <span
                        style={{
                          ...styles.categoryDot,
                          background: CATEGORY_COLORS[o.category] || '#666',
                        }}
                      />
                      <div style={styles.entryBody}>
                        <span style={styles.entryText}>{o.observation}</span>
                        <div style={styles.entryMeta}>
                          <span
                            style={{
                              ...styles.categoryBadge,
                              color: CATEGORY_COLORS[o.category] || '#666',
                            }}
                          >
                            {o.category}
                          </span>
                          <span style={styles.confidenceBadge}>
                            {Math.round(o.confidence * 100)}%
                          </span>
                          <span style={styles.sourceBadge}>{o.occurrences}x seen</span>
                        </div>
                      </div>
                      {/* Confidence bar */}
                      <div style={styles.miniBar}>
                        <div
                          style={{
                            ...styles.miniBarFill,
                            width: `${o.confidence * 100}%`,
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Episodes */}
          {tab === 'episodes' && (
            <div style={styles.section}>
              {episodes.length === 0 ? (
                <div style={styles.empty}>No conversation episodes recorded yet</div>
              ) : (
                <div style={styles.entryList}>
                  {episodes.map((ep) => (
                    <div key={ep.id} style={styles.episodeEntry}>
                      <div style={styles.episodeHeader}>
                        <span style={styles.episodeTime}>
                          {new Date(ep.startTime).toLocaleDateString()} {' '}
                          {new Date(ep.startTime).toLocaleTimeString([], {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </span>
                        <span style={{
                          ...styles.sentimentChip,
                          color: ep.emotionalTone === 'positive' ? '#22c55e' :
                            ep.emotionalTone === 'negative' ? '#ef4444' : '#666680',
                        }}>
                          {ep.emotionalTone}
                        </span>
                        <span style={styles.turnCount}>{ep.turnCount} turns</span>
                      </div>
                      <div style={styles.episodeSummary}>{ep.summary}</div>
                      {ep.topics.length > 0 && (
                        <div style={styles.topicRow}>
                          {ep.topics.map((t) => (
                            <span key={t} style={styles.topicChip}>{t}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Semantic search */}
          {tab === 'search' && (
            <div style={styles.section}>
              <div style={styles.searchRow}>
                <span style={styles.searchIcon}>🔍</span>
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={handleSearchChange}
                  placeholder="Search across all memories, observations, and episodes..."
                  style={styles.searchInput}
                />
                {isSearching && <span style={styles.searchSpinner}>⟳</span>}
              </div>

              {searchResults.length === 0 && searchQuery.trim() && !isSearching && (
                <div style={styles.empty}>No results found for "{searchQuery}"</div>
              )}

              {searchResults.length > 0 && (
                <div style={styles.entryList}>
                  {searchResults.map((r) => (
                    <div key={`${r.type}-${r.id}`} style={styles.entry}>
                      <span
                        style={{
                          ...styles.typeBadge,
                          color: r.type === 'memory' ? '#22c55e' :
                            r.type === 'observation' ? '#f59e0b' : '#818cf8',
                        }}
                      >
                        {r.type}
                      </span>
                      <div style={styles.entryBody}>
                        <span style={styles.entryText}>{r.text}</span>
                        <div style={styles.entryMeta}>
                          {r.category && (
                            <span style={{
                              ...styles.categoryBadge,
                              color: CATEGORY_COLORS[r.category] || '#666',
                            }}>
                              {r.category}
                            </span>
                          )}
                          {r.timestamp && (
                            <span style={styles.sourceBadge}>{timeAgo(r.timestamp)}</span>
                          )}
                          <span style={styles.scoreBadge}>
                            {Math.round(r.score * 100)}% match
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0, 0, 0, 0.7)',
    backdropFilter: 'blur(12px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 100,
  },
  panel: {
    width: 720,
    maxWidth: '95vw',
    maxHeight: '85vh',
    background: 'rgba(12, 12, 20, 0.98)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 20,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    boxShadow: '0 20px 60px rgba(0, 0, 0, 0.5)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '18px 24px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  headerIcon: { fontSize: 18 },
  headerTitle: {
    fontSize: 15,
    fontWeight: 700,
    color: '#e0e0e8',
  },
  headerSubtitle: {
    fontSize: 11,
    color: '#555568',
    marginLeft: 8,
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#555568',
    fontSize: 16,
    cursor: 'pointer',
    padding: '4px 8px',
    borderRadius: 4,
  },
  tabs: {
    display: 'flex',
    gap: 0,
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    padding: '0 24px',
  },
  tab: {
    background: 'none',
    border: 'none',
    borderBottom: '2px solid transparent',
    color: '#666680',
    fontSize: 12,
    fontWeight: 600,
    padding: '12px 16px',
    cursor: 'pointer',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    transition: 'color 0.15s, border-color 0.15s',
  },
  tabActive: {
    color: '#00f0ff',
    borderBottomColor: '#00f0ff',
  },
  tabCount: {
    fontSize: 10,
    color: '#555568',
    background: 'rgba(255,255,255,0.04)',
    padding: '1px 6px',
    borderRadius: 6,
    fontWeight: 500,
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    padding: '16px 24px',
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  filterRow: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  filterChip: {
    fontSize: 11,
    color: '#888898',
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 6,
    padding: '4px 10px',
    cursor: 'pointer',
    fontWeight: 500,
    textTransform: 'capitalize' as const,
    transition: 'background 0.15s',
  },
  filterChipActive: {
    color: '#00f0ff',
    background: 'rgba(0, 240, 255, 0.08)',
    borderColor: 'rgba(0, 240, 255, 0.3)',
  },
  entryList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  entry: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 14px',
    background: 'rgba(255, 255, 255, 0.02)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 10,
  },
  categoryDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
    marginTop: 5,
  },
  entryBody: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  entryText: {
    fontSize: 13,
    color: '#d0d0d8',
    lineHeight: 1.4,
  },
  entryMeta: {
    display: 'flex',
    gap: 8,
    alignItems: 'center',
    flexWrap: 'wrap' as const,
  },
  categoryBadge: {
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'capitalize' as const,
  },
  confirmedBadge: {
    fontSize: 9,
    color: '#22c55e',
    background: 'rgba(34, 197, 94, 0.1)',
    padding: '1px 6px',
    borderRadius: 3,
    fontWeight: 600,
  },
  sourceBadge: {
    fontSize: 10,
    color: '#555568',
  },
  confidenceBadge: {
    fontSize: 10,
    fontWeight: 600,
    color: '#f59e0b',
  },
  miniBar: {
    width: 40,
    height: 4,
    borderRadius: 2,
    background: 'rgba(255, 255, 255, 0.06)',
    overflow: 'hidden',
    flexShrink: 0,
    alignSelf: 'center',
  },
  miniBarFill: {
    height: '100%',
    borderRadius: 2,
    background: '#f59e0b',
    transition: 'width 0.3s',
  },
  episodeEntry: {
    padding: '12px 14px',
    background: 'rgba(255, 255, 255, 0.02)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 10,
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  episodeHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  episodeTime: {
    fontSize: 11,
    color: '#555568',
    fontFamily: "'JetBrains Mono', monospace",
  },
  sentimentChip: {
    fontSize: 10,
    fontWeight: 600,
    textTransform: 'capitalize' as const,
  },
  turnCount: {
    fontSize: 10,
    color: '#444',
    marginLeft: 'auto',
  },
  episodeSummary: {
    fontSize: 13,
    color: '#c0c0d0',
    lineHeight: 1.4,
  },
  topicRow: {
    display: 'flex',
    gap: 4,
    flexWrap: 'wrap' as const,
  },
  topicChip: {
    fontSize: 9,
    color: '#818cf8',
    background: 'rgba(129, 140, 248, 0.08)',
    padding: '2px 6px',
    borderRadius: 3,
    fontWeight: 600,
  },
  searchRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '10px 14px',
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 10,
  },
  searchIcon: { fontSize: 14, flexShrink: 0 },
  searchInput: {
    flex: 1,
    background: 'none',
    border: 'none',
    outline: 'none',
    color: '#e0e0e8',
    fontSize: 14,
    fontFamily: 'inherit',
  },
  searchSpinner: {
    fontSize: 16,
    color: '#00f0ff',
    animation: 'spin 1s linear infinite',
  },
  typeBadge: {
    fontSize: 9,
    fontWeight: 700,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
    flexShrink: 0,
    paddingTop: 4,
  },
  scoreBadge: {
    fontSize: 10,
    color: '#00f0ff',
    fontWeight: 600,
  },
  empty: {
    fontSize: 13,
    color: '#555568',
    fontStyle: 'italic',
    padding: '24px 0',
    textAlign: 'center',
  },
};

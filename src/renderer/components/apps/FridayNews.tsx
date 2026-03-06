/**
 * FridayNews.tsx — Agent Friday News & Intelligence Feed
 *
 * World monitor integration for news search, briefings,
 * and topic tracking with auto-refresh capability.
 *
 * IPC: window.eve.connectors.callTool('world_monitor_*', ...)
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

interface NewsItem {
  id?: string;
  title: string;
  source?: string;
  url?: string;
  date?: string;
  summary?: string;
  category?: string;
  sentiment?: string;
  relevance?: number;
}

interface Topic {
  name: string;
  count?: number;
  trend?: string;
}

interface Briefing {
  summary: string;
  highlights?: string[];
  generatedAt?: string;
  period?: string;
}

interface Props {
  visible: boolean;
  onClose: () => void;
}

type Tab = 'feed' | 'briefing' | 'topics';

export default function FridayNews({ visible, onClose }: Props) {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [topics, setTopics] = useState<Topic[]>([]);
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('feed');
  const [search, setSearch] = useState('');
  const [searching, setSearching] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const callTool = useCallback(async (tool: string, args: Record<string, any>) => {
    return (window as any).eve.connectors.callTool(tool, args);
  }, []);

  const loadFeed = useCallback(async (query?: string) => {
    if (query) setSearching(true);
    else setLoading(true);
    setError(null);
    try {
      const result = await callTool('world_monitor_search', {
        query: query || 'latest news',
      });
      const items = Array.isArray(result) ? result : result?.results || result?.items || [];
      setNews(items);
    } catch (err: any) {
      setError(err?.message || 'Failed to load news feed');
    } finally {
      setLoading(false);
      setSearching(false);
    }
  }, [callTool]);

  const loadBriefing = useCallback(async () => {
    setBriefingLoading(true);
    setError(null);
    try {
      const result = await callTool('world_monitor_briefing', {});
      setBriefing(
        typeof result === 'string'
          ? { summary: result }
          : result || { summary: 'No briefing available' }
      );
    } catch (err: any) {
      setError(err?.message || 'Failed to load briefing');
    } finally {
      setBriefingLoading(false);
    }
  }, [callTool]);

  const loadTopics = useCallback(async () => {
    setTopicsLoading(true);
    setError(null);
    try {
      const result = await callTool('world_monitor_topics', {});
      setTopics(Array.isArray(result) ? result : result?.topics || []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load topics');
    } finally {
      setTopicsLoading(false);
    }
  }, [callTool]);

  useEffect(() => {
    if (visible) {
      loadFeed();
    }
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [visible, loadFeed]);

  useEffect(() => {
    if (autoRefresh) {
      refreshTimer.current = setInterval(() => loadFeed(), 60_000);
    } else {
      if (refreshTimer.current) {
        clearInterval(refreshTimer.current);
        refreshTimer.current = null;
      }
    }
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [autoRefresh, loadFeed]);

  useEffect(() => {
    if (visible && activeTab === 'briefing' && !briefing && !briefingLoading) {
      loadBriefing();
    }
    if (visible && activeTab === 'topics' && topics.length === 0 && !topicsLoading) {
      loadTopics();
    }
  }, [visible, activeTab, briefing, briefingLoading, topics.length, topicsLoading, loadBriefing, loadTopics]);

  const handleSearch = () => {
    if (search.trim()) loadFeed(search.trim());
  };

  const handleTopicClick = (topic: Topic) => {
    setSearch(topic.name);
    setActiveTab('feed');
    loadFeed(topic.name);
  };

  const getSentimentColor = (sentiment?: string): string => {
    switch (sentiment?.toLowerCase()) {
      case 'positive': return '#22c55e';
      case 'negative': return '#ef4444';
      case 'neutral': return '#8888a0';
      default: return '#8888a0';
    }
  };

  const getTrendIcon = (trend?: string): string => {
    switch (trend?.toLowerCase()) {
      case 'up': case 'rising': return '\u2191';
      case 'down': case 'falling': return '\u2193';
      default: return '\u2022';
    }
  };

  const getTrendColor = (trend?: string): string => {
    switch (trend?.toLowerCase()) {
      case 'up': case 'rising': return '#22c55e';
      case 'down': case 'falling': return '#ef4444';
      default: return '#8888a0';
    }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'feed', label: 'News Feed' },
    { key: 'briefing', label: 'Intel Briefing' },
    { key: 'topics', label: 'Topics' },
  ];

  return (
    <AppShell visible={visible} onClose={onClose} title="News" icon="📰" width={940}>
      {/* Tab Bar */}
      <div style={s.tabRow}>
        <div style={s.tabGroup}>
          {tabs.map((t) => (
            <button
              key={t.key}
              style={{
                ...s.tab,
                ...(activeTab === t.key ? s.tabActive : {}),
              }}
              onClick={() => setActiveTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div style={s.refreshToggle}>
          <span style={s.refreshLabel}>Auto-refresh</span>
          <button
            style={{
              ...s.toggleBtn,
              background: autoRefresh
                ? 'rgba(34,197,94,0.15)'
                : 'rgba(255,255,255,0.03)',
              borderColor: autoRefresh
                ? 'rgba(34,197,94,0.4)'
                : 'rgba(255,255,255,0.07)',
              color: autoRefresh ? '#22c55e' : '#4a4a62',
            }}
            onClick={() => setAutoRefresh(!autoRefresh)}
          >
            {autoRefresh ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {error && (
        <div style={s.errorBar}>
          <span>{error}</span>
          <button style={s.dismissBtn} onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {/* ── Feed Tab ── */}
      {activeTab === 'feed' && (
        <>
          <div style={s.searchBar}>
            <input
              type="text"
              placeholder="Search news topics..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              style={s.searchInput}
            />
            <button
              style={s.searchBtn}
              onClick={handleSearch}
              disabled={searching}
            >
              {searching ? '...' : 'Search'}
            </button>
          </div>

          {loading ? (
            <div style={s.center}>
              <span style={s.spinner}>⟳</span>
              <span style={s.secondaryText}>Loading news feed...</span>
            </div>
          ) : news.length === 0 ? (
            <div style={s.center}>
              <div style={s.emptyIcon}>📰</div>
              <span style={s.mutedText}>No news articles found</span>
            </div>
          ) : (
            <div style={s.feedGrid}>
              {news.map((item, i) => (
                <div key={item.id || i} style={s.newsCard}>
                  <div style={s.newsHeader}>
                    {item.category && (
                      <span style={s.categoryPill}>{item.category}</span>
                    )}
                    {item.sentiment && (
                      <span
                        style={{
                          ...s.sentimentDot,
                          background: getSentimentColor(item.sentiment),
                        }}
                        title={item.sentiment}
                      />
                    )}
                  </div>
                  <div style={s.newsTitle}>{item.title}</div>
                  {item.summary && (
                    <div style={s.newsSummary}>
                      {item.summary.slice(0, 150)}
                      {item.summary.length > 150 ? '...' : ''}
                    </div>
                  )}
                  <div style={s.newsFooter}>
                    {item.source && (
                      <span style={s.newsSource}>{item.source}</span>
                    )}
                    {item.date && (
                      <span style={s.newsDate}>
                        {new Date(item.date).toLocaleDateString()}
                      </span>
                    )}
                    {item.relevance !== undefined && (
                      <span style={s.relevance}>
                        Relevance: {Math.round(item.relevance * 100)}%
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── Briefing Tab ── */}
      {activeTab === 'briefing' && (
        <div style={s.briefingContainer}>
          {briefingLoading ? (
            <div style={s.center}>
              <span style={s.spinner}>⟳</span>
              <span style={s.secondaryText}>Generating intelligence briefing...</span>
            </div>
          ) : !briefing ? (
            <div style={s.center}>
              <span style={s.mutedText}>No briefing available</span>
              <button style={s.actionBtn} onClick={loadBriefing}>
                Generate Briefing
              </button>
            </div>
          ) : (
            <>
              <div style={s.briefingCard}>
                <div style={s.briefingHeader}>
                  <span style={s.sectionTitle}>Intelligence Briefing</span>
                  {briefing.generatedAt && (
                    <span style={s.briefingTime}>
                      {new Date(briefing.generatedAt).toLocaleString()}
                    </span>
                  )}
                </div>
                {briefing.period && (
                  <div style={s.briefingPeriod}>Period: {briefing.period}</div>
                )}
                <div style={s.briefingSummary}>{briefing.summary}</div>
              </div>

              {briefing.highlights && briefing.highlights.length > 0 && (
                <div style={s.highlightsCard}>
                  <div style={s.sectionTitle}>Key Highlights</div>
                  {briefing.highlights.map((h, i) => (
                    <div key={i} style={s.highlightRow}>
                      <span style={s.highlightBullet}>▸</span>
                      <span style={s.highlightText}>{h}</span>
                    </div>
                  ))}
                </div>
              )}

              <button style={s.actionBtn} onClick={loadBriefing}>
                Refresh Briefing
              </button>
            </>
          )}
        </div>
      )}

      {/* ── Topics Tab ── */}
      {activeTab === 'topics' && (
        <div style={s.topicsContainer}>
          {topicsLoading ? (
            <div style={s.center}>
              <span style={s.spinner}>⟳</span>
              <span style={s.secondaryText}>Loading topics...</span>
            </div>
          ) : topics.length === 0 ? (
            <div style={s.center}>
              <span style={s.mutedText}>No trending topics</span>
              <button style={s.actionBtn} onClick={loadTopics}>
                Refresh Topics
              </button>
            </div>
          ) : (
            <>
              <div style={s.topicsGrid}>
                {topics.map((t, i) => (
                  <div
                    key={i}
                    style={s.topicCard}
                    onClick={() => handleTopicClick(t)}
                  >
                    <div style={s.topicName}>{t.name}</div>
                    <div style={s.topicMeta}>
                      {t.count !== undefined && (
                        <span style={s.topicCount}>{t.count} articles</span>
                      )}
                      {t.trend && (
                        <span style={{ color: getTrendColor(t.trend), fontWeight: 700 }}>
                          {getTrendIcon(t.trend)} {t.trend}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <button style={s.actionBtn} onClick={loadTopics}>
                Refresh Topics
              </button>
            </>
          )}
        </div>
      )}
    </AppShell>
  );
}

/* ── Styles ─────────────────────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  tabRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderBottom: '1px solid rgba(255,255,255,0.07)',
    paddingBottom: 12,
    marginBottom: 4,
  },
  tabGroup: { display: 'flex', gap: 4 },
  tab: {
    background: 'transparent',
    border: '1px solid transparent',
    borderRadius: 8,
    padding: '6px 14px',
    color: '#8888a0',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0,240,255,0.08)',
    borderColor: 'rgba(0,240,255,0.3)',
    color: '#00f0ff',
  },
  refreshToggle: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  refreshLabel: {
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  toggleBtn: {
    border: '1px solid',
    borderRadius: 6,
    padding: '3px 10px',
    fontSize: 11,
    fontWeight: 700,
    cursor: 'pointer',
    fontFamily: "'JetBrains Mono', monospace",
    transition: 'all 0.15s',
  },
  searchBar: {
    display: 'flex',
    gap: 8,
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
  searchBtn: {
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '8px 16px',
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
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
  },
  feedGrid: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    flex: 1,
    overflowY: 'auto',
  },
  newsCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '12px 14px',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    transition: 'border-color 0.15s',
    cursor: 'default',
  },
  newsHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  categoryPill: {
    fontSize: 10,
    fontWeight: 700,
    color: '#8A2BE2',
    background: 'rgba(138,43,226,0.12)',
    padding: '2px 8px',
    borderRadius: 4,
    fontFamily: "'Inter', system-ui, sans-serif",
    textTransform: 'uppercase' as const,
  },
  sentimentDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
  },
  newsTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.3,
  },
  newsSummary: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.4,
  },
  newsFooter: {
    display: 'flex',
    gap: 12,
    alignItems: 'center',
    marginTop: 2,
  },
  newsSource: {
    fontSize: 11,
    color: '#00f0ff',
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  newsDate: {
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  relevance: {
    fontSize: 10,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
    marginLeft: 'auto',
  },
  briefingContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    flex: 1,
  },
  briefingCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  briefingHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  sectionTitle: {
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  briefingTime: {
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'JetBrains Mono', monospace",
  },
  briefingPeriod: {
    fontSize: 12,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  briefingSummary: {
    color: '#F8FAFC',
    fontSize: 14,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.6,
  },
  highlightsCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  highlightRow: {
    display: 'flex',
    gap: 8,
    alignItems: 'flex-start',
  },
  highlightBullet: {
    color: '#f97316',
    fontSize: 14,
    flexShrink: 0,
    lineHeight: 1.4,
  },
  highlightText: {
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.4,
  },
  topicsContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    flex: 1,
  },
  topicsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
    gap: 10,
  },
  topicCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '12px 14px',
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.15s',
  },
  topicName: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 4,
  },
  topicMeta: {
    display: 'flex',
    gap: 10,
    alignItems: 'center',
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  topicCount: {
    color: '#8888a0',
  },
  actionBtn: {
    alignSelf: 'flex-start',
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '7px 16px',
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
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
  emptyIcon: { fontSize: 32, opacity: 0.5 },
};

import React, { useState, useEffect, useCallback } from 'react';

interface Briefing {
  id: string;
  topic: string;
  content: string;
  createdAt: number;
  delivered: boolean;
  priority: 'high' | 'medium' | 'low';
}

interface ResearchPanelProps {
  onSendText: (text: string) => void;
}

const PRIORITY_COLORS: Record<string, { bg: string; border: string; dot: string }> = {
  high: { bg: 'rgba(255, 100, 100, 0.06)', border: 'rgba(255, 100, 100, 0.15)', dot: '#ff6464' },
  medium: { bg: 'rgba(0, 240, 255, 0.06)', border: 'rgba(0, 240, 255, 0.15)', dot: '#00f0ff' },
  low: { bg: 'rgba(255, 255, 255, 0.03)', border: 'rgba(255, 255, 255, 0.08)', dot: '#555568' },
};

function timeAgo(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function ResearchPanel({ onSendText }: ResearchPanelProps) {
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadBriefings = useCallback(async () => {
    try {
      const data = await window.eve.intelligence.listAll();
      setBriefings(data || []);
    } catch {
      setBriefings([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadBriefings();
    // Refresh every 60s
    const interval = setInterval(loadBriefings, 60_000);
    return () => clearInterval(interval);
  }, [loadBriefings]);

  const handleAskAbout = (topic: string) => {
    onSendText(`Tell me more about: ${topic}`);
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <span style={styles.headerIcon}>◆</span>
        <span style={styles.headerText}>Research</span>
        <span style={styles.count}>{briefings.length}</span>
        <button onClick={loadBriefings} style={styles.refreshBtn} title="Refresh">↻</button>
      </div>

      <div style={styles.list}>
        {loading && (
          <div style={styles.empty}>
            <div style={styles.emptyIcon}>⟳</div>
            <div style={styles.emptyText}>Loading briefings...</div>
          </div>
        )}

        {!loading && briefings.length === 0 && (
          <div style={styles.empty}>
            <div style={styles.emptyIcon}>◇</div>
            <div style={styles.emptyText}>No research yet</div>
            <div style={styles.emptyHint}>Set up intelligence topics to receive research briefings</div>
          </div>
        )}

        {briefings.map((b) => {
          const colors = PRIORITY_COLORS[b.priority] || PRIORITY_COLORS.medium;
          const isExpanded = expanded === b.id;

          return (
            <div
              key={b.id}
              className="hover-lift"
              style={{
                ...styles.card,
                background: isExpanded ? colors.bg : 'rgba(255,255,255,0.02)',
                borderColor: isExpanded ? colors.border : 'rgba(255,255,255,0.06)',
              }}
              onClick={() => setExpanded(isExpanded ? null : b.id)}
            >
              {/* Card header — always visible */}
              <div style={styles.cardHeader}>
                <span style={{ ...styles.priorityDot, background: colors.dot }} />
                <span style={styles.cardTopic}>{b.topic}</span>
                {!b.delivered && <span style={styles.newBadge}>NEW</span>}
              </div>

              {/* Preview — shown when collapsed */}
              {!isExpanded && (
                <div style={styles.cardPreview}>
                  {b.content.slice(0, 120)}{b.content.length > 120 ? '…' : ''}
                </div>
              )}

              {/* Full content — shown when expanded */}
              {isExpanded && (
                <div style={styles.cardContent}>
                  {b.content}
                  <div style={styles.cardActions}>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleAskAbout(b.topic);
                      }}
                      style={styles.askBtn}
                    >
                      Ask about this →
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(b.content);
                      }}
                      style={styles.copyBtn}
                    >
                      Copy
                    </button>
                  </div>
                </div>
              )}

              {/* Metadata footer */}
              <div style={styles.cardMeta}>
                <span style={styles.metaTime}>{timeAgo(b.createdAt)}</span>
                <span style={styles.metaPriority}>{b.priority}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '16px 20px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
  },
  headerIcon: {
    color: '#a855f7',
    fontSize: 14,
  },
  headerText: {
    fontSize: 13,
    fontWeight: 600,
    color: '#e0e0e8',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
  },
  count: {
    marginLeft: 'auto',
    fontSize: 11,
    color: '#555568',
    background: 'rgba(255,255,255,0.05)',
    padding: '2px 8px',
    borderRadius: 10,
  },
  refreshBtn: {
    background: 'none',
    border: 'none',
    color: '#555568',
    fontSize: 14,
    cursor: 'pointer',
    padding: '2px 4px',
    marginLeft: 4,
    transition: 'color 0.15s',
  },
  list: {
    flex: 1,
    overflowY: 'auto' as const,
    padding: '12px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  empty: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    height: '100%',
    gap: 8,
    opacity: 0.4,
  },
  emptyIcon: {
    fontSize: 32,
    color: '#a855f7',
  },
  emptyText: {
    fontSize: 14,
    fontWeight: 500,
    color: '#e0e0e8',
  },
  emptyHint: {
    fontSize: 12,
    color: '#555568',
    textAlign: 'center' as const,
    maxWidth: 200,
  },
  card: {
    border: '1px solid',
    borderRadius: 10,
    padding: '12px 14px',
    cursor: 'pointer',
    transition: 'background 0.2s, border-color 0.2s',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  cardHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  priorityDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    flexShrink: 0,
  },
  cardTopic: {
    fontSize: 13,
    fontWeight: 600,
    color: '#e0e0e8',
    flex: 1,
    lineHeight: 1.3,
  },
  newBadge: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    color: '#a855f7',
    background: 'rgba(168, 85, 247, 0.12)',
    border: '1px solid rgba(168, 85, 247, 0.2)',
    borderRadius: 4,
    padding: '1px 6px',
    flexShrink: 0,
  },
  cardPreview: {
    fontSize: 12,
    lineHeight: 1.5,
    color: '#888898',
    overflow: 'hidden',
  },
  cardContent: {
    fontSize: 13,
    lineHeight: 1.6,
    color: '#c0c0c8',
    whiteSpace: 'pre-wrap' as const,
  },
  cardActions: {
    display: 'flex',
    gap: 8,
    marginTop: 10,
  },
  askBtn: {
    background: 'rgba(168, 85, 247, 0.1)',
    border: '1px solid rgba(168, 85, 247, 0.2)',
    borderRadius: 6,
    color: '#a855f7',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 10px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  copyBtn: {
    background: 'rgba(255,255,255,0.05)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 6,
    color: '#888898',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 10px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  cardMeta: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  metaTime: {
    fontSize: 10,
    color: '#444458',
  },
  metaPriority: {
    fontSize: 9,
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
    color: '#555568',
  },
};

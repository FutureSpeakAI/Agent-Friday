import React, { useState, useEffect, useCallback, useRef } from 'react';
import ContextCard from './dashboard/ContextCard';
import AgentCard from './dashboard/AgentCard';
import MoodTimeline from './dashboard/MoodTimeline';

interface DashboardProps {
  visible: boolean;
  onClose: () => void;
}

interface AmbientState {
  activeApp: string;
  windowTitle: string;
  appDurations: Record<string, number>;
  focusStreak: number;
  inferredTask: string;
  lastUpdated: number;
}

interface SentimentState {
  currentMood: string;
  confidence: number;
  energyLevel: number;
  moodStreak: number;
  lastAnalysed: number;
}

interface ClipboardEntry {
  text: string;
  type: string;
  preview: string;
  timestamp: number;
}

interface AgentTask {
  id: string;
  agentType: string;
  description: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  result?: string;
  error?: string;
  logs: string[];
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
}

interface EpisodeEntry {
  id: string;
  summary: string;
  startTime: number;
  endTime: number;
  topics: string[];
  sentiment: string;
}

export default function Dashboard({ visible, onClose }: DashboardProps) {
  const [ambient, setAmbient] = useState<AmbientState | null>(null);
  const [sentiment, setSentiment] = useState<SentimentState | null>(null);
  const [clipboard, setClipboard] = useState<ClipboardEntry | null>(null);
  const [agentTasks, setAgentTasks] = useState<AgentTask[]>([]);
  const [episodes, setEpisodes] = useState<EpisodeEntry[]>([]);
  const [projects, setProjects] = useState<Array<{ name: string; type: string; rootPath: string }>>([]);
  const [documents, setDocuments] = useState<Array<{ id: string; fileName: string; size: number }>>([]);
  const overlayRef = useRef<HTMLDivElement>(null);

  const loadData = useCallback(async () => {
    try {
      const [ambientRes, sentimentRes, clipRes, tasksRes, episodesRes, projRes, docsRes] =
        await Promise.allSettled([
          window.eve.ambient.getState(),
          window.eve.sentiment.getState(),
          window.eve.clipboard.getCurrent(),
          window.eve.agents.listTasks(),
          window.eve.episodes.search('', 5),
          window.eve.project.list(),
          window.eve.documents.list(),
        ]);

      if (ambientRes.status === 'fulfilled') setAmbient(ambientRes.value);
      if (sentimentRes.status === 'fulfilled') setSentiment(sentimentRes.value);
      if (clipRes.status === 'fulfilled') setClipboard(clipRes.value);
      if (tasksRes.status === 'fulfilled') setAgentTasks(tasksRes.value);
      if (episodesRes.status === 'fulfilled') setEpisodes(episodesRes.value);
      if (projRes.status === 'fulfilled') setProjects(projRes.value);
      if (docsRes.status === 'fulfilled') setDocuments(docsRes.value);
    } catch {
      // partial loads are fine
    }
  }, []);

  // Poll for updates while visible
  useEffect(() => {
    if (!visible) return;
    loadData();
    const interval = setInterval(loadData, 5000);
    return () => clearInterval(interval);
  }, [visible, loadData]);

  // Listen for real-time clipboard + agent updates
  useEffect(() => {
    if (!visible) return;

    const cleanupClip = window.eve.clipboard.onChanged((entry) => {
      setClipboard(entry);
    });

    const cleanupAgent = window.eve.agents.onUpdate((task) => {
      setAgentTasks((prev) => {
        const idx = prev.findIndex((t) => t.id === task.id);
        if (idx >= 0) {
          const copy = [...prev];
          copy[idx] = task;
          return copy;
        }
        return [task, ...prev];
      });
    });

    return () => {
      cleanupClip();
      cleanupAgent();
    };
  }, [visible]);

  // Auto-focus for Escape
  useEffect(() => {
    if (visible) setTimeout(() => overlayRef.current?.focus(), 50);
  }, [visible]);

  const getMoodHistory = useCallback(async () => {
    try {
      return await window.eve.sentiment.getHistory();
    } catch {
      return [];
    }
  }, []);

  const handleCancelTask = useCallback(async (id: string) => {
    try {
      await window.eve.agents.cancelTask(id);
      setAgentTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, status: 'cancelled' as const } : t))
      );
    } catch {
      // ignore
    }
  }, []);

  if (!visible) return null;

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
            <span style={styles.headerIcon}>◈</span>
            <span style={styles.headerTitle}>Command Center</span>
          </div>
          <button onClick={onClose} style={styles.closeBtn}>✕</button>
        </div>

        {/* Scrollable content */}
        <div style={styles.content}>
          {/* Top row: Context + Agents side by side */}
          <div style={styles.topRow}>
            <div style={styles.topLeft}>
              <ContextCard ambient={ambient} sentiment={sentiment} clipboard={clipboard} />
            </div>
            <div style={styles.topRight}>
              <AgentCard tasks={agentTasks} onCancel={handleCancelTask} />
            </div>
          </div>

          {/* Mood timeline — full width */}
          <MoodTimeline getMoodHistory={getMoodHistory} />

          {/* Bottom row: Recent Episodes + Projects/Docs */}
          <div style={styles.bottomRow}>
            {/* Recent episodes */}
            <div style={styles.sectionCard}>
              <div style={styles.sectionHeader}>
                <span style={styles.sectionTitle}>Recent Conversations</span>
                <span style={styles.badge}>{episodes.length}</span>
              </div>
              {episodes.length === 0 ? (
                <div style={styles.empty}>No conversation episodes recorded yet</div>
              ) : (
                <div style={styles.episodeList}>
                  {episodes.map((ep) => (
                    <div key={ep.id} style={styles.episode}>
                      <div style={styles.episodeTime}>
                        {new Date(ep.startTime).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </div>
                      <div style={styles.episodeBody}>
                        <div style={styles.episodeSummary}>{ep.summary}</div>
                        {ep.topics.length > 0 && (
                          <div style={styles.topicRow}>
                            {ep.topics.slice(0, 3).map((t) => (
                              <span key={t} style={styles.topicChip}>{t}</span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div style={{
                        ...styles.sentimentDot,
                        background: ep.sentiment === 'positive' ? '#22c55e' :
                          ep.sentiment === 'negative' ? '#ef4444' : '#666680',
                      }} />
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Projects + Documents */}
            <div style={styles.sectionCard}>
              <div style={styles.sectionHeader}>
                <span style={styles.sectionTitle}>Workspace</span>
              </div>

              {projects.length > 0 && (
                <div style={styles.miniSection}>
                  <div style={styles.miniLabel}>Projects</div>
                  {projects.map((p) => (
                    <div key={p.rootPath} style={styles.workspaceItem}>
                      <span style={styles.workspaceIcon}>◆</span>
                      <span style={styles.workspaceName}>{p.name}</span>
                      <span style={styles.workspaceType}>{p.type}</span>
                    </div>
                  ))}
                </div>
              )}

              {documents.length > 0 && (
                <div style={styles.miniSection}>
                  <div style={styles.miniLabel}>
                    Documents <span style={styles.badge}>{documents.length}</span>
                  </div>
                  {documents.slice(0, 5).map((d) => (
                    <div key={d.id} style={styles.workspaceItem}>
                      <span style={styles.workspaceIcon}>◇</span>
                      <span style={styles.workspaceName}>{d.fileName}</span>
                      <span style={styles.workspaceType}>
                        {d.size > 1024 * 1024
                          ? `${(d.size / (1024 * 1024)).toFixed(1)}MB`
                          : `${Math.round(d.size / 1024)}KB`}
                      </span>
                    </div>
                  ))}
                  {documents.length > 5 && (
                    <div style={styles.moreLabel}>+{documents.length - 5} more</div>
                  )}
                </div>
              )}

              {projects.length === 0 && documents.length === 0 && (
                <div style={styles.empty}>
                  No projects or documents — ask EVE to watch a project or ingest a file
                </div>
              )}
            </div>
          </div>
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
    animation: 'fadeIn 0.2s ease',
  },
  panel: {
    width: 900,
    maxWidth: '95vw',
    maxHeight: '88vh',
    background: 'rgba(12, 12, 20, 0.98)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 20,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    boxShadow: '0 20px 60px rgba(0, 0, 0, 0.5), 0 0 40px rgba(0, 240, 255, 0.04)',
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
  headerIcon: {
    fontSize: 18,
    color: '#00f0ff',
  },
  headerTitle: {
    fontSize: 15,
    fontWeight: 700,
    color: '#e0e0e8',
    letterSpacing: '0.02em',
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
  content: {
    flex: 1,
    overflowY: 'auto',
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  topRow: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 16,
  },
  topLeft: {},
  topRight: {},
  bottomRow: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 16,
  },
  sectionCard: {
    background: 'rgba(255, 255, 255, 0.02)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 14,
    padding: '16px 18px',
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 700,
    color: '#888898',
    letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
  },
  badge: {
    fontSize: 10,
    color: '#555568',
    background: 'rgba(255, 255, 255, 0.05)',
    padding: '2px 7px',
    borderRadius: 8,
    fontWeight: 500,
  },
  empty: {
    fontSize: 12,
    color: '#555568',
    fontStyle: 'italic',
    padding: '12px 0',
    textAlign: 'center',
  },
  episodeList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    maxHeight: 200,
    overflowY: 'auto',
  },
  episode: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '8px 10px',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 8,
    border: '1px solid rgba(255, 255, 255, 0.04)',
  },
  episodeTime: {
    fontSize: 10,
    color: '#555568',
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
    paddingTop: 2,
  },
  episodeBody: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  episodeSummary: {
    fontSize: 12,
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
    letterSpacing: '0.02em',
  },
  sentimentDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    flexShrink: 0,
    marginTop: 4,
  },
  miniSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  miniLabel: {
    fontSize: 10,
    fontWeight: 600,
    color: '#555568',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    marginBottom: 2,
  },
  workspaceItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '5px 8px',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 6,
  },
  workspaceIcon: {
    fontSize: 10,
    color: '#00f0ff',
    flexShrink: 0,
  },
  workspaceName: {
    fontSize: 12,
    color: '#c0c0d0',
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  workspaceType: {
    fontSize: 10,
    color: '#555568',
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },
  moreLabel: {
    fontSize: 10,
    color: '#555568',
    textAlign: 'center',
    padding: '4px 0',
  },
};

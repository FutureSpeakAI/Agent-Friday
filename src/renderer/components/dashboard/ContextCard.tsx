import React from 'react';

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

interface ContextCardProps {
  ambient: AmbientState | null;
  sentiment: SentimentState | null;
  clipboard: ClipboardEntry | null;
}

const MOOD_EMOJI: Record<string, string> = {
  positive: ':-)',
  excited: ':D',
  curious: '?',
  focused: '>',
  neutral: '-',
  tired: '~',
  frustrated: '!',
  stressed: '!!',
};

const TASK_LABELS: Record<string, string> = {
  coding: 'Coding',
  writing: 'Writing',
  communicating: 'Communicating',
  researching: 'Researching',
  browsing: 'Browsing',
  designing: 'Designing',
  idle: 'Idle',
  unknown: 'General',
};

function formatDuration(ms: number): string {
  const mins = Math.floor(ms / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ${mins % 60}m`;
}

export default function ContextCard({ ambient, sentiment, clipboard }: ContextCardProps) {
  const topApps = ambient
    ? Object.entries(ambient.appDurations)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 4)
    : [];

  return (
    <div style={styles.card}>
      <div style={styles.cardHeader}>
        <span style={styles.cardTitle}>Current Context</span>
        <span style={styles.cardBadge}>LIVE</span>
      </div>

      <div style={styles.grid}>
        {/* Active task */}
        <div style={styles.stat}>
          <div style={styles.statLabel}>Task</div>
          <div style={styles.statValue}>
            {ambient ? TASK_LABELS[ambient.inferredTask] || ambient.inferredTask : '--'}
          </div>
        </div>

        {/* Mood */}
        <div style={styles.stat}>
          <div style={styles.statLabel}>Mood</div>
          <div style={styles.statValue}>
            <span style={styles.moodEmoji}>{MOOD_EMOJI[sentiment?.currentMood || 'neutral'] || '-'}</span>
            {sentiment ? sentiment.currentMood : '--'}
          </div>
        </div>

        {/* Energy */}
        <div style={styles.stat}>
          <div style={styles.statLabel}>Energy</div>
          <div style={styles.energyBar}>
            <div
              style={{
                ...styles.energyFill,
                width: `${(sentiment?.energyLevel || 0) * 100}%`,
                background:
                  (sentiment?.energyLevel || 0) > 0.6
                    ? '#22c55e'
                    : (sentiment?.energyLevel || 0) > 0.3
                      ? '#eab308'
                      : '#ef4444',
              }}
            />
          </div>
        </div>

        {/* Focus streak */}
        <div style={styles.stat}>
          <div style={styles.statLabel}>Focus</div>
          <div style={styles.statValue}>
            {ambient ? `${ambient.focusStreak}m streak` : '--'}
          </div>
        </div>
      </div>

      {/* Active app */}
      {ambient && ambient.activeApp && (
        <div style={styles.activeApp}>
          <span style={styles.activeAppLabel}>Active:</span>
          <span style={styles.activeAppName}>{ambient.activeApp}</span>
          {ambient.windowTitle && (
            <span style={styles.windowTitle}>
              {ambient.windowTitle.length > 50
                ? ambient.windowTitle.slice(0, 50) + '...'
                : ambient.windowTitle}
            </span>
          )}
        </div>
      )}

      {/* Top apps today */}
      {topApps.length > 0 && (
        <div style={styles.topApps}>
          {topApps.map(([app, duration]) => (
            <div key={app} style={styles.appChip}>
              <span style={styles.appName}>{app}</span>
              <span style={styles.appDuration}>{formatDuration(duration)}</span>
            </div>
          ))}
        </div>
      )}

      {/* Clipboard */}
      {clipboard && clipboard.type !== 'empty' && (
        <div style={styles.clipboardRow}>
          <span style={styles.clipboardType}>{clipboard.type}</span>
          <span style={styles.clipboardPreview}>
            {clipboard.preview.length > 60 ? clipboard.preview.slice(0, 60) + '...' : clipboard.preview}
          </span>
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    background: 'rgba(255, 255, 255, 0.02)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 14,
    padding: '16px 18px',
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
  },
  cardHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cardTitle: {
    fontSize: 12,
    fontWeight: 700,
    color: '#888898',
    letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
  },
  cardBadge: {
    fontSize: 9,
    fontWeight: 700,
    color: '#22c55e',
    background: 'rgba(34, 197, 94, 0.1)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    borderRadius: 4,
    padding: '2px 6px',
    letterSpacing: '0.08em',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: 12,
  },
  stat: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  statLabel: {
    fontSize: 10,
    color: '#555568',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
    fontWeight: 600,
  },
  statValue: {
    fontSize: 14,
    color: '#d0d0d8',
    fontWeight: 500,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  moodEmoji: {
    fontSize: 11,
    fontFamily: 'monospace',
    color: '#00f0ff',
  },
  energyBar: {
    height: 6,
    borderRadius: 3,
    background: 'rgba(255, 255, 255, 0.06)',
    overflow: 'hidden',
    marginTop: 4,
  },
  energyFill: {
    height: '100%',
    borderRadius: 3,
    transition: 'width 0.5s ease',
  },
  activeApp: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 12,
    padding: '8px 10px',
    background: 'rgba(0, 240, 255, 0.03)',
    borderRadius: 8,
    border: '1px solid rgba(0, 240, 255, 0.06)',
  },
  activeAppLabel: {
    color: '#555568',
    flexShrink: 0,
  },
  activeAppName: {
    color: '#00f0ff',
    fontWeight: 600,
    flexShrink: 0,
  },
  windowTitle: {
    color: '#666680',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  topApps: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap' as const,
  },
  appChip: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '4px 10px',
    background: 'rgba(255, 255, 255, 0.03)',
    borderRadius: 6,
    border: '1px solid rgba(255, 255, 255, 0.05)',
  },
  appName: {
    fontSize: 11,
    color: '#a0a0b0',
    fontWeight: 500,
  },
  appDuration: {
    fontSize: 10,
    color: '#555568',
    fontFamily: 'monospace',
  },
  clipboardRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 11,
    padding: '6px 10px',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 6,
    border: '1px solid rgba(255, 255, 255, 0.04)',
  },
  clipboardType: {
    fontSize: 9,
    fontWeight: 700,
    color: '#818cf8',
    background: 'rgba(129, 140, 248, 0.1)',
    padding: '2px 6px',
    borderRadius: 3,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
    flexShrink: 0,
  },
  clipboardPreview: {
    color: '#888898',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
    fontFamily: 'monospace',
  },
};

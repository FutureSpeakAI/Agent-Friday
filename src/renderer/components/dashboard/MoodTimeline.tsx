import React, { useState, useEffect } from 'react';

interface MoodPoint {
  mood: string;
  confidence: number;
  energyLevel: number;
  timestamp: number;
}

interface MoodTimelineProps {
  /** Fetch recent mood history from the backend */
  getMoodHistory: () => Promise<MoodPoint[]>;
}

const MOOD_COLORS: Record<string, string> = {
  positive: '#22c55e',
  excited: '#f59e0b',
  curious: '#818cf8',
  focused: '#00f0ff',
  neutral: '#666680',
  tired: '#a78bfa',
  frustrated: '#f87171',
  stressed: '#ef4444',
};

const MOOD_Y: Record<string, number> = {
  excited: 0,
  positive: 1,
  curious: 2,
  focused: 3,
  neutral: 4,
  tired: 5,
  frustrated: 6,
  stressed: 7,
};

const CHART_HEIGHT = 100;
const CHART_PADDING = 12;
const DOT_R = 4;

function moodToY(mood: string): number {
  const rank = MOOD_Y[mood] ?? 4;
  const usable = CHART_HEIGHT - CHART_PADDING * 2;
  return CHART_PADDING + (rank / 7) * usable;
}

function formatTime(ts: number): string {
  const d = new Date(ts);
  const h = d.getHours();
  const m = d.getMinutes();
  const ampm = h >= 12 ? 'pm' : 'am';
  return `${h % 12 || 12}:${m.toString().padStart(2, '0')}${ampm}`;
}

export default function MoodTimeline({ getMoodHistory }: MoodTimelineProps) {
  const [points, setPoints] = useState<MoodPoint[]>([]);
  const [hovered, setHovered] = useState<number | null>(null);

  useEffect(() => {
    getMoodHistory()
      .then((data) => setPoints(data.slice(-24))) // Last 24 data points
      .catch(() => {});

    const interval = setInterval(() => {
      getMoodHistory()
        .then((data) => setPoints(data.slice(-24)))
        .catch(() => {});
    }, 30_000);

    return () => clearInterval(interval);
  }, [getMoodHistory]);

  if (points.length < 2) {
    return (
      <div style={styles.card}>
        <div style={styles.cardHeader}>
          <span style={styles.cardTitle}>Mood Timeline</span>
        </div>
        <div style={styles.empty}>
          Not enough data yet — mood tracking builds over time
        </div>
      </div>
    );
  }

  const width = Math.max(300, points.length * 28);

  // Build SVG path
  const pathParts: string[] = [];
  const dotPositions: Array<{ x: number; y: number; mood: string; ts: number; energy: number }> = [];

  points.forEach((pt, i) => {
    const x = CHART_PADDING + (i / (points.length - 1)) * (width - CHART_PADDING * 2);
    const y = moodToY(pt.mood);
    dotPositions.push({ x, y, mood: pt.mood, ts: pt.timestamp, energy: pt.energyLevel });

    if (i === 0) {
      pathParts.push(`M ${x} ${y}`);
    } else {
      // Smooth curve
      const prev = dotPositions[i - 1];
      const cpx = (prev.x + x) / 2;
      pathParts.push(`C ${cpx} ${prev.y}, ${cpx} ${y}, ${x} ${y}`);
    }
  });

  const gradientId = 'mood-gradient';

  return (
    <div style={styles.card}>
      <div style={styles.cardHeader}>
        <span style={styles.cardTitle}>Mood Timeline</span>
        <span style={styles.timeRange}>
          {formatTime(points[0].timestamp)} — {formatTime(points[points.length - 1].timestamp)}
        </span>
      </div>

      <div style={styles.chartContainer}>
        <svg
          width={width}
          height={CHART_HEIGHT}
          viewBox={`0 0 ${width} ${CHART_HEIGHT}`}
          style={styles.svg}
        >
          {/* Gradient definition */}
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="0">
              {dotPositions.map((dp, i) => (
                <stop
                  key={i}
                  offset={`${(i / (dotPositions.length - 1)) * 100}%`}
                  stopColor={MOOD_COLORS[dp.mood] || '#666680'}
                />
              ))}
            </linearGradient>
          </defs>

          {/* Horizontal mood-level guides */}
          {[0, 2, 4, 6].map((rank) => {
            const y = CHART_PADDING + (rank / 7) * (CHART_HEIGHT - CHART_PADDING * 2);
            return (
              <line
                key={rank}
                x1={CHART_PADDING}
                y1={y}
                x2={width - CHART_PADDING}
                y2={y}
                stroke="rgba(255,255,255,0.04)"
                strokeDasharray="4 4"
              />
            );
          })}

          {/* Main curve */}
          <path
            d={pathParts.join(' ')}
            fill="none"
            stroke={`url(#${gradientId})`}
            strokeWidth={2}
            strokeLinecap="round"
          />

          {/* Dots */}
          {dotPositions.map((dp, i) => (
            <circle
              key={i}
              cx={dp.x}
              cy={dp.y}
              r={hovered === i ? DOT_R + 2 : DOT_R}
              fill={MOOD_COLORS[dp.mood] || '#666680'}
              stroke="rgba(0,0,0,0.3)"
              strokeWidth={1}
              style={{ cursor: 'pointer', transition: 'r 0.15s' }}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            />
          ))}
        </svg>

        {/* Tooltip */}
        {hovered !== null && dotPositions[hovered] && (
          <div
            style={{
              ...styles.tooltip,
              left: dotPositions[hovered].x,
              top: dotPositions[hovered].y - 36,
            }}
          >
            <span style={{ color: MOOD_COLORS[dotPositions[hovered].mood] }}>
              {dotPositions[hovered].mood}
            </span>
            {' · '}
            <span style={styles.tooltipTime}>{formatTime(dotPositions[hovered].ts)}</span>
            {' · '}
            <span style={styles.tooltipEnergy}>
              E:{Math.round(dotPositions[hovered].energy * 100)}%
            </span>
          </div>
        )}
      </div>

      {/* Mood legend */}
      <div style={styles.legend}>
        {Object.entries(MOOD_COLORS).slice(0, 6).map(([mood, color]) => (
          <div key={mood} style={styles.legendItem}>
            <span style={{ ...styles.legendDot, background: color }} />
            <span style={styles.legendLabel}>{mood}</span>
          </div>
        ))}
      </div>
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
    gap: 12,
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
  timeRange: {
    fontSize: 10,
    color: '#555568',
    fontFamily: "'JetBrains Mono', monospace",
  },
  chartContainer: {
    position: 'relative',
    overflowX: 'auto',
    overflowY: 'hidden',
  },
  svg: {
    display: 'block',
  },
  tooltip: {
    position: 'absolute',
    transform: 'translateX(-50%)',
    background: 'rgba(10, 10, 18, 0.95)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 6,
    padding: '4px 8px',
    fontSize: 10,
    color: '#d0d0d8',
    whiteSpace: 'nowrap' as const,
    pointerEvents: 'none' as const,
    zIndex: 10,
  },
  tooltipTime: {
    color: '#888898',
  },
  tooltipEnergy: {
    color: '#818cf8',
  },
  legend: {
    display: 'flex',
    gap: 12,
    flexWrap: 'wrap' as const,
    justifyContent: 'center',
  },
  legendItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  legendDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
  },
  legendLabel: {
    fontSize: 10,
    color: '#555568',
    textTransform: 'capitalize' as const,
  },
  empty: {
    fontSize: 12,
    color: '#555568',
    fontStyle: 'italic',
    padding: '16px 0',
    textAlign: 'center',
  },
};

import React from 'react';

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

interface AgentCardProps {
  tasks: AgentTask[];
  onCancel: (id: string) => void;
}

const STATUS_STYLES: Record<string, { color: string; bg: string; border: string }> = {
  queued: { color: '#eab308', bg: 'rgba(234, 179, 8, 0.08)', border: 'rgba(234, 179, 8, 0.2)' },
  running: { color: '#00f0ff', bg: 'rgba(0, 240, 255, 0.08)', border: 'rgba(0, 240, 255, 0.2)' },
  completed: { color: '#22c55e', bg: 'rgba(34, 197, 94, 0.08)', border: 'rgba(34, 197, 94, 0.2)' },
  failed: { color: '#ef4444', bg: 'rgba(239, 68, 68, 0.08)', border: 'rgba(239, 68, 68, 0.2)' },
  cancelled: { color: '#666680', bg: 'rgba(102, 102, 128, 0.08)', border: 'rgba(102, 102, 128, 0.2)' },
};

function timeAgo(ms: number): string {
  const seconds = Math.floor((Date.now() - ms) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ago`;
}

export default function AgentCard({ tasks, onCancel }: AgentCardProps) {
  const activeTasks = tasks.filter((t) => t.status === 'running' || t.status === 'queued');
  const recentDone = tasks
    .filter((t) => t.status === 'completed' || t.status === 'failed')
    .slice(0, 3);

  return (
    <div style={styles.card}>
      <div style={styles.cardHeader}>
        <span style={styles.cardTitle}>Background Agents</span>
        {activeTasks.length > 0 && (
          <span style={styles.activeBadge}>{activeTasks.length} active</span>
        )}
      </div>

      {tasks.length === 0 ? (
        <div style={styles.empty}>No agents running — ask EVE to research something</div>
      ) : (
        <div style={styles.taskList}>
          {activeTasks.map((task) => {
            const s = STATUS_STYLES[task.status];
            return (
              <div key={task.id} style={styles.task}>
                <div style={styles.taskTop}>
                  <span style={{ ...styles.statusDot, background: s.color }} />
                  <span style={styles.taskType}>{task.agentType}</span>
                  <span style={styles.taskDesc}>{task.description}</span>
                  {task.status === 'running' && (
                    <button onClick={() => onCancel(task.id)} style={styles.cancelBtn}>
                      Cancel
                    </button>
                  )}
                </div>
                {task.status === 'running' && (
                  <div style={styles.progressBar}>
                    <div
                      style={{
                        ...styles.progressFill,
                        width: `${task.progress}%`,
                      }}
                    />
                  </div>
                )}
                {task.logs.length > 0 && (
                  <div style={styles.lastLog}>
                    {task.logs[task.logs.length - 1]}
                  </div>
                )}
              </div>
            );
          })}

          {recentDone.map((task) => {
            const s = STATUS_STYLES[task.status];
            return (
              <div key={task.id} style={{ ...styles.task, opacity: 0.6 }}>
                <div style={styles.taskTop}>
                  <span style={{ ...styles.statusDot, background: s.color }} />
                  <span style={styles.taskType}>{task.agentType}</span>
                  <span style={styles.taskDesc}>{task.description}</span>
                  <span style={styles.taskTime}>{timeAgo(task.completedAt || task.createdAt)}</span>
                </div>
                {task.status === 'failed' && task.error && (
                  <div style={styles.errorMsg}>{task.error}</div>
                )}
                {task.status === 'completed' && task.result && (
                  <div style={styles.resultPreview}>
                    {task.result.length > 120 ? task.result.slice(0, 120) + '...' : task.result}
                  </div>
                )}
              </div>
            );
          })}
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
  activeBadge: {
    fontSize: 10,
    fontWeight: 600,
    color: '#00f0ff',
    background: 'rgba(0, 240, 255, 0.1)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    borderRadius: 4,
    padding: '2px 8px',
  },
  empty: {
    fontSize: 12,
    color: '#555568',
    fontStyle: 'italic',
    padding: '8px 0',
  },
  taskList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  task: {
    padding: '10px 12px',
    background: 'rgba(255, 255, 255, 0.02)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 10,
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  taskTop: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    flexShrink: 0,
  },
  taskType: {
    fontSize: 10,
    fontWeight: 700,
    color: '#818cf8',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
    flexShrink: 0,
  },
  taskDesc: {
    fontSize: 12,
    color: '#c0c0d0',
    flex: 1,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  taskTime: {
    fontSize: 10,
    color: '#555568',
    flexShrink: 0,
  },
  cancelBtn: {
    fontSize: 10,
    color: '#ef4444',
    background: 'rgba(239, 68, 68, 0.08)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 4,
    padding: '2px 8px',
    cursor: 'pointer',
    flexShrink: 0,
  },
  progressBar: {
    height: 3,
    borderRadius: 2,
    background: 'rgba(255, 255, 255, 0.06)',
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
    background: 'linear-gradient(90deg, #00f0ff, #818cf8)',
    transition: 'width 0.3s ease',
  },
  lastLog: {
    fontSize: 10,
    color: '#666680',
    fontFamily: "'JetBrains Mono', monospace",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  errorMsg: {
    fontSize: 11,
    color: '#ef4444',
    fontFamily: "'JetBrains Mono', monospace",
  },
  resultPreview: {
    fontSize: 11,
    color: '#888898',
    lineHeight: 1.4,
  },
};

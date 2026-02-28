/**
 * AgentDashboard.tsx — Overlay panel showing background agent tasks.
 *
 * Displays all agent tasks with status badges, progress bars,
 * expandable logs, and cancel/retry actions.
 */

import React, { useState, useEffect, useCallback } from 'react';

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
  parentId?: string;
}

interface AgentDashboardProps {
  visible: boolean;
  onClose: () => void;
}

const STATUS_COLOURS: Record<string, string> = {
  queued: '#818cf8',
  running: '#00f0ff',
  completed: '#4ade80',
  failed: '#f87171',
  cancelled: '#888',
};

const STATUS_LABELS: Record<string, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export default function AgentDashboard({ visible, onClose }: AgentDashboardProps) {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [agentTypes, setAgentTypes] = useState<Array<{ name: string; description: string }>>([]);

  // Load tasks and agent types
  useEffect(() => {
    if (!visible) return;

    window.eve.agents.list().then(setTasks).catch(() => {});
    window.eve.agents.getTypes().then(setAgentTypes).catch(() => {});
  }, [visible]);

  // Listen for real-time task updates
  useEffect(() => {
    if (!visible) return;

    const cleanup = window.eve.agents.onUpdate((update: AgentTask) => {
      setTasks((prev) => {
        const idx = prev.findIndex((t) => t.id === update.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = update;
          return next;
        }
        return [update, ...prev];
      });
    });

    return cleanup;
  }, [visible]);

  const handleCancel = useCallback(async (taskId: string) => {
    await window.eve.agents.cancel(taskId);
  }, []);

  const formatTime = useCallback((ts: number) => {
    return new Date(ts).toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }, []);

  const formatDuration = useCallback((ms: number) => {
    const secs = Math.round(ms / 1000);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    const remainSecs = secs % 60;
    return `${mins}m ${remainSecs}s`;
  }, []);

  if (!visible) return null;

  const activeTasks = tasks.filter((t) => t.status === 'running' || t.status === 'queued');
  const completedTasks = tasks.filter(
    (t) => t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled'
  );

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.panel} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <div style={styles.headerIcon}>⚡</div>
            <div>
              <div style={styles.title}>Background Agents</div>
              <div style={styles.subtitle}>
                {activeTasks.length} active · {completedTasks.length} completed
              </div>
            </div>
          </div>
          <button style={styles.closeBtn} onClick={onClose} className="hover-bright">
            ✕
          </button>
        </div>

        {/* Agent types reference */}
        {agentTypes.length > 0 && (
          <div style={styles.typesBar}>
            {agentTypes.map((t) => (
              <span key={t.name} style={styles.typeChip} title={t.description}>
                {t.name}
              </span>
            ))}
          </div>
        )}

        {/* Task list */}
        <div style={styles.taskList}>
          {tasks.length === 0 && (
            <div style={styles.emptyState}>
              No agent tasks yet. Ask Friday to research something or review code to spawn a
              background agent.
            </div>
          )}

          {tasks.map((task) => {
            const isExpanded = expandedId === task.id;
            const colour = STATUS_COLOURS[task.status] || '#888';
            const duration =
              task.completedAt && task.startedAt
                ? formatDuration(task.completedAt - task.startedAt)
                : task.startedAt
                  ? formatDuration(Date.now() - task.startedAt)
                  : '';

            return (
              <div
                key={task.id}
                style={{
                  ...styles.taskCard,
                  borderColor: `${colour}22`,
                }}
              >
                {/* Task header row */}
                <div
                  style={styles.taskHeader}
                  onClick={() => setExpandedId(isExpanded ? null : task.id)}
                >
                  <div style={styles.taskMeta}>
                    <span
                      style={{
                        ...styles.statusBadge,
                        background: `${colour}18`,
                        color: colour,
                        borderColor: `${colour}30`,
                      }}
                    >
                      {STATUS_LABELS[task.status]}
                    </span>
                    <span style={styles.taskType}>{task.agentType}</span>
                    <span style={styles.taskTime}>{formatTime(task.createdAt)}</span>
                    {duration && <span style={styles.taskDuration}>{duration}</span>}
                  </div>
                  <span style={styles.expandIcon}>{isExpanded ? '▾' : '▸'}</span>
                </div>

                {/* Description */}
                <div style={styles.taskDesc}>{task.description}</div>

                {/* Progress bar */}
                {(task.status === 'running' || task.status === 'queued') && (
                  <div style={styles.progressTrack}>
                    <div
                      style={{
                        ...styles.progressFill,
                        width: `${task.progress}%`,
                        background: colour,
                      }}
                    />
                    <span style={styles.progressText}>{task.progress}%</span>
                  </div>
                )}

                {/* Expanded details */}
                {isExpanded && (
                  <div style={styles.expandedSection}>
                    {/* Logs */}
                    {task.logs.length > 0 && (
                      <div style={styles.logsContainer}>
                        <div style={styles.logsTitle}>Logs</div>
                        {task.logs.map((log, i) => (
                          <div key={i} style={styles.logLine}>
                            {log}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Result */}
                    {task.result && (
                      <div style={styles.resultContainer}>
                        <div style={styles.resultTitle}>Result</div>
                        <div style={styles.resultText}>{task.result}</div>
                      </div>
                    )}

                    {/* Error */}
                    {task.error && (
                      <div style={styles.errorContainer}>
                        <div style={styles.errorTitle}>Error</div>
                        <div style={styles.errorText}>{task.error}</div>
                      </div>
                    )}

                    {/* Actions */}
                    <div style={styles.taskActions}>
                      {(task.status === 'running' || task.status === 'queued') && (
                        <button
                          style={styles.cancelBtn}
                          onClick={() => handleCancel(task.id)}
                          className="hover-bright"
                        >
                          Cancel
                        </button>
                      )}
                      <span style={styles.taskId}>{task.id.slice(0, 8)}</span>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'center',
    paddingTop: 60,
    zIndex: 55,
    background: 'rgba(0, 0, 0, 0.45)',
    backdropFilter: 'blur(6px)',
    animation: 'fadeIn 0.2s ease',
  },
  panel: {
    background: 'rgba(12, 12, 22, 0.97)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 16,
    width: '90%',
    maxWidth: 640,
    maxHeight: '80vh',
    display: 'flex',
    flexDirection: 'column',
    boxShadow: '0 12px 48px rgba(0, 0, 0, 0.5), 0 0 30px rgba(0, 240, 255, 0.06)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '18px 22px 14px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
  },
  headerLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  headerIcon: {
    fontSize: 22,
  },
  title: {
    fontSize: 15,
    fontWeight: 700,
    color: '#e0e0f0',
    letterSpacing: '0.02em',
  },
  subtitle: {
    fontSize: 11,
    color: '#666680',
    marginTop: 2,
  },
  closeBtn: {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 6,
    color: '#888',
    fontSize: 13,
    width: 28,
    height: 28,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  typesBar: {
    display: 'flex',
    gap: 6,
    padding: '10px 22px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
    flexWrap: 'wrap',
  },
  typeChip: {
    fontSize: 10,
    color: '#00f0ff',
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 10,
    padding: '3px 10px',
    fontFamily: "'JetBrains Mono', monospace",
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
  },
  taskList: {
    flex: 1,
    overflowY: 'auto',
    padding: '12px 16px',
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  emptyState: {
    textAlign: 'center',
    color: '#555570',
    fontSize: 13,
    padding: '40px 20px',
    lineHeight: '1.6',
  },
  taskCard: {
    background: 'rgba(255, 255, 255, 0.02)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 10,
    padding: '12px 14px',
    transition: 'border-color 0.2s',
  },
  taskHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    cursor: 'pointer',
  },
  taskMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexWrap: 'wrap',
  },
  statusBadge: {
    fontSize: 10,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 6,
    border: '1px solid',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    fontFamily: "'JetBrains Mono', monospace",
  },
  taskType: {
    fontSize: 11,
    color: '#888',
    fontFamily: "'JetBrains Mono', monospace",
  },
  taskTime: {
    fontSize: 10,
    color: '#555',
  },
  taskDuration: {
    fontSize: 10,
    color: '#666',
    fontFamily: "'JetBrains Mono', monospace",
  },
  expandIcon: {
    color: '#555',
    fontSize: 12,
  },
  taskDesc: {
    fontSize: 13,
    color: '#c0c0d0',
    marginTop: 6,
    lineHeight: '1.4',
  },
  progressTrack: {
    position: 'relative',
    height: 4,
    background: 'rgba(255, 255, 255, 0.06)',
    borderRadius: 2,
    marginTop: 10,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    borderRadius: 2,
    transition: 'width 0.4s ease',
    opacity: 0.8,
  },
  progressText: {
    position: 'absolute',
    right: 0,
    top: -16,
    fontSize: 10,
    color: '#666',
    fontFamily: "'JetBrains Mono', monospace",
  },
  expandedSection: {
    marginTop: 12,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    borderTop: '1px solid rgba(255, 255, 255, 0.04)',
    paddingTop: 10,
  },
  logsContainer: {
    background: 'rgba(0, 0, 0, 0.25)',
    borderRadius: 8,
    padding: '8px 10px',
    maxHeight: 150,
    overflowY: 'auto',
  },
  logsTitle: {
    fontSize: 10,
    color: '#666',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 4,
  },
  logLine: {
    fontSize: 11,
    color: '#888',
    fontFamily: "'JetBrains Mono', monospace",
    lineHeight: '1.6',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  resultContainer: {
    background: 'rgba(74, 222, 128, 0.04)',
    border: '1px solid rgba(74, 222, 128, 0.1)',
    borderRadius: 8,
    padding: '10px 12px',
    maxHeight: 250,
    overflowY: 'auto',
  },
  resultTitle: {
    fontSize: 10,
    color: '#4ade80',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 6,
  },
  resultText: {
    fontSize: 12,
    color: '#c0d0c0',
    lineHeight: '1.6',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
  errorContainer: {
    background: 'rgba(248, 113, 113, 0.06)',
    border: '1px solid rgba(248, 113, 113, 0.15)',
    borderRadius: 8,
    padding: '10px 12px',
  },
  errorTitle: {
    fontSize: 10,
    color: '#f87171',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.06em',
    marginBottom: 4,
  },
  errorText: {
    fontSize: 12,
    color: '#e0a0a0',
    lineHeight: '1.5',
    fontFamily: "'JetBrains Mono', monospace",
  },
  taskActions: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cancelBtn: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 6,
    color: '#ef4444',
    fontSize: 11,
    fontWeight: 600,
    padding: '4px 14px',
    cursor: 'pointer',
  },
  taskId: {
    fontSize: 10,
    color: '#444',
    fontFamily: "'JetBrains Mono', monospace",
  },
};

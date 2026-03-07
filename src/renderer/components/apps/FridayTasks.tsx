/**
 * FridayTasks.tsx — Agent Friday Task Scheduler & Commitments
 *
 * Manage scheduled tasks and interpersonal commitments.
 * Create, view, and cancel scheduled actions. Track commitments
 * with due dates and status.
 *
 * IPC: window.eve.scheduler.*, window.eve.commitments.*
 */

import React, { useState, useEffect, useCallback } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

interface ScheduledTask {
  id: string;
  description: string;
  type: 'once' | 'recurring';
  cronPattern?: string;
  triggerTime?: number;
  action: string;
  payload?: string;
  enabled: boolean;
  createdAt?: number;
  lastTriggered?: number;
}

interface Commitment {
  id: string;
  personName: string;
  description: string;
  deadline: number | null;
  status?: string;
  createdAt?: number;
}

interface Props {
  visible: boolean;
  onClose: () => void;
}

type Tab = 'tasks' | 'commitments' | 'create';

export default function FridayTasks({ visible, onClose }: Props) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('tasks');
  const [cancelling, setCancelling] = useState<string | null>(null);

  // Create task form
  const [newDesc, setNewDesc] = useState('');
  const [newCron, setNewCron] = useState('');
  const [newRunAt, setNewRunAt] = useState('');
  const [newAction, setNewAction] = useState('reminder');
  const [creating, setCreating] = useState(false);

  // Create commitment form
  const [cmPerson, setCmPerson] = useState('');
  const [cmDesc, setCmDesc] = useState('');
  const [cmDue, setCmDue] = useState('');
  const [creatingCm, setCreatingCm] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tasksRes, commitmentsRes] = await Promise.all([
        (window as any).eve.scheduler.listTasks(),
        (window as any).eve.commitments.getAll(),
      ]);
      setTasks(Array.isArray(tasksRes) ? tasksRes : []);
      setCommitments(Array.isArray(commitmentsRes) ? commitmentsRes : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) loadData();
  }, [visible, loadData]);

  const handleCancel = async (taskId: string) => {
    setCancelling(taskId);
    try {
      await (window as any).eve.scheduler.deleteTask(taskId);
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to cancel task');
    } finally {
      setCancelling(null);
    }
  };

  const handleCreateTask = async () => {
    if (!newDesc.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await (window as any).eve.scheduler.createTask({
        description: newDesc.trim(),
        type: newCron.trim() ? 'recurring' : 'once',
        cron_pattern: newCron.trim() || undefined,
        trigger_time: newRunAt ? new Date(newRunAt).getTime() : undefined,
        action: newAction === 'reminder' ? 'remind' : newAction === 'notification' ? 'remind' : newAction,
        payload: '',
      });
      setNewDesc('');
      setNewCron('');
      setNewRunAt('');
      setNewAction('reminder');
      setActiveTab('tasks');
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to create task');
    } finally {
      setCreating(false);
    }
  };

  const handleCreateCommitment = async () => {
    if (!cmPerson.trim() || !cmDesc.trim()) return;
    setCreatingCm(true);
    setError(null);
    try {
      await (window as any).eve.commitments.add({
        description: cmDesc.trim(),
        personName: cmPerson.trim(),
        direction: 'user_promised' as const,
        source: 'manual' as const,
        deadline: cmDue ? new Date(cmDue).getTime() : null,
        domain: 'general',
        confidence: 1.0,
        contextSnippet: 'Manual entry via Tasks app',
      });
      setCmPerson('');
      setCmDesc('');
      setCmDue('');
      setActiveTab('commitments');
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to create commitment');
    } finally {
      setCreatingCm(false);
    }
  };

  const getStatusColor = (status?: string): string => {
    switch (status) {
      case 'active': case 'running': return '#22c55e';
      case 'overdue': case 'failed': return '#ef4444';
      case 'complete': case 'done': return '#00f0ff';
      case 'pending': return '#f97316';
      default: return '#8888a0';
    }
  };

  const overdueCommitments = commitments.filter((c) => c.status === 'overdue');
  const upcomingCommitments = commitments.filter(
    (c) => c.status !== 'overdue' && c.status !== 'completed'
  );
  const completedCommitments = commitments.filter((c) => c.status === 'completed');

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: 'tasks', label: 'Scheduled Tasks', count: tasks.length },
    { key: 'commitments', label: 'Commitments', count: commitments.length },
    { key: 'create', label: '+ Create' },
  ];

  return (
    <AppShell visible={visible} onClose={onClose} title="Tasks" icon="✅" width={920}>
      <ContextBar appId="friday-tasks" />
      {/* Tab bar */}
      <div style={s.tabBar}>
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
            {t.count !== undefined && (
              <span style={s.tabBadge}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {error && (
        <div style={s.errorBar}>
          <span>{error}</span>
          <button style={s.dismissBtn} onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      {loading ? (
        <div style={s.center}>
          <span style={s.spinner}>⟳</span>
          <span style={s.secondaryText}>Loading tasks...</span>
        </div>
      ) : activeTab === 'tasks' ? (
        /* ── Scheduled Tasks ── */
        <div style={s.listContainer}>
          {tasks.length === 0 ? (
            <div style={s.emptyState}>
              <div style={s.emptyIcon}>📋</div>
              <div style={s.mutedText}>No scheduled tasks</div>
              <button style={s.createBtn} onClick={() => setActiveTab('create')}>
                Create Task
              </button>
            </div>
          ) : (
            tasks.map((t) => (
              <div key={t.id} style={s.taskCard}>
                <div style={s.taskHeader}>
                  <div style={{ flex: 1 }}>
                    <div style={s.taskTitle}>{t.description}</div>
                    <div style={s.taskMeta}>
                      <span style={s.monoSmall}>{t.action}</span>
                      <span style={s.monoSmall}>{t.type}</span>
                      {t.cronPattern && (
                        <span style={s.monoSmall}>cron: {t.cronPattern}</span>
                      )}
                      {t.triggerTime && (
                        <span style={s.monoSmall}>
                          at: {new Date(t.triggerTime).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      )}
                    </div>
                  </div>
                  <div style={s.taskActions}>
                    <span
                      style={{
                        ...s.statusPill,
                        color: t.enabled ? '#22c55e' : '#8888a0',
                        borderColor: t.enabled ? '#22c55e44' : '#8888a044',
                      }}
                    >
                      {t.enabled ? 'active' : 'disabled'}
                    </span>
                    <button
                      style={s.cancelBtn}
                      onClick={() => handleCancel(t.id)}
                      disabled={cancelling === t.id}
                      title="Cancel task"
                    >
                      {cancelling === t.id ? '...' : '✕'}
                    </button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      ) : activeTab === 'commitments' ? (
        /* ── Commitments ── */
        <div style={s.listContainer}>
          {overdueCommitments.length > 0 && (
            <div style={s.groupSection}>
              <div style={{ ...s.groupTitle, color: '#ef4444' }}>
                Overdue ({overdueCommitments.length})
              </div>
              {overdueCommitments.map((c) => (
                <CommitmentRow key={c.id} commitment={c} />
              ))}
            </div>
          )}
          {upcomingCommitments.length > 0 && (
            <div style={s.groupSection}>
              <div style={{ ...s.groupTitle, color: '#f97316' }}>
                Active / Upcoming ({upcomingCommitments.length})
              </div>
              {upcomingCommitments.map((c) => (
                <CommitmentRow key={c.id} commitment={c} />
              ))}
            </div>
          )}
          {completedCommitments.length > 0 && (
            <div style={s.groupSection}>
              <div style={{ ...s.groupTitle, color: '#22c55e' }}>
                Completed ({completedCommitments.length})
              </div>
              {completedCommitments.map((c) => (
                <CommitmentRow key={c.id} commitment={c} />
              ))}
            </div>
          )}
          {commitments.length === 0 && (
            <div style={s.emptyState}>
              <div style={s.emptyIcon}>🤝</div>
              <div style={s.mutedText}>No commitments tracked</div>
              <button style={s.createBtn} onClick={() => setActiveTab('create')}>
                Add Commitment
              </button>
            </div>
          )}
        </div>
      ) : (
        /* ── Create Tab ── */
        <div style={s.createContainer}>
          {/* Create Task Form */}
          <div style={s.formCard}>
            <div style={s.formTitle}>New Scheduled Task</div>
            <input
              style={s.input}
              placeholder="Task description..."
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
            />
            <div style={s.formRow}>
              <div style={s.formField}>
                <label style={s.label}>Cron Expression</label>
                <input
                  style={s.input}
                  placeholder="e.g. 0 9 * * 1-5"
                  value={newCron}
                  onChange={(e) => setNewCron(e.target.value)}
                />
              </div>
              <div style={s.formField}>
                <label style={s.label}>Or Run At</label>
                <input
                  style={s.input}
                  type="datetime-local"
                  value={newRunAt}
                  onChange={(e) => setNewRunAt(e.target.value)}
                />
              </div>
            </div>
            <div style={s.formField}>
              <label style={s.label}>Action Type</label>
              <select
                style={s.select}
                value={newAction}
                onChange={(e) => setNewAction(e.target.value)}
              >
                <option value="reminder">Reminder</option>
                <option value="email">Email</option>
                <option value="webhook">Webhook</option>
                <option value="script">Script</option>
                <option value="notification">Notification</option>
              </select>
            </div>
            <button
              style={s.submitBtn}
              onClick={handleCreateTask}
              disabled={creating || !newDesc.trim()}
            >
              {creating ? 'Creating...' : 'Create Task'}
            </button>
          </div>

          {/* Create Commitment Form */}
          <div style={s.formCard}>
            <div style={s.formTitle}>New Commitment</div>
            <input
              style={s.input}
              placeholder="Person name..."
              value={cmPerson}
              onChange={(e) => setCmPerson(e.target.value)}
            />
            <input
              style={s.input}
              placeholder="What was committed..."
              value={cmDesc}
              onChange={(e) => setCmDesc(e.target.value)}
            />
            <div style={s.formField}>
              <label style={s.label}>Due Date (optional)</label>
              <input
                style={s.input}
                type="date"
                value={cmDue}
                onChange={(e) => setCmDue(e.target.value)}
              />
            </div>
            <button
              style={s.submitBtn}
              onClick={handleCreateCommitment}
              disabled={creatingCm || !cmPerson.trim() || !cmDesc.trim()}
            >
              {creatingCm ? 'Creating...' : 'Add Commitment'}
            </button>
          </div>
        </div>
      )}
    </AppShell>
  );
}

/* ── Sub-component ──────────────────────────────────────────────────────── */
function CommitmentRow({ commitment: c }: { commitment: Commitment }) {
  const color =
    c.status === 'overdue'
      ? '#ef4444'
      : c.status === 'completed'
      ? '#22c55e'
      : '#f97316';
  const deadlineStr = c.deadline
    ? new Date(c.deadline).toLocaleDateString([], { month: 'short', day: 'numeric' })
    : null;
  return (
    <div style={s.commitmentCard}>
      <div style={{ ...s.commitDot, background: color }} />
      <div style={{ flex: 1 }}>
        <div style={s.primaryText}>{c.description}</div>
        <div style={s.commitMeta}>
          <span style={s.secondaryText}>👤 {c.personName}</span>
          {deadlineStr && <span style={s.mutedText}>Due: {deadlineStr}</span>}
        </div>
      </div>
      <span style={{ ...s.statusPill, color, borderColor: `${color}44` }}>
        {c.status || 'pending'}
      </span>
    </div>
  );
}

/* ── Styles ─────────────────────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  tabBar: {
    display: 'flex',
    gap: 4,
    borderBottom: '1px solid rgba(255,255,255,0.07)',
    paddingBottom: 12,
    marginBottom: 4,
  },
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
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0,240,255,0.08)',
    borderColor: 'rgba(0,240,255,0.3)',
    color: '#00f0ff',
  },
  tabBadge: {
    fontSize: 10,
    fontWeight: 700,
    background: 'rgba(255,255,255,0.06)',
    borderRadius: 10,
    padding: '1px 6px',
    fontFamily: "'JetBrains Mono', monospace",
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
  listContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    flex: 1,
  },
  taskCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '12px 14px',
  },
  taskHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 12,
  },
  taskTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 4,
  },
  taskMeta: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 8,
  },
  monoSmall: {
    fontSize: 11,
    color: '#8A2BE2',
    fontFamily: "'JetBrains Mono', monospace",
    background: 'rgba(138,43,226,0.1)',
    padding: '1px 6px',
    borderRadius: 4,
  },
  taskActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    flexShrink: 0,
  },
  statusPill: {
    fontSize: 11,
    fontWeight: 600,
    border: '1px solid',
    borderRadius: 6,
    padding: '2px 8px',
    textTransform: 'capitalize' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  cancelBtn: {
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 6,
    width: 26,
    height: 26,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: '#ef4444',
    fontSize: 12,
    cursor: 'pointer',
  },
  groupSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    marginBottom: 8,
  },
  groupTitle: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.05em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
    padding: '4px 0',
  },
  commitmentCard: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '10px 14px',
  },
  commitDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    marginTop: 5,
    flexShrink: 0,
  },
  commitMeta: {
    display: 'flex',
    gap: 12,
    marginTop: 2,
  },
  createContainer: {
    display: 'flex',
    gap: 16,
    flex: 1,
  },
  formCard: {
    flex: 1,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
  },
  formTitle: {
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 700,
    letterSpacing: '0.03em',
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 4,
  },
  formRow: {
    display: 'flex',
    gap: 10,
  },
  formField: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  input: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
  },
  select: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
  },
  label: {
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  submitBtn: {
    marginTop: 4,
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '8px 20px',
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    alignSelf: 'flex-start',
  },
  createBtn: {
    marginTop: 8,
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '6px 16px',
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 8,
    padding: 40,
  },
  emptyIcon: { fontSize: 32, opacity: 0.5 },
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
  primaryText: {
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  secondaryText: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  mutedText: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
};

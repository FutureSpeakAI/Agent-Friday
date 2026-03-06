/**
 * FridayCalendar.tsx — Google Calendar integration for Agent Friday
 *
 * IPC: window.eve.calendar.*
 * Features: Auth, today's schedule, upcoming events, create/delete events
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

interface CalendarEvent {
  id: string;
  summary: string;
  start: string;
  end: string;
  description?: string;
  location?: string;
  allDay?: boolean;
}

interface CreateEventForm {
  summary: string;
  startTime: string;
  endTime: string;
  description: string;
}

interface FridayCalendarProps {
  visible: boolean;
  onClose: () => void;
}

const EMPTY_FORM: CreateEventForm = {
  summary: '',
  startTime: '',
  endTime: '',
  description: '',
};

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  } catch {
    return iso;
  }
}

function formatDateRange(start: string, end: string): string {
  return `${formatTime(start)} – ${formatTime(end)}`;
}

export default function FridayCalendar({ visible, onClose }: FridayCalendarProps) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [todayEvents, setTodayEvents] = useState<CalendarEvent[]>([]);
  const [upcomingEvents, setUpcomingEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [authLoading, setAuthLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<CreateEventForm>(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [today, upcoming] = await Promise.all([
        window.eve.calendar.getToday(),
        window.eve.calendar.getUpcoming(7),
      ]);
      setTodayEvents(Array.isArray(today) ? today : []);
      setUpcomingEvents(Array.isArray(upcoming) ? upcoming : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load calendar data');
    } finally {
      setLoading(false);
    }
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const result = await window.eve.calendar.isAuthenticated?.() ?? true;
      setAuthenticated(result);
      if (result) await loadData();
    } catch {
      setAuthenticated(false);
    }
  }, [loadData]);

  useEffect(() => {
    if (!visible) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    checkAuth();
    pollRef.current = setInterval(() => {
      if (authenticated) loadData();
    }, 30000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [visible, authenticated, checkAuth, loadData]);

  const handleAuth = async () => {
    try {
      setAuthLoading(true);
      setError(null);
      const result = await window.eve.calendar.authenticate();
      if (result) {
        setAuthenticated(true);
        await loadData();
      }
    } catch (err: any) {
      setError(err?.message || 'Authentication failed');
    } finally {
      setAuthLoading(false);
    }
  };

  const handleCreate = async () => {
    if (!form.summary || !form.startTime || !form.endTime) return;
    try {
      setCreating(true);
      setError(null);
      await window.eve.calendar.createEvent({
        summary: form.summary,
        startTime: form.startTime,
        endTime: form.endTime,
        description: form.description || undefined,
      });
      setForm(EMPTY_FORM);
      setShowForm(false);
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to create event');
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (eventId: string) => {
    try {
      setDeleting(eventId);
      setError(null);
      // Calendar delete not yet exposed in preload — show as not available
      throw new Error('Event deletion is not yet available');
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to delete event');
    } finally {
      setDeleting(null);
    }
  };

  const renderEventCard = (ev: CalendarEvent, showDate = false) => (
    <div key={ev.id} style={s.eventCard}>
      <div style={s.eventHeader}>
        <div style={{ flex: 1 }}>
          <div style={s.eventTitle}>{ev.summary}</div>
          <div style={s.eventTime}>
            {showDate && <span>{formatDate(ev.start)} &middot; </span>}
            {ev.allDay ? 'All Day' : formatDateRange(ev.start, ev.end)}
          </div>
          {ev.location && <div style={s.eventMeta}>📍 {ev.location}</div>}
          {ev.description && <div style={s.eventMeta}>{ev.description}</div>}
        </div>
        <button
          style={s.deleteBtn}
          onClick={() => handleDelete(ev.id)}
          disabled={deleting === ev.id}
          title="Delete event"
        >
          {deleting === ev.id ? '...' : '🗑'}
        </button>
      </div>
    </div>
  );

  return (
    <AppShell visible={visible} onClose={onClose} icon="📅" title="Calendar" width={720}>
      {/* Auth Banner */}
      {authenticated === false && (
        <div style={s.authBanner}>
          <div style={s.authText}>
            <span style={{ fontSize: 20 }}>🔒</span>
            <span>Google Calendar not connected</span>
          </div>
          <button style={s.authBtn} onClick={handleAuth} disabled={authLoading}>
            {authLoading ? 'Connecting...' : 'Connect Google Calendar'}
          </button>
        </div>
      )}

      {/* Error */}
      {error && <div style={s.errorBar}>{error}</div>}

      {/* Loading */}
      {loading && authenticated !== false && (
        <div style={s.loadingBar}>Loading calendar...</div>
      )}

      {authenticated && (
        <>
          {/* Today's Schedule */}
          <div style={s.section}>
            <div style={s.sectionHeader}>
              <span style={s.sectionTitle}>Today's Schedule</span>
              <span style={s.badge}>{todayEvents.length}</span>
            </div>
            {todayEvents.length === 0 && !loading && (
              <div style={s.empty}>No events today</div>
            )}
            {todayEvents.map((ev) => renderEventCard(ev))}
          </div>

          {/* Upcoming Events */}
          <div style={s.section}>
            <div style={s.sectionHeader}>
              <span style={s.sectionTitle}>Upcoming (7 days)</span>
              <span style={s.badge}>{upcomingEvents.length}</span>
            </div>
            {upcomingEvents.length === 0 && !loading && (
              <div style={s.empty}>No upcoming events</div>
            )}
            {upcomingEvents.map((ev) => renderEventCard(ev, true))}
          </div>

          {/* Create Button / Form */}
          {!showForm ? (
            <button style={s.createToggle} onClick={() => setShowForm(true)}>
              + New Event
            </button>
          ) : (
            <div style={s.formCard}>
              <div style={s.sectionTitle}>Create Event</div>
              <input
                style={s.input}
                placeholder="Event summary"
                value={form.summary}
                onChange={(e) => setForm({ ...form, summary: e.target.value })}
              />
              <div style={s.row}>
                <div style={{ flex: 1 }}>
                  <label style={s.label}>Start</label>
                  <input
                    style={s.input}
                    type="datetime-local"
                    value={form.startTime}
                    onChange={(e) => setForm({ ...form, startTime: e.target.value })}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={s.label}>End</label>
                  <input
                    style={s.input}
                    type="datetime-local"
                    value={form.endTime}
                    onChange={(e) => setForm({ ...form, endTime: e.target.value })}
                  />
                </div>
              </div>
              <textarea
                style={{ ...s.input, minHeight: 60, resize: 'vertical' }}
                placeholder="Description (optional)"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
              <div style={s.formActions}>
                <button style={s.cancelBtn} onClick={() => { setShowForm(false); setForm(EMPTY_FORM); }}>
                  Cancel
                </button>
                <button
                  style={s.submitBtn}
                  onClick={handleCreate}
                  disabled={creating || !form.summary || !form.startTime || !form.endTime}
                >
                  {creating ? 'Creating...' : 'Create Event'}
                </button>
              </div>
            </div>
          )}

          {/* Refresh */}
          <button style={s.refreshBtn} onClick={loadData} disabled={loading}>
            {loading ? 'Refreshing...' : '↻ Refresh'}
          </button>
        </>
      )}
    </AppShell>
  );
}

const s: Record<string, React.CSSProperties> = {
  authBanner: {
    background: 'rgba(138, 43, 226, 0.1)',
    border: '1px solid rgba(138, 43, 226, 0.3)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 16,
  },
  authText: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    color: '#F8FAFC',
    fontSize: 14,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  authBtn: {
    background: 'linear-gradient(135deg, #8A2BE2, #6a1fb0)',
    border: 'none',
    borderRadius: 8,
    color: '#fff',
    padding: '8px 20px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    whiteSpace: 'nowrap',
  },
  errorBar: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 8,
    padding: '10px 16px',
    color: '#ef4444',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  loadingBar: {
    color: '#8888a0',
    fontSize: 13,
    textAlign: 'center',
    padding: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 4,
  },
  sectionTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  badge: {
    background: 'rgba(0, 240, 255, 0.15)',
    color: '#00f0ff',
    fontSize: 11,
    fontWeight: 700,
    borderRadius: 10,
    padding: '2px 8px',
    fontFamily: "'JetBrains Mono', monospace",
  },
  eventCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 10,
    padding: '12px 16px',
    transition: 'border-color 0.15s',
  },
  eventHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 12,
  },
  eventTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 2,
  },
  eventTime: {
    color: '#00f0ff',
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
  },
  eventMeta: {
    color: '#8888a0',
    fontSize: 12,
    marginTop: 4,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  deleteBtn: {
    background: 'none',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 6,
    color: '#ef4444',
    cursor: 'pointer',
    padding: '4px 8px',
    fontSize: 14,
    flexShrink: 0,
    transition: 'background 0.15s',
  },
  empty: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: '16px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  createToggle: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px dashed rgba(0, 240, 255, 0.3)',
    borderRadius: 10,
    color: '#00f0ff',
    padding: '12px 0',
    fontSize: 14,
    fontWeight: 500,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    width: '100%',
  },
  formCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  input: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#F8FAFC',
    padding: '10px 14px',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    width: '100%',
    boxSizing: 'border-box',
  },
  label: {
    color: '#8888a0',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    marginBottom: 4,
    display: 'block',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  row: {
    display: 'flex',
    gap: 12,
  },
  formActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: 10,
    marginTop: 4,
  },
  cancelBtn: {
    background: 'none',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '8px 18px',
    fontSize: 13,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  submitBtn: {
    background: 'linear-gradient(135deg, #00f0ff, #0090cc)',
    border: 'none',
    borderRadius: 8,
    color: '#000',
    padding: '8px 20px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  refreshBtn: {
    background: 'none',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '8px 16px',
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    alignSelf: 'center',
  },
};

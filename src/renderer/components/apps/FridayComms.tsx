/**
 * FridayComms.tsx — Unified communications hub for Agent Friday
 *
 * IPC: window.eve.inbox.*, window.eve.outbound.*, window.eve.communications.*
 * Features: Inbox, outbound drafts with approve/reject, compose email/SMS
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

type CommTab = 'inbox' | 'drafts' | 'compose';

interface Message {
  id: string;
  from?: string;
  to?: string;
  subject?: string;
  body?: string;
  date?: string;
  status?: string;
  channel?: string;
  read?: boolean;
}

interface ComposeForm {
  mode: 'email' | 'sms';
  to: string;
  subject: string;
  body: string;
}

interface FridayCommsProps {
  visible: boolean;
  onClose: () => void;
}

const EMPTY_COMPOSE: ComposeForm = { mode: 'email', to: '', subject: '', body: '' };

function timeAgo(dateStr?: string): string {
  if (!dateStr) return '';
  try {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  } catch {
    return dateStr;
  }
}

export default function FridayComms({ visible, onClose }: FridayCommsProps) {
  const [tab, setTab] = useState<CommTab>('inbox');
  const [inbox, setInbox] = useState<Message[]>([]);
  const [drafts, setDrafts] = useState<Message[]>([]);
  const [selected, setSelected] = useState<Message | null>(null);
  const [compose, setCompose] = useState<ComposeForm>(EMPTY_COMPOSE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [inboxData, outboundData] = await Promise.all([
        window.eve.inbox.getMessages(),
        window.eve.outbound.getDrafts(),
      ]);
      setInbox(Array.isArray(inboxData) ? inboxData : []);
      setDrafts(Array.isArray(outboundData) ? outboundData : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load messages');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      setSelected(null);
      return;
    }
    loadData();
    pollRef.current = setInterval(loadData, 30000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [visible, loadData]);

  const handleApprove = async (id: string) => {
    try {
      setActionLoading(id);
      await window.eve.outbound.approve(id);
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to approve');
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (id: string) => {
    try {
      setActionLoading(id);
      await window.eve.outbound.reject(id);
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to reject');
    } finally {
      setActionLoading(null);
    }
  };

  const handleSend = async () => {
    if (!compose.to || !compose.body) return;
    try {
      setSending(true);
      setError(null);
      // Create draft through outbound system, then approve and send
      const draft = await window.eve.outbound.createDraft({
        type: compose.mode === 'email' ? 'email' : 'message',
        to: compose.to,
        subject: compose.subject || undefined,
        body: compose.body,
      });
      if (draft?.id) {
        await window.eve.outbound.approveAndSend(draft.id);
      }
      setCompose(EMPTY_COMPOSE);
      setTab('inbox');
      await loadData();
    } catch (err: any) {
      setError(err?.message || 'Failed to send');
    } finally {
      setSending(false);
    }
  };

  const renderTabBar = () => (
    <div style={s.tabBar}>
      {(['inbox', 'drafts', 'compose'] as CommTab[]).map((t) => (
        <button
          key={t}
          style={tab === t ? { ...s.tab, ...s.tabActive } : s.tab}
          onClick={() => { setTab(t); setSelected(null); }}
        >
          {t === 'inbox' ? `Inbox (${inbox.length})` : t === 'drafts' ? `Drafts (${drafts.length})` : '+ Compose'}
        </button>
      ))}
    </div>
  );

  const renderMessageRow = (msg: Message, isDraft: boolean) => (
    <div
      key={msg.id}
      style={selected?.id === msg.id ? { ...s.msgRow, ...s.msgRowActive } : s.msgRow}
      onClick={() => setSelected(msg)}
    >
      <div style={s.msgRowTop}>
        <span style={s.msgSender}>{isDraft ? `To: ${msg.to || '—'}` : msg.from || 'Unknown'}</span>
        <span style={s.msgDate}>{timeAgo(msg.date)}</span>
      </div>
      <div style={s.msgSubject}>{msg.subject || '(no subject)'}</div>
      {msg.channel && <span style={s.channelBadge}>{msg.channel}</span>}
      {isDraft && msg.status && <span style={s.statusBadge}>{msg.status}</span>}
    </div>
  );

  const renderDetail = () => {
    if (!selected) return <div style={s.emptyDetail}>Select a message to view</div>;
    const isDraft = tab === 'drafts';
    return (
      <div style={s.detail}>
        <div style={s.detailHeader}>
          <div style={s.detailSubject}>{selected.subject || '(no subject)'}</div>
          <div style={s.detailMeta}>
            {isDraft ? `To: ${selected.to || '—'}` : `From: ${selected.from || 'Unknown'}`}
            {selected.date && <span> &middot; {new Date(selected.date).toLocaleString()}</span>}
          </div>
        </div>
        <div style={s.detailBody}>{selected.body || '(empty)'}</div>
        {isDraft && (
          <div style={s.draftActions}>
            <button
              style={s.approveBtn}
              onClick={() => handleApprove(selected.id)}
              disabled={actionLoading === selected.id}
            >
              {actionLoading === selected.id ? '...' : '✓ Approve & Send'}
            </button>
            <button
              style={s.rejectBtn}
              onClick={() => handleReject(selected.id)}
              disabled={actionLoading === selected.id}
            >
              {actionLoading === selected.id ? '...' : '✕ Reject'}
            </button>
          </div>
        )}
      </div>
    );
  };

  const renderCompose = () => (
    <div style={s.composeCard}>
      <div style={s.modeToggle}>
        {(['email', 'sms'] as const).map((m) => (
          <button
            key={m}
            style={compose.mode === m ? { ...s.modeBtn, ...s.modeBtnActive } : s.modeBtn}
            onClick={() => setCompose({ ...compose, mode: m })}
          >
            {m === 'email' ? '📧 Email' : '💬 SMS'}
          </button>
        ))}
      </div>
      <input
        style={s.input}
        placeholder={compose.mode === 'email' ? 'To (email)' : 'To (phone)'}
        value={compose.to}
        onChange={(e) => setCompose({ ...compose, to: e.target.value })}
      />
      {compose.mode === 'email' && (
        <input
          style={s.input}
          placeholder="Subject"
          value={compose.subject}
          onChange={(e) => setCompose({ ...compose, subject: e.target.value })}
        />
      )}
      <textarea
        style={{ ...s.input, minHeight: 120, resize: 'vertical' }}
        placeholder="Message body..."
        value={compose.body}
        onChange={(e) => setCompose({ ...compose, body: e.target.value })}
      />
      <div style={s.composeActions}>
        <button
          style={s.sendBtn}
          onClick={handleSend}
          disabled={sending || !compose.to || !compose.body}
        >
          {sending ? 'Sending...' : compose.mode === 'email' ? 'Send Email' : 'Send SMS'}
        </button>
      </div>
    </div>
  );

  return (
    <AppShell visible={visible} onClose={onClose} icon="✉️" title="Mail" width={900}>
      {error && <div style={s.errorBar}>{error}</div>}
      {renderTabBar()}

      {loading && <div style={s.loadingBar}>Loading messages...</div>}

      {tab === 'compose' ? (
        renderCompose()
      ) : (
        <div style={s.splitView}>
          <div style={s.messageList}>
            {(tab === 'inbox' ? inbox : drafts).length === 0 && !loading && (
              <div style={s.emptyList}>{tab === 'inbox' ? 'Inbox is empty' : 'No drafts'}</div>
            )}
            {(tab === 'inbox' ? inbox : drafts).map((msg) =>
              renderMessageRow(msg, tab === 'drafts'),
            )}
          </div>
          <div style={s.detailPane}>{renderDetail()}</div>
        </div>
      )}

      <button style={s.refreshBtn} onClick={loadData} disabled={loading}>
        {loading ? 'Refreshing...' : '↻ Refresh'}
      </button>
    </AppShell>
  );
}

const s: Record<string, React.CSSProperties> = {
  tabBar: {
    display: 'flex',
    gap: 2,
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: 10,
    padding: 3,
    marginBottom: 4,
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    color: '#8888a0',
    padding: '10px 0',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    borderRadius: 8,
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0, 240, 255, 0.08)',
    color: '#00f0ff',
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
  splitView: {
    display: 'flex',
    gap: 12,
    flex: 1,
    minHeight: 320,
  },
  messageList: {
    width: 320,
    flexShrink: 0,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    maxHeight: 420,
  },
  msgRow: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 8,
    padding: '10px 14px',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  msgRowActive: {
    borderColor: 'rgba(0, 240, 255, 0.3)',
    background: 'rgba(0, 240, 255, 0.04)',
  },
  msgRowTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  msgSender: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  msgDate: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
  },
  msgSubject: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  channelBadge: {
    display: 'inline-block',
    marginTop: 4,
    background: 'rgba(138, 43, 226, 0.15)',
    color: '#8A2BE2',
    fontSize: 10,
    fontWeight: 600,
    borderRadius: 4,
    padding: '2px 6px',
    fontFamily: "'JetBrains Mono', monospace",
  },
  statusBadge: {
    display: 'inline-block',
    marginTop: 4,
    marginLeft: 6,
    background: 'rgba(249, 115, 22, 0.15)',
    color: '#f97316',
    fontSize: 10,
    fontWeight: 600,
    borderRadius: 4,
    padding: '2px 6px',
    fontFamily: "'JetBrains Mono', monospace",
  },
  detailPane: {
    flex: 1,
    minWidth: 0,
  },
  emptyDetail: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: '60px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  emptyList: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: '40px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  detail: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 10,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    height: '100%',
    boxSizing: 'border-box',
  },
  detailHeader: {
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    paddingBottom: 12,
  },
  detailSubject: {
    color: '#F8FAFC',
    fontSize: 16,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 4,
  },
  detailMeta: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  detailBody: {
    color: '#c8c8d8',
    fontSize: 13,
    lineHeight: 1.7,
    fontFamily: "'Inter', system-ui, sans-serif",
    flex: 1,
    whiteSpace: 'pre-wrap',
  },
  draftActions: {
    display: 'flex',
    gap: 10,
    borderTop: '1px solid rgba(255, 255, 255, 0.06)',
    paddingTop: 12,
  },
  approveBtn: {
    background: 'rgba(34, 197, 94, 0.15)',
    border: '1px solid rgba(34, 197, 94, 0.3)',
    borderRadius: 8,
    color: '#22c55e',
    padding: '8px 20px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  rejectBtn: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 8,
    color: '#ef4444',
    padding: '8px 20px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  composeCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 12,
    padding: 20,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  modeToggle: {
    display: 'flex',
    gap: 6,
    marginBottom: 4,
  },
  modeBtn: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '8px 18px',
    fontSize: 13,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'all 0.15s',
  },
  modeBtnActive: {
    borderColor: 'rgba(0, 240, 255, 0.3)',
    color: '#00f0ff',
    background: 'rgba(0, 240, 255, 0.06)',
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
  composeActions: {
    display: 'flex',
    justifyContent: 'flex-end',
  },
  sendBtn: {
    background: 'linear-gradient(135deg, #00f0ff, #0090cc)',
    border: 'none',
    borderRadius: 8,
    color: '#000',
    padding: '10px 28px',
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

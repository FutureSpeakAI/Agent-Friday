/**
 * FridayGateway.tsx — Gateway management for Agent Friday
 *
 * IPC: window.eve.gateway.*
 * Features: Start/stop gateway, view channels, manage paired identities, pair new devices
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

interface GatewayChannel {
  id: string;
  name: string;
  type: string;
  connected?: boolean;
  messageCount?: number;
}

interface PairedIdentity {
  id: string;
  name: string;
  pairedAt?: string;
  lastSeen?: string;
  platform?: string;
}

interface GatewayStatus {
  enabled: boolean;
  adapters: string[];
  activeSessions: number;
  channels?: GatewayChannel[];
  pairedIdentities?: PairedIdentity[];
}

interface FridayGatewayProps {
  visible: boolean;
  onClose: () => void;
}

function timeAgo(dateStr?: string): string {
  if (!dateStr) return 'never';
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

export default function FridayGateway({ visible, onClose }: FridayGatewayProps) {
  const [status, setStatus] = useState<GatewayStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pairCode, setPairCode] = useState('');
  const [pairing, setPairing] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await window.eve.gateway.getStatus();
      // Also load paired identities if available
      let identities: PairedIdentity[] = [];
      try {
        identities = await window.eve.gateway.getPairedIdentities() ?? [];
      } catch { /* not available */ }
      setStatus({
        ...result,
        pairedIdentities: identities,
        channels: result.adapters?.map((a: string) => ({ id: a, name: a, type: a, connected: result.enabled })) ?? [],
      });
    } catch (err: any) {
      setError(err?.message || 'Failed to load gateway status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    loadStatus();
    pollRef.current = setInterval(loadStatus, 10000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [visible, loadStatus]);

  const handleToggle = async () => {
    if (!status) return;
    try {
      setToggling(true);
      setError(null);
      await window.eve.gateway.setEnabled(!status.enabled);
      await loadStatus();
    } catch (err: any) {
      setError(err?.message || 'Failed to toggle gateway');
    } finally {
      setToggling(false);
    }
  };

  const handlePair = async () => {
    if (!pairCode.trim()) return;
    try {
      setPairing(true);
      setError(null);
      await window.eve.gateway.approvePairing(pairCode.trim());
      setPairCode('');
      await loadStatus();
    } catch (err: any) {
      setError(err?.message || 'Pairing failed');
    } finally {
      setPairing(false);
    }
  };

  const handleRevoke = async (identityId: string) => {
    try {
      setRevoking(identityId);
      setError(null);
      await window.eve.gateway.revokePairing(identityId);
      await loadStatus();
    } catch (err: any) {
      setError(err?.message || 'Failed to revoke identity');
    } finally {
      setRevoking(null);
    }
  };

  const isRunning = status?.enabled ?? false;
  const channels = status?.channels ?? [];
  const identities = status?.pairedIdentities ?? [];

  return (
    <AppShell visible={visible} onClose={onClose} icon="🔗" title="Gateway" width={720}>
      <ContextBar appId="friday-gateway" />
      {error && <div style={s.errorBar}>{error}</div>}

      {/* Power Toggle */}
      <div style={s.toggleCard}>
        <div style={s.toggleInfo}>
          <div style={s.toggleTitle}>Communication Gateway</div>
          <div style={s.toggleDesc}>
            {isRunning
              ? 'Gateway is active. External channels are connected.'
              : 'Gateway is offline. Start it to enable external messaging.'}
          </div>
        </div>
        <button
          style={isRunning ? { ...s.toggleBtn, ...s.toggleBtnOn } : { ...s.toggleBtn, ...s.toggleBtnOff }}
          onClick={handleToggle}
          disabled={toggling || loading}
        >
          <div
            style={{
              ...s.toggleKnob,
              transform: isRunning ? 'translateX(24px)' : 'translateX(0)',
            }}
          />
        </button>
      </div>

      {/* Status Indicator */}
      <div style={s.statusRow}>
        <div style={{ ...s.statusDot, background: isRunning ? '#22c55e' : '#ef4444' }} />
        <span style={s.statusText}>{isRunning ? 'Running' : 'Stopped'}</span>
        {toggling && <span style={s.statusMuted}> (switching...)</span>}
      </div>

      {/* Active Channels */}
      <div style={s.section}>
        <div style={s.sectionHeader}>
          <span style={s.sectionTitle}>Active Channels</span>
          <span style={s.badge}>{channels.length}</span>
        </div>
        {channels.length === 0 && (
          <div style={s.empty}>{isRunning ? 'No channels configured' : 'Start gateway to see channels'}</div>
        )}
        <div style={s.channelGrid}>
          {channels.map((ch) => (
            <div key={ch.id} style={s.channelCard}>
              <div style={s.channelTop}>
                <span style={s.channelName}>{ch.name}</span>
                <div style={{ ...s.connDot, background: ch.connected ? '#22c55e' : '#4a4a62' }} />
              </div>
              <div style={s.channelType}>{ch.type}</div>
              {ch.messageCount !== undefined && (
                <div style={s.channelMsgs}>{ch.messageCount} messages</div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Paired Identities */}
      <div style={s.section}>
        <div style={s.sectionHeader}>
          <span style={s.sectionTitle}>Paired Identities</span>
          <span style={s.badge}>{identities.length}</span>
        </div>
        {identities.length === 0 && (
          <div style={s.empty}>No identities paired</div>
        )}
        {identities.map((identity) => (
          <div key={identity.id} style={s.identityRow}>
            <div style={s.identityInfo}>
              <div style={s.identityName}>{identity.name}</div>
              <div style={s.identityMeta}>
                {identity.platform && <span>{identity.platform} &middot; </span>}
                Paired {identity.pairedAt ? timeAgo(identity.pairedAt) : 'unknown'}
                {identity.lastSeen && <span> &middot; Last seen {timeAgo(identity.lastSeen)}</span>}
              </div>
            </div>
            <button
              style={s.revokeBtn}
              onClick={() => handleRevoke(identity.id)}
              disabled={revoking === identity.id}
            >
              {revoking === identity.id ? '...' : 'Revoke'}
            </button>
          </div>
        ))}
      </div>

      {/* Pair New Device */}
      <div style={s.pairCard}>
        <div style={s.sectionTitle}>Pair New Device</div>
        <div style={s.pairRow}>
          <input
            style={s.input}
            placeholder="Enter pairing code"
            value={pairCode}
            onChange={(e) => setPairCode(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handlePair(); }}
          />
          <button
            style={s.pairBtn}
            onClick={handlePair}
            disabled={pairing || !pairCode.trim()}
          >
            {pairing ? 'Pairing...' : 'Pair'}
          </button>
        </div>
      </div>

      {/* Refresh */}
      <button style={s.refreshBtn} onClick={loadStatus} disabled={loading}>
        {loading ? 'Refreshing...' : '↻ Refresh'}
      </button>
    </AppShell>
  );
}

const s: Record<string, React.CSSProperties> = {
  errorBar: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 8,
    padding: '10px 16px',
    color: '#ef4444',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  toggleCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 14,
    padding: '20px 24px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 20,
  },
  toggleInfo: {
    flex: 1,
  },
  toggleTitle: {
    color: '#F8FAFC',
    fontSize: 15,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 4,
  },
  toggleDesc: {
    color: '#8888a0',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.5,
  },
  toggleBtn: {
    width: 56,
    height: 32,
    borderRadius: 16,
    border: 'none',
    cursor: 'pointer',
    position: 'relative',
    transition: 'background 0.25s',
    flexShrink: 0,
    padding: 0,
  },
  toggleBtnOn: {
    background: 'rgba(34, 197, 94, 0.4)',
  },
  toggleBtnOff: {
    background: 'rgba(255, 255, 255, 0.1)',
  },
  toggleKnob: {
    width: 24,
    height: 24,
    borderRadius: 12,
    background: '#F8FAFC',
    position: 'absolute',
    top: 4,
    left: 4,
    transition: 'transform 0.25s',
    boxShadow: '0 2px 6px rgba(0,0,0,0.3)',
  },
  statusRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '4px 0',
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    flexShrink: 0,
  },
  statusText: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  statusMuted: {
    color: '#4a4a62',
    fontSize: 12,
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
  empty: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: '16px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  channelGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
    gap: 10,
  },
  channelCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 10,
    padding: '12px 16px',
  },
  channelTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 4,
  },
  channelName: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  connDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    flexShrink: 0,
  },
  channelType: {
    color: '#8888a0',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  channelMsgs: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    marginTop: 4,
  },
  identityRow: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 10,
    padding: '12px 16px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  identityInfo: {
    flex: 1,
    minWidth: 0,
  },
  identityName: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 500,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 2,
  },
  identityMeta: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  revokeBtn: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.25)',
    borderRadius: 8,
    color: '#ef4444',
    padding: '6px 16px',
    fontSize: 12,
    fontWeight: 500,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    flexShrink: 0,
  },
  pairCard: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 12,
    padding: '16px 20px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  pairRow: {
    display: 'flex',
    gap: 10,
  },
  input: {
    flex: 1,
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#F8FAFC',
    padding: '10px 14px',
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    outline: 'none',
    letterSpacing: '0.08em',
  },
  pairBtn: {
    background: 'linear-gradient(135deg, #8A2BE2, #6a1fb0)',
    border: 'none',
    borderRadius: 8,
    color: '#fff',
    padding: '10px 24px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    flexShrink: 0,
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

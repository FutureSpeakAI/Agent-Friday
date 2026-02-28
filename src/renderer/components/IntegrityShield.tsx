/**
 * IntegrityShield — Compact integrity status indicator for the StatusBar.
 *
 * Displays a small shield icon that reflects the three integrity tiers:
 * - Core Laws: green if intact, red if tampered (safe mode)
 * - Identity: green if intact, amber if externally modified
 * - Memory: green if intact, amber if external changes detected
 *
 * Click to expand a detail panel showing all integrity state.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';

interface IntegrityState {
  initialized: boolean;
  lawsIntact: boolean;
  identityIntact: boolean;
  memoriesIntact: boolean;
  safeMode: boolean;
  safeModeReason: string | null;
  lastVerified: number;
  memoryChanges: {
    longTermAdded: string[];
    longTermRemoved: string[];
    longTermModified: string[];
    mediumTermAdded: string[];
    mediumTermRemoved: string[];
    mediumTermModified: string[];
    detectedAt: number;
    acknowledged: boolean;
  } | null;
}

const DEFAULT_STATE: IntegrityState = {
  initialized: false,
  lawsIntact: true,
  identityIntact: true,
  memoriesIntact: true,
  safeMode: false,
  safeModeReason: null,
  lastVerified: 0,
  memoryChanges: null,
};

export default function IntegrityShield() {
  const [state, setState] = useState<IntegrityState>(DEFAULT_STATE);
  const [expanded, setExpanded] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Poll integrity state every 30 seconds
  const fetchState = useCallback(async () => {
    try {
      const s = await window.eve.integrity.getState();
      setState(s);
    } catch {
      // Integrity system may not be ready yet
    }
  }, []);

  useEffect(() => {
    fetchState();
    const interval = setInterval(fetchState, 30_000);
    return () => clearInterval(interval);
  }, [fetchState]);

  // Close panel on outside click
  useEffect(() => {
    if (!expanded) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [expanded]);

  const handleVerify = async () => {
    setVerifying(true);
    try {
      await window.eve.integrity.verify();
      await fetchState();
    } catch {
      // ignore
    }
    setVerifying(false);
  };

  const handleReset = async () => {
    setResetting(true);
    setResetMessage(null);
    try {
      const result = await window.eve.integrity.reset();
      setResetMessage(result.message);
      if (result.success) {
        await fetchState();
        // Auto-clear success message after 5 seconds
        setTimeout(() => setResetMessage(null), 5000);
      }
    } catch (err) {
      setResetMessage('Reset failed. Try restarting the application.');
    }
    setResetting(false);
  };

  // Determine overall shield color
  const allGreen = state.initialized && state.lawsIntact && state.identityIntact && state.memoriesIntact;
  const isCritical = state.safeMode || !state.lawsIntact;
  const isWarning = !isCritical && (!state.identityIntact || !state.memoriesIntact);
  const shieldColor = !state.initialized
    ? '#444458'
    : isCritical
      ? '#ef4444'
      : isWarning
        ? '#f59e0b'
        : '#22c55e';

  const glowColor = !state.initialized
    ? 'none'
    : isCritical
      ? '0 0 8px rgba(239, 68, 68, 0.5)'
      : isWarning
        ? '0 0 6px rgba(245, 158, 11, 0.4)'
        : '0 0 6px rgba(34, 197, 94, 0.3)';

  const statusLabel = !state.initialized
    ? 'Initializing…'
    : isCritical
      ? 'SAFE MODE'
      : isWarning
        ? 'Changes Detected'
        : 'All Systems Intact';

  return (
    <div style={{ position: 'relative' }} ref={panelRef}>
      {/* Shield icon button */}
      <button
        onClick={() => setExpanded(!expanded)}
        style={shieldButton}
        title={`Integrity: ${statusLabel}`}
      >
        <svg
          width="14"
          height="16"
          viewBox="0 0 14 16"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          style={{ filter: `drop-shadow(${glowColor})` }}
        >
          <path
            d="M7 0L0 3V7.5C0 11.64 2.99 15.48 7 16C11.01 15.48 14 11.64 14 7.5V3L7 0Z"
            fill={shieldColor}
            fillOpacity={0.25}
            stroke={shieldColor}
            strokeWidth={1.2}
          />
          {allGreen && (
            <path
              d="M4.5 8L6.2 9.7L9.5 6.3"
              stroke={shieldColor}
              strokeWidth={1.5}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}
          {isCritical && (
            <>
              <line x1="7" y1="5" x2="7" y2="9" stroke={shieldColor} strokeWidth={1.5} strokeLinecap="round" />
              <circle cx="7" cy="11.5" r="0.8" fill={shieldColor} />
            </>
          )}
          {isWarning && !isCritical && (
            <>
              <line x1="7" y1="5" x2="7" y2="9" stroke={shieldColor} strokeWidth={1.5} strokeLinecap="round" />
              <circle cx="7" cy="11.5" r="0.8" fill={shieldColor} />
            </>
          )}
        </svg>
        <span style={{ ...shieldLabel, color: shieldColor }}>{state.safeMode ? 'SAFE' : 'INT'}</span>
      </button>

      {/* Expanded detail panel */}
      {expanded && (
        <div style={panelStyles.container}>
          <div style={panelStyles.header}>
            <span style={panelStyles.title}>INTEGRITY STATUS</span>
            <span style={{ ...panelStyles.badge, background: `${shieldColor}22`, color: shieldColor, borderColor: `${shieldColor}44` }}>
              {statusLabel}
            </span>
          </div>

          <div style={panelStyles.divider} />

          {/* Three tiers */}
          <TierRow
            label="Core Laws"
            description="Fundamental behavioral laws (Asimov)"
            intact={state.lawsIntact}
            critical
          />
          <TierRow
            label="Agent Identity"
            description="Name, voice, personality, backstory"
            intact={state.identityIntact}
          />
          <TierRow
            label="Memory Store"
            description="Long-term facts & medium-term patterns"
            intact={state.memoriesIntact}
          />

          {/* Memory change details */}
          {state.memoryChanges && !state.memoryChanges.acknowledged && (
            <>
              <div style={panelStyles.divider} />
              <div style={panelStyles.changeSection}>
                <span style={panelStyles.changeTitle}>Memory Changes Detected</span>
                <span style={panelStyles.changeTime}>
                  {new Date(state.memoryChanges.detectedAt).toLocaleTimeString()}
                </span>
                {state.memoryChanges.longTermAdded.length > 0 && (
                  <ChangeList label="Added (long-term)" items={state.memoryChanges.longTermAdded} color="#22c55e" />
                )}
                {state.memoryChanges.longTermRemoved.length > 0 && (
                  <ChangeList label="Removed (long-term)" items={state.memoryChanges.longTermRemoved} color="#ef4444" />
                )}
                {state.memoryChanges.longTermModified.length > 0 && (
                  <ChangeList label="Modified (long-term)" items={state.memoryChanges.longTermModified} color="#f59e0b" />
                )}
                {state.memoryChanges.mediumTermAdded.length > 0 && (
                  <ChangeList label="Added (medium-term)" items={state.memoryChanges.mediumTermAdded} color="#22c55e" />
                )}
                {state.memoryChanges.mediumTermRemoved.length > 0 && (
                  <ChangeList label="Removed (medium-term)" items={state.memoryChanges.mediumTermRemoved} color="#ef4444" />
                )}
                {state.memoryChanges.mediumTermModified.length > 0 && (
                  <ChangeList label="Modified (medium-term)" items={state.memoryChanges.mediumTermModified} color="#f59e0b" />
                )}
              </div>
            </>
          )}

          {/* Safe mode warning */}
          {state.safeMode && state.safeModeReason && (
            <>
              <div style={panelStyles.divider} />
              <div style={panelStyles.safeModeBox}>
                <span style={panelStyles.safeModeLabel}>⚠ SAFE MODE ACTIVE</span>
                <span style={panelStyles.safeModeReason}>{state.safeModeReason}</span>
                <button
                  onClick={handleReset}
                  disabled={resetting}
                  style={{
                    ...panelStyles.resetButton,
                    opacity: resetting ? 0.5 : 1,
                  }}
                >
                  {resetting ? 'Resetting Integrity…' : '🔄 Reset Asimov\'s cLaws'}
                </button>
                {resetMessage && (
                  <span style={{
                    display: 'block',
                    fontSize: 9,
                    color: resetMessage.includes('failed') || resetMessage.includes('Failed') ? '#ef4444' : '#22c55e',
                    marginTop: 6,
                    lineHeight: 1.3,
                  }}>
                    {resetMessage}
                  </span>
                )}
              </div>
            </>
          )}

          <div style={panelStyles.divider} />

          {/* Verify button */}
          <button
            onClick={handleVerify}
            disabled={verifying}
            style={{
              ...panelStyles.verifyButton,
              opacity: verifying ? 0.5 : 1,
            }}
          >
            {verifying ? 'Verifying…' : 'Run Verification'}
          </button>

          {state.lastVerified > 0 && (
            <span style={panelStyles.lastVerified}>
              Last verified: {new Date(state.lastVerified).toLocaleTimeString()}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Sub-components ────────────────────────────────────────────────── */

function TierRow({ label, description, intact, critical }: {
  label: string;
  description: string;
  intact: boolean;
  critical?: boolean;
}) {
  const color = intact ? '#22c55e' : critical ? '#ef4444' : '#f59e0b';
  return (
    <div style={tierStyles.row}>
      <div style={{ ...tierStyles.dot, background: color, boxShadow: `0 0 4px ${color}` }} />
      <div style={tierStyles.text}>
        <span style={tierStyles.label}>{label}</span>
        <span style={tierStyles.desc}>{description}</span>
      </div>
      <span style={{ ...tierStyles.status, color }}>{intact ? 'INTACT' : critical ? 'TAMPERED' : 'MODIFIED'}</span>
    </div>
  );
}

function ChangeList({ label, items, color }: { label: string; items: string[]; color: string }) {
  return (
    <div style={{ marginTop: 6 }}>
      <span style={{ fontSize: 10, fontWeight: 600, color, letterSpacing: '0.05em' }}>{label}</span>
      {items.slice(0, 5).map((item, i) => (
        <div key={i} style={{ fontSize: 10, color: '#8a8a9a', marginLeft: 8, marginTop: 2, lineHeight: 1.3 }}>
          • {item.length > 80 ? item.slice(0, 80) + '…' : item}
        </div>
      ))}
      {items.length > 5 && (
        <div style={{ fontSize: 9, color: '#555568', marginLeft: 8, marginTop: 2 }}>
          +{items.length - 5} more
        </div>
      )}
    </div>
  );
}

/* ── Styles ────────────────────────────────────────────────────────── */

const shieldButton: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 4,
  background: 'none',
  border: 'none',
  cursor: 'pointer',
  padding: '2px 4px',
  borderRadius: 4,
  transition: 'background 0.2s',
};

const shieldLabel: React.CSSProperties = {
  fontSize: 9,
  fontWeight: 700,
  letterSpacing: '0.08em',
  fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
};

const panelStyles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    bottom: 32,
    right: 0,
    width: 320,
    background: 'rgba(10, 14, 28, 0.95)',
    border: '1px solid rgba(0, 240, 255, 0.12)',
    borderRadius: 8,
    padding: 16,
    backdropFilter: 'blur(20px)',
    boxShadow: '0 8px 32px rgba(0, 0, 0, 0.5)',
    zIndex: 100,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  title: {
    fontSize: 11,
    fontWeight: 800,
    letterSpacing: '0.15em',
    color: '#00f0ff',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
  },
  badge: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.05em',
    padding: '2px 8px',
    borderRadius: 4,
    border: '1px solid',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
  },
  divider: {
    height: 1,
    background: 'rgba(0, 240, 255, 0.06)',
    margin: '10px 0',
  },
  changeSection: {
    padding: '4px 0',
  },
  changeTitle: {
    fontSize: 10,
    fontWeight: 700,
    color: '#f59e0b',
    letterSpacing: '0.05em',
  },
  changeTime: {
    fontSize: 9,
    color: '#555568',
    marginLeft: 8,
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
  },
  safeModeBox: {
    background: 'rgba(239, 68, 68, 0.08)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 6,
    padding: 10,
  },
  safeModeLabel: {
    display: 'block',
    fontSize: 11,
    fontWeight: 800,
    color: '#ef4444',
    letterSpacing: '0.1em',
    marginBottom: 4,
  },
  safeModeReason: {
    display: 'block',
    fontSize: 10,
    color: '#ef4444aa',
    lineHeight: 1.4,
  },
  resetButton: {
    display: 'block',
    width: '100%',
    padding: '8px 0',
    marginTop: 10,
    background: 'rgba(239, 68, 68, 0.12)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 4,
    color: '#ef4444',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.06em',
    cursor: 'pointer',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    transition: 'all 0.2s',
  },
  verifyButton: {
    width: '100%',
    padding: '6px 0',
    background: 'rgba(0, 240, 255, 0.06)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 4,
    color: '#00f0ff',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.08em',
    cursor: 'pointer',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    transition: 'all 0.2s',
  },
  lastVerified: {
    display: 'block',
    textAlign: 'center' as const,
    fontSize: 9,
    color: '#444458',
    marginTop: 6,
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
  },
};

const tierStyles: Record<string, React.CSSProperties> = {
  row: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '6px 0',
  },
  dot: {
    width: 7,
    height: 7,
    borderRadius: '50%',
    flexShrink: 0,
  },
  text: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 1,
  },
  label: {
    fontSize: 10,
    fontWeight: 700,
    color: '#c0c0d0',
    letterSpacing: '0.04em',
  },
  desc: {
    fontSize: 9,
    color: '#555568',
    lineHeight: 1.2,
  },
  status: {
    fontSize: 9,
    fontWeight: 800,
    letterSpacing: '0.1em',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    flexShrink: 0,
  },
};

import React from 'react';
import IntegrityShield from './IntegrityShield';

interface ApiStatus {
  gemini: 'connected' | 'connecting' | 'offline' | 'no-key';
  claude: 'ready' | 'no-key';
  elevenlabs: 'ready' | 'no-key';
  openrouter: 'ready' | 'no-key';
  browser: 'ready' | 'unavailable';
}

interface StatusBarProps {
  status: string;
  isWebcamActive?: boolean;
  isInCall?: boolean;
  apiStatus?: ApiStatus;
}

function StatusDot({ label, state }: { label: string; state: string }) {
  const isGood = state === 'connected' || state === 'ready';
  const isWarn = state === 'connecting';
  const color = isGood ? '#22c55e' : isWarn ? '#f59e0b' : state === 'no-key' ? '#555568' : '#ef4444';

  return (
    <div style={dotStyles.wrapper} title={`${label}: ${state}`}>
      <div style={{
        ...dotStyles.dot,
        background: color,
        boxShadow: isGood ? `0 0 6px ${color}` : isWarn ? `0 0 4px ${color}` : 'none',
      }} />
      <span style={{ ...dotStyles.label, color: isGood ? '#8a8a9a' : '#444458' }}>{label}</span>
    </div>
  );
}

export default function StatusBar({ status, isWebcamActive, isInCall, apiStatus }: StatusBarProps) {
  const isOk = status === 'Ready' || status === 'Connected' || status === 'Connected — Listening';
  const isError =
    status.startsWith('Error') ||
    status.startsWith('Failed') ||
    status.startsWith('Retrying');
  const isListening = status === 'Listening...' || status === 'Connected — Listening';
  const isSpeaking = status === 'Speaking...';

  const statusColor = isOk
    ? '#22c55e'
    : isError
      ? '#ef4444'
      : isSpeaking
        ? '#8A2BE2'
        : isListening
          ? '#00f0ff'
          : '#00f0ff';

  const api = apiStatus || { gemini: 'offline', claude: 'no-key', elevenlabs: 'no-key', openrouter: 'no-key', browser: 'unavailable' };

  return (
    <div className="hover-glow" style={styles.bar}>
      <div style={styles.left}>
        <div style={{
          ...styles.dot,
          background: statusColor,
          boxShadow: `0 0 6px ${statusColor}`,
        }} />
        <span style={{ ...styles.statusText, color: statusColor }}>{status}</span>
        {isWebcamActive && (
          <span style={styles.cameraIndicator} title="Webcam active">
            <span style={styles.cameraRedDot} />
            CAM
          </span>
        )}
        {isInCall && (
          <span style={styles.callIndicator} title="In call — audio routed to virtual mic">
            <span style={styles.callIcon} />
            CALL
          </span>
        )}
      </div>
      <div style={styles.center}>
        <div style={styles.brandContainer}>
          <div style={styles.brandRow}>
            <span className="brand-glow-pulse" style={styles.brandName}>AGENT FRIDAY</span>
            <span style={styles.brandSep}>&middot;</span>
            <span className="brand-glow-pulse" style={styles.brandCompany}>FutureSpeak.AI</span>
          </div>
          {/* Gradient accent line under branding */}
          <div style={styles.brandLine} />
        </div>
      </div>
      <div style={styles.right}>
        <IntegrityShield />
        <StatusDot label="GEM" state={api.gemini} />
        <StatusDot label="CLU" state={api.claude} />
        <StatusDot label="ORT" state={api.openrouter} />
        <StatusDot label="TTS" state={api.elevenlabs} />
        <StatusDot label="BRW" state={api.browser} />
      </div>
    </div>
  );
}

const dotStyles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    cursor: 'default',
  },
  dot: {
    width: 5,
    height: 5,
    borderRadius: '50%',
    flexShrink: 0,
  },
  label: {
    fontSize: 9,
    fontWeight: 700,
    letterSpacing: '0.08em',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
  },
};

const styles: Record<string, React.CSSProperties> = {
  bar: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: 38,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 20px',
    background: 'rgba(6, 11, 25, 0.65)',
    borderTop: '1px solid rgba(0, 240, 255, 0.08)',
    backdropFilter: 'blur(12px)',
    zIndex: 20,
  },
  left: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  center: {
    position: 'absolute',
    left: '50%',
    transform: 'translateX(-50%)',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  brandContainer: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 3,
  },
  brandRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  right: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
  },
  statusText: {
    fontSize: 11,
    fontWeight: 500,
    letterSpacing: '0.04em',
  },
  brandName: {
    fontSize: 12,
    fontWeight: 800,
    letterSpacing: '0.2em',
    color: '#00f0ff',
    textTransform: 'uppercase' as const,
    textShadow: '0 0 10px rgba(0, 240, 255, 0.4), 0 0 30px rgba(0, 240, 255, 0.15)',
  },
  brandSep: {
    color: '#444460',
    fontSize: 12,
    fontWeight: 300,
  },
  brandCompany: {
    fontSize: 11,
    fontWeight: 600,
    letterSpacing: '0.1em',
    color: '#a78bfa',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    textShadow: '0 0 8px rgba(138, 43, 226, 0.25)',
  },
  brandLine: {
    width: 120,
    height: 1,
    background: 'linear-gradient(90deg, rgba(0, 240, 255, 0.0), rgba(0, 240, 255, 0.25), rgba(138, 43, 226, 0.3), rgba(0, 240, 255, 0.25), rgba(0, 240, 255, 0.0))',
    borderRadius: 1,
  },
  cameraIndicator: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.08em',
    color: '#ef4444',
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.25)',
    borderRadius: 4,
    padding: '1px 6px',
    marginLeft: 8,
    animation: 'pulse 2s infinite',
  },
  cameraRedDot: {
    width: 5,
    height: 5,
    borderRadius: '50%',
    background: '#ef4444',
    boxShadow: '0 0 6px rgba(239, 68, 68, 0.6)',
  },
  callIndicator: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.08em',
    color: '#22c55e',
    background: 'rgba(34, 197, 94, 0.1)',
    border: '1px solid rgba(34, 197, 94, 0.25)',
    borderRadius: 4,
    padding: '1px 6px',
    marginLeft: 8,
    animation: 'pulse 2s infinite',
  },
  callIcon: {
    width: 5,
    height: 5,
    borderRadius: '50%',
    background: '#22c55e',
    boxShadow: '0 0 6px rgba(34, 197, 94, 0.6)',
  },
};

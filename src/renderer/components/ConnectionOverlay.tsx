import React, { useMemo } from 'react';

interface ConnectionOverlayProps {
  error: string;
  isConnecting: boolean;
  retryCount: number;
  maxRetries: number;
  onRetry: () => void;
  onOpenSettings: () => void;
  /** When true, error messages reference local infrastructure (Ollama/Whisper) instead of Gemini */
  isLocalMode?: boolean;
}

type ErrorCategory = 'no_api_key' | 'ollama' | 'whisper' | 'network' | 'timeout' | 'auth' | 'generic';

function categorizeError(error: string, isLocal: boolean): ErrorCategory {
  const lower = error.toLowerCase();

  // Local-specific categories
  if (isLocal) {
    if (lower.includes('ollama') || lower.includes('11434') || lower.includes('connection refused')) {
      return 'ollama';
    }
    if (lower.includes('whisper') || (lower.includes('model') && lower.includes('not found'))) {
      return 'whisper';
    }
  }

  if (lower.includes('no gemini api key') || lower.includes('api key') || lower.includes('api_key')) {
    return 'no_api_key';
  }
  if (lower.includes('timed out') || lower.includes('timeout')) {
    return 'timeout';
  }
  if (lower.includes('401') || lower.includes('403') || lower.includes('invalid') || lower.includes('unauthorized')) {
    return 'auth';
  }
  if (lower.includes('network') || lower.includes('failed to fetch') || lower.includes('websocket')) {
    return 'network';
  }
  return 'generic';
}

const ERROR_INFO: Record<ErrorCategory, { title: string; explanation: string; showSettings: boolean }> = {
  no_api_key: {
    title: 'No API Key',
    explanation: 'Friday needs a Gemini API key to connect. Open Settings to add one, or use local mode with Ollama.',
    showSettings: true,
  },
  ollama: {
    title: 'Ollama Not Running',
    explanation: 'Friday uses Ollama for local AI. Make sure Ollama is running (ollama serve) and has a model pulled (ollama pull llama3.2).',
    showSettings: false,
  },
  whisper: {
    title: 'Whisper Model Missing',
    explanation: 'The local speech-to-text model needs to be downloaded. This happens automatically on first launch \u2014 try again.',
    showSettings: false,
  },
  auth: {
    title: 'Authentication Failed',
    explanation: 'Your Gemini API key appears to be invalid or expired. Check your key in Settings.',
    showSettings: true,
  },
  timeout: {
    title: 'Connection Timed Out',
    explanation: 'The connection didn\'t respond in time. This is usually temporary \u2014 try again.',
    showSettings: false,
  },
  network: {
    title: 'Network Error',
    explanation: 'Couldn\'t reach the server. Check your connection.',
    showSettings: false,
  },
  generic: {
    title: 'Connection Failed',
    explanation: 'Something went wrong establishing the connection.',
    showSettings: false,
  },
};

export default function ConnectionOverlay({
  error,
  isConnecting,
  retryCount,
  maxRetries,
  onRetry,
  onOpenSettings,
  isLocalMode = false,
}: ConnectionOverlayProps) {
  const category = useMemo(() => categorizeError(error, isLocalMode), [error, isLocalMode]);
  const info = ERROR_INFO[category];

  // Don't show if there's no error and we're not connecting
  if (!error && !isConnecting) return null;

  // Show a minimal connecting state (not an error)
  if (isConnecting && !error) return null;

  // If retries are in progress, show a compact retry indicator
  if (isConnecting && retryCount > 0) {
    return (
      <div style={styles.overlay}>
        <div style={styles.card}>
          <div style={styles.spinner} />
          <span style={styles.retryText}>
            Retrying... ({retryCount}/{maxRetries})
          </span>
        </div>
      </div>
    );
  }

  // Only show the full error overlay when retries are exhausted
  const retriesExhausted = retryCount >= maxRetries;
  if (!retriesExhausted && !error.startsWith('Failed')) return null;

  return (
    <div style={styles.overlay}>
      <div style={styles.card}>
        {/* Error icon */}
        <div style={styles.iconCircle}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#ef4444" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
        </div>

        <h3 style={styles.title}>{info.title}</h3>
        <p style={styles.explanation}>{info.explanation}</p>

        {/* Raw error detail \u2014 collapsed */}
        <details style={styles.details}>
          <summary style={styles.detailsSummary}>Technical details</summary>
          <code style={styles.errorCode}>{error}</code>
        </details>

        {/* Action buttons */}
        <div style={styles.actions}>
          <button onClick={onRetry} style={styles.retryBtn}>
            Retry Connection
          </button>
          {info.showSettings && (
            <button onClick={onOpenSettings} style={styles.settingsLink}>
              Open Settings
            </button>
          )}
        </div>

        {/* Keyboard hint */}
        <span style={styles.hint}>or click the orb to retry</span>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'absolute',
    inset: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 30,
    pointerEvents: 'none',
  },
  card: {
    pointerEvents: 'auto',
    background: 'rgba(10, 10, 18, 0.92)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 16,
    padding: '32px 40px',
    maxWidth: 420,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 12,
    backdropFilter: 'blur(20px)',
    boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
  },
  iconCircle: {
    width: 48,
    height: 48,
    borderRadius: '50%',
    background: 'rgba(239, 68, 68, 0.1)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 4,
  },
  title: {
    margin: 0,
    fontSize: 18,
    fontWeight: 600,
    color: '#ef4444',
    letterSpacing: '0.02em',
  },
  explanation: {
    margin: 0,
    fontSize: 13,
    color: '#a0a0b8',
    textAlign: 'center',
    lineHeight: 1.5,
    maxWidth: 320,
  },
  details: {
    width: '100%',
    marginTop: 4,
  },
  detailsSummary: {
    fontSize: 11,
    color: '#666680',
    cursor: 'pointer',
    textAlign: 'center',
    userSelect: 'none',
  },
  errorCode: {
    display: 'block',
    marginTop: 8,
    fontSize: 10,
    color: '#888898',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 6,
    padding: '8px 12px',
    wordBreak: 'break-all',
    whiteSpace: 'pre-wrap',
    maxHeight: 80,
    overflow: 'auto',
  },
  actions: {
    display: 'flex',
    gap: 12,
    marginTop: 8,
  },
  retryBtn: {
    background: 'rgba(0, 240, 255, 0.12)',
    border: '1px solid rgba(0, 240, 255, 0.3)',
    borderRadius: 8,
    color: '#00f0ff',
    padding: '8px 20px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  settingsLink: {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    color: '#a0a0b8',
    padding: '8px 20px',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'all 0.2s',
  },
  hint: {
    fontSize: 11,
    color: '#555568',
    marginTop: 4,
  },
  spinner: {
    width: 20,
    height: 20,
    border: '2px solid rgba(0, 240, 255, 0.2)',
    borderTopColor: '#00f0ff',
    borderRadius: '50%',
    animation: 'spin-slow 1s linear infinite',
  },
  retryText: {
    fontSize: 13,
    color: '#8888a0',
  },
};

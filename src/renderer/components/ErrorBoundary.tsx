import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * Global React Error Boundary — catches render errors and prevents white-screen.
 * Displays a recovery UI with error details and a retry button.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, errorInfo: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.setState({ errorInfo });
    console.error('[ErrorBoundary] Caught render error:', error, errorInfo);

    // Report to session health + telemetry if available
    try {
      window.eve?.sessionHealth?.recordError?.('react-error-boundary', error.message);
    } catch {
      // Ignore if IPC isn't available
    }
    try {
      window.eve?.telemetry?.recordError?.(error.name || 'UnknownError', error.message);
    } catch {
      // Ignore if telemetry isn't available
    }
  }

  handleRetry = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div style={styles.container}>
          <div style={styles.card}>
            <div style={styles.icon}>⚠</div>
            <h2 style={styles.title}>Something went wrong</h2>
            <p style={styles.subtitle}>
              The interface hit an unexpected error. Your data and connections are safe.
            </p>

            {this.state.error && (
              <div style={styles.errorBox}>
                <code style={styles.errorText}>
                  {this.state.error.message}
                </code>
              </div>
            )}

            {this.state.errorInfo && (
              <details style={styles.details}>
                <summary style={styles.summary}>Stack trace</summary>
                <pre style={styles.stack}>
                  {this.state.errorInfo.componentStack}
                </pre>
              </details>
            )}

            <div style={styles.actions}>
              <button onClick={this.handleRetry} style={styles.retryBtn}>
                Try Again
              </button>
              <button onClick={this.handleReload} style={styles.reloadBtn}>
                Reload App
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    width: '100%',
    height: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: '#060B19',
    color: '#e0e0e8',
    fontFamily: "'Inter', 'Segoe UI', sans-serif",
  },
  card: {
    maxWidth: 520,
    width: '90%',
    padding: '40px 36px',
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 20,
    textAlign: 'center' as const,
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 16,
    alignItems: 'center',
  },
  icon: {
    fontSize: 48,
    marginBottom: 8,
  },
  title: {
    fontSize: 20,
    fontWeight: 700,
    color: '#fff',
    margin: 0,
  },
  subtitle: {
    fontSize: 14,
    color: '#999',
    lineHeight: '1.5',
    margin: 0,
    maxWidth: 360,
  },
  errorBox: {
    width: '100%',
    padding: '12px 16px',
    background: 'rgba(239, 68, 68, 0.08)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 10,
    textAlign: 'left' as const,
  },
  errorText: {
    fontSize: 12,
    color: '#f87171',
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    wordBreak: 'break-all' as const,
  },
  details: {
    width: '100%',
    textAlign: 'left' as const,
  },
  summary: {
    fontSize: 12,
    color: '#666',
    cursor: 'pointer',
    padding: '4px 0',
  },
  stack: {
    fontSize: 11,
    color: '#888',
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    overflow: 'auto' as const,
    maxHeight: 200,
    padding: '8px 12px',
    background: 'rgba(0, 0, 0, 0.3)',
    borderRadius: 8,
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-all' as const,
  },
  actions: {
    display: 'flex',
    gap: 12,
    marginTop: 8,
  },
  retryBtn: {
    background: 'rgba(0, 240, 255, 0.12)',
    border: '1px solid rgba(0, 240, 255, 0.3)',
    borderRadius: 10,
    color: '#00f0ff',
    fontSize: 14,
    fontWeight: 600,
    padding: '10px 28px',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  reloadBtn: {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    borderRadius: 10,
    color: '#aaa',
    fontSize: 14,
    fontWeight: 500,
    padding: '10px 28px',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
};

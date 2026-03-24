/**
 * IntegrationsStep.tsx — External service integrations.
 *
 * "Integrations." — Optional connections to Google Calendar, Obsidian,
 * Telegram messaging gateway, and system-level features like auto-launch
 * and file watching. All integrations are optional.
 */

import React, { useState, useCallback, useEffect } from 'react';
import { Calendar, BookOpen, MessageSquare, Settings2, Check, Loader2 } from 'lucide-react';
import CyberInput from './shared/CyberInput';
import NextButton from './shared/NextButton';

interface IntegrationsStepProps {
  onComplete: () => void;
  onBack?: () => void;
}

interface IntegrationState {
  calendarConnected: boolean;
  calendarConnecting: boolean;
  calendarError: string;
  obsidianVaultPath: string;
  gatewayEnabled: boolean;
  telegramToken: string;
  telegramOwnerId: string;
  hasTelegramToken: boolean;
  autoLaunch: boolean;
  fileWatcherEnabled: boolean;
}

const INITIAL_STATE: IntegrationState = {
  calendarConnected: false,
  calendarConnecting: false,
  calendarError: '',
  obsidianVaultPath: '',
  gatewayEnabled: false,
  telegramToken: '',
  telegramOwnerId: '',
  hasTelegramToken: false,
  autoLaunch: false,
  fileWatcherEnabled: false,
};

const IntegrationsStep: React.FC<IntegrationsStepProps> = ({ onComplete }) => {
  const [fadeIn, setFadeIn] = useState(false);
  const [state, setState] = useState<IntegrationState>(INITIAL_STATE);

  const update = useCallback(<K extends keyof IntegrationState>(key: K, value: IntegrationState[K]) => {
    setState((prev) => ({ ...prev, [key]: value }));
  }, []);

  /* ---- Load existing settings on mount ---- */
  useEffect(() => {
    let cancelled = false;
    setTimeout(() => { if (!cancelled) setFadeIn(true); }, 100);

    (async () => {
      try {
        const settings = await window.eve.settings.get() as Record<string, unknown>;
        if (cancelled) return;

        let calendarAuth = false;
        try {
          calendarAuth = await window.eve.calendar.isAuthenticated();
        } catch {
          // Calendar API may not be available
        }

        setState((prev) => ({
          ...prev,
          calendarConnected: calendarAuth || !!settings.googleCalendarEnabled,
          obsidianVaultPath: String(settings.obsidianVaultPath || ''),
          gatewayEnabled: !!settings.gatewayEnabled,
          hasTelegramToken: !!settings.hasTelegramToken,
          telegramOwnerId: String(settings.telegramOwnerId || ''),
          autoLaunch: !!settings.autoLaunch,
        }));
      } catch {
        // Settings unavailable
      }
    })();

    return () => { cancelled = true; };
  }, []);

  /* ---- Google Calendar ---- */
  const handleCalendarConnect = useCallback(async () => {
    update('calendarConnecting', true);
    update('calendarError', '');
    try {
      const result = await window.eve.calendar.authenticate();
      if (result) {
        update('calendarConnected', true);
      } else {
        update('calendarError', 'Authentication was not completed');
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to connect';
      update('calendarError', message);
    }
    update('calendarConnecting', false);
  }, [update]);

  /* ---- Obsidian vault path ---- */
  const handleSaveVaultPath = useCallback(async () => {
    const trimmed = state.obsidianVaultPath.trim();
    if (!trimmed) return;
    try {
      await window.eve.settings.setObsidianVaultPath(trimmed);
    } catch {
      // Best effort
    }
  }, [state.obsidianVaultPath]);

  /* ---- Messaging gateway ---- */
  const handleToggleGateway = useCallback(async () => {
    const newValue = !state.gatewayEnabled;
    update('gatewayEnabled', newValue);
    try {
      await window.eve.gateway.setEnabled(newValue);
    } catch {
      // Best effort
    }
  }, [state.gatewayEnabled, update]);

  const handleSaveTelegramToken = useCallback(async () => {
    const trimmed = state.telegramToken.trim();
    if (!trimmed) return;
    try {
      await window.eve.settings.setTelegramConfig(trimmed, state.telegramOwnerId.trim());
      update('hasTelegramToken', true);
    } catch {
      // Best effort
    }
  }, [state.telegramToken, state.telegramOwnerId, update]);

  const handleSaveTelegramOwnerId = useCallback(async () => {
    const trimmed = state.telegramOwnerId.trim();
    if (!trimmed) return;
    try {
      await window.eve.settings.setTelegramConfig(state.telegramToken.trim(), trimmed);
    } catch {
      // Best effort
    }
  }, [state.telegramToken, state.telegramOwnerId]);

  /* ---- System toggles ---- */
  const handleToggleAutoLaunch = useCallback(async () => {
    const newValue = !state.autoLaunch;
    update('autoLaunch', newValue);
    try {
      await window.eve.settings.setAutoLaunch(newValue);
    } catch {
      // Best effort
    }
  }, [state.autoLaunch, update]);

  const handleToggleFileWatcher = useCallback(async () => {
    const newValue = !state.fileWatcherEnabled;
    update('fileWatcherEnabled', newValue);
    try {
      await window.eve.settings.set('fileWatcherEnabled', newValue);
    } catch {
      // Best effort
    }
  }, [state.fileWatcherEnabled, update]);

  /* ---- Render ---- */
  return (
    <section style={{
      ...styles.container,
      opacity: fadeIn ? 1 : 0,
      transform: fadeIn ? 'translateY(0)' : 'translateY(16px)',
      transition: 'all 0.7s cubic-bezier(0.16, 1, 0.3, 1)',
    }} aria-label="External service integrations">
      <div style={styles.headerBlock}>
        <h2 style={styles.heading}>Integrations.</h2>
        <p style={styles.subtitle}>
          Connect external services. Everything here is optional.
        </p>
      </div>

      <div style={styles.scrollArea}>
        {/* ─── Section 1: Productivity ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(59, 130, 246, 0.08)', border: '1px solid rgba(59, 130, 246, 0.15)' }}>
              <Calendar size={18} color="#3b82f6" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Productivity</div>
              <div style={styles.sectionDesc}>Calendar and knowledge base</div>
            </div>
          </div>

          {/* Google Calendar */}
          <div style={styles.integrationRow}>
            <div style={styles.integrationInfo}>
              <span style={styles.integrationLabel}>Google Calendar</span>
              <span style={styles.integrationHint}>Sync events and schedule management</span>
            </div>
            <div style={styles.integrationActions}>
              {state.calendarConnected ? (
                <span style={styles.connectedBadge} role="status" aria-live="polite">
                  <Check size={10} aria-hidden="true" />
                  Connected
                </span>
              ) : (
                <button
                  onClick={handleCalendarConnect}
                  disabled={state.calendarConnecting}
                  style={styles.connectButton}
                >
                  {state.calendarConnecting ? (
                    <>
                      <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} />
                      <span>Connecting...</span>
                    </>
                  ) : (
                    'Connect with Google'
                  )}
                </button>
              )}
            </div>
            {state.calendarError && (
              <span style={styles.errorText} role="alert">{state.calendarError}</span>
            )}
          </div>

          {/* Obsidian Vault */}
          <div style={styles.integrationRow}>
            <div style={styles.integrationInfo}>
              <div style={styles.integrationLabelRow}>
                <BookOpen size={14} color="var(--text-40)" aria-hidden="true" />
                <span style={styles.integrationLabel}>Obsidian Vault</span>
              </div>
              <span style={styles.integrationHint}>Sync memories & notes with your knowledge base</span>
            </div>
            <div style={styles.vaultInputRow}>
              <CyberInput
                id="obsidian-vault-path"
                label="Vault Path"
                value={state.obsidianVaultPath}
                onChange={(v) => update('obsidianVaultPath', v)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveVaultPath();
                }}
              />
              {state.obsidianVaultPath.trim() && (
                <button onClick={handleSaveVaultPath} style={styles.saveSmallButton}>
                  Save
                </button>
              )}
            </div>
          </div>
        </div>

        {/* ─── Section 2: Messaging Gateway ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(138, 43, 226, 0.08)', border: '1px solid rgba(138, 43, 226, 0.15)' }}>
              <MessageSquare size={18} color="#8A2BE2" />
            </div>
            <div>
              <div style={styles.sectionTitle}>Messaging Gateway</div>
              <div style={styles.sectionDesc}>Let people reach your agent externally</div>
            </div>
          </div>

          {/* Master toggle */}
          <button
            onClick={handleToggleGateway}
            style={styles.toggleRow}
            role="checkbox"
            aria-checked={state.gatewayEnabled}
          >
            <div style={{
              ...styles.toggleTrack,
              background: state.gatewayEnabled ? 'var(--accent-cyan-10)' : 'rgba(255,255,255,0.04)',
              borderColor: state.gatewayEnabled ? 'var(--accent-cyan-30)' : 'rgba(255,255,255,0.06)',
            }}>
              <div style={{
                ...styles.toggleThumb,
                transform: state.gatewayEnabled ? 'translateX(16px)' : 'translateX(0)',
                background: state.gatewayEnabled ? 'var(--accent-cyan)' : 'rgba(255,255,255,0.2)',
              }} />
            </div>
            <span style={styles.toggleLabel}>Enable messaging gateway</span>
          </button>

          {state.gatewayEnabled && (
            <div style={styles.gatewayFields}>
              {/* Telegram Bot Token */}
              <div style={styles.field}>
                <CyberInput
                  id="telegram-bot-token"
                  label="Telegram Bot Token"
                  value={state.telegramToken}
                  onChange={(v) => update('telegramToken', v)}
                  type="password"
                  monospace
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSaveTelegramToken();
                  }}
                  success={state.hasTelegramToken && !state.telegramToken.trim()}
                />
                {state.hasTelegramToken && !state.telegramToken.trim() && (
                  <span style={styles.savedHint}>
                    <Check size={10} aria-hidden="true" /> Saved
                  </span>
                )}
                {state.telegramToken.trim() && (
                  <button onClick={handleSaveTelegramToken} style={styles.saveSmallButton}>
                    Save Token
                  </button>
                )}
              </div>

              {/* Telegram Owner Chat ID */}
              <div style={styles.field}>
                <CyberInput
                  id="telegram-owner-id"
                  label="Telegram Owner Chat ID"
                  value={state.telegramOwnerId}
                  onChange={(v) => update('telegramOwnerId', v)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSaveTelegramOwnerId();
                  }}
                />
                {state.telegramOwnerId.trim() && (
                  <button onClick={handleSaveTelegramOwnerId} style={styles.saveSmallButton}>
                    Save ID
                  </button>
                )}
              </div>

              {/* Discord — coming soon */}
              <div style={styles.disabledRow}>
                <MessageSquare size={12} color="var(--text-20)" aria-hidden="true" />
                <span style={styles.disabledLabel}>Discord</span>
                <span style={styles.comingSoonBadge}>Coming soon</span>
              </div>
            </div>
          )}
        </div>

        {/* ─── Section 3: System ─── */}
        <div style={styles.sectionCard}>
          <div style={styles.sectionHeader}>
            <div style={{ ...styles.sectionIconBox, background: 'rgba(34, 197, 94, 0.08)', border: '1px solid rgba(34, 197, 94, 0.15)' }}>
              <Settings2 size={18} color="#22c55e" />
            </div>
            <div>
              <div style={styles.sectionTitle}>System</div>
              <div style={styles.sectionDesc}>Startup and monitoring</div>
            </div>
          </div>

          <button
            onClick={handleToggleAutoLaunch}
            style={styles.toggleRow}
            role="checkbox"
            aria-checked={state.autoLaunch}
          >
            <div style={{
              ...styles.toggleTrack,
              background: state.autoLaunch ? 'var(--accent-cyan-10)' : 'rgba(255,255,255,0.04)',
              borderColor: state.autoLaunch ? 'var(--accent-cyan-30)' : 'rgba(255,255,255,0.06)',
            }}>
              <div style={{
                ...styles.toggleThumb,
                transform: state.autoLaunch ? 'translateX(16px)' : 'translateX(0)',
                background: state.autoLaunch ? 'var(--accent-cyan)' : 'rgba(255,255,255,0.2)',
              }} />
            </div>
            <span style={styles.toggleLabel}>Start on Windows login</span>
          </button>

          <button
            onClick={handleToggleFileWatcher}
            style={styles.toggleRow}
            role="checkbox"
            aria-checked={state.fileWatcherEnabled}
          >
            <div style={{
              ...styles.toggleTrack,
              background: state.fileWatcherEnabled ? 'var(--accent-cyan-10)' : 'rgba(255,255,255,0.04)',
              borderColor: state.fileWatcherEnabled ? 'var(--accent-cyan-30)' : 'rgba(255,255,255,0.06)',
            }}>
              <div style={{
                ...styles.toggleThumb,
                transform: state.fileWatcherEnabled ? 'translateX(16px)' : 'translateX(0)',
                background: state.fileWatcherEnabled ? 'var(--accent-cyan)' : 'rgba(255,255,255,0.2)',
              }} />
            </div>
            <span style={styles.toggleLabel}>Monitor folders for changes</span>
          </button>
        </div>
      </div>

      <NextButton label="Continue" onClick={onComplete} />

      <p style={styles.hint}>
        All integrations are optional and can be configured later in Settings.
      </p>
    </section>
  );
};

/* ── Styles ─────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 24,
    maxWidth: 560,
    width: '100%',
    padding: '0 24px',
  },
  headerBlock: {
    textAlign: 'center',
    maxWidth: 500,
  },
  heading: {
    fontSize: 28,
    fontWeight: 300,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
    letterSpacing: '0.05em',
    margin: '0 0 12px 0',
  },
  subtitle: {
    fontSize: 13,
    color: 'var(--text-30)',
    textAlign: 'center',
    lineHeight: 1.6,
    margin: 0,
    fontFamily: "'Inter', sans-serif",
  },
  scrollArea: {
    width: '100%',
    maxHeight: 440,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 20,
    paddingRight: 4,
  },
  sectionCard: {
    background: 'var(--onboarding-card)',
    border: '1px solid rgba(255, 255, 255, 0.04)',
    borderRadius: 12,
    padding: '20px 24px',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  sectionHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 14,
  },
  sectionIconBox: {
    width: 40,
    height: 40,
    borderRadius: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    flexShrink: 0,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  sectionDesc: {
    fontSize: 11,
    color: 'var(--text-30)',
    fontFamily: "'Inter', sans-serif",
  },

  /* Productivity integration rows */
  integrationRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    padding: '12px 0',
    borderTop: '1px solid rgba(255,255,255,0.03)',
  },
  integrationInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  integrationLabelRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  integrationLabel: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-primary)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  integrationHint: {
    fontSize: 10,
    color: 'var(--text-20)',
    fontFamily: "'Inter', sans-serif",
  },
  integrationActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  connectedBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 11,
    fontWeight: 500,
    color: 'rgba(34, 197, 94, 0.9)',
    padding: '5px 12px',
    borderRadius: 6,
    background: 'rgba(34, 197, 94, 0.06)',
    border: '1px solid rgba(34, 197, 94, 0.2)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  connectButton: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 12,
    fontWeight: 500,
    color: 'var(--accent-cyan-90)',
    padding: '8px 16px',
    borderRadius: 6,
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    cursor: 'pointer',
    fontFamily: "'Space Grotesk', sans-serif",
    transition: 'all 0.2s ease',
  },
  errorText: {
    fontSize: 10,
    color: 'var(--accent-red)',
    fontFamily: "'Inter', sans-serif",
  },
  vaultInputRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  saveSmallButton: {
    alignSelf: 'flex-start',
    fontSize: 11,
    fontWeight: 500,
    color: 'var(--accent-cyan-90)',
    padding: '6px 14px',
    borderRadius: 6,
    background: 'var(--accent-cyan-10)',
    border: '1px solid var(--accent-cyan-20)',
    cursor: 'pointer',
    fontFamily: "'Space Grotesk', sans-serif",
    transition: 'all 0.2s ease',
  },

  /* Toggle rows */
  toggleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '8px 0',
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    textAlign: 'left',
    width: '100%',
  },
  toggleTrack: {
    width: 36,
    height: 20,
    borderRadius: 10,
    border: '1px solid rgba(255,255,255,0.06)',
    background: 'rgba(255,255,255,0.04)',
    position: 'relative',
    flexShrink: 0,
    transition: 'all 0.2s ease',
  },
  toggleThumb: {
    width: 14,
    height: 14,
    borderRadius: '50%',
    position: 'absolute',
    top: 2,
    left: 2,
    transition: 'all 0.2s ease',
  },
  toggleLabel: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-60)',
    fontFamily: "'Space Grotesk', sans-serif",
  },

  /* Gateway fields */
  gatewayFields: {
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
    paddingLeft: 4,
  },
  field: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  savedHint: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    fontSize: 10,
    color: 'var(--accent-cyan-50)',
    fontFamily: "'Space Grotesk', sans-serif",
  },
  disabledRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 0',
    opacity: 0.4,
  },
  disabledLabel: {
    fontSize: 13,
    fontWeight: 500,
    color: 'var(--text-40)',
    fontFamily: "'Space Grotesk', sans-serif",
    flex: 1,
  },
  comingSoonBadge: {
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: '0.08em',
    color: 'var(--text-30)',
    padding: '3px 8px',
    borderRadius: 4,
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    fontFamily: "'JetBrains Mono', monospace",
  },

  hint: {
    fontSize: 10,
    color: 'var(--text-20)',
    margin: 0,
    textAlign: 'center',
    fontFamily: "'Inter', sans-serif",
    maxWidth: 400,
  },
};

export default IntegrationsStep;

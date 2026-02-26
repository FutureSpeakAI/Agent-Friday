import React, { useState, useEffect, useCallback } from 'react';

type Tab = 'general' | 'memory' | 'tasks';

interface SettingsProps {
  visible: boolean;
  onClose: () => void;
}

interface MaskedSettings {
  autoLaunch: boolean;
  autoScreenCapture: boolean;
  obsidianVaultPath: string;
  hasGeminiKey: boolean;
  hasAnthropicKey: boolean;
  hasElevenLabsKey: boolean;
  hasOpenaiKey: boolean;
  hasPerplexityKey: boolean;
  hasFirecrawlKey: boolean;
  geminiKeyHint: string;
  anthropicKeyHint: string;
  elevenLabsKeyHint: string;
  openaiKeyHint: string;
  perplexityKeyHint: string;
  firecrawlKeyHint: string;
  agentVoicesEnabled: boolean;
  wakeWordEnabled: boolean;
  notificationWhisperEnabled: boolean;
  notificationAllowedApps: string[];
  clipboardIntelligenceEnabled: boolean;
  googleCalendarEnabled: boolean;
}

interface LongTermEntry {
  id: string;
  fact: string;
  category: string;
  confirmed: boolean;
  source: string;
}

interface MediumTermEntry {
  id: string;
  observation: string;
  category: string;
  confidence: number;
  occurrences: number;
}

interface TaskEntry {
  id: string;
  description: string;
  type: string;
  action: string;
  payload: string;
  enabled: boolean;
  triggerTime?: number;
  cronPattern?: string;
}

/* ── Reusable sub-components ── */

function SectionHeader({ children }: { children: React.ReactNode }) {
  return <h3 style={styles.sectionTitle}>{children}</h3>;
}

function Toggle({
  value,
  label,
  hint,
  onToggle,
}: {
  value: boolean;
  label: string;
  hint?: string;
  onToggle: () => void;
}) {
  return (
    <>
      <div style={styles.toggleRow} onClick={onToggle}>
        <div
          style={{
            ...styles.toggle,
            background: value ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
            borderColor: value ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
          }}
        >
          <div
            style={{
              ...styles.toggleDot,
              transform: value ? 'translateX(16px)' : 'translateX(0)',
              background: value ? '#00f0ff' : '#555568',
            }}
          />
        </div>
        <span style={styles.toggleLabel}>{label}</span>
      </div>
      {hint && <div style={styles.toggleHint}>{hint}</div>}
    </>
  );
}

function ApiKeyField({
  label,
  hasKey,
  hint,
  value,
  onChange,
  onSave,
  description,
}: {
  label: string;
  hasKey: boolean;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  onSave: () => void;
  description?: string;
}) {
  return (
    <div style={styles.fieldGroup}>
      <label style={styles.label}>
        {label}
        {hasKey && <span style={styles.keyHint}>{hint}</span>}
        {hasKey && <span style={styles.connectedDot} />}
      </label>
      <div style={styles.keyRow}>
        <input
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={hasKey ? 'Enter new key to replace' : `Paste your ${label}`}
          style={styles.keyInput}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && value.trim()) onSave();
          }}
        />
        <button onClick={onSave} style={styles.saveBtn} disabled={!value.trim()}>
          Save
        </button>
      </div>
      {description && <div style={styles.toggleHint}>{description}</div>}
    </div>
  );
}

function Divider() {
  return <div style={styles.divider} />;
}

/* ── Main Settings component ── */

export default function Settings({ visible, onClose }: SettingsProps) {
  const [tab, setTab] = useState<Tab>('general');
  const [settings, setSettings] = useState<MaskedSettings | null>(null);
  const [longTerm, setLongTerm] = useState<LongTermEntry[]>([]);
  const [mediumTerm, setMediumTerm] = useState<MediumTermEntry[]>([]);
  const [tasks, setTasks] = useState<TaskEntry[]>([]);

  // API key input states
  const [geminiKey, setGeminiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [elevenLabsKey, setElevenLabsKey] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [perplexityKey, setPerplexityKey] = useState('');
  const [firecrawlKey, setFirecrawlKey] = useState('');
  const [vaultPath, setVaultPath] = useState('');
  const [saveMsg, setSaveMsg] = useState('');

  const loadSettings = useCallback(async () => {
    try {
      const s = await window.eve.settings.get();
      setSettings(s as unknown as MaskedSettings);
    } catch {
      // ignore
    }
  }, []);

  const loadMemory = useCallback(async () => {
    try {
      const [lt, mt] = await Promise.all([
        window.eve.memory.getLongTerm(),
        window.eve.memory.getMediumTerm(),
      ]);
      setLongTerm(lt);
      setMediumTerm(mt);
    } catch {
      // ignore
    }
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const t = await window.eve.scheduler.listTasks();
      setTasks(t);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    loadSettings();
    loadMemory();
    loadTasks();
  }, [visible, loadSettings, loadMemory, loadTasks]);

  const overlayRef = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (visible) {
      setTimeout(() => overlayRef.current?.focus(), 50);
    }
  }, [visible]);

  // Confirmation dialog state
  const [confirmAction, setConfirmAction] = useState<{
    message: string;
    onConfirm: () => void;
  } | null>(null);

  if (!visible) return null;

  const flash = (msg: string) => {
    setSaveMsg(msg);
    setTimeout(() => setSaveMsg(''), 3000);
  };

  const saveApiKey = async (
    key: 'gemini' | 'anthropic' | 'elevenlabs' | 'openai' | 'perplexity' | 'firecrawl',
    value: string,
    setter: (v: string) => void,
    successMsg: string,
  ) => {
    if (!value.trim()) return;
    await window.eve.settings.setApiKey(key, value.trim());
    setter('');
    flash(successMsg);
    await loadSettings();
  };

  const handleSaveVaultPath = async () => {
    await window.eve.settings.setObsidianVaultPath(vaultPath.trim());
    setVaultPath('');
    flash(vaultPath.trim() ? 'Obsidian vault linked — memories will sync' : 'Obsidian vault disconnected');
    await loadSettings();
  };

  const handleClearVaultPath = async () => {
    await window.eve.settings.setObsidianVaultPath('');
    setVaultPath('');
    flash('Obsidian vault disconnected');
    await loadSettings();
  };

  const handleDeleteLongTerm = (id: string) => {
    setConfirmAction({
      message: 'Delete this memory? This cannot be undone.',
      onConfirm: async () => {
        await window.eve.memory.deleteLongTerm(id);
        await loadMemory();
        setConfirmAction(null);
      },
    });
  };

  const handleDeleteMediumTerm = (id: string) => {
    setConfirmAction({
      message: 'Delete this observation? This cannot be undone.',
      onConfirm: async () => {
        await window.eve.memory.deleteMediumTerm(id);
        await loadMemory();
        setConfirmAction(null);
      },
    });
  };

  const handleDeleteTask = (id: string) => {
    setConfirmAction({
      message: 'Delete this scheduled task? This cannot be undone.',
      onConfirm: async () => {
        await window.eve.scheduler.deleteTask(id);
        await loadTasks();
        setConfirmAction(null);
      },
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  };

  const toggleSetting = async (key: string) => {
    if (!settings) return;
    await window.eve.settings.set(key, !(settings as unknown as Record<string, boolean>)[key]);
    await loadSettings();
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'general', label: 'General' },
    { key: 'memory', label: 'Memory' },
    { key: 'tasks', label: 'Tasks' },
  ];

  return (
    <div ref={overlayRef} style={styles.overlay} onKeyDown={handleKeyDown} tabIndex={-1}>
      <div style={styles.panel}>
        {/* Confirmation dialog */}
        {confirmAction && (
          <div style={styles.confirmOverlay}>
            <div style={styles.confirmBox}>
              <div style={styles.confirmMsg}>{confirmAction.message}</div>
              <div style={styles.confirmBtns}>
                <button onClick={() => setConfirmAction(null)} style={styles.confirmCancel}>
                  Cancel
                </button>
                <button onClick={confirmAction.onConfirm} style={styles.confirmDelete}>
                  Delete
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Header */}
        <div style={styles.header}>
          <span style={styles.headerIcon}>⚙</span>
          <span style={styles.headerTitle}>Settings</span>
          <button onClick={onClose} style={styles.closeBtn}>✕</button>
        </div>

        {/* Tabs */}
        <div style={styles.tabs}>
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                ...styles.tab,
                ...(tab === t.key ? styles.tabActive : {}),
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Save feedback */}
        {saveMsg && <div style={styles.saveMsg}>{saveMsg}</div>}

        {/* Content — scrollable with custom scrollbar */}
        <div className="settings-scroll" style={styles.content}>
          {tab === 'general' && settings && (
            <div style={styles.section}>
              {/* ═══════════════ CORE API KEYS ═══════════════ */}
              <SectionHeader>Core API Keys</SectionHeader>
              <div style={styles.sectionHint}>
                Required for core functionality — Gemini powers voice, Claude powers reasoning
              </div>

              <ApiKeyField
                label="Gemini API Key"
                hasKey={settings.hasGeminiKey}
                hint={settings.geminiKeyHint}
                value={geminiKey}
                onChange={setGeminiKey}
                onSave={() => saveApiKey('gemini', geminiKey, setGeminiKey, 'Gemini key saved — restart to apply')}
                description="Powers real-time voice conversation via Gemini Live"
              />

              <ApiKeyField
                label="Anthropic API Key"
                hasKey={settings.hasAnthropicKey}
                hint={settings.anthropicKeyHint}
                value={anthropicKey}
                onChange={setAnthropicKey}
                onSave={() =>
                  saveApiKey('anthropic', anthropicKey, setAnthropicKey, 'Anthropic key saved — restart to apply')
                }
                description="Claude handles deep research, code analysis, and psychological profiling"
              />

              <Divider />

              {/* ═══════════════ SERVICE API KEYS ═══════════════ */}
              <SectionHeader>Service API Keys</SectionHeader>
              <div style={styles.sectionHint}>
                Optional services that enhance capabilities — configure as needed
              </div>

              <ApiKeyField
                label="ElevenLabs API Key"
                hasKey={settings.hasElevenLabsKey}
                hint={settings.elevenLabsKeyHint}
                value={elevenLabsKey}
                onChange={setElevenLabsKey}
                onSave={() =>
                  saveApiKey('elevenlabs', elevenLabsKey, setElevenLabsKey, 'ElevenLabs key saved — agent voices enabled')
                }
                description="Distinct voices for sub-agents (Atlas, Nova, Cipher)"
              />

              <ApiKeyField
                label="OpenAI API Key"
                hasKey={settings.hasOpenaiKey}
                hint={settings.openaiKeyHint}
                value={openaiKey}
                onChange={setOpenaiKey}
                onSave={() =>
                  saveApiKey('openai', openaiKey, setOpenaiKey, 'OpenAI key saved')
                }
                description="Used for embeddings and specialized model calls"
              />

              <ApiKeyField
                label="Perplexity API Key"
                hasKey={settings.hasPerplexityKey}
                hint={settings.perplexityKeyHint}
                value={perplexityKey}
                onChange={setPerplexityKey}
                onSave={() =>
                  saveApiKey('perplexity', perplexityKey, setPerplexityKey, 'Perplexity key saved')
                }
                description="Powers live web search and real-time information retrieval"
              />

              <ApiKeyField
                label="Firecrawl API Key"
                hasKey={settings.hasFirecrawlKey}
                hint={settings.firecrawlKeyHint}
                value={firecrawlKey}
                onChange={setFirecrawlKey}
                onSave={() =>
                  saveApiKey('firecrawl', firecrawlKey, setFirecrawlKey, 'Firecrawl key saved')
                }
                description="Web scraping and deep page analysis for research tasks"
              />

              <Divider />

              {/* ═══════════════ VOICE & AUDIO ═══════════════ */}
              <SectionHeader>Voice &amp; Audio</SectionHeader>

              <Toggle
                value={settings.agentVoicesEnabled}
                label="Sub-agents speak with distinct voices"
                hint="Atlas, Nova, and Cipher each get their own ElevenLabs voice when delivering results"
                onToggle={() => toggleSetting('agentVoicesEnabled')}
              />

              <Toggle
                value={settings.wakeWordEnabled}
                label={'Say "Hey Friday" to connect'}
                hint="Listens for wake word when disconnected — auto-connects on detection"
                onToggle={() => toggleSetting('wakeWordEnabled')}
              />

              <Divider />

              {/* ═══════════════ INTELLIGENCE FEATURES ═══════════════ */}
              <SectionHeader>Intelligence Features</SectionHeader>

              <Toggle
                value={settings.autoScreenCapture}
                label="Auto-share screen on connect"
                hint="Agent sees your screen when connected — disable for privacy"
                onToggle={async () => {
                  await window.eve.settings.setAutoScreenCapture(!settings.autoScreenCapture);
                  await loadSettings();
                }}
              />

              <Toggle
                value={settings.notificationWhisperEnabled}
                label="Notification whisper"
                hint="Captures notifications from allowed apps and mentions them naturally"
                onToggle={() => toggleSetting('notificationWhisperEnabled')}
              />

              <Toggle
                value={settings.clipboardIntelligenceEnabled}
                label="Clipboard intelligence"
                hint="Monitors clipboard for URLs, code, and context — surfaces relevant info naturally"
                onToggle={() => toggleSetting('clipboardIntelligenceEnabled')}
              />

              <Divider />

              {/* ═══════════════ INTEGRATIONS ═══════════════ */}
              <SectionHeader>Integrations</SectionHeader>

              <Toggle
                value={settings.googleCalendarEnabled}
                label="Google Calendar"
                hint="Reads your schedule, prepares meeting briefings, and can create events"
                onToggle={() => toggleSetting('googleCalendarEnabled')}
              />
              {settings.googleCalendarEnabled && (
                <div style={{ marginTop: 4, paddingLeft: 48 }}>
                  <button
                    onClick={async () => {
                      const success = await window.eve.calendar.authenticate();
                      if (success) {
                        flash('Google Calendar connected successfully!');
                      }
                    }}
                    style={{
                      ...styles.saveBtn,
                      width: '100%',
                      padding: '10px 16px',
                      fontSize: 13,
                      cursor: 'pointer',
                    }}
                  >
                    Connect Google Calendar
                  </button>
                  <div style={{ ...styles.toggleHint, paddingLeft: 0, marginTop: 6 }}>
                    Opens Google sign-in — grants read/write access to your primary calendar
                  </div>
                </div>
              )}

              {/* Obsidian vault */}
              <div style={{ marginTop: 8 }}>
                <label style={{ ...styles.label, fontSize: 13, color: '#d0d0d8', marginBottom: 6 }}>
                  Knowledge Graph (Obsidian)
                </label>
                {settings.obsidianVaultPath ? (
                  <div style={styles.fieldGroup}>
                    <div style={styles.vaultConnected}>
                      <span style={styles.vaultDot} />
                      <span style={styles.vaultPathText}>{settings.obsidianVaultPath}</span>
                    </div>
                    <div style={styles.toggleHint}>
                      Memories sync to <code style={styles.codeBadge}>EVE/memories/</code> and observations to{' '}
                      <code style={styles.codeBadge}>EVE/observations/</code>
                    </div>
                    <button onClick={handleClearVaultPath} style={styles.disconnectBtn}>
                      Disconnect vault
                    </button>
                  </div>
                ) : (
                  <div style={styles.fieldGroup}>
                    <div style={styles.keyRow}>
                      <input
                        type="text"
                        value={vaultPath}
                        onChange={(e) => setVaultPath(e.target.value)}
                        placeholder="C:\Users\you\Documents\MyVault"
                        style={styles.keyInput}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && vaultPath.trim()) handleSaveVaultPath();
                        }}
                      />
                      <button onClick={handleSaveVaultPath} style={styles.saveBtn} disabled={!vaultPath.trim()}>
                        Link
                      </button>
                    </div>
                    <div style={styles.toggleHint}>
                      Paste the full path to your Obsidian vault. An EVE/ folder will be created inside it.
                    </div>
                  </div>
                )}
              </div>

              <Divider />

              {/* ═══════════════ SYSTEM ═══════════════ */}
              <SectionHeader>System</SectionHeader>

              <Toggle
                value={settings.autoLaunch}
                label="Launch Agent Friday on startup"
                hint="Starts automatically when you log in"
                onToggle={async () => {
                  await window.eve.settings.setAutoLaunch(!settings.autoLaunch);
                  await loadSettings();
                }}
              />

              <Divider />

              <SectionHeader>Keyboard Shortcuts</SectionHeader>
              <div style={styles.shortcutList}>
                <div style={styles.shortcutRow}>
                  <span style={styles.shortcutKey}>Space</span>
                  <span style={styles.shortcutDesc}>Toggle microphone</span>
                </div>
                <div style={styles.shortcutRow}>
                  <span style={styles.shortcutKey}>Tab</span>
                  <span style={styles.shortcutDesc}>Toggle text input</span>
                </div>
                <div style={styles.shortcutRow}>
                  <span style={styles.shortcutKey}>Ctrl+Shift+N</span>
                  <span style={styles.shortcutDesc}>Show/hide Agent Friday (global)</span>
                </div>
                <div style={styles.shortcutRow}>
                  <span style={styles.shortcutKey}>Escape</span>
                  <span style={styles.shortcutDesc}>Close settings / panels</span>
                </div>
              </div>

              {/* Bottom spacer for comfortable scrolling */}
              <div style={{ height: 20 }} />
            </div>
          )}

          {tab === 'general' && !settings && (
            <div style={styles.loading}>Loading settings...</div>
          )}

          {tab === 'memory' && (
            <MemoryTab
              longTerm={longTerm}
              mediumTerm={mediumTerm}
              onDeleteLongTerm={handleDeleteLongTerm}
              onDeleteMediumTerm={handleDeleteMediumTerm}
            />
          )}

          {tab === 'tasks' && <TasksTab tasks={tasks} onDelete={handleDeleteTask} />}
        </div>
      </div>

      {/* Injected scrollbar styles */}
      <style>{`
        .settings-scroll::-webkit-scrollbar {
          width: 6px;
        }
        .settings-scroll::-webkit-scrollbar-track {
          background: transparent;
        }
        .settings-scroll::-webkit-scrollbar-thumb {
          background: rgba(0, 240, 255, 0.15);
          border-radius: 3px;
        }
        .settings-scroll::-webkit-scrollbar-thumb:hover {
          background: rgba(0, 240, 255, 0.3);
        }
      `}</style>
    </div>
  );
}

/* --- Memory Tab --- */

function MemoryTab({
  longTerm,
  mediumTerm,
  onDeleteLongTerm,
  onDeleteMediumTerm,
}: {
  longTerm: LongTermEntry[];
  mediumTerm: MediumTermEntry[];
  onDeleteLongTerm: (id: string) => void;
  onDeleteMediumTerm: (id: string) => void;
}) {
  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>
        Long-term Memory
        <span style={styles.badge}>{longTerm.length}</span>
      </h3>
      {longTerm.length === 0 ? (
        <div style={styles.emptyState}>No memories yet — talk to your agent to build your profile</div>
      ) : (
        <div style={styles.entryList}>
          {longTerm.map((entry) => (
            <div key={entry.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryCategory}>{entry.category}</span>
                <span style={styles.entryText}>{entry.fact}</span>
              </div>
              <button onClick={() => onDeleteLongTerm(entry.id)} style={styles.deleteBtn} title="Delete memory">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <Divider />

      <h3 style={styles.sectionTitle}>
        Medium-term Observations
        <span style={styles.badge}>{mediumTerm.length}</span>
      </h3>
      {mediumTerm.length === 0 ? (
        <div style={styles.emptyState}>No observations yet</div>
      ) : (
        <div style={styles.entryList}>
          {mediumTerm.map((entry) => (
            <div key={entry.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryCategory}>{entry.category}</span>
                <span style={styles.entryText}>{entry.observation}</span>
                <span style={styles.entryMeta}>
                  {Math.round(entry.confidence * 100)}% confidence · {entry.occurrences} occurrences
                </span>
              </div>
              <button
                onClick={() => onDeleteMediumTerm(entry.id)}
                style={styles.deleteBtn}
                title="Delete observation"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* --- Tasks Tab --- */

function TasksTab({
  tasks,
  onDelete,
}: {
  tasks: TaskEntry[];
  onDelete: (id: string) => void;
}) {
  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>
        Scheduled Tasks
        <span style={styles.badge}>{tasks.length}</span>
      </h3>
      {tasks.length === 0 ? (
        <div style={styles.emptyState}>No scheduled tasks — ask your agent to set a reminder</div>
      ) : (
        <div style={styles.entryList}>
          {tasks.map((task) => (
            <div key={task.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryText}>{task.description}</span>
                <span style={styles.entryMeta}>
                  {task.type === 'recurring' ? `Recurring: ${task.cronPattern}` : ''}
                  {task.type === 'once' && task.triggerTime
                    ? `Once: ${new Date(task.triggerTime).toLocaleString()}`
                    : ''}
                  {' · '}
                  {task.action}: {task.payload}
                </span>
              </div>
              <button onClick={() => onDelete(task.id)} style={styles.deleteBtn} title="Delete task">
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* --- Styles --- */

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.6)',
    backdropFilter: 'blur(8px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 100,
  },
  panel: {
    width: 620,
    maxHeight: '85vh',
    background: '#111118',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    position: 'relative',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '18px 24px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    flexShrink: 0,
  },
  headerIcon: {
    fontSize: 18,
    color: '#00f0ff',
  },
  headerTitle: {
    fontSize: 15,
    fontWeight: 600,
    color: '#e0e0e8',
    flex: 1,
  },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#555568',
    fontSize: 16,
    cursor: 'pointer',
    padding: '4px 8px',
    borderRadius: 4,
  },
  tabs: {
    display: 'flex',
    gap: 0,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    padding: '0 24px',
    flexShrink: 0,
  },
  tab: {
    background: 'none',
    border: 'none',
    borderBottom: '2px solid transparent',
    color: '#666680',
    fontSize: 12,
    fontWeight: 600,
    padding: '12px 16px',
    cursor: 'pointer',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    transition: 'color 0.15s, border-color 0.15s',
  },
  tabActive: {
    color: '#00f0ff',
    borderBottomColor: '#00f0ff',
  },
  saveMsg: {
    padding: '8px 24px',
    fontSize: 12,
    color: '#22c55e',
    background: 'rgba(34,197,94,0.06)',
    borderBottom: '1px solid rgba(34,197,94,0.1)',
    flexShrink: 0,
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    padding: '20px 24px',
    minHeight: 0,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 700,
    color: '#00f0ff',
    letterSpacing: '0.08em',
    textTransform: 'uppercase' as const,
    margin: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  sectionHint: {
    fontSize: 11,
    color: '#555568',
    marginTop: -6,
    marginBottom: 4,
  },
  badge: {
    fontSize: 10,
    color: '#555568',
    background: 'rgba(255,255,255,0.05)',
    padding: '2px 7px',
    borderRadius: 8,
    fontWeight: 500,
  },
  fieldGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  label: {
    fontSize: 12,
    color: '#999',
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  keyHint: {
    fontSize: 11,
    color: '#555568',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
  },
  connectedDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    background: '#22c55e',
    boxShadow: '0 0 6px rgba(34, 197, 94, 0.4)',
    display: 'inline-block',
  },
  keyRow: {
    display: 'flex',
    gap: 8,
  },
  keyInput: {
    flex: 1,
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#e0e0e8',
    fontSize: 13,
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    outline: 'none',
    transition: 'border-color 0.15s',
  },
  saveBtn: {
    background: 'rgba(0, 240, 255, 0.1)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    padding: '8px 16px',
    cursor: 'pointer',
    transition: 'background 0.15s',
    whiteSpace: 'nowrap',
  },
  divider: {
    height: 1,
    background: 'rgba(255,255,255,0.06)',
    margin: '8px 0',
  },
  toggleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    cursor: 'pointer',
    padding: '4px 0',
  },
  toggle: {
    width: 36,
    height: 20,
    borderRadius: 10,
    border: '1px solid',
    position: 'relative',
    transition: 'background 0.2s, border-color 0.2s',
    flexShrink: 0,
  },
  toggleDot: {
    width: 14,
    height: 14,
    borderRadius: '50%',
    position: 'absolute',
    top: 2,
    left: 2,
    transition: 'transform 0.2s, background 0.2s',
  },
  toggleLabel: {
    fontSize: 13,
    color: '#d0d0d8',
  },
  toggleHint: {
    fontSize: 11,
    color: '#555568',
    marginTop: -4,
    paddingLeft: 48,
  },
  shortcutList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  shortcutRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  shortcutKey: {
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 4,
    padding: '3px 10px',
    fontSize: 11,
    fontWeight: 600,
    color: '#888898',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    minWidth: 100,
    textAlign: 'center',
  },
  shortcutDesc: {
    fontSize: 13,
    color: '#999',
  },
  emptyState: {
    fontSize: 13,
    color: '#555568',
    padding: '16px 0',
    textAlign: 'center',
    fontStyle: 'italic',
  },
  entryList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    maxHeight: 300,
    overflowY: 'auto',
  },
  entry: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    padding: '10px 12px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid rgba(255,255,255,0.05)',
    borderRadius: 8,
  },
  entryContent: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  entryCategory: {
    fontSize: 10,
    fontWeight: 600,
    color: '#00f0ff',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
  },
  entryText: {
    fontSize: 13,
    color: '#d0d0d8',
    lineHeight: 1.4,
  },
  entryMeta: {
    fontSize: 11,
    color: '#555568',
  },
  deleteBtn: {
    background: 'none',
    border: '1px solid rgba(239,68,68,0.2)',
    borderRadius: 4,
    color: '#ef4444',
    fontSize: 11,
    cursor: 'pointer',
    padding: '2px 6px',
    opacity: 0.5,
    flexShrink: 0,
  },
  loading: {
    fontSize: 13,
    color: '#555568',
    padding: 24,
    textAlign: 'center',
  },
  confirmOverlay: {
    position: 'absolute',
    inset: 0,
    background: 'rgba(0,0,0,0.5)',
    backdropFilter: 'blur(4px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 110,
    borderRadius: 16,
  },
  confirmBox: {
    background: '#1a1a24',
    border: '1px solid rgba(239,68,68,0.2)',
    borderRadius: 12,
    padding: '24px 28px',
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    maxWidth: 320,
  },
  confirmMsg: {
    fontSize: 14,
    color: '#e0e0e8',
    lineHeight: 1.5,
    textAlign: 'center',
  },
  confirmBtns: {
    display: 'flex',
    gap: 10,
    justifyContent: 'center',
  },
  confirmCancel: {
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    color: '#999',
    fontSize: 12,
    fontWeight: 600,
    padding: '8px 20px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  confirmDelete: {
    background: 'rgba(239,68,68,0.15)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 8,
    color: '#ef4444',
    fontSize: 12,
    fontWeight: 600,
    padding: '8px 20px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  vaultConnected: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 12px',
    background: 'rgba(0, 240, 255, 0.04)',
    border: '1px solid rgba(0, 240, 255, 0.12)',
    borderRadius: 8,
  },
  vaultDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    background: '#22c55e',
    flexShrink: 0,
  },
  vaultPathText: {
    fontSize: 12,
    color: '#d0d0d8',
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  codeBadge: {
    fontSize: 11,
    background: 'rgba(255,255,255,0.06)',
    padding: '1px 5px',
    borderRadius: 3,
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    color: '#888898',
  },
  disconnectBtn: {
    background: 'rgba(239,68,68,0.08)',
    border: '1px solid rgba(239,68,68,0.2)',
    borderRadius: 8,
    color: '#ef4444',
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    cursor: 'pointer',
    alignSelf: 'flex-start',
    transition: 'background 0.15s',
  },
};

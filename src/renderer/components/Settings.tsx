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
  geminiKeyHint: string;
  anthropicKeyHint: string;
  elevenLabsKeyHint: string;
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

export default function Settings({ visible, onClose }: SettingsProps) {
  const [tab, setTab] = useState<Tab>('general');
  const [settings, setSettings] = useState<MaskedSettings | null>(null);
  const [longTerm, setLongTerm] = useState<LongTermEntry[]>([]);
  const [mediumTerm, setMediumTerm] = useState<MediumTermEntry[]>([]);
  const [tasks, setTasks] = useState<TaskEntry[]>([]);
  const [geminiKey, setGeminiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [elevenLabsKey, setElevenLabsKey] = useState('');
  const [vaultPath, setVaultPath] = useState('');
  const [saveMsg, setSaveMsg] = useState('');

  const loadSettings = useCallback(async () => {
    try {
      const s = await window.eve.settings.get();
      setSettings(s);
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

  // Auto-focus the overlay so Escape works immediately
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

  const handleAutoLaunchToggle = async () => {
    if (!settings) return;
    await window.eve.settings.setAutoLaunch(!settings.autoLaunch);
    await loadSettings();
  };

  const handleAutoScreenCaptureToggle = async () => {
    if (!settings) return;
    await window.eve.settings.setAutoScreenCapture(!settings.autoScreenCapture);
    await loadSettings();
  };

  const handleSaveGeminiKey = async () => {
    if (!geminiKey.trim()) return;
    await window.eve.settings.setApiKey('gemini', geminiKey.trim());
    setGeminiKey('');
    setSaveMsg('Gemini key saved — restart to apply');
    await loadSettings();
    setTimeout(() => setSaveMsg(''), 3000);
  };

  const handleSaveAnthropicKey = async () => {
    if (!anthropicKey.trim()) return;
    await window.eve.settings.setApiKey('anthropic', anthropicKey.trim());
    setAnthropicKey('');
    setSaveMsg('Anthropic key saved — restart to apply');
    await loadSettings();
    setTimeout(() => setSaveMsg(''), 3000);
  };

  const handleSaveElevenLabsKey = async () => {
    if (!elevenLabsKey.trim()) return;
    await window.eve.settings.setApiKey('elevenlabs', elevenLabsKey.trim());
    setElevenLabsKey('');
    setSaveMsg('ElevenLabs key saved — agent voices enabled');
    await loadSettings();
    setTimeout(() => setSaveMsg(''), 3000);
  };

  const handleSaveVaultPath = async () => {
    await window.eve.settings.setObsidianVaultPath(vaultPath.trim());
    setVaultPath('');
    setSaveMsg(vaultPath.trim() ? 'Obsidian vault linked — memories will sync' : 'Obsidian vault disconnected');
    await loadSettings();
    setTimeout(() => setSaveMsg(''), 3000);
  };

  const handleClearVaultPath = async () => {
    await window.eve.settings.setObsidianVaultPath('');
    setVaultPath('');
    setSaveMsg('Obsidian vault disconnected');
    await loadSettings();
    setTimeout(() => setSaveMsg(''), 3000);
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
                <button
                  onClick={() => setConfirmAction(null)}
                  style={styles.confirmCancel}
                >
                  Cancel
                </button>
                <button
                  onClick={confirmAction.onConfirm}
                  style={styles.confirmDelete}
                >
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

        {/* Content */}
        <div style={styles.content}>
          {tab === 'general' && (
            <GeneralTab
              settings={settings}
              geminiKey={geminiKey}
              anthropicKey={anthropicKey}
              elevenLabsKey={elevenLabsKey}
              vaultPath={vaultPath}
              onGeminiKeyChange={setGeminiKey}
              onAnthropicKeyChange={setAnthropicKey}
              onElevenLabsKeyChange={setElevenLabsKey}
              onVaultPathChange={setVaultPath}
              onSaveGeminiKey={handleSaveGeminiKey}
              onSaveAnthropicKey={handleSaveAnthropicKey}
              onSaveElevenLabsKey={handleSaveElevenLabsKey}
              onSaveVaultPath={handleSaveVaultPath}
              onClearVaultPath={handleClearVaultPath}
              onAutoLaunchToggle={handleAutoLaunchToggle}
              onAutoScreenCaptureToggle={handleAutoScreenCaptureToggle}
              onReloadSettings={loadSettings}
            />
          )}
          {tab === 'memory' && (
            <MemoryTab
              longTerm={longTerm}
              mediumTerm={mediumTerm}
              onDeleteLongTerm={handleDeleteLongTerm}
              onDeleteMediumTerm={handleDeleteMediumTerm}
            />
          )}
          {tab === 'tasks' && (
            <TasksTab tasks={tasks} onDelete={handleDeleteTask} />
          )}
        </div>
      </div>
    </div>
  );
}

/* --- Sub-tabs --- */

function GeneralTab({
  settings,
  geminiKey,
  anthropicKey,
  elevenLabsKey,
  vaultPath,
  onGeminiKeyChange,
  onAnthropicKeyChange,
  onElevenLabsKeyChange,
  onVaultPathChange,
  onSaveGeminiKey,
  onSaveAnthropicKey,
  onSaveElevenLabsKey,
  onSaveVaultPath,
  onClearVaultPath,
  onAutoLaunchToggle,
  onAutoScreenCaptureToggle,
  onReloadSettings,
}: {
  settings: MaskedSettings | null;
  geminiKey: string;
  anthropicKey: string;
  elevenLabsKey: string;
  vaultPath: string;
  onGeminiKeyChange: (v: string) => void;
  onAnthropicKeyChange: (v: string) => void;
  onElevenLabsKeyChange: (v: string) => void;
  onVaultPathChange: (v: string) => void;
  onSaveGeminiKey: () => void;
  onSaveAnthropicKey: () => void;
  onSaveElevenLabsKey: () => void;
  onSaveVaultPath: () => void;
  onClearVaultPath: () => void;
  onAutoLaunchToggle: () => void;
  onAutoScreenCaptureToggle: () => void;
  onReloadSettings: () => void;
}) {
  if (!settings) return <div style={styles.loading}>Loading...</div>;

  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>API Keys</h3>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>
          Gemini API Key
          {settings.hasGeminiKey && (
            <span style={styles.keyHint}>{settings.geminiKeyHint}</span>
          )}
        </label>
        <div style={styles.keyRow}>
          <input
            type="password"
            value={geminiKey}
            onChange={(e) => onGeminiKeyChange(e.target.value)}
            placeholder={settings.hasGeminiKey ? 'Enter new key to replace' : 'Paste your Gemini API key'}
            style={styles.keyInput}
          />
          <button onClick={onSaveGeminiKey} style={styles.saveBtn} disabled={!geminiKey.trim()}>
            Save
          </button>
        </div>
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>
          Anthropic API Key
          {settings.hasAnthropicKey && (
            <span style={styles.keyHint}>{settings.anthropicKeyHint}</span>
          )}
        </label>
        <div style={styles.keyRow}>
          <input
            type="password"
            value={anthropicKey}
            onChange={(e) => onAnthropicKeyChange(e.target.value)}
            placeholder={settings.hasAnthropicKey ? 'Enter new key to replace' : 'Paste your Anthropic API key'}
            style={styles.keyInput}
          />
          <button onClick={onSaveAnthropicKey} style={styles.saveBtn} disabled={!anthropicKey.trim()}>
            Save
          </button>
        </div>
      </div>

      <div style={styles.fieldGroup}>
        <label style={styles.label}>
          ElevenLabs API Key
          {settings.hasElevenLabsKey && (
            <span style={styles.keyHint}>{settings.elevenLabsKeyHint}</span>
          )}
        </label>
        <div style={styles.keyRow}>
          <input
            type="password"
            value={elevenLabsKey}
            onChange={(e) => onElevenLabsKeyChange(e.target.value)}
            placeholder={settings.hasElevenLabsKey ? 'Enter new key to replace' : 'Paste your ElevenLabs API key'}
            style={styles.keyInput}
          />
          <button onClick={onSaveElevenLabsKey} style={styles.saveBtn} disabled={!elevenLabsKey.trim()}>
            Save
          </button>
        </div>
        <div style={styles.toggleHint}>Powers distinct voices for background agents (Atlas, Nova, Cipher)</div>
      </div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Agent Voices</h3>
      <div style={styles.toggleRow} onClick={async () => {
        if (!settings) return;
        await window.eve.settings.set('agentVoicesEnabled', !settings.agentVoicesEnabled);
        await onReloadSettings();
      }}>
        <div style={{
          ...styles.toggle,
          background: settings.agentVoicesEnabled ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.agentVoicesEnabled ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.agentVoicesEnabled ? 'translateX(16px)' : 'translateX(0)',
            background: settings.agentVoicesEnabled ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>Sub-agents speak with distinct voices</span>
      </div>
      <div style={styles.toggleHint}>Atlas, Nova, and Cipher each get their own ElevenLabs voice when delivering results</div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Startup</h3>
      <div style={styles.toggleRow} onClick={onAutoLaunchToggle}>
        <div style={{
          ...styles.toggle,
          background: settings.autoLaunch ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.autoLaunch ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.autoLaunch ? 'translateX(16px)' : 'translateX(0)',
            background: settings.autoLaunch ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>Launch EVE on startup</span>
      </div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Vision</h3>
      <div style={styles.toggleRow} onClick={onAutoScreenCaptureToggle}>
        <div style={{
          ...styles.toggle,
          background: settings.autoScreenCapture ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.autoScreenCapture ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.autoScreenCapture ? 'translateX(16px)' : 'translateX(0)',
            background: settings.autoScreenCapture ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>Auto-share screen on connect</span>
      </div>
      <div style={styles.toggleHint}>EVE sees your screen when connected — disable for privacy</div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Wake Word</h3>
      <div style={styles.toggleRow} onClick={async () => {
        if (!settings) return;
        await window.eve.settings.set('wakeWordEnabled', !settings.wakeWordEnabled);
        await onReloadSettings();
      }}>
        <div style={{
          ...styles.toggle,
          background: settings.wakeWordEnabled ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.wakeWordEnabled ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.wakeWordEnabled ? 'translateX(16px)' : 'translateX(0)',
            background: settings.wakeWordEnabled ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>Say "Hey EVE" to connect</span>
      </div>
      <div style={styles.toggleHint}>Listens for wake word when disconnected — auto-connects on detection</div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Notification Whisper</h3>
      <div style={styles.toggleRow} onClick={async () => {
        if (!settings) return;
        await window.eve.settings.set('notificationWhisperEnabled', !settings.notificationWhisperEnabled);
        await onReloadSettings();
      }}>
        <div style={{
          ...styles.toggle,
          background: settings.notificationWhisperEnabled ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.notificationWhisperEnabled ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.notificationWhisperEnabled ? 'translateX(16px)' : 'translateX(0)',
            background: settings.notificationWhisperEnabled ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>EVE reads notifications aloud</span>
      </div>
      <div style={styles.toggleHint}>Captures notifications from allowed apps and mentions them naturally</div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Clipboard Intelligence</h3>
      <div style={styles.toggleRow} onClick={async () => {
        if (!settings) return;
        await window.eve.settings.set('clipboardIntelligenceEnabled', !settings.clipboardIntelligenceEnabled);
        await onReloadSettings();
      }}>
        <div style={{
          ...styles.toggle,
          background: settings.clipboardIntelligenceEnabled ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.clipboardIntelligenceEnabled ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.clipboardIntelligenceEnabled ? 'translateX(16px)' : 'translateX(0)',
            background: settings.clipboardIntelligenceEnabled ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>EVE sees your clipboard</span>
      </div>
      <div style={styles.toggleHint}>Monitors clipboard for URLs, code, and context — surfaces relevant info naturally</div>

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Google Calendar</h3>
      <div style={styles.toggleRow} onClick={async () => {
        if (!settings) return;
        await window.eve.settings.set('googleCalendarEnabled', !settings.googleCalendarEnabled);
        await onReloadSettings();
      }}>
        <div style={{
          ...styles.toggle,
          background: settings.googleCalendarEnabled ? 'rgba(0, 240, 255, 0.2)' : 'rgba(255,255,255,0.06)',
          borderColor: settings.googleCalendarEnabled ? 'rgba(0, 240, 255, 0.4)' : 'rgba(255,255,255,0.1)',
        }}>
          <div style={{
            ...styles.toggleDot,
            transform: settings.googleCalendarEnabled ? 'translateX(16px)' : 'translateX(0)',
            background: settings.googleCalendarEnabled ? '#00f0ff' : '#555568',
          }} />
        </div>
        <span style={styles.toggleLabel}>Calendar integration</span>
      </div>
      <div style={styles.toggleHint}>EVE reads your schedule, prepares meeting briefings, and can create events</div>
      {settings.googleCalendarEnabled && (
        <div style={{ marginTop: 10 }}>
          <button
            onClick={async () => {
              const success = await window.eve.calendar.authenticate();
              if (success) {
                alert('Google Calendar connected successfully!');
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
          <div style={styles.toggleHint}>Opens Google sign-in — grants read/write access to your primary calendar</div>
        </div>
      )}

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Knowledge Graph (Obsidian)</h3>
      {settings.obsidianVaultPath ? (
        <div style={styles.fieldGroup}>
          <div style={styles.vaultConnected}>
            <span style={styles.vaultDot} />
            <span style={styles.vaultPathText}>{settings.obsidianVaultPath}</span>
          </div>
          <div style={styles.toggleHint}>
            Memories sync to <code style={styles.codeBadge}>EVE/memories/</code> and observations to <code style={styles.codeBadge}>EVE/observations/</code>
          </div>
          <button onClick={onClearVaultPath} style={styles.disconnectBtn}>
            Disconnect vault
          </button>
        </div>
      ) : (
        <div style={styles.fieldGroup}>
          <label style={styles.label}>Obsidian vault folder path</label>
          <div style={styles.keyRow}>
            <input
              type="text"
              value={vaultPath}
              onChange={(e) => onVaultPathChange(e.target.value)}
              placeholder="C:\Users\you\Documents\MyVault"
              style={styles.keyInput}
            />
            <button onClick={onSaveVaultPath} style={styles.saveBtn} disabled={!vaultPath.trim()}>
              Link
            </button>
          </div>
          <div style={styles.toggleHint}>
            Paste the full path to your Obsidian vault. EVE will create an EVE/ folder inside it.
          </div>
        </div>
      )}

      <div style={styles.divider} />

      <h3 style={styles.sectionTitle}>Keyboard Shortcuts</h3>
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
          <span style={styles.shortcutDesc}>Show/hide EVE (global)</span>
        </div>
      </div>
    </div>
  );
}

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
        <div style={styles.emptyState}>No memories yet — talk to EVE to build your profile</div>
      ) : (
        <div style={styles.entryList}>
          {longTerm.map((entry) => (
            <div key={entry.id} style={styles.entry}>
              <div style={styles.entryContent}>
                <span style={styles.entryCategory}>{entry.category}</span>
                <span style={styles.entryText}>{entry.fact}</span>
              </div>
              <button
                onClick={() => onDeleteLongTerm(entry.id)}
                style={styles.deleteBtn}
                title="Delete memory"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      <div style={styles.divider} />

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
        <div style={styles.emptyState}>No scheduled tasks — ask EVE to set a reminder</div>
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
                  {' · '}{task.action}: {task.payload}
                </span>
              </div>
              <button
                onClick={() => onDelete(task.id)}
                style={styles.deleteBtn}
                title="Delete task"
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
    width: 600,
    maxHeight: '80vh',
    background: '#111118',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '18px 24px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
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
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    padding: '20px 24px',
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 700,
    color: '#888898',
    letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
    margin: 0,
    display: 'flex',
    alignItems: 'center',
    gap: 8,
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
    fontFamily: 'monospace',
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
    fontFamily: 'monospace',
    outline: 'none',
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
    fontFamily: 'monospace',
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
    fontFamily: 'monospace',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  codeBadge: {
    fontSize: 11,
    background: 'rgba(255,255,255,0.06)',
    padding: '1px 5px',
    borderRadius: 3,
    fontFamily: 'monospace',
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

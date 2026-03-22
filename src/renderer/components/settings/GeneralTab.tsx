import React, { useState } from 'react';
import { validateApiKey } from '../../hooks/useApiKeyValidation';
import { styles } from './styles';
import { SectionHeader, Toggle, ApiKeyField, Divider } from './shared';
import type { MaskedSettings } from './types';

interface GeneralTabProps {
  settings: MaskedSettings;
  loadSettings: () => Promise<void>;
  flash: (msg: string) => void;
}

export default function GeneralTab({ settings, loadSettings, flash }: GeneralTabProps) {
  // API key input states
  const [geminiKey, setGeminiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [elevenLabsKey, setElevenLabsKey] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [perplexityKey, setPerplexityKey] = useState('');
  const [firecrawlKey, setFirecrawlKey] = useState('');
  const [openrouterKey, setOpenrouterKey] = useState('');
  const [openrouterModel, setOpenrouterModel] = useState('');
  const [telegramToken, setTelegramToken] = useState('');
  const [telegramOwnerId, setTelegramOwnerId] = useState('');
  const [discordToken, setDiscordToken] = useState('');
  const [discordOwnerId, setDiscordOwnerId] = useState('');
  const [vaultPath, setVaultPath] = useState('');
  const [validatingKey, setValidatingKey] = useState<string | null>(null);

  const saveApiKey = async (
    key: 'gemini' | 'anthropic' | 'elevenlabs' | 'openai' | 'perplexity' | 'firecrawl' | 'openrouter',
    value: string,
    setter: (v: string) => void,
    successMsg: string,
  ) => {
    if (!value.trim()) return;

    // Pre-validate keys that have validators (gemini, anthropic, openrouter)
    setValidatingKey(key);
    const result = await validateApiKey(key, value.trim());
    setValidatingKey(null);

    if (!result.valid) {
      flash(`Invalid key: ${result.error}`);
      return;
    }

    try {
      await window.eve.settings.setApiKey(key, value.trim());
      setter('');
      flash(successMsg);
      await loadSettings();
    } catch (err) {
      flash(`Failed to save key: ${err instanceof Error ? err.message : 'unknown error'}`);
    }
  };

  const handleSaveVaultPath = async () => {
    try {
      await window.eve.settings.setObsidianVaultPath(vaultPath.trim());
      setVaultPath('');
      flash(vaultPath.trim() ? 'Obsidian vault linked — memories will sync' : 'Obsidian vault disconnected');
      await loadSettings();
    } catch (err) {
      flash(`Failed to save vault path: ${err instanceof Error ? err.message : 'unknown error'}`);
    }
  };

  const handleClearVaultPath = async () => {
    try {
      await window.eve.settings.setObsidianVaultPath('');
      setVaultPath('');
      flash('Obsidian vault disconnected');
      await loadSettings();
    } catch (err) {
      flash(`Failed to clear vault path: ${err instanceof Error ? err.message : 'unknown error'}`);
    }
  };

  const toggleSetting = async (key: string) => {
    try {
      await window.eve.settings.set(key, !(settings as unknown as Record<string, boolean>)[key]);
      await loadSettings();
    } catch (err) {
      flash(`Failed to update setting: ${err instanceof Error ? err.message : 'unknown error'}`);
    }
  };

  return (
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
        validating={validatingKey === 'gemini'}
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
        validating={validatingKey === 'anthropic'}
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

      {/* ═══════════════ OPENROUTER / MODEL PROVIDER ═══════════════ */}
      <SectionHeader>Model Provider</SectionHeader>
      <div style={styles.sectionHint}>
        OpenRouter gives access to 200+ models — use it as an alternative to direct Anthropic API
      </div>

      <ApiKeyField
        label="OpenRouter API Key"
        hasKey={settings.hasOpenrouterKey}
        hint={settings.openrouterKeyHint}
        value={openrouterKey}
        onChange={setOpenrouterKey}
        onSave={() =>
          saveApiKey('openrouter', openrouterKey, setOpenrouterKey, 'OpenRouter key saved')
        }
        description="Access 200+ AI models including Claude, GPT-4, Llama, Mistral, and more"
        validating={validatingKey === 'openrouter'}
      />

      {settings.hasOpenrouterKey && (
        <>
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Preferred Provider for Agent Tasks</label>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={async () => {
                  await window.eve.settings.set('preferredProvider', 'anthropic');
                  await loadSettings();
                  flash('Switched to Anthropic (direct) for agent tasks');
                }}
                style={{
                  ...styles.saveBtn,
                  flex: 1,
                  padding: '8px 12px',
                  background: settings.preferredProvider === 'anthropic'
                    ? 'rgba(0, 240, 255, 0.15)' : 'rgba(255,255,255,0.04)',
                  borderColor: settings.preferredProvider === 'anthropic'
                    ? 'rgba(0, 240, 255, 0.3)' : 'rgba(255,255,255,0.08)',
                  color: settings.preferredProvider === 'anthropic'
                    ? '#00f0ff' : '#666680',
                }}
              >
                Anthropic Direct
              </button>
              <button
                onClick={async () => {
                  await window.eve.settings.set('preferredProvider', 'openrouter');
                  await loadSettings();
                  flash('Switched to OpenRouter for agent tasks');
                }}
                style={{
                  ...styles.saveBtn,
                  flex: 1,
                  padding: '8px 12px',
                  background: settings.preferredProvider === 'openrouter'
                    ? 'rgba(168, 85, 247, 0.15)' : 'rgba(255,255,255,0.04)',
                  borderColor: settings.preferredProvider === 'openrouter'
                    ? 'rgba(168, 85, 247, 0.3)' : 'rgba(255,255,255,0.08)',
                  color: settings.preferredProvider === 'openrouter'
                    ? '#a855f7' : '#666680',
                }}
              >
                OpenRouter
              </button>
              {settings.localModelEnabled && (
                <button
                  onClick={async () => {
                    await window.eve.settings.set('preferredProvider', 'local');
                    await loadSettings();
                    flash('Switched to Local AI for agent tasks');
                  }}
                  style={{
                    ...styles.saveBtn,
                    flex: 1,
                    padding: '8px 12px',
                    background: settings.preferredProvider === 'local'
                      ? 'rgba(34, 197, 94, 0.15)' : 'rgba(255,255,255,0.04)',
                    borderColor: settings.preferredProvider === 'local'
                      ? 'rgba(34, 197, 94, 0.3)' : 'rgba(255,255,255,0.08)',
                    color: settings.preferredProvider === 'local'
                      ? '#22c55e' : '#666680',
                  }}
                >
                  Local AI
                </button>
              )}
            </div>
            <div style={styles.toggleHint}>
              {settings.preferredProvider === 'openrouter'
                ? `Using OpenRouter model: ${settings.openrouterModel || 'anthropic/claude-sonnet-4'}`
                : 'Using direct Anthropic API for agent reasoning tasks'}
            </div>
          </div>

          {settings.preferredProvider === 'openrouter' && (
            <div style={styles.fieldGroup}>
              <label style={styles.label}>OpenRouter Model</label>
              <div style={styles.keyRow}>
                <input
                  type="text"
                  value={openrouterModel || settings.openrouterModel || ''}
                  onChange={(e) => setOpenrouterModel(e.target.value)}
                  placeholder="anthropic/claude-sonnet-4"
                  style={styles.keyInput}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && openrouterModel.trim()) {
                      window.eve.settings.set('openrouterModel', openrouterModel.trim()).then(() => {
                        flash(`Model set to ${openrouterModel.trim()}`);
                        setOpenrouterModel('');
                        loadSettings();
                      });
                    }
                  }}
                />
                <button
                  onClick={() => {
                    if (!openrouterModel.trim()) return;
                    window.eve.settings.set('openrouterModel', openrouterModel.trim()).then(() => {
                      flash(`Model set to ${openrouterModel.trim()}`);
                      setOpenrouterModel('');
                      loadSettings();
                    });
                  }}
                  style={styles.saveBtn}
                  disabled={!openrouterModel.trim()}
                >
                  Set
                </button>
              </div>
              <div style={styles.toggleHint}>
                Examples: anthropic/claude-sonnet-4, openai/gpt-4o, google/gemini-2.0-flash, meta-llama/llama-3.3-70b
              </div>
            </div>
          )}
        </>
      )}

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
              Memories sync to <code style={styles.codeBadge}>Friday/memories/</code> and observations to{' '}
              <code style={styles.codeBadge}>Friday/observations/</code>
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
              Paste the full path to your Obsidian vault. A Friday/ folder will be created inside it.
            </div>
          </div>
        )}
      </div>

      <Divider />

      {/* ═══════════════ MESSAGING GATEWAY ═══════════════ */}
      <SectionHeader>Messaging Gateway</SectionHeader>

      <Toggle
        value={settings.gatewayEnabled}
        label="Enable messaging gateway"
        hint="Lets people reach your agent via Telegram or Discord"
        onToggle={async () => {
          await window.eve.gateway.setEnabled(!settings.gatewayEnabled);
          await loadSettings();
        }}
      />

      {settings.gatewayEnabled && (<>
        <div style={{ marginTop: 12 }}>
          <ApiKeyField
            label="Telegram Bot Token"
            hasKey={settings.hasTelegramToken}
            hint=""
            value={telegramToken}
            onChange={setTelegramToken}
            onSave={async () => {
              if (!telegramToken.trim()) return;
              await window.eve.settings.set('telegramBotToken', telegramToken.trim());
              setTelegramToken('');
              flash('Telegram bot token saved');
              await loadSettings();
            }}
          />
        </div>

        {settings.hasTelegramToken && (
          <div style={styles.fieldGroup}>
            <label style={styles.fieldLabel}>Telegram Owner Chat ID</label>
            <div style={styles.keyRow}>
              <input
                type="text"
                value={telegramOwnerId || settings.telegramOwnerId}
                onChange={(e) => setTelegramOwnerId(e.target.value)}
                placeholder="Your Telegram numeric chat ID"
                style={styles.keyInput}
                onKeyDown={async (e) => {
                  if (e.key === 'Enter' && telegramOwnerId.trim()) {
                    await window.eve.settings.set('telegramOwnerId', telegramOwnerId.trim());
                    flash('Telegram owner ID saved');
                    await loadSettings();
                  }
                }}
              />
              <button
                onClick={async () => {
                  if (telegramOwnerId.trim()) {
                    await window.eve.settings.set('telegramOwnerId', telegramOwnerId.trim());
                    flash('Telegram owner ID saved');
                    await loadSettings();
                  }
                }}
                style={styles.saveBtn}
                disabled={!telegramOwnerId.trim()}
              >
                Save
              </button>
            </div>
            <div style={styles.toggleHint}>
              Only this chat ID can control the agent. Send /start to your bot to find your ID.
            </div>
          </div>
        )}
      </>)}

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
          <span style={styles.shortcutKey}>Ctrl+K</span>
          <span style={styles.shortcutDesc}>Quick actions</span>
        </div>
        <div style={styles.shortcutRow}>
          <span style={styles.shortcutKey}>Ctrl+Shift+D</span>
          <span style={styles.shortcutDesc}>Command center</span>
        </div>
        <div style={styles.shortcutRow}>
          <span style={styles.shortcutKey}>Ctrl+Shift+M</span>
          <span style={styles.shortcutDesc}>Memory explorer</span>
        </div>
        <div style={styles.shortcutRow}>
          <span style={styles.shortcutKey}>Ctrl+Shift+A</span>
          <span style={styles.shortcutDesc}>Agent dashboard</span>
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
  );
}

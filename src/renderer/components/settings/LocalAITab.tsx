import React, { useState, useEffect } from 'react';
import { styles } from './styles';
import { SectionHeader, Toggle, ApiKeyField, Divider } from './shared';
import type { MaskedSettings } from './types';

interface LocalAITabProps {
  settings: MaskedSettings;
  loadSettings: () => Promise<void>;
  flash: (msg: string) => void;
}

export default function LocalAITab({ settings, loadSettings, flash }: LocalAITabProps) {
  const [endpoint, setEndpoint] = useState(settings.localInferenceEndpoint || 'http://localhost:11434/v1');
  const [modelId, setModelId] = useState(settings.localModelId || '');
  const [huggingfaceKey, setHuggingfaceKey] = useState('');
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Discover local models on mount
  useEffect(() => {
    if (settings.localModelEnabled) {
      handleDiscover();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps — discover once on mount
  }, []);

  const handleDiscover = async () => {
    setDiscovering(true);
    try {
      const result = await window.eve.intelligenceRouter.discoverLocalModels();
      setDiscoveredModels(result.models || []);
      if (result.found > 0) {
        flash(`Found ${result.found} local model${result.found === 1 ? '' : 's'}`);
      } else {
        flash('No local models found — is Ollama running?');
      }
    } catch {
      flash('Failed to discover models');
      setDiscoveredModels([]);
    } finally {
      setDiscovering(false);
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      // Save the endpoint first so the health check uses it
      await window.eve.settings.set('localInferenceEndpoint', endpoint);

      // Try to discover models as a connectivity test
      const result = await window.eve.intelligenceRouter.discoverLocalModels();
      if (result.found > 0) {
        setTestResult({ ok: true, message: `Connected! Found ${result.found} model${result.found === 1 ? '' : 's'}: ${result.models.join(', ')}` });
        setDiscoveredModels(result.models || []);
      } else {
        setTestResult({ ok: false, message: 'Connected to endpoint but no models found. Run `ollama pull llama3.3` to download a model.' });
      }
    } catch {
      setTestResult({ ok: false, message: 'Connection failed. Make sure Ollama is running (ollama serve).' });
    } finally {
      setTesting(false);
    }
  };

  const handleSaveEndpoint = async () => {
    if (!endpoint.trim()) return;
    await window.eve.settings.set('localInferenceEndpoint', endpoint.trim());
    await loadSettings();
    flash('Local endpoint saved');
  };

  const handleSelectModel = async (id: string) => {
    setModelId(id);
    await window.eve.settings.set('localModelId', id);
    await loadSettings();
    flash(`Active local model: ${id}`);
  };

  return (
    <div style={styles.section}>
      {/* ═══════════════ LOCAL AI OVERVIEW ═══════════════ */}
      <SectionHeader>Local AI</SectionHeader>
      <div style={styles.sectionHint}>
        Run AI models on your own hardware with Ollama — your data never leaves your device
      </div>

      {/* Master enable toggle */}
      <Toggle
        value={settings.localModelEnabled}
        label="Enable local model inference"
        hint="Uses Ollama, TGI, or vLLM running on your machine for AI tasks"
        onToggle={async () => {
          const next = !settings.localModelEnabled;
          await window.eve.settings.set('localModelEnabled', next);
          if (next) {
            // Auto-discover when enabling
            await loadSettings();
            handleDiscover();
          } else {
            // If we were set to local provider, switch back to anthropic
            if (settings.preferredProvider === 'local') {
              await window.eve.settings.set('preferredProvider', 'anthropic');
            }
            await loadSettings();
          }
        }}
      />

      {settings.localModelEnabled && (
        <>
          <Divider />

          {/* ═══════════════ CONNECTION ═══════════════ */}
          <SectionHeader>Connection</SectionHeader>

          {/* Endpoint URL */}
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Inference Endpoint</label>
            <div style={styles.keyRow}>
              <input
                type="text"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="http://localhost:11434/v1"
                style={styles.keyInput}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleSaveEndpoint();
                }}
              />
              <button
                onClick={handleSaveEndpoint}
                style={styles.saveBtn}
              >
                Save
              </button>
            </div>
            <div style={styles.toggleHint}>
              Ollama default: http://localhost:11434/v1 &middot; TGI/vLLM: http://localhost:8080/v1
            </div>
          </div>

          {/* Test connection button */}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleTestConnection}
              disabled={testing}
              style={{
                ...styles.saveBtn,
                flex: 1,
                padding: '10px 16px',
                background: 'rgba(34, 197, 94, 0.1)',
                borderColor: 'rgba(34, 197, 94, 0.25)',
                color: '#22c55e',
                opacity: testing ? 0.6 : 1,
              }}
            >
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
            <button
              onClick={handleDiscover}
              disabled={discovering}
              style={{
                ...styles.saveBtn,
                flex: 1,
                padding: '10px 16px',
                opacity: discovering ? 0.6 : 1,
              }}
            >
              {discovering ? 'Scanning...' : 'Discover Models'}
            </button>
          </div>

          {/* Test result */}
          {testResult && (
            <div style={{
              padding: '10px 14px',
              borderRadius: 8,
              fontSize: 12,
              lineHeight: 1.5,
              background: testResult.ok ? 'rgba(34, 197, 94, 0.08)' : 'rgba(239, 68, 68, 0.08)',
              border: `1px solid ${testResult.ok ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)'}`,
              color: testResult.ok ? '#22c55e' : '#ef4444',
            }}>
              {testResult.ok ? '\u2705' : '\u274c'} {testResult.message}
            </div>
          )}

          <Divider />

          {/* ═══════════════ MODEL SELECTION ═══════════════ */}
          <SectionHeader>Active Model</SectionHeader>
          <div style={styles.sectionHint}>
            Select which local model handles AI tasks &middot; {discoveredModels.length > 0
              ? `${discoveredModels.length} model${discoveredModels.length === 1 ? '' : 's'} available`
              : 'No models discovered yet'}
          </div>

          {/* Model selector — discovered models as clickable chips */}
          {discoveredModels.length > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {discoveredModels.map((m) => (
                <button
                  key={m}
                  onClick={() => handleSelectModel(m)}
                  style={{
                    ...styles.saveBtn,
                    padding: '6px 14px',
                    fontSize: 12,
                    background: (modelId || settings.localModelId) === m
                      ? 'rgba(34, 197, 94, 0.15)' : 'rgba(255,255,255,0.04)',
                    borderColor: (modelId || settings.localModelId) === m
                      ? 'rgba(34, 197, 94, 0.3)' : 'rgba(255,255,255,0.08)',
                    color: (modelId || settings.localModelId) === m
                      ? '#22c55e' : '#888898',
                  }}
                >
                  {m}
                </button>
              ))}
            </div>
          ) : (
            <div style={{
              padding: '16px',
              borderRadius: 8,
              background: 'rgba(255,255,255,0.02)',
              border: '1px dashed rgba(255,255,255,0.08)',
              textAlign: 'center',
              fontSize: 12,
              color: '#555568',
            }}>
              No models found. Install a model with: <code style={{ color: '#00f0ff', fontSize: 11 }}>ollama pull llama3.3</code>
            </div>
          )}

          {/* Manual model ID override */}
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Manual Model ID</label>
            <div style={styles.keyRow}>
              <input
                type="text"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                placeholder={settings.localModelId || 'e.g. llama3.3:70b'}
                style={styles.keyInput}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && modelId.trim()) handleSelectModel(modelId.trim());
                }}
              />
              <button
                onClick={() => modelId.trim() && handleSelectModel(modelId.trim())}
                style={styles.saveBtn}
                disabled={!modelId.trim()}
              >
                Set
              </button>
            </div>
            <div style={styles.toggleHint}>
              Override the selected model. Use the Ollama model name (e.g. llama3.3, qwen2.5-coder:32b, deepseek-r1)
            </div>
          </div>

          <Divider />

          {/* ═══════════════ PROVIDER PREFERENCE ═══════════════ */}
          <SectionHeader>Routing</SectionHeader>
          <div style={styles.sectionHint}>
            Choose when to use local models vs. cloud providers
          </div>

          {/* Active provider */}
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Active Provider for Agent Tasks</label>
            <div style={{ display: 'flex', gap: 8 }}>
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
              <button
                onClick={async () => {
                  await window.eve.settings.set('preferredProvider', 'anthropic');
                  await loadSettings();
                  flash('Switched to Anthropic (cloud) for agent tasks');
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
                Anthropic
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
            </div>
            <div style={styles.toggleHint}>
              {settings.preferredProvider === 'local'
                ? `Using local model: ${settings.localModelId || 'auto-detected'} — your data stays on-device`
                : settings.preferredProvider === 'openrouter'
                  ? 'Using OpenRouter (cloud) for agent tasks'
                  : 'Using Anthropic Claude (cloud) for agent tasks'}
            </div>
          </div>

          <Divider />

          {/* ═══════════════ HUGGINGFACE API KEY (optional) ═══════════════ */}
          <SectionHeader>HuggingFace Cloud <span style={styles.badge}>Optional</span></SectionHeader>
          <div style={styles.sectionHint}>
            Use HuggingFace Inference API for cloud-hosted open-weight models. Not needed for Ollama.
          </div>

          <ApiKeyField
            label="HuggingFace API Key"
            hasKey={settings.hasHuggingfaceKey}
            hint={settings.huggingfaceKeyHint}
            value={huggingfaceKey}
            onChange={setHuggingfaceKey}
            onSave={async () => {
              if (!huggingfaceKey.trim()) return;
              await window.eve.settings.setApiKey('huggingface', huggingfaceKey.trim());
              setHuggingfaceKey('');
              await loadSettings();
              flash('HuggingFace key saved');
            }}
            description="Access HuggingFace Inference Endpoints for cloud-hosted Llama, Mistral, and more"
          />

          {/* Bottom spacer */}
          <div style={{ height: 20 }} />
        </>
      )}
    </div>
  );
}

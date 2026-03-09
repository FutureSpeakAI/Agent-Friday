/**
 * providers/index.ts — Provider initialization and registration.
 *
 * Called at app startup to register all available LLM backends
 * with the unified LLMClient singleton.
 */

import { llmClient } from '../llm-client';
import { AnthropicProvider } from './anthropic-provider';
import { OpenRouterProvider } from './openrouter-provider';
import { HuggingFaceProvider } from './hf-provider';
import { OllamaProvider } from './ollama-provider';
import { settingsManager } from '../settings';
import { privacyShield } from '../privacy-shield';

/**
 * Initialize all LLM providers and register them with the LLMClient.
 * Call this once during app startup (after settings are loaded).
 */
export function initializeProviders(): void {
  // Always register Anthropic (it's the primary provider)
  const anthropic = new AnthropicProvider();
  llmClient.registerProvider(anthropic);

  // Always register OpenRouter (available when API key is set)
  const openrouter = new OpenRouterProvider();
  llmClient.registerProvider(openrouter);

  // Always register HuggingFace / Local Inference
  // (available when HF API key is set OR local endpoint is reachable)
  const huggingface = new HuggingFaceProvider();
  llmClient.registerProvider(huggingface);

  // Always register Ollama (native API — available when Ollama is running)
  const ollama = new OllamaProvider();
  llmClient.registerProvider(ollama);

  // Set default provider based on user preference
  const preferred = settingsManager.getPreferredProvider();
  if (preferred === 'ollama' && ollama.isAvailable()) {
    llmClient.setDefaultProvider('ollama');
    console.log('[Providers] Default provider: Ollama (native)');
  } else if (preferred === 'local' && huggingface.isAvailable()) {
    llmClient.setDefaultProvider('local');
    console.log('[Providers] Default provider: Local (HuggingFace)');
  } else if (preferred === 'openrouter' && openrouter.isAvailable()) {
    llmClient.setDefaultProvider('openrouter');
    console.log('[Providers] Default provider: OpenRouter');
  } else {
    llmClient.setDefaultProvider('anthropic');
    console.log('[Providers] Default provider: Anthropic');
  }

  // Log availability
  const available = ['anthropic', 'openrouter', 'local', 'ollama'] as const;
  for (const name of available) {
    const status = llmClient.isProviderAvailable(name) ? '✓' : '✗';
    console.log(`[Providers]   ${status} ${name}`);
  }

  // ── Privacy Shield: Register known names for PII scrubbing ──────────
  // Populate the shield with known identifiable names from agent config
  // so they get scrubbed before reaching any cloud provider.
  initializePrivacyShield();
}

/**
 * Initialize the Privacy Shield with known names from settings.
 * Called during provider init and whenever agent config changes.
 */
export function initializePrivacyShield(): void {
  try {
    const agentConfig = settingsManager.getAgentConfig();
    const knownNames: string[] = [];

    if (agentConfig.userName) knownNames.push(agentConfig.userName);
    if (agentConfig.agentName) knownNames.push(agentConfig.agentName);

    // Also register the OS username for path scrubbing
    const osUser = process.env.USERNAME || process.env.USER || '';
    if (osUser && osUser.length >= 2) knownNames.push(osUser);

    privacyShield.registerKnownNames(knownNames);

    console.log(
      `[PrivacyShield] Initialized with ${knownNames.length} known names. ` +
      `Shield enabled: ${privacyShield.isEnabled()}`
    );
  } catch (err) {
    console.warn('[PrivacyShield] Failed to initialize known names:', err);
  }
}

// Re-export providers for direct access if needed
export { AnthropicProvider } from './anthropic-provider';
export { OpenRouterProvider } from './openrouter-provider';
export { HuggingFaceProvider } from './hf-provider';
export { OllamaProvider } from './ollama-provider';

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
}

// Re-export providers for direct access if needed
export { AnthropicProvider } from './anthropic-provider';
export { OpenRouterProvider } from './openrouter-provider';
export { HuggingFaceProvider } from './hf-provider';
export { OllamaProvider } from './ollama-provider';

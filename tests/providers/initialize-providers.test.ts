/**
 * initializeProviders — Unit tests for provider bootstrap.
 *
 * Tests that the initialization function registers all three providers
 * with the LLMClient and correctly sets the default based on user preference.
 *
 * Phase A.1: "First Words" — Provider Core
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mock dependencies ───────────────────────────────────────────────
// vi.mock factories are hoisted — cannot reference outer variables.
// Use vi.hoisted() to create shared mocks that survive hoisting.

const mocks = vi.hoisted(() => ({
  registerProvider: vi.fn(),
  setDefaultProvider: vi.fn(),
  isProviderAvailable: vi.fn(),
  getPreferredProvider: vi.fn(),
  anthropicIsAvailable: vi.fn(() => true),
  openrouterIsAvailable: vi.fn(() => true),
  hfIsAvailable: vi.fn(() => false),
}));

vi.mock('../../src/main/llm-client', () => ({
  llmClient: {
    registerProvider: mocks.registerProvider,
    setDefaultProvider: mocks.setDefaultProvider,
    isProviderAvailable: mocks.isProviderAvailable,
  },
}));

vi.mock('../../src/main/settings', () => ({
  settingsManager: {
    getPreferredProvider: mocks.getPreferredProvider,
  },
}));

vi.mock('../../src/main/providers/anthropic-provider', () => {
  return {
    AnthropicProvider: class {
      name = 'anthropic' as const;
      isAvailable() { return mocks.anthropicIsAvailable(); }
    },
  };
});

vi.mock('../../src/main/providers/openrouter-provider', () => {
  return {
    OpenRouterProvider: class {
      name = 'openrouter' as const;
      isAvailable() { return mocks.openrouterIsAvailable(); }
    },
  };
});

vi.mock('../../src/main/providers/hf-provider', () => {
  return {
    HuggingFaceProvider: class {
      name = 'local' as const;
      isAvailable() { return mocks.hfIsAvailable(); }
    },
  };
});

import { initializeProviders } from '../../src/main/providers/index';

describe('initializeProviders', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getPreferredProvider.mockReturnValue('anthropic');
    mocks.anthropicIsAvailable.mockReturnValue(true);
    mocks.openrouterIsAvailable.mockReturnValue(true);
    mocks.hfIsAvailable.mockReturnValue(false);
    mocks.isProviderAvailable.mockReturnValue(false);
  });

  it('registers all three providers with the LLM client', () => {
    initializeProviders();
    expect(mocks.registerProvider).toHaveBeenCalledTimes(3);
    const names = mocks.registerProvider.mock.calls.map(
      (call: unknown[]) => (call[0] as { name: string }).name
    );
    expect(names).toContain('anthropic');
    expect(names).toContain('openrouter');
    expect(names).toContain('local');
  });

  it('defaults to anthropic when preferred provider is anthropic', () => {
    mocks.getPreferredProvider.mockReturnValue('anthropic');
    initializeProviders();
    expect(mocks.setDefaultProvider).toHaveBeenCalledWith('anthropic');
  });

  it('sets default to openrouter when preferred and available', () => {
    mocks.getPreferredProvider.mockReturnValue('openrouter');
    mocks.openrouterIsAvailable.mockReturnValue(true);
    initializeProviders();
    expect(mocks.setDefaultProvider).toHaveBeenCalledWith('openrouter');
  });

  it('sets default to local when preferred and HF is available', () => {
    mocks.getPreferredProvider.mockReturnValue('local');
    mocks.hfIsAvailable.mockReturnValue(true);
    initializeProviders();
    expect(mocks.setDefaultProvider).toHaveBeenCalledWith('local');
  });

  it('falls back to anthropic when local is preferred but unavailable', () => {
    mocks.getPreferredProvider.mockReturnValue('local');
    mocks.hfIsAvailable.mockReturnValue(false);
    initializeProviders();
    expect(mocks.setDefaultProvider).toHaveBeenCalledWith('anthropic');
  });

  it('falls back to anthropic when openrouter is preferred but unavailable', () => {
    mocks.getPreferredProvider.mockReturnValue('openrouter');
    mocks.openrouterIsAvailable.mockReturnValue(false);
    initializeProviders();
    expect(mocks.setDefaultProvider).toHaveBeenCalledWith('anthropic');
  });
});

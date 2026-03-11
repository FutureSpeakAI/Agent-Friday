/**
 * useApiKeyValidation.ts — Lightweight pre-validation for API keys.
 *
 * Validates keys by making cheap REST calls before saving, so users
 * get immediate feedback rather than cryptic WebSocket failures later.
 */

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

/**
 * Validate a Gemini API key by listing models (cheap, fast endpoint).
 * Returns immediately if the key format is obviously wrong.
 */
export async function validateGeminiKey(key: string): Promise<ValidationResult> {
  const trimmed = key.trim();
  if (!trimmed) return { valid: false, error: 'Key is empty' };
  if (!trimmed.startsWith('AIza')) return { valid: false, error: 'Gemini keys start with "AIza"' };

  try {
    const resp = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(trimmed)}`,
      { method: 'GET', signal: AbortSignal.timeout(8000) },
    );

    if (resp.ok) return { valid: true };

    if (resp.status === 400 || resp.status === 403 || resp.status === 401) {
      return { valid: false, error: 'API key is invalid or has been revoked' };
    }
    return { valid: false, error: `Unexpected response (${resp.status})` };
  } catch (err: any) {
    if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
      return { valid: false, error: 'Validation timed out — check your network' };
    }
    return { valid: false, error: 'Could not reach Google servers — check your connection' };
  }
}

/**
 * Validate an Anthropic API key by calling the /v1/messages endpoint
 * with a minimal request. A 401 means invalid key.
 */
export async function validateAnthropicKey(key: string): Promise<ValidationResult> {
  const trimmed = key.trim();
  if (!trimmed) return { valid: false, error: 'Key is empty' };
  if (!trimmed.startsWith('sk-ant-')) return { valid: false, error: 'Anthropic keys start with "sk-ant-"' };

  try {
    // Use a minimal request — will succeed or fail based on auth, not content
    const resp = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'x-api-key': trimmed,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 1,
        messages: [{ role: 'user', content: 'hi' }],
      }),
      signal: AbortSignal.timeout(8000),
    });

    // 200 = valid key (request succeeded)
    // 400 = valid key (bad request body, but auth passed)
    // 429 = valid key (rate limited, but auth passed)
    if (resp.ok || resp.status === 400 || resp.status === 429) return { valid: true };
    if (resp.status === 401) return { valid: false, error: 'API key is invalid or has been revoked' };
    if (resp.status === 403) return { valid: false, error: 'API key lacks required permissions' };
    return { valid: false, error: `Unexpected response (${resp.status})` };
  } catch (err: any) {
    if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
      return { valid: false, error: 'Validation timed out — check your network' };
    }
    return { valid: false, error: 'Could not reach Anthropic servers — check your connection' };
  }
}

/**
 * Validate an OpenRouter API key via their auth endpoint.
 */
export async function validateOpenRouterKey(key: string): Promise<ValidationResult> {
  const trimmed = key.trim();
  if (!trimmed) return { valid: false, error: 'Key is empty' };

  try {
    const resp = await fetch('https://openrouter.ai/api/v1/auth/key', {
      headers: { Authorization: `Bearer ${trimmed}` },
      signal: AbortSignal.timeout(8000),
    });

    if (resp.ok) return { valid: true };
    if (resp.status === 401 || resp.status === 403) {
      return { valid: false, error: 'API key is invalid' };
    }
    return { valid: false, error: `Unexpected response (${resp.status})` };
  } catch (err: any) {
    if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
      return { valid: false, error: 'Validation timed out — check your network' };
    }
    return { valid: false, error: 'Could not reach OpenRouter — check your connection' };
  }
}

/** Map of key type to validator. Keys without a validator are saved without pre-check. */
const VALIDATORS: Partial<Record<string, (key: string) => Promise<ValidationResult>>> = {
  gemini: validateGeminiKey,
  anthropic: validateAnthropicKey,
  openrouter: validateOpenRouterKey,
};

/**
 * Validate an API key if a validator exists for the given key type.
 * Returns { valid: true } for key types without a validator.
 */
export async function validateApiKey(
  keyType: string,
  value: string,
): Promise<ValidationResult> {
  const validator = VALIDATORS[keyType];
  if (!validator) return { valid: true };
  return validator(value);
}

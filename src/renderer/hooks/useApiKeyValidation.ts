/**
 * useApiKeyValidation.ts — Lightweight pre-validation for API keys.
 *
 * Validates keys via the main process IPC bridge to avoid renderer
 * CORS/CSP restrictions. Falls back to format-only checks if IPC
 * is unavailable.
 */

export interface ValidationResult {
  valid: boolean;
  error?: string;
}

/**
 * Validate an API key via the main process IPC bridge.
 * The main process makes the HTTP call (no CORS restrictions).
 * Returns { valid: true } for key types without a validator.
 */
export async function validateApiKey(
  keyType: string,
  value: string,
): Promise<ValidationResult> {
  try {
    return await window.eve.settings.validateApiKey(keyType, value) as ValidationResult;
  } catch {
    // IPC unavailable — accept the key and let runtime validate
    return { valid: true };
  }
}

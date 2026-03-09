/**
 * privacy-shield.ts — Outbound PII Scrubber + Inbound Rehydrator
 *
 * The Privacy Shield treats every frontier model provider as an untrusted,
 * identity-blind compute node. Before ANY data leaves the machine for a
 * cloud LLM, it passes through this module which:
 *
 *   1. SCRUBS known PII (user name, agent name, third-party names) by
 *      replacing them with stable, reversible placeholders.
 *   2. DETECTS unknown PII (emails, phones, API keys, IPs, credit cards,
 *      file paths containing the OS username) via regex patterns and
 *      replaces them with typed placeholders.
 *   3. REHYDRATES inbound responses — any placeholder the model echoes
 *      back is transparently restored to the original value before the
 *      user ever sees it.
 *
 * Architecture:
 *   ┌───────────────┐     scrub()      ┌──────────────┐
 *   │  LLMClient    │ ──────────────▶  │  Cloud LLM   │
 *   │  (complete /  │                   │  (untrusted)  │
 *   │   stream)     │ ◀──────────────  │              │
 *   └───────────────┘    rehydrate()    └──────────────┘
 *
 * cLaw Compliance:
 *   - All PII mappings are held in volatile memory only — never persisted.
 *   - Session maps are destroyed when the shield is reset or app exits.
 *   - The shield itself never logs original PII values.
 *   - Placeholder format is deterministic per-session but unpredictable
 *     across sessions (uses a session nonce).
 */

import { randomBytes } from 'crypto';

// ── Types ─────────────────────────────────────────────────────────────

export type PiiCategory =
  | 'NAME'           // Known person names (user, agent, contacts)
  | 'EMAIL'          // Email addresses
  | 'PHONE'          // Phone numbers
  | 'IP'             // IP addresses
  | 'SECRET'         // API keys, tokens, passwords
  | 'CREDIT_CARD'    // Credit/debit card numbers
  | 'SSN'            // Social security / national ID numbers
  | 'PATH'           // File paths containing OS username
  | 'URL_ID'         // URLs containing user-identifying parameters
  | 'MISC';          // Catch-all for high-entropy strings in sensitive positions

export interface PiiMapping {
  placeholder: string;
  original: string;
  category: PiiCategory;
  /** Number of times this mapping was applied in this session */
  hitCount: number;
}

export interface PrivacyShieldConfig {
  /** Master on/off switch */
  enabled: boolean;
  /** Scrub known names (user, agent, contacts) */
  scrubNames: boolean;
  /** Scrub detected emails */
  scrubEmails: boolean;
  /** Scrub detected phone numbers */
  scrubPhones: boolean;
  /** Scrub detected API keys / tokens / secrets */
  scrubSecrets: boolean;
  /** Scrub file paths containing the OS username */
  scrubPaths: boolean;
  /** Scrub IP addresses */
  scrubIps: boolean;
  /** Scrub credit card numbers */
  scrubCreditCards: boolean;
  /** Scrub SSN / national ID patterns */
  scrubSsn: boolean;
  /** Log placeholder stats (never logs original values) */
  debugStats: boolean;
}

export interface ScrubResult {
  /** The scrubbed text with placeholders */
  text: string;
  /** Number of replacements made */
  replacementCount: number;
  /** Categories of PII found */
  categoriesFound: PiiCategory[];
}

export interface ShieldStats {
  /** Total scrub operations performed */
  totalScrubs: number;
  /** Total rehydrations performed */
  totalRehydrations: number;
  /** Number of unique PII values tracked */
  uniqueMappings: number;
  /** Breakdown by category */
  categoryBreakdown: Record<PiiCategory, number>;
  /** Whether the shield is currently enabled */
  enabled: boolean;
}

// ── Constants ─────────────────────────────────────────────────────────

const DEFAULT_CONFIG: PrivacyShieldConfig = {
  enabled: true,
  scrubNames: true,
  scrubEmails: true,
  scrubPhones: true,
  scrubSecrets: true,
  scrubPaths: true,
  scrubIps: true,
  scrubCreditCards: true,
  scrubSsn: true,
  debugStats: false,
};

// Placeholder format: «PII:CATEGORY:hash»
// Uses guillemets (« ») because they're visually distinct from any natural
// text and extremely unlikely to appear in code, prose, or model output.
const PLACEHOLDER_PREFIX = '«PII:';
const PLACEHOLDER_SUFFIX = '»';

// ── Regex Patterns ────────────────────────────────────────────────────
// All patterns use bounded quantifiers to prevent catastrophic backtracking.

const EMAIL_PATTERN = /\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Z|a-z]{2,10}\b/g;

const PHONE_PATTERN = /(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b/g;

const IP_V4_PATTERN = /\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b/g;

const CREDIT_CARD_PATTERN = /\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2}))[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/g;

const SSN_PATTERN = /\b\d{3}[\s-]?\d{2}[\s-]?\d{4}\b/g;

// Secret patterns (reused from git-scanner.ts architecture)
const SECRET_PATTERNS: Array<{ name: string; pattern: RegExp }> = [
  { name: 'aws-access-key', pattern: /AKIA[0-9A-Z]{16}/g },
  { name: 'github-token', pattern: /gh[pousr]_[A-Za-z0-9_]{36,}/g },
  { name: 'stripe-key', pattern: /(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}/g },
  { name: 'google-api-key', pattern: /AIza[0-9A-Za-z_-]{35}/g },
  { name: 'slack-token', pattern: /xox[bpors]-[0-9]{10,}-[A-Za-z0-9]{10,}/g },
  { name: 'sendgrid-key', pattern: /SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}/g },
  { name: 'jwt-token', pattern: /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g },
  { name: 'private-key', pattern: /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/g },
  { name: 'openai-key', pattern: /sk-[A-Za-z0-9]{20,}T3BlbkFJ[A-Za-z0-9]{20,}/g },
  { name: 'generic-api-key', pattern: /(?:api[_-]?key|apikey|api[_-]?secret)[\s]*[=:][\s]*['"][A-Za-z0-9_-]{20,}['"]/gi },
];

// Known safe IPs that should NOT be scrubbed
const SAFE_IPS = new Set([
  '127.0.0.1', '0.0.0.0', '255.255.255.255',
  '192.168.0.1', '192.168.1.1', '10.0.0.1',
]);

// Known safe phone-like patterns (years, port numbers, etc.)
const PHONE_FALSE_POSITIVE_PATTERNS = [
  /^20[0-2]\d$/,    // years 2000-2029
  /^19\d{2}$/,      // years 1900-1999
  /^\d{1,5}$/,      // just a number (port, count, etc.)
];

// ── Privacy Shield Engine ─────────────────────────────────────────────

export class PrivacyShield {
  private config: PrivacyShieldConfig;
  private sessionNonce: string;

  // Bidirectional maps for O(1) lookup in both directions
  private originalToPlaceholder = new Map<string, string>();  // original → placeholder
  private placeholderToOriginal = new Map<string, PiiMapping>(); // placeholder → mapping

  // Known names to scrub (populated from settings at runtime)
  private knownNames = new Set<string>();

  // OS username for path scrubbing
  private osUsername: string;

  // Stats
  private stats = {
    totalScrubs: 0,
    totalRehydrations: 0,
  };

  constructor(config: Partial<PrivacyShieldConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.sessionNonce = randomBytes(8).toString('hex');
    this.osUsername = process.env.USERNAME || process.env.USER || '';
  }

  // ── Configuration ────────────────────────────────────────────────

  /**
   * Register known names that should be scrubbed from outbound text.
   * Call this after settings are loaded and whenever agent config changes.
   */
  registerKnownNames(names: string[]): void {
    this.knownNames.clear();
    for (const name of names) {
      if (name && name.length >= 2) {
        this.knownNames.add(name);
        // Also register individual parts of multi-word names
        const parts = name.split(/\s+/);
        if (parts.length > 1) {
          for (const part of parts) {
            if (part.length >= 3) {
              this.knownNames.add(part);
            }
          }
        }
      }
    }
  }

  /**
   * Update configuration at runtime.
   */
  updateConfig(config: Partial<PrivacyShieldConfig>): void {
    this.config = { ...this.config, ...config };
  }

  /**
   * Check if the shield is currently enabled.
   */
  isEnabled(): boolean {
    return this.config.enabled;
  }

  // ── Core: Scrub Outbound Text ────────────────────────────────────

  /**
   * Scrub PII from outbound text, replacing with reversible placeholders.
   * This is the primary outbound function — call before sending to any provider.
   */
  scrub(text: string): ScrubResult {
    if (!this.config.enabled || !text) {
      return { text, replacementCount: 0, categoriesFound: [] };
    }

    this.stats.totalScrubs++;
    let result = text;
    let replacementCount = 0;
    const categoriesFound = new Set<PiiCategory>();

    // Order matters: scrub specific patterns first (secrets, emails),
    // then broader patterns (names, paths) to avoid partial matches.

    // 1. Secrets (highest priority — must catch before name/email patterns eat them)
    if (this.config.scrubSecrets) {
      for (const secretDef of SECRET_PATTERNS) {
        const r = this.replacePattern(result, secretDef.pattern, 'SECRET');
        if (r.count > 0) {
          result = r.text;
          replacementCount += r.count;
          categoriesFound.add('SECRET');
        }
      }
    }

    // 2. Credit cards
    if (this.config.scrubCreditCards) {
      const r = this.replacePattern(result, CREDIT_CARD_PATTERN, 'CREDIT_CARD');
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('CREDIT_CARD');
      }
    }

    // 3. SSN / national ID
    if (this.config.scrubSsn) {
      const r = this.replacePattern(result, SSN_PATTERN, 'SSN', (match) => {
        // Only scrub if it looks like a real SSN (not a random 9-digit number)
        // SSNs don't start with 000, 666, or 900-999
        const digits = match.replace(/[\s-]/g, '');
        const area = parseInt(digits.slice(0, 3));
        if (area === 0 || area === 666 || area >= 900) return false;
        return true;
      });
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('SSN');
      }
    }

    // 4. Email addresses
    if (this.config.scrubEmails) {
      const r = this.replacePattern(result, EMAIL_PATTERN, 'EMAIL');
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('EMAIL');
      }
    }

    // 5. Phone numbers
    if (this.config.scrubPhones) {
      const r = this.replacePattern(result, PHONE_PATTERN, 'PHONE', (match) => {
        const digits = match.replace(/\D/g, '');
        // Must have at least 7 digits to be a real phone number
        if (digits.length < 7) return false;
        // Filter out false positives (years, ports, etc.)
        for (const fp of PHONE_FALSE_POSITIVE_PATTERNS) {
          if (fp.test(digits)) return false;
        }
        return true;
      });
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('PHONE');
      }
    }

    // 6. IP addresses
    if (this.config.scrubIps) {
      const r = this.replacePattern(result, IP_V4_PATTERN, 'IP', (match) => {
        return !SAFE_IPS.has(match) && !match.startsWith('192.168.') && !match.startsWith('10.');
      });
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('IP');
      }
    }

    // 7. File paths containing OS username
    if (this.config.scrubPaths && this.osUsername && this.osUsername.length >= 2) {
      const r = this.scrubPaths(result);
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('PATH');
      }
    }

    // 8. Known names (last — broadest pattern, most likely to cause false positives)
    if (this.config.scrubNames) {
      const r = this.scrubKnownNames(result);
      if (r.count > 0) {
        result = r.text;
        replacementCount += r.count;
        categoriesFound.add('NAME');
      }
    }

    if (this.config.debugStats && replacementCount > 0) {
      console.log(
        `[PrivacyShield] Scrubbed ${replacementCount} PII items. ` +
        `Categories: ${[...categoriesFound].join(', ')}. ` +
        `Total mappings: ${this.originalToPlaceholder.size}`
      );
    }

    return {
      text: result,
      replacementCount,
      categoriesFound: [...categoriesFound],
    };
  }

  // ── Core: Rehydrate Inbound Text ────────────────────────────────

  /**
   * Restore placeholders in inbound text with original PII values.
   * Call this on every response before it reaches the user.
   */
  rehydrate(text: string): string {
    if (!this.config.enabled || !text || this.placeholderToOriginal.size === 0) {
      return text;
    }

    this.stats.totalRehydrations++;
    let result = text;

    // Replace all placeholders found in the text
    // Use a single regex to find all placeholders efficiently
    const placeholderRegex = /«PII:[A-Z_]+:[a-f0-9]+»/g;
    result = result.replace(placeholderRegex, (match) => {
      const mapping = this.placeholderToOriginal.get(match);
      if (mapping) {
        mapping.hitCount++;
        return mapping.original;
      }
      return match; // Unknown placeholder — leave as-is
    });

    return result;
  }

  // ── Convenience: Scrub LLM Request ─────────────────────────────

  /**
   * Scrub all text fields in an LLM request object.
   * Returns a new request with scrubbed content (does not mutate original).
   */
  scrubRequest(request: {
    messages?: Array<{ role: string; content: string | any[] | null; [key: string]: any }>;
    systemPrompt?: string;
    [key: string]: any;
  }): typeof request {
    if (!this.config.enabled) return request;

    const scrubbed = { ...request };

    // Scrub system prompt
    if (scrubbed.systemPrompt && typeof scrubbed.systemPrompt === 'string') {
      scrubbed.systemPrompt = this.scrub(scrubbed.systemPrompt).text;
    }

    // Scrub messages
    if (scrubbed.messages) {
      scrubbed.messages = scrubbed.messages.map((msg: any) => {
        const newMsg = { ...msg };
        if (typeof newMsg.content === 'string') {
          newMsg.content = this.scrub(newMsg.content).text;
        } else if (Array.isArray(newMsg.content)) {
          newMsg.content = newMsg.content.map((part: any) => {
            if (part.type === 'text' && typeof part.text === 'string') {
              return { ...part, text: this.scrub(part.text).text };
            }
            return part;
          });
        }
        return newMsg;
      });
    }

    return scrubbed;
  }

  /**
   * Rehydrate all text fields in an LLM response object.
   */
  rehydrateResponse(response: {
    content?: string;
    toolCalls?: Array<{ input: any; [key: string]: any }>;
    [key: string]: any;
  }): typeof response {
    if (!this.config.enabled || this.placeholderToOriginal.size === 0) return response;

    const rehydrated = { ...response };

    if (typeof rehydrated.content === 'string') {
      rehydrated.content = this.rehydrate(rehydrated.content);
    }

    // Rehydrate tool call arguments (model might echo back placeholders in tool inputs)
    if (rehydrated.toolCalls) {
      rehydrated.toolCalls = rehydrated.toolCalls.map((tc: any) => {
        const newTc = { ...tc };
        if (typeof newTc.input === 'string') {
          newTc.input = this.rehydrate(newTc.input);
        } else if (newTc.input && typeof newTc.input === 'object') {
          newTc.input = this.rehydrateObject(newTc.input);
        }
        return newTc;
      });
    }

    return rehydrated;
  }

  // ── Stats & Diagnostics ─────────────────────────────────────────

  /**
   * Get current shield statistics (never exposes original PII values).
   */
  getStats(): ShieldStats {
    const categoryBreakdown: Record<PiiCategory, number> = {
      NAME: 0, EMAIL: 0, PHONE: 0, IP: 0, SECRET: 0,
      CREDIT_CARD: 0, SSN: 0, PATH: 0, URL_ID: 0, MISC: 0,
    };

    for (const mapping of this.placeholderToOriginal.values()) {
      categoryBreakdown[mapping.category] = (categoryBreakdown[mapping.category] || 0) + 1;
    }

    return {
      totalScrubs: this.stats.totalScrubs,
      totalRehydrations: this.stats.totalRehydrations,
      uniqueMappings: this.originalToPlaceholder.size,
      categoryBreakdown,
      enabled: this.config.enabled,
    };
  }

  /**
   * Reset all session mappings. Call on session boundary or app exit.
   */
  reset(): void {
    this.originalToPlaceholder.clear();
    this.placeholderToOriginal.clear();
    this.sessionNonce = randomBytes(8).toString('hex');
    this.stats = { totalScrubs: 0, totalRehydrations: 0 };
  }

  /**
   * Get the current config (for UI display / settings).
   */
  getConfig(): PrivacyShieldConfig {
    return { ...this.config };
  }

  // ── Private: Pattern Replacement Engine ──────────────────────────

  /**
   * Replace all matches of a regex pattern with placeholders.
   * Uses a validation callback to filter false positives.
   */
  private replacePattern(
    text: string,
    pattern: RegExp,
    category: PiiCategory,
    validate?: (match: string) => boolean,
  ): { text: string; count: number } {
    let count = 0;
    // Clone the regex to reset lastIndex
    const regex = new RegExp(pattern.source, pattern.flags.replace('g', '') + 'g');

    const result = text.replace(regex, (match) => {
      // Skip if already a placeholder
      if (match.startsWith(PLACEHOLDER_PREFIX)) return match;

      // Validate if callback provided
      if (validate && !validate(match)) return match;

      count++;
      return this.getOrCreatePlaceholder(match, category);
    });

    return { text: result, count };
  }

  /**
   * Scrub known names from text using word-boundary matching.
   * Sorted longest-first to prevent partial replacements.
   */
  private scrubKnownNames(text: string): { text: string; count: number } {
    if (this.knownNames.size === 0) return { text, count: 0 };

    let result = text;
    let count = 0;

    // Sort longest first to match "John Smith" before "John"
    const sortedNames = [...this.knownNames].sort((a, b) => b.length - a.length);

    for (const name of sortedNames) {
      // Skip very short names to avoid false positives (e.g., "AI", "OS")
      if (name.length < 3) continue;

      // Escape regex special chars in the name
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

      // Word-boundary match, case-sensitive for proper nouns
      const nameRegex = new RegExp(`\\b${escaped}\\b`, 'g');
      const before = result;
      result = result.replace(nameRegex, (match) => {
        count++;
        return this.getOrCreatePlaceholder(match, 'NAME');
      });
    }

    return { text: result, count };
  }

  /**
   * Scrub file paths that contain the OS username.
   */
  private scrubPaths(text: string): { text: string; count: number } {
    if (!this.osUsername) return { text, count: 0 };

    let count = 0;
    const escaped = this.osUsername.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

    // Match Windows and Unix paths containing the username
    // Windows: C:\Users\username\... or /Users/username/...
    const pathPatterns = [
      new RegExp(`[A-Za-z]:\\\\(?:Users|users)\\\\${escaped}(?:\\\\[^\\s"'<>|*?]+)*`, 'g'),
      new RegExp(`/(?:Users|home|users)/${escaped}(?:/[^\\s"'<>|*?]+)*`, 'g'),
    ];

    let result = text;
    for (const pattern of pathPatterns) {
      result = result.replace(pattern, (match) => {
        count++;
        return this.getOrCreatePlaceholder(match, 'PATH');
      });
    }

    return { text: result, count };
  }

  /**
   * Get or create a placeholder for a PII value.
   * Deterministic per-session: same input always produces same placeholder.
   */
  private getOrCreatePlaceholder(original: string, category: PiiCategory): string {
    // Check if we already have a placeholder for this exact value
    const existing = this.originalToPlaceholder.get(original);
    if (existing) return existing;

    // Generate a short deterministic hash for this value + session
    const hash = this.shortHash(original);
    const placeholder = `${PLACEHOLDER_PREFIX}${category}:${hash}${PLACEHOLDER_SUFFIX}`;

    // Store bidirectional mapping
    this.originalToPlaceholder.set(original, placeholder);
    this.placeholderToOriginal.set(placeholder, {
      placeholder,
      original,
      category,
      hitCount: 1,
    });

    return placeholder;
  }

  /**
   * Generate a short hex hash for a value, incorporating the session nonce.
   * Not cryptographically secure — just needs to be unique within a session.
   */
  private shortHash(value: string): string {
    // Simple FNV-1a hash with session nonce for uniqueness
    let h = 0x811c9dc5;
    const input = this.sessionNonce + value;
    for (let i = 0; i < input.length; i++) {
      h ^= input.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    // Convert to 8-char hex
    return (h >>> 0).toString(16).padStart(8, '0');
  }

  /**
   * Recursively rehydrate string values in an object.
   * Used for tool call arguments which may be nested objects.
   */
  private rehydrateObject(obj: any): any {
    if (typeof obj === 'string') {
      return this.rehydrate(obj);
    }
    if (Array.isArray(obj)) {
      return obj.map((item) => this.rehydrateObject(item));
    }
    if (obj && typeof obj === 'object') {
      const result: any = {};
      for (const [key, value] of Object.entries(obj)) {
        result[key] = this.rehydrateObject(value);
      }
      return result;
    }
    return obj;
  }
}

// ── Singleton ─────────────────────────────────────────────────────────

export const privacyShield = new PrivacyShield();

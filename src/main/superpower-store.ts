/**
 * superpower-store.ts — Persistent Superpower Registry & Store.
 *
 * Track II, Phase 3: The Absorber — Superpower Registry.
 *
 * A Superpower is a persisted adapted connector that:
 *   - Survives app restarts (JSON persistence in friday-data/)
 *   - Can be toggled on/off, configured, updated, and removed
 *   - Registers tools dynamically with the ConnectorRegistry
 *   - Tracks health, usage, and lifecycle state
 *
 * The SuperpowerStore is the single source of truth for all installed superpowers.
 * It bridges:
 *   - Phase 2 output (AdaptedConnector) → persistent storage
 *   - Persistent storage → ConnectorRegistry (tool registration)
 *   - User actions (enable/disable/uninstall) → state management
 *
 * cLaw Safety Boundary:
 *   - Installation REQUIRES a valid SecurityVerdict + user consent (consentToken).
 *   - No code path can bypass the consent check.
 *   - The consentToken is set by the UI consent flow (Phase 4) — never auto-generated.
 *   - Uninstall is clean: no files, no tools, no dangling state.
 */

import fs from 'fs/promises';
import path from 'path';
import type {
  AdaptedConnector,
  SuperpowerSandboxConfig,
  AdaptationPlan,
} from './adapter-engine';
import { validateAdaptedConnector } from './adapter-engine';
import type { ToolDeclaration, ConnectorCategory } from './connectors/registry';
import { contextToolRouter } from './context-tool-router';

// ── Superpower Data Model ───────────────────────────────────────────

export interface Superpower {
  /** Unique ID (matches connector.id, starts with 'sp-') */
  id: string;
  /** Human-readable name */
  name: string;
  /** What this superpower does */
  description: string;
  /** Source repository URL */
  sourceUrl: string;
  /** Source repository name */
  sourceRepo: string;
  /** Commit hash this was built from */
  sourceCommit: string;
  /** Primary programming language */
  language: string;

  /** Installation state */
  status: SuperpowerStatus;
  /** Whether this superpower is enabled (tools available) */
  enabled: boolean;
  /** User consent token (set by UI consent flow) */
  consentToken: string;
  /** When consent was given */
  consentedAt: number;

  /** Tool declarations for registry integration */
  tools: ToolDeclaration[];
  /** Generated connector source code */
  sourceCode: string;
  /** Bridge script for subprocess superpowers */
  bridgeScript?: string;
  /** Sandbox configuration */
  sandbox: SuperpowerSandboxConfig;

  /** Connector category */
  category: ConnectorCategory;
  /** Adaptation strategy used */
  strategy: string;
  /** Adaptation plan (for debugging/display) */
  plan: AdaptationPlan;

  /** Security verdict from Track I */
  securityVerdict: SecurityVerdictSummary;
  /** Runtime dependencies */
  dependencies: string[];

  /** Lifecycle timestamps */
  installedAt: number;
  updatedAt: number;
  lastUsedAt: number;
  /** Usage counter */
  usageCount: number;

  /** Health status */
  health: SuperpowerHealth;
  /** Version number (increments on update) */
  version: number;
}

export type SuperpowerStatus =
  | 'pending-consent'   // Adapted but awaiting user consent
  | 'installing'        // Consent given, setting up
  | 'installed'         // Ready to use
  | 'updating'          // Re-running analysis/adaptation for new version
  | 'error'             // Installation/runtime error
  | 'uninstalling'      // Being removed
  | 'uninstalled';      // Removed (kept briefly for audit)

export interface SuperpowerHealth {
  /** Overall health score 0-1 */
  score: number;
  /** Last health check timestamp */
  lastCheck: number;
  /** Error count since last healthy state */
  errorCount: number;
  /** Last error message */
  lastError?: string;
  /** Warnings */
  warnings: string[];
}

/**
 * Condensed security verdict for storage (full verdict can be large).
 */
export interface SecurityVerdictSummary {
  approved: boolean;
  riskLevel: string;
  summary: string;
  reviewedAt: number;
}

// ── Store Configuration ─────────────────────────────────────────────

export interface SuperpowerStoreConfig {
  /** Maximum number of installed superpowers */
  maxSuperpowers: number;
  /** Auto-disable superpowers after N consecutive errors */
  autoDisableAfterErrors: number;
  /** Whether to allow unsigned superpowers (no security verdict) */
  allowUnsigned: boolean;
}

const DEFAULT_CONFIG: SuperpowerStoreConfig = {
  maxSuperpowers: 50,
  autoDisableAfterErrors: 5,
  allowUnsigned: false,
};

// ── Superpower Store ────────────────────────────────────────────────

/**
 * Persistent store for installed superpowers.
 * Follows the same JSON persistence pattern as friday-data (memories, episodes, etc).
 *
 * cLaw: The store enforces the consent boundary — no superpower can be
 * installed without a valid consentToken. The token is ONLY set by the
 * UI consent flow in Phase 4.
 */
export class SuperpowerStore {
  private superpowers = new Map<string, Superpower>();
  private config: SuperpowerStoreConfig;
  private saveQueued = false;
  private filePath = '';
  private initialized = false;

  constructor(config: Partial<SuperpowerStoreConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ── Initialization ──────────────────────────────────────────────

  /**
   * Initialize the store — load persisted superpowers from disk.
   * Called during app startup from index.ts.
   */
  async initialize(dataDir: string): Promise<void> {
    if (this.initialized) return;

    this.filePath = `${dataDir}/superpowers.json`;

    try {
      const data = await this.loadFromDisk();
      if (data && Array.isArray(data)) {
        for (const sp of data) {
          if (sp.id && sp.status !== 'uninstalled') {
            this.superpowers.set(sp.id, sp);
          }
        }
      }
    } catch {
      // Fresh install — no file yet
      console.log('[SuperpowerStore] No existing superpowers found — fresh start');
    }

    this.initialized = true;
    console.log(`[SuperpowerStore] Loaded ${this.superpowers.size} superpowers`);
  }

  // ── Installation ────────────────────────────────────────────────

  /**
   * Prepare a superpower for installation from an adapted connector.
   * Returns a Superpower in 'pending-consent' state.
   *
   * cLaw: This does NOT install the superpower. It creates a record
   * awaiting user consent. The UI consent flow must call `confirmInstall()`
   * with a valid consent token to actually install.
   */
  prepareInstall(
    connector: AdaptedConnector,
    verdict: SecurityVerdictSummary,
    sourceUrl: string,
    sourceCommit: string,
  ): Superpower {
    // Validate the connector
    const validation = validateAdaptedConnector(connector);
    if (!validation.valid) {
      throw new Error(`Invalid connector: ${validation.errors.join(', ')}`);
    }

    // Check limits
    if (this.superpowers.size >= this.config.maxSuperpowers) {
      throw new Error(`Superpower limit reached (${this.config.maxSuperpowers}). Uninstall one first.`);
    }

    // Check security verdict
    if (!this.config.allowUnsigned && !verdict.approved) {
      throw new Error('Security verdict rejected — cannot install unapproved superpower');
    }

    // Check for existing superpower with same ID
    const existing = this.superpowers.get(connector.id);
    if (existing && existing.status !== 'uninstalled') {
      throw new Error(`Superpower "${connector.id}" is already installed. Uninstall first or update.`);
    }

    const superpower: Superpower = {
      id: connector.id,
      name: connector.label,
      description: connector.description,
      sourceUrl,
      sourceRepo: connector.plan.repoName,
      sourceCommit,
      language: connector.plan.strategy.reason.split(' ')[0] || 'unknown',

      status: 'pending-consent',
      enabled: false,
      consentToken: '', // Set by UI consent flow
      consentedAt: 0,

      tools: connector.tools,
      sourceCode: connector.sourceCode,
      bridgeScript: connector.bridgeScript,
      sandbox: connector.sandbox,

      category: connector.category,
      strategy: connector.plan.strategy.type,
      plan: connector.plan,

      securityVerdict: verdict,
      dependencies: connector.dependencies,

      installedAt: 0,
      updatedAt: 0,
      lastUsedAt: 0,
      usageCount: 0,

      health: { score: 1.0, lastCheck: Date.now(), errorCount: 0, warnings: [] },
      version: 1,
    };

    this.superpowers.set(superpower.id, superpower);
    this.queueSave();

    return superpower;
  }

  /**
   * Confirm installation with a consent token from the UI.
   *
   * cLaw Gate: This is the ONLY way to finalize installation.
   * The consentToken MUST be non-empty and is set by the consent UI.
   */
  confirmInstall(id: string, consentToken: string): Superpower {
    if (!consentToken || consentToken.trim().length === 0) {
      throw new Error('cLaw: consent token is required — cannot install without user consent');
    }

    const sp = this.superpowers.get(id);
    if (!sp) throw new Error(`Superpower not found: ${id}`);
    if (sp.status !== 'pending-consent') {
      throw new Error(`Superpower "${id}" is not pending consent (status: ${sp.status})`);
    }

    sp.consentToken = consentToken;
    sp.consentedAt = Date.now();
    sp.status = 'installed';
    sp.enabled = true;
    sp.installedAt = Date.now();

    // Register tools with the context-aware tool router
    this.registerToolsWithRouter(sp);

    this.queueSave();
    console.log(`[SuperpowerStore] Installed: ${sp.name} (${sp.tools.length} tools)`);

    return sp;
  }

  // ── Enable/Disable ──────────────────────────────────────────────

  /**
   * Enable a superpower (make its tools available).
   */
  enableSuperpower(id: string): Superpower {
    const sp = this.superpowers.get(id);
    if (!sp) throw new Error(`Superpower not found: ${id}`);
    if (sp.status !== 'installed') {
      throw new Error(`Cannot enable — status is "${sp.status}"`);
    }

    sp.enabled = true;
    this.registerToolsWithRouter(sp);
    this.queueSave();
    return sp;
  }

  /**
   * Disable a superpower (remove its tools from the palette).
   */
  disableSuperpower(id: string): Superpower {
    const sp = this.superpowers.get(id);
    if (!sp) throw new Error(`Superpower not found: ${id}`);

    sp.enabled = false;
    this.unregisterToolsFromRouter(sp);
    this.queueSave();
    return sp;
  }

  // ── Uninstall ───────────────────────────────────────────────────

  /**
   * Uninstall a superpower completely.
   * Clean removal: no files, no tools, no dangling state.
   */
  uninstallSuperpower(id: string): void {
    const sp = this.superpowers.get(id);
    if (!sp) return; // Already gone

    sp.status = 'uninstalled';
    sp.enabled = false;
    this.unregisterToolsFromRouter(sp);

    // Remove from store entirely
    this.superpowers.delete(id);
    this.queueSave();

    console.log(`[SuperpowerStore] Uninstalled: ${sp.name}`);
  }

  // ── Updates ─────────────────────────────────────────────────────

  /**
   * Update a superpower with a new adapted connector.
   * Preserves consent token, usage stats, and version history.
   */
  updateSuperpower(
    id: string,
    newConnector: AdaptedConnector,
    newVerdict: SecurityVerdictSummary,
    newCommit: string,
  ): Superpower {
    const sp = this.superpowers.get(id);
    if (!sp) throw new Error(`Superpower not found: ${id}`);
    if (sp.status !== 'installed') {
      throw new Error(`Cannot update — status is "${sp.status}"`);
    }

    const validation = validateAdaptedConnector(newConnector);
    if (!validation.valid) {
      throw new Error(`Invalid connector update: ${validation.errors.join(', ')}`);
    }

    // Preserve consent and usage, update everything else
    sp.tools = newConnector.tools;
    sp.sourceCode = newConnector.sourceCode;
    sp.bridgeScript = newConnector.bridgeScript;
    sp.sandbox = newConnector.sandbox;
    sp.plan = newConnector.plan;
    sp.securityVerdict = newVerdict;
    sp.dependencies = newConnector.dependencies;
    sp.sourceCommit = newCommit;
    sp.updatedAt = Date.now();
    sp.version += 1;
    sp.health = { score: 1.0, lastCheck: Date.now(), errorCount: 0, warnings: [] };

    this.queueSave();
    console.log(`[SuperpowerStore] Updated: ${sp.name} → v${sp.version}`);

    return sp;
  }

  // ── Usage Tracking ──────────────────────────────────────────────

  /**
   * Record a tool usage event.
   */
  recordUsage(id: string): void {
    const sp = this.superpowers.get(id);
    if (!sp) return;

    sp.lastUsedAt = Date.now();
    sp.usageCount += 1;
    this.queueSave();
  }

  /**
   * Record an error event.
   */
  recordError(id: string, error: string): void {
    const sp = this.superpowers.get(id);
    if (!sp) return;

    sp.health.errorCount += 1;
    sp.health.lastError = error;
    sp.health.score = Math.max(0, sp.health.score - 0.1);

    // Auto-disable after too many errors
    if (sp.health.errorCount >= this.config.autoDisableAfterErrors) {
      sp.enabled = false;
      sp.health.warnings.push(`Auto-disabled after ${sp.health.errorCount} consecutive errors`);
      console.warn(`[SuperpowerStore] Auto-disabled ${sp.name} after ${sp.health.errorCount} errors`);
    }

    this.queueSave();
  }

  /**
   * Reset error count (e.g., after successful execution).
   */
  resetErrors(id: string): void {
    const sp = this.superpowers.get(id);
    if (!sp) return;

    sp.health.errorCount = 0;
    sp.health.lastError = undefined;
    sp.health.score = Math.min(1.0, sp.health.score + 0.1);
    this.queueSave();
  }

  // ── Queries ─────────────────────────────────────────────────────

  /**
   * Get a superpower by ID.
   */
  get(id: string): Superpower | null {
    return this.superpowers.get(id) || null;
  }

  /**
   * Get all installed superpowers.
   */
  getAll(): Superpower[] {
    return Array.from(this.superpowers.values());
  }

  /**
   * Get all enabled superpowers (tools should be in palette).
   */
  getEnabled(): Superpower[] {
    return this.getAll().filter(sp => sp.enabled && sp.status === 'installed');
  }

  /**
   * Get all tool declarations from enabled superpowers.
   * Used by the ConnectorRegistry to include superpower tools.
   */
  getEnabledTools(): ToolDeclaration[] {
    return this.getEnabled().flatMap(sp => sp.tools);
  }

  /**
   * Get superpowers by category.
   */
  getByCategory(category: Superpower['category']): Superpower[] {
    return this.getAll().filter(sp => sp.category === category);
  }

  /**
   * Get superpowers needing attention (errors, outdated).
   */
  getNeedingAttention(): Superpower[] {
    return this.getAll().filter(sp =>
      sp.health.errorCount > 0 ||
      sp.health.score < 0.5 ||
      sp.status === 'error',
    );
  }

  /**
   * Check if a tool name belongs to a superpower.
   */
  findSuperpowerByTool(toolName: string): Superpower | null {
    for (const sp of this.superpowers.values()) {
      if (sp.tools.some(t => t.name === toolName)) {
        return sp;
      }
    }
    return null;
  }

  /**
   * Get the store status.
   */
  getStatus(): SuperpowerStoreStatus {
    const all = this.getAll();
    return {
      totalInstalled: all.filter(sp => sp.status === 'installed').length,
      totalEnabled: all.filter(sp => sp.enabled).length,
      totalTools: this.getEnabledTools().length,
      totalUsage: all.reduce((sum, sp) => sum + sp.usageCount, 0),
      healthyCount: all.filter(sp => sp.health.score > 0.7).length,
      errorCount: all.filter(sp => sp.health.errorCount > 0).length,
      pendingConsent: all.filter(sp => sp.status === 'pending-consent').length,
    };
  }

  // ── Tool Router Integration ────────────────────────────────────

  /**
   * Register a superpower's tools with the context-aware tool router.
   */
  private registerToolsWithRouter(sp: Superpower): void {
    try {
      contextToolRouter.registerToolsFromDeclarations(
        sp.tools.map(t => ({ name: t.name, description: t.description })),
      );
    } catch {
      // Non-fatal — router may not be initialized yet during startup
    }
  }

  /**
   * Unregister a superpower's tools from the context-aware tool router.
   */
  private unregisterToolsFromRouter(sp: Superpower): void {
    try {
      for (const tool of sp.tools) {
        contextToolRouter.unregisterTool(tool.name);
      }
    } catch {
      // Non-fatal
    }
  }

  // ── Prompt Context ──────────────────────────────────────────────

  /**
   * Generate prompt context describing installed superpowers.
   * Injected into the system prompt so the agent knows what superpowers exist.
   */
  getPromptContext(): string {
    const enabled = this.getEnabled();
    if (enabled.length === 0) return '';

    const lines: string[] = ['INSTALLED SUPERPOWERS:'];
    for (const sp of enabled) {
      const toolNames = sp.tools.map(t => t.name).join(', ');
      lines.push(`• ${sp.name} (${sp.language}) — ${sp.description} | Tools: ${toolNames}`);
    }

    return lines.join('\n');
  }

  // ── Persistence ─────────────────────────────────────────────────

  /**
   * Queue a save operation (debounced to avoid excessive writes).
   */
  private queueSave(): void {
    if (this.saveQueued) return;
    this.saveQueued = true;

    // Debounce: save after 500ms of no changes
    setTimeout(async () => {
      this.saveQueued = false;
      await this.saveToDisk();
    }, 500);
  }

  /**
   * Save all superpowers to disk.
   * Writes atomically via temp file + rename to avoid corruption.
   */
  private async saveToDisk(): Promise<void> {
    if (!this.filePath) return;

    try {
      const data = JSON.stringify(this.getAll(), null, 2);
      this.lastSavedData = data;

      // Ensure directory exists
      const dir = path.dirname(this.filePath);
      await fs.mkdir(dir, { recursive: true });

      // Atomic write: write to temp file, then rename
      const tmpPath = `${this.filePath}.tmp`;
      await fs.writeFile(tmpPath, data, 'utf-8');
      await fs.rename(tmpPath, this.filePath);
    } catch (err) {
      console.error('[SuperpowerStore] Failed to save:', err instanceof Error ? err.message : 'Unknown error');
    }
  }

  /**
   * Load superpowers from disk.
   */
  private async loadFromDisk(): Promise<Superpower[] | null> {
    if (!this.filePath) return null;

    try {
      const raw = await fs.readFile(this.filePath, 'utf-8');
      const data = JSON.parse(raw);
      if (Array.isArray(data)) return data;
      return null;
    } catch {
      // File doesn't exist or is corrupted — fresh start
      return null;
    }
  }

  // Exposed for testing
  lastSavedData = '';

  /**
   * Serialize all superpowers for export/backup.
   */
  exportAll(): string {
    return JSON.stringify(this.getAll(), null, 2);
  }

  /**
   * Import superpowers from serialized data.
   * Does NOT bypass consent — imported superpowers must already have tokens.
   */
  importAll(json: string): { imported: number; skipped: number; errors: string[] } {
    const errors: string[] = [];
    let imported = 0;
    let skipped = 0;

    try {
      const data = JSON.parse(json);
      if (!Array.isArray(data)) {
        return { imported: 0, skipped: 0, errors: ['Data is not an array'] };
      }

      for (const sp of data) {
        if (!sp.id || !sp.consentToken) {
          errors.push(`Skipped: ${sp.id || 'unknown'} — missing consent token`);
          skipped++;
          continue;
        }
        if (this.superpowers.has(sp.id)) {
          errors.push(`Skipped: ${sp.id} — already installed`);
          skipped++;
          continue;
        }
        this.superpowers.set(sp.id, sp);
        imported++;
      }

      this.queueSave();
    } catch (err) {
      errors.push(`Parse error: ${err}`);
    }

    return { imported, skipped, errors };
  }
}

export interface SuperpowerStoreStatus {
  totalInstalled: number;
  totalEnabled: number;
  totalTools: number;
  totalUsage: number;
  healthyCount: number;
  errorCount: number;
  pendingConsent: number;
}

// ── Singleton Export ────────────────────────────────────────────────

export const superpowerStore = new SuperpowerStore();

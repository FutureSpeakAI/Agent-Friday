/**
 * superpowers-registry.ts — Manages installed "Superpowers" (loaded programs).
 *
 * Wraps the GitLoader with enable/disable state, per-tool toggles,
 * per-permission controls, usage statistics, and NASSE-style safety scoring.
 *
 * "Superpowers" is the user-facing term for loaded programs. When Agent Friday
 * loads a repo, it becomes a superpower — a set of capabilities the agent can
 * use, each individually toggleable.
 */

import { promises as fs } from 'fs';
import * as path from 'path';
import { app } from 'electron';
import { gitLoader, type LoadedRepo } from './git-loader';

/* ── Types ─────────────────────────────────────────────────────────── */

export interface SuperpowerTool {
  name: string;
  description: string;
  enabled: boolean;
  invocations: number;
  lastUsed: number | null;
  avgLatencyMs: number;
  errors: number;
}

export interface SuperpowerPermissions {
  networkDomains: string[];        // Allowed outbound domains
  filesystemAccess: 'none' | 'scratch' | 'readonly' | 'readwrite';
  memoryAccess: boolean;           // Can read agent memory?
  maxCpuMs: number;                // Max CPU time per invocation
  maxMemoryMb: number;             // Max memory per invocation
}

export type SuperpowerStatus =
  | 'active'       // Loaded and enabled
  | 'disabled'     // Loaded but disabled by user
  | 'analyzing'    // Being analyzed by safety scanner
  | 'installing'   // Being cloned and indexed
  | 'error'        // Failed to load or analyze
  | 'pending';     // Queued for installation

export type NasseRisk = 'low' | 'medium' | 'high';

export interface NasseScore {
  score: number;         // 0.0 – 1.0
  risk: NasseRisk;       // Derived from score
  findings: string[];    // What the scanner found
  scannedAt: number;     // Timestamp of last scan
  autoApproved: boolean; // Low-risk auto-approved
}

export interface ImprovementInfo {
  analyzed: boolean;
  analysisDate: number | null;
  improvementsFound: number;
  improvementsApplied: number;
  categories: string[];
}

export interface ForkInfo {
  hasFork: boolean;
  forkUrl: string | null;
  prsOpened: number;
  prsMerged: number;
  lastSyncDate: number | null;
}

export interface Superpower {
  id: string;                      // Same as LoadedRepo.id — "owner/name@branch"
  name: string;                    // Display name (repo name)
  owner: string;
  description: string;
  repoUrl: string;
  status: SuperpowerStatus;
  enabled: boolean;
  installedAt: number;
  lastUsed: number | null;

  // Tools extracted from this program
  tools: SuperpowerTool[];

  // Safety
  nasse: NasseScore | null;

  // Permissions (user-configurable)
  permissions: SuperpowerPermissions;

  // Improvement engine
  improvement: ImprovementInfo;

  // Fork status
  fork: ForkInfo;

  // Aggregate stats
  totalInvocations: number;
  totalErrors: number;
  avgLatencyMs: number;
}

/* ── Default permissions (restrictive by default) ─────────────────── */

const DEFAULT_PERMISSIONS: SuperpowerPermissions = {
  networkDomains: [],
  filesystemAccess: 'scratch',
  memoryAccess: false,
  maxCpuMs: 5000,
  maxMemoryMb: 256,
};

/* ── First-Party Trusted Repos ────────────────────────────────────── */
//
// Sprint 6 Track E Phase 1: Known first-party repos get reduced security
// scanning. These repos are auto-approved regardless of NASSE score because
// they are maintained by the Agent Friday team and contain expected patterns
// (shell execution, filesystem access) that are features, not threats.
//

/**
 * Set of GitHub owner/repo identifiers that are first-party trusted.
 * Repos in this list bypass NASSE auto-approval thresholds and are
 * always approved with a 'low' risk classification and annotated findings.
 */
export const TRUSTED_FIRST_PARTY_REPOS = new Set([
  'FutureSpeakAI/agent-fridays-coding-kit',
  'FutureSpeakAI/agent-friday',
]);

/* ── Registry ─────────────────────────────────────────────────────── */

class SuperpowersRegistry {
  private superpowers = new Map<string, Superpower>();
  private dataPath: string;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.dataPath = path.join(app.getPath('userData'), 'superpowers-registry.json');
  }

  async initialize(): Promise<void> {
    try {
      const raw = await fs.readFile(this.dataPath, 'utf-8');
      const data: Superpower[] = JSON.parse(raw);
      for (const sp of data) {
        this.superpowers.set(sp.id, sp);
      }
      console.log(`[Superpowers] Loaded ${this.superpowers.size} superpowers from registry`);
    } catch {
      // No existing registry — start fresh
      console.log('[Superpowers] No existing registry, starting fresh');
    }

    // Sync with GitLoader — discover any repos loaded outside the registry
    this.syncWithGitLoader();
  }

  /**
   * Discover repos loaded in GitLoader that aren't in the registry.
   * This handles the case where someone loaded a repo via voice/tools
   * before the Superpowers UI existed.
   */
  private syncWithGitLoader(): void {
    const loaded = gitLoader.listLoaded();
    for (const repo of loaded) {
      if (!this.superpowers.has(repo.id)) {
        this.registerFromRepo(repo);
      }
    }
  }

  /**
   * Create a Superpower entry from a loaded GitLoader repo.
   */
  private registerFromRepo(repo: {
    id: string;
    name: string;
    owner: string;
    branch: string;
    files: number;
    loadedAt: number;
  }): Superpower {
    const superpower: Superpower = {
      id: repo.id,
      name: repo.name,
      owner: repo.owner,
      description: '',
      repoUrl: `https://github.com/${repo.owner}/${repo.name}`,
      status: 'active',
      enabled: true,
      installedAt: repo.loadedAt,
      lastUsed: null,
      tools: [],
      nasse: null,
      permissions: { ...DEFAULT_PERMISSIONS },
      improvement: {
        analyzed: false,
        analysisDate: null,
        improvementsFound: 0,
        improvementsApplied: 0,
        categories: [],
      },
      fork: {
        hasFork: false,
        forkUrl: null,
        prsOpened: 0,
        prsMerged: 0,
        lastSyncDate: null,
      },
      totalInvocations: 0,
      totalErrors: 0,
      avgLatencyMs: 0,
    };

    // Try to get description from GitLoader summary
    try {
      const summary = gitLoader.getSummary(repo.id);
      superpower.description = summary.description || `${repo.owner}/${repo.name}`;
    } catch {
      superpower.description = `${repo.owner}/${repo.name}`;
    }

    this.superpowers.set(repo.id, superpower);
    this.scheduleSave();
    return superpower;
  }

  /* ── Public API ─────────────────────────────────────────────────── */

  /** List all registered superpowers. */
  listAll(): Superpower[] {
    this.syncWithGitLoader();
    return Array.from(this.superpowers.values());
  }

  /** Get a specific superpower by ID. */
  get(id: string): Superpower | null {
    return this.superpowers.get(id) || null;
  }

  /** Toggle a superpower on or off. */
  async setEnabled(id: string, enabled: boolean): Promise<Superpower | null> {
    const sp = this.superpowers.get(id);
    if (!sp) return null;

    sp.enabled = enabled;
    sp.status = enabled ? 'active' : 'disabled';
    this.scheduleSave();

    console.log(`[Superpowers] ${sp.name} ${enabled ? 'enabled' : 'disabled'}`);
    return sp;
  }

  /** Toggle an individual tool within a superpower. */
  setToolEnabled(superpowerId: string, toolName: string, enabled: boolean): boolean {
    const sp = this.superpowers.get(superpowerId);
    if (!sp) return false;

    const tool = sp.tools.find((t) => t.name === toolName);
    if (!tool) return false;

    tool.enabled = enabled;
    this.scheduleSave();

    console.log(`[Superpowers] ${sp.name}/${toolName} ${enabled ? 'enabled' : 'disabled'}`);
    return true;
  }

  /** Update permissions for a superpower. */
  updatePermissions(id: string, perms: Partial<SuperpowerPermissions>): boolean {
    const sp = this.superpowers.get(id);
    if (!sp) return false;

    Object.assign(sp.permissions, perms);
    this.scheduleSave();

    console.log(`[Superpowers] ${sp.name} permissions updated`);
    return true;
  }

  /** Record a tool invocation for usage stats. */
  recordInvocation(superpowerId: string, toolName: string, latencyMs: number, success: boolean): void {
    const sp = this.superpowers.get(superpowerId);
    if (!sp) return;

    sp.lastUsed = Date.now();
    sp.totalInvocations++;
    if (!success) sp.totalErrors++;

    // Rolling average latency
    sp.avgLatencyMs =
      sp.totalInvocations === 1
        ? latencyMs
        : sp.avgLatencyMs + (latencyMs - sp.avgLatencyMs) / sp.totalInvocations;

    const tool = sp.tools.find((t) => t.name === toolName);
    if (tool) {
      tool.invocations++;
      tool.lastUsed = Date.now();
      if (!success) tool.errors++;
      tool.avgLatencyMs =
        tool.invocations === 1
          ? latencyMs
          : tool.avgLatencyMs + (latencyMs - tool.avgLatencyMs) / tool.invocations;
    }

    this.scheduleSave();
  }

  /** Install a new superpower from a repo URL. */
  async install(repoUrl: string): Promise<Superpower> {
    // Create a pending entry
    const tempId = `pending-${Date.now()}`;
    const pending: Superpower = {
      id: tempId,
      name: repoUrl.split('/').pop()?.replace('.git', '') || 'unknown',
      owner: '',
      description: 'Installing...',
      repoUrl,
      status: 'installing',
      enabled: false,
      installedAt: Date.now(),
      lastUsed: null,
      tools: [],
      nasse: null,
      permissions: { ...DEFAULT_PERMISSIONS },
      improvement: {
        analyzed: false,
        analysisDate: null,
        improvementsFound: 0,
        improvementsApplied: 0,
        categories: [],
      },
      fork: {
        hasFork: false,
        forkUrl: null,
        prsOpened: 0,
        prsMerged: 0,
        lastSyncDate: null,
      },
      totalInvocations: 0,
      totalErrors: 0,
      avgLatencyMs: 0,
    };

    this.superpowers.set(tempId, pending);

    try {
      // Load via GitLoader
      const repo = await gitLoader.load(repoUrl);

      // Replace the pending entry with the real one
      this.superpowers.delete(tempId);

      const superpower = this.registerFromRepo({
        id: repo.id,
        name: repo.name,
        owner: repo.owner,
        branch: repo.branch,
        files: repo.files.length,
        loadedAt: repo.loadedAt,
      });

      superpower.status = 'analyzing';

      // Run safety analysis (basic heuristic for now — will use NASSE later)
      const nasse = this.runBasicSafetyScan(repo);
      superpower.nasse = nasse;

      superpower.status = nasse.autoApproved ? 'active' : 'disabled';
      superpower.enabled = nasse.autoApproved;

      this.scheduleSave();
      console.log(
        `[Superpowers] Installed: ${superpower.name} — NASSE ${nasse.score.toFixed(2)} (${nasse.risk})`
      );

      return superpower;
    } catch (err) {
      // Update the pending entry to show error
      const entry = this.superpowers.get(tempId);
      if (entry) {
        entry.status = 'error';
        entry.description = err instanceof Error ? err.message : String(err);
      }
      this.scheduleSave();
      throw err;
    }
  }

  /** Uninstall a superpower — removes from registry and GitLoader. */
  async uninstall(id: string): Promise<boolean> {
    const sp = this.superpowers.get(id);
    if (!sp) return false;

    // Unload from GitLoader
    try {
      await gitLoader.unload(id);
    } catch {
      // May have already been unloaded
    }

    this.superpowers.delete(id);
    this.scheduleSave();

    console.log(`[Superpowers] Uninstalled: ${sp.name}`);
    return true;
  }

  /** Get usage stats for a superpower. */
  getUsageStats(id: string): {
    totalInvocations: number;
    totalErrors: number;
    avgLatencyMs: number;
    toolStats: Array<{ name: string; invocations: number; errors: number; avgLatencyMs: number }>;
    lastUsed: number | null;
  } | null {
    const sp = this.superpowers.get(id);
    if (!sp) return null;

    return {
      totalInvocations: sp.totalInvocations,
      totalErrors: sp.totalErrors,
      avgLatencyMs: sp.avgLatencyMs,
      toolStats: sp.tools.map((t) => ({
        name: t.name,
        invocations: t.invocations,
        errors: t.errors,
        avgLatencyMs: t.avgLatencyMs,
      })),
      lastUsed: sp.lastUsed,
    };
  }

  /** Get a list of tools exposed by a superpower (for tool registration). */
  getEnabledTools(id: string): SuperpowerTool[] {
    const sp = this.superpowers.get(id);
    if (!sp || !sp.enabled) return [];
    return sp.tools.filter((t) => t.enabled);
  }

  /** Get all enabled tools across all active superpowers. */
  getAllEnabledTools(): Array<{ superpowerId: string; tool: SuperpowerTool }> {
    const result: Array<{ superpowerId: string; tool: SuperpowerTool }> = [];
    for (const sp of this.superpowers.values()) {
      if (!sp.enabled || sp.status !== 'active') continue;
      for (const tool of sp.tools) {
        if (tool.enabled) {
          result.push({ superpowerId: sp.id, tool });
        }
      }
    }
    return result;
  }

  /* ── Basic Safety Scan (heuristic — placeholder for full NASSE) ── */

  /**
   * Check if a repo is a trusted first-party repo.
   * Trusted repos get auto-approved with annotated findings.
   */
  private isTrustedRepo(repo: LoadedRepo): boolean {
    const ownerRepo = `${repo.owner}/${repo.name}`;
    return TRUSTED_FIRST_PARTY_REPOS.has(ownerRepo);
  }

  private runBasicSafetyScan(repo: LoadedRepo): NasseScore {
    // Sprint 6 Track E: First-party trusted repos get auto-approved
    if (this.isTrustedRepo(repo)) {
      console.log(`[Superpowers] NASSE: ${repo.owner}/${repo.name} is a trusted first-party repo — auto-approved`);
      return {
        score: 0,
        risk: 'low',
        findings: [
          'First-party trusted repository — auto-approved',
          'Shell execution, filesystem access, and network calls are expected features',
        ],
        scannedAt: Date.now(),
        autoApproved: true,
      };
    }

    const findings: string[] = [];
    let score = 0;

    // Check for potentially dangerous patterns
    for (const file of repo.files) {
      const content = file.content.toLowerCase();

      // Network access indicators
      if (content.includes('fetch(') || content.includes('axios') || content.includes('http.get')) {
        if (!findings.includes('Network access detected')) {
          findings.push('Network access detected');
          score += 0.15;
        }
      }

      // Filesystem access indicators
      if (content.includes('fs.write') || content.includes('fs.unlink') || content.includes('fs.rm')) {
        if (!findings.includes('Filesystem write/delete detected')) {
          findings.push('Filesystem write/delete detected');
          score += 0.2;
        }
      }

      // Process execution
      if (content.includes('child_process') || content.includes('exec(') || content.includes('spawn(')) {
        if (!findings.includes('Process execution detected')) {
          findings.push('Process execution detected');
          score += 0.25;
        }
      }

      // eval / dynamic code execution
      if (content.includes('eval(') || content.includes('new function(')) {
        if (!findings.includes('Dynamic code execution (eval) detected')) {
          findings.push('Dynamic code execution (eval) detected');
          score += 0.3;
        }
      }

      // Crypto / sensitive operations
      if (content.includes('private_key') || content.includes('api_key') || content.includes('secret')) {
        if (!findings.includes('Sensitive data handling detected')) {
          findings.push('Sensitive data handling detected');
          score += 0.1;
        }
      }
    }

    score = Math.min(1, score);

    if (findings.length === 0) {
      findings.push('No concerning patterns detected');
    }

    const risk: NasseRisk = score < 0.3 ? 'low' : score < 0.7 ? 'medium' : 'high';

    return {
      score,
      risk,
      findings,
      scannedAt: Date.now(),
      autoApproved: score < 0.3,
    };
  }

  /* ── Persistence ────────────────────────────────────────────────── */

  private scheduleSave(): void {
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      this.save().catch((err) => {
        // Crypto Sprint 17: Sanitize error output.
        console.warn('[Superpowers] Save failed:', err instanceof Error ? err.message : 'Unknown error');
      });
    }, 2000);
  }

  private async save(): Promise<void> {
    const data = Array.from(this.superpowers.values());
    await fs.writeFile(this.dataPath, JSON.stringify(data, null, 2), 'utf-8');
  }

  /** Flush any pending saves. */
  async flush(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    await this.save();
  }
}

export const superpowersRegistry = new SuperpowersRegistry();

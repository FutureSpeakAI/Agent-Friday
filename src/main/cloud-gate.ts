/**
 * cloud-gate.ts - Consent-based cloud escalation system (Phase H.2: The Threshold).
 *
 * CloudGate is the guardian between local-first sovereignty and cloud
 * intelligence. When the ConfidenceAssessor flags a response for escalation,
 * CloudGate ensures the user explicitly consents before any data leaves
 * the machine.
 *
 * Sovereign-first: when in doubt, stay local. No renderer means no consent
 * means no cloud.
 *
 * cLaw Gate: Nothing leaves the machine without user knowledge and permission.
 * CloudGate enforces this boundary.
 *
 * Sprint 3 H.2: The Threshold - CloudGate
 */

import { ipcMain } from 'electron';
import type { BrowserWindow } from 'electron';
import type { ConfidenceResult } from './confidence-assessor';
import { settingsManager } from './settings';

// -- Types -------------------------------------------------------------------

export type TaskCategory = 'code' | 'chat' | 'analysis' | 'creative' | 'tool-use' | 'general';
export type PolicyScope = 'once' | 'session' | 'always';

export interface EscalationContext {
  taskCategory: TaskCategory;
  confidence: ConfidenceResult;
  promptPreview: string;
  targetProvider: string;
}

export interface GateDecision {
  allowed: boolean;
  reason: 'policy-allow' | 'policy-deny' | 'user-allow' | 'user-deny' | 'no-renderer' | 'no-cloud';
}

export interface GatePolicy {
  decision: 'allow' | 'deny';
  scope: PolicyScope;
  createdAt: number;
}

export interface EscalationStats {
  localDelivered: number;
  escalatedAllowed: number;
  escalatedDenied: number;
}

const SETTINGS_KEY = 'cloudGatePolicies';

export class CloudGate {
  private static instance: CloudGate | null = null;
  private mainWindow: BrowserWindow | null = null;
  private policies: Map<TaskCategory, GatePolicy> = new Map();
  private stats: EscalationStats = { localDelivered: 0, escalatedAllowed: 0, escalatedDenied: 0 };
  private started = false;

  private constructor() {}

  static getInstance(): CloudGate {
    if (!CloudGate.instance) {
      CloudGate.instance = new CloudGate();
    }
    return CloudGate.instance;
  }

  static resetInstance(): void {
    if (CloudGate.instance) {
      CloudGate.instance.stop();
    }
    CloudGate.instance = null;
  }

  start(mainWindow?: BrowserWindow): void {
    this.mainWindow = mainWindow ?? null;
    this.policies.clear();
    this.stats = { localDelivered: 0, escalatedAllowed: 0, escalatedDenied: 0 };
    this.started = true;
    this.loadPersistedPolicies();
  }

  stop(): void {
    this.policies.clear();
    this.mainWindow = null;
    this.stats = { localDelivered: 0, escalatedAllowed: 0, escalatedDenied: 0 };
    this.started = false;
  }

  async requestEscalation(context: EscalationContext): Promise<GateDecision> {
    const policy = this.policies.get(context.taskCategory);
    if (policy) {
      if (policy.scope === 'once') {
        this.policies.delete(context.taskCategory);
      }
      if (policy.decision === 'allow') {
        this.stats.escalatedAllowed++;
        return { allowed: true, reason: 'policy-allow' };
      } else {
        this.stats.escalatedDenied++;
        return { allowed: false, reason: 'policy-deny' };
      }
    }
    if (!this.mainWindow) {
      this.stats.escalatedDenied++;
      return { allowed: false, reason: 'no-renderer' };
    }
    return this.requestUserConsent(context);
  }

  setPolicy(category: TaskCategory, decision: 'allow' | 'deny', scope: PolicyScope): void {
    const policy: GatePolicy = { decision, scope, createdAt: Date.now() };
    this.policies.set(category, policy);
    if (scope === 'always') {
      this.persistPolicy(category, policy);
    }
  }

  getPolicy(category: TaskCategory): GatePolicy | null {
    return this.policies.get(category) ?? null;
  }

  getStats(): EscalationStats {
    return { ...this.stats };
  }

  incrementStat(type: 'localDelivered' | 'escalatedAllowed' | 'escalatedDenied'): void {
    this.stats[type]++;
  }

  private requestUserConsent(context: EscalationContext): Promise<GateDecision> {
    return new Promise((resolve) => {
      const channel = 'cloud-gate:consent-response:' + context.taskCategory + ':' + Date.now();
      ipcMain.once(channel, (_event, response: { decision: 'allow' | 'deny'; scope: PolicyScope }) => {
        this.setPolicy(context.taskCategory, response.decision, response.scope);
        if (response.decision === 'allow') {
          this.stats.escalatedAllowed++;
          resolve({ allowed: true, reason: 'user-allow' });
        } else {
          this.stats.escalatedDenied++;
          resolve({ allowed: false, reason: 'user-deny' });
        }
      });
      this.mainWindow!.webContents.send('cloud-gate:request-consent', {
        taskCategory: context.taskCategory,
        confidence: context.confidence,
        promptPreview: context.promptPreview.slice(0, 100),
        targetProvider: context.targetProvider,
        responseChannel: channel,
      });
    });
  }

  private loadPersistedPolicies(): void {
    try {
      const settings = settingsManager.get();
      const persisted = (settings as unknown as Record<string, unknown>)[SETTINGS_KEY] as Record<string, GatePolicy> | undefined;
      if (persisted && typeof persisted === 'object') {
        for (const [category, policy] of Object.entries(persisted)) {
          if (policy && policy.scope === 'always') {
            this.policies.set(category as TaskCategory, policy);
          }
        }
      }
    } catch {
      // First run or no persisted policies
    }
  }

  private persistPolicy(category: TaskCategory, policy: GatePolicy): void {
    try {
      const settings = settingsManager.get();
      const existing = ((settings as unknown as Record<string, unknown>)[SETTINGS_KEY] as Record<string, GatePolicy> | undefined) ?? {};
      const updated = { ...existing, [category]: policy };
      void settingsManager.setSetting(SETTINGS_KEY, updated);
    } catch {
      console.warn('[CloudGate] Failed to persist policy to settings');
    }
  }
}

export const cloudGate = CloudGate.getInstance();

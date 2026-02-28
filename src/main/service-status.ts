/**
 * service-status.ts — Service availability tracking & graceful degradation.
 *
 * Tracks the health of external services (Gemini, Claude, OpenRouter, etc.)
 * and provides the system with degradation awareness.
 *
 * Minimum Viable Agent Friday (offline):
 *   ✓ Memory (local files)
 *   ✓ Settings (local)
 *   ✓ Scheduled tasks (local)
 *   ✓ UI renders
 *   ✗ Semantic search (needs embeddings API)
 *   ✗ Agents (need Claude)
 *   ✗ Voice (needs Gemini)
 *   ✗ Consolidation (needs Claude)
 */

import type { ErrorSource } from './errors';

// ── Types ────────────────────────────────────────────────────────────

export type ServiceState = 'online' | 'degraded' | 'offline';

export interface ServiceInfo {
  source: ErrorSource;
  state: ServiceState;
  lastChecked: number;
  lastOnline: number;
  consecutiveFailures: number;
  lastError: string | null;
}

export type SystemMode = 'full' | 'degraded' | 'offline';

export interface SystemStatus {
  mode: SystemMode;
  services: Record<string, ServiceInfo>;
  offlineCapabilities: string[];
  unavailableCapabilities: string[];
}

// ── Capabilities by dependency ───────────────────────────────────────

const OFFLINE_CAPABILITIES = [
  'Memory browsing (local files)',
  'Settings management',
  'Scheduled task viewing',
  'UI interaction',
  'Conversation history viewing',
  'Integrity monitoring',
];

const SERVICE_CAPABILITIES: Record<string, string[]> = {
  gemini: ['Voice conversations', 'Real-time audio'],
  claude: ['Agent orchestration', 'Memory consolidation', 'Memory extraction'],
  openrouter: ['Embedding generation', 'Semantic search'],
  mcp: ['MCP server tools'],
  soc: ['Desktop automation', 'SOC subprocess'],
};

// ── Service Status Manager ───────────────────────────────────────────

export class ServiceStatusManager {
  private services = new Map<string, ServiceInfo>();

  /** Register a service as being monitored. */
  register(source: ErrorSource): void {
    if (!this.services.has(source)) {
      this.services.set(source, {
        source,
        state: 'online',
        lastChecked: 0,
        lastOnline: 0,
        consecutiveFailures: 0,
        lastError: null,
      });
    }
  }

  /** Mark a service as successfully responding. */
  markOnline(source: ErrorSource): void {
    const svc = this.getOrCreate(source);
    svc.state = 'online';
    svc.lastChecked = Date.now();
    svc.lastOnline = Date.now();
    svc.consecutiveFailures = 0;
    svc.lastError = null;
  }

  /** Mark a service as having failed. */
  markFailed(source: ErrorSource, error: string): void {
    const svc = this.getOrCreate(source);
    svc.consecutiveFailures++;
    svc.lastChecked = Date.now();
    svc.lastError = error;

    // 1 failure = degraded, 3+ = offline
    svc.state = svc.consecutiveFailures >= 3 ? 'offline' : 'degraded';
  }

  /** Get the status of a single service. */
  getServiceState(source: ErrorSource): ServiceState {
    return this.services.get(source)?.state ?? 'online';
  }

  /** Get overall system status with capability analysis. */
  getSystemStatus(): SystemStatus {
    const allServices: Record<string, ServiceInfo> = {};
    let hasOffline = false;
    let hasDegraded = false;
    const unavailable: string[] = [];

    for (const [key, svc] of this.services) {
      allServices[key] = { ...svc };
      if (svc.state === 'offline') {
        hasOffline = true;
        const caps = SERVICE_CAPABILITIES[key];
        if (caps) unavailable.push(...caps);
      } else if (svc.state === 'degraded') {
        hasDegraded = true;
      }
    }

    let mode: SystemMode = 'full';
    if (hasOffline) mode = 'offline';
    else if (hasDegraded) mode = 'degraded';

    return {
      mode,
      services: allServices,
      offlineCapabilities: OFFLINE_CAPABILITIES,
      unavailableCapabilities: [...new Set(unavailable)],
    };
  }

  /** Check if a specific capability is currently available. */
  isCapabilityAvailable(capability: string): boolean {
    const status = this.getSystemStatus();
    return !status.unavailableCapabilities.includes(capability);
  }

  /** Get a user-friendly status summary. */
  getSummary(): string {
    const status = this.getSystemStatus();
    if (status.mode === 'full') return 'All systems operational.';

    const offlineServices = Object.values(status.services)
      .filter(s => s.state === 'offline')
      .map(s => s.source);

    if (offlineServices.length === 0) return 'Some services experiencing delays.';

    return `Offline: ${offlineServices.join(', ')}. ` +
      `Available: ${status.offlineCapabilities.slice(0, 3).join(', ')}, and more.`;
  }

  private getOrCreate(source: ErrorSource): ServiceInfo {
    if (!this.services.has(source)) {
      this.register(source);
    }
    return this.services.get(source)!;
  }
}

export const serviceStatus = new ServiceStatusManager();

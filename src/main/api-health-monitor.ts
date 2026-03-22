/**
 * api-health-monitor.ts — Periodic API health checking with push-based updates.
 * Polls APIs from main process and pushes changes to renderer only when status changes.
 */
import { BrowserWindow } from 'electron';
import { settingsManager } from './settings';

type HealthStatus = 'connected' | 'offline' | 'no-key';

class ApiHealthMonitor {
  private interval: ReturnType<typeof setInterval> | null = null;
  private lastStatus: Record<string, HealthStatus> = {};
  private getMainWindow: (() => BrowserWindow | null) | null = null;

  setWindowGetter(getter: () => BrowserWindow | null): void {
    this.getMainWindow = getter;
  }

  start(intervalMs = 60_000): void {
    if (this.interval) return;
    // Run first check after a short delay (let app settle)
    setTimeout(() => this.check(), 5_000);
    this.interval = setInterval(() => this.check(), intervalMs);
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
  }

  private async check(): Promise<void> {
    const geminiKey = settingsManager.getGeminiApiKey();
    const anthropicKey = settingsManager.getAnthropicApiKey();
    const openrouterKey = settingsManager.getOpenrouterApiKey();
    const elevenlabsKey = settingsManager.getElevenLabsApiKey();

    const results: Record<string, HealthStatus> = {
      gemini: 'no-key',
      claude: 'no-key',
      openrouter: 'no-key',
      elevenlabs: 'no-key',
    };

    const checks: Array<Promise<void>> = [];

    if (geminiKey) {
      checks.push(
        fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(geminiKey)}`, {
          method: 'GET', signal: AbortSignal.timeout(6000),
        }).then((r) => { results.gemini = r.ok ? 'connected' : 'offline'; })
          .catch(() => { results.gemini = 'offline'; }),
      );
    }

    if (anthropicKey) {
      checks.push(
        fetch('https://api.anthropic.com/v1/models', {
          method: 'GET',
          headers: { 'x-api-key': anthropicKey, 'anthropic-version': '2023-06-01' },
          signal: AbortSignal.timeout(6000),
        }).then((r) => { results.claude = (r.ok || r.status === 429) ? 'connected' : 'offline'; })
          .catch(() => { results.claude = 'offline'; }),
      );
    }

    if (openrouterKey) {
      checks.push(
        fetch('https://openrouter.ai/api/v1/auth/key', {
          headers: { Authorization: `Bearer ${openrouterKey}` },
          signal: AbortSignal.timeout(6000),
        }).then((r) => { results.openrouter = r.ok ? 'connected' : 'offline'; })
          .catch(() => { results.openrouter = 'offline'; }),
      );
    }

    if (elevenlabsKey) {
      checks.push(
        fetch('https://api.elevenlabs.io/v1/user', {
          headers: { 'xi-api-key': elevenlabsKey },
          signal: AbortSignal.timeout(6000),
        }).then((r) => { results.elevenlabs = r.ok ? 'connected' : 'offline'; })
          .catch(() => { results.elevenlabs = 'offline'; }),
      );
    }

    await Promise.all(checks);

    // Only push if status actually changed
    const changed = Object.keys(results).some(
      (key) => results[key] !== this.lastStatus[key],
    );

    if (changed) {
      this.lastStatus = { ...results };
      const win = this.getMainWindow?.();
      if (win && !win.isDestroyed()) {
        win.webContents.send('api-health:update', results);
      }
    }
  }
}

export const apiHealthMonitor = new ApiHealthMonitor();

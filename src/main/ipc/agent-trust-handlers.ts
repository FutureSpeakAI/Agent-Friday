/**
 * Agent Trust IPC handlers — expose trust state management to renderer.
 */
import { ipcMain } from 'electron';
import { settingsManager } from '../settings';
import {
  processUserMessage,
  resetSessionCounters,
  buildTrustAwarenessBlock,
  getDefaultTrustState,
  getTrustLabel,
} from '../agent-trust';
import type { AgentTrustState } from '../settings';

export function registerAgentTrustHandlers(): void {
  // Get current trust state
  ipcMain.handle('agent-trust:get-state', (): AgentTrustState => {
    return settingsManager.get().agentTrustState || getDefaultTrustState();
  });

  // Process a user message and update trust signals
  ipcMain.handle('agent-trust:process-message', async (_event, userMessage: string): Promise<AgentTrustState> => {
    const current = settingsManager.get().agentTrustState;
    const updated = processUserMessage(current, userMessage);
    await settingsManager.setSetting('agentTrustState', updated);
    return updated;
  });

  // Reset session counters (called at session start)
  ipcMain.handle('agent-trust:reset-session', async (): Promise<AgentTrustState> => {
    const current = settingsManager.get().agentTrustState;
    const reset = resetSessionCounters(current);
    await settingsManager.setSetting('agentTrustState', reset);
    return reset;
  });

  // Get the trust awareness prompt block (for manual injection)
  ipcMain.handle('agent-trust:get-prompt-block', (): string => {
    const state = settingsManager.get().agentTrustState;
    return buildTrustAwarenessBlock(state);
  });

  // Get human-readable trust label
  ipcMain.handle('agent-trust:get-label', (): string => {
    const state = settingsManager.get().agentTrustState;
    return getTrustLabel(state);
  });

  // Manually boost trust (e.g., user completed a successful task)
  ipcMain.handle('agent-trust:boost', async (_event, amount: number): Promise<AgentTrustState> => {
    const current = settingsManager.get().agentTrustState || getDefaultTrustState();
    const boosted: AgentTrustState = {
      ...current,
      score: Math.min(1, current.score + Math.abs(amount)),
      successStreak: current.successStreak + 1,
      recoveryMode: (current.score + Math.abs(amount)) < 0.3,
    };
    await settingsManager.setSetting('agentTrustState', boosted);
    return boosted;
  });
}

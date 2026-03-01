/**
 * Delegation Engine IPC handlers — expose recursive agent delegation
 * to the renderer process for AgentDashboard tree display and control.
 *
 * Track XI, Phase 3: The Delegation Engine.
 *
 * cLaw Gate: All delegation requests route through the delegation engine's
 * built-in trust-tier inheritance and depth limits. No raw agent spawning
 * bypasses the delegation boundary. Delegation trees are serialized
 * (no live process handles cross IPC).
 */
import { ipcMain, BrowserWindow } from 'electron';
import { delegationEngine } from '../agents/delegation-engine';
import type { TrustTier } from '../agents/delegation-engine';

/** Reference to main window for event forwarding */
let mainWindowRef: BrowserWindow | null = null;

export function registerDelegationEngineHandlers(mainWindow?: BrowserWindow): void {
  if (mainWindow) mainWindowRef = mainWindow;

  // Subscribe to delegation updates and forward to renderer
  delegationEngine.onUpdate((update) => {
    if (mainWindowRef && !mainWindowRef.isDestroyed()) {
      mainWindowRef.webContents.send('delegation:update', update);
    }
  });

  // ── Register a root delegation node ──────────────────────────────
  ipcMain.handle(
    'delegation:register-root',
    (_event, taskId: unknown, agentType: unknown, description: unknown, trustTier?: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('delegation:register-root requires a string taskId');
      }
      if (!agentType || typeof agentType !== 'string') {
        throw new Error('delegation:register-root requires a string agentType');
      }
      if (!description || typeof description !== 'string') {
        throw new Error('delegation:register-root requires a string description');
      }

      const validTiers: TrustTier[] = ['local', 'owner-dm', 'approved-dm', 'group', 'public'];
      const tier = (typeof trustTier === 'string' && validTiers.includes(trustTier as TrustTier))
        ? trustTier as TrustTier
        : 'local';

      return delegationEngine.registerRoot(taskId, agentType, description, tier);
    },
  );

  // ── Spawn a sub-agent ────────────────────────────────────────────
  ipcMain.handle(
    'delegation:spawn-sub-agent',
    async (_event, payload: unknown) => {
      if (!payload || typeof payload !== 'object') {
        throw new Error('delegation:spawn-sub-agent requires a payload object');
      }

      const p = payload as Record<string, unknown>;

      if (!p.agentType || typeof p.agentType !== 'string') {
        throw new Error('delegation:spawn-sub-agent requires a string agentType');
      }
      if (!p.description || typeof p.description !== 'string') {
        throw new Error('delegation:spawn-sub-agent requires a string description');
      }
      if (!p.parentTaskId || typeof p.parentTaskId !== 'string') {
        throw new Error('delegation:spawn-sub-agent requires a string parentTaskId');
      }

      return delegationEngine.spawnSubAgent({
        agentType: p.agentType as string,
        description: p.description as string,
        input: (p.input && typeof p.input === 'object') ? p.input as Record<string, unknown> : {},
        parentTaskId: p.parentTaskId as string,
        depthLimit: typeof p.depthLimit === 'number' ? p.depthLimit : undefined,
        trustTier: typeof p.trustTier === 'string' ? p.trustTier as TrustTier : undefined,
        parentContext: typeof p.parentContext === 'string' ? p.parentContext : undefined,
      });
    },
  );

  // ── Report task completion ───────────────────────────────────────
  ipcMain.handle(
    'delegation:report-completion',
    (_event, taskId: unknown, result: unknown, error: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('delegation:report-completion requires a string taskId');
      }
      delegationEngine.reportCompletion(
        taskId,
        typeof result === 'string' ? result : null,
        typeof error === 'string' ? error : null,
      );
    },
  );

  // ── Collect child results ────────────────────────────────────────
  ipcMain.handle(
    'delegation:collect-results',
    (_event, parentTaskId: unknown) => {
      if (!parentTaskId || typeof parentTaskId !== 'string') {
        throw new Error('delegation:collect-results requires a string parentTaskId');
      }
      return delegationEngine.collectChildResults(parentTaskId);
    },
  );

  // ── Halt a delegation tree ───────────────────────────────────────
  ipcMain.handle(
    'delegation:halt-tree',
    async (_event, taskId: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('delegation:halt-tree requires a string taskId');
      }
      return delegationEngine.haltTree(taskId);
    },
  );

  // ── Halt all delegation trees ────────────────────────────────────
  ipcMain.handle('delegation:halt-all', async () => {
    return delegationEngine.haltAll();
  });

  // ── Get a delegation tree ────────────────────────────────────────
  ipcMain.handle(
    'delegation:get-tree',
    (_event, rootId: unknown) => {
      if (!rootId || typeof rootId !== 'string') {
        throw new Error('delegation:get-tree requires a string rootId');
      }
      return delegationEngine.getTree(rootId);
    },
  );

  // ── Get a specific node ──────────────────────────────────────────
  ipcMain.handle(
    'delegation:get-node',
    (_event, taskId: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('delegation:get-node requires a string taskId');
      }
      return delegationEngine.getNode(taskId);
    },
  );

  // ── Get all active trees ─────────────────────────────────────────
  ipcMain.handle('delegation:get-active-trees', () => {
    return delegationEngine.getActiveTrees();
  });

  // ── Get all trees ────────────────────────────────────────────────
  ipcMain.handle('delegation:get-all-trees', () => {
    return delegationEngine.getAllTrees();
  });

  // ── Get ancestry chain ───────────────────────────────────────────
  ipcMain.handle(
    'delegation:get-ancestry',
    (_event, taskId: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('delegation:get-ancestry requires a string taskId');
      }
      return delegationEngine.getAncestry(taskId);
    },
  );

  // ── Get engine statistics ────────────────────────────────────────
  ipcMain.handle('delegation:get-stats', () => {
    return delegationEngine.getStats();
  });

  // ── Get engine config ────────────────────────────────────────────
  ipcMain.handle('delegation:get-config', () => {
    return delegationEngine.getConfig();
  });

  // ── Update engine config ─────────────────────────────────────────
  ipcMain.handle(
    'delegation:update-config',
    (_event, updates: unknown) => {
      if (!updates || typeof updates !== 'object') {
        throw new Error('delegation:update-config requires an object');
      }
      delegationEngine.updateConfig(updates as Record<string, unknown>);
      return delegationEngine.getConfig();
    },
  );

  // ── Cleanup old trees ────────────────────────────────────────────
  ipcMain.handle(
    'delegation:cleanup',
    (_event, maxAgeMs?: unknown) => {
      const age = typeof maxAgeMs === 'number' ? maxAgeMs : undefined;
      return delegationEngine.cleanup(age);
    },
  );
}

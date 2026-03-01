/**
 * Container Engine IPC handlers — expose container execution
 * to the renderer process for AgentDashboard display and tool invocation.
 *
 * Track XI, Phase 2: The Container Engine.
 *
 * cLaw Gate: All execution requests route through the container engine's
 * built-in consent gate. No raw Docker access is exposed. Container
 * results are serialized (no live process handles cross IPC).
 */
import { ipcMain } from 'electron';
import { containerEngine } from '../container-engine';
import type {
  ContainerTrigger,
  NetworkPolicy,
  ResourceLimits,
} from '../container-engine';

export function registerContainerEngineHandlers(): void {
  // ── Execute code in a container ───────────────────────────────────
  ipcMain.handle(
    'container:execute',
    async (_event, payload: unknown) => {
      if (!payload || typeof payload !== 'object') {
        throw new Error('container:execute requires a payload object');
      }

      const p = payload as Record<string, unknown>;

      if (!p.code || typeof p.code !== 'string') {
        throw new Error('container:execute requires a string "code" field');
      }
      if (!p.language || typeof p.language !== 'string') {
        throw new Error('container:execute requires a string "language" field');
      }

      const validLanguages = ['python', 'bash', 'node'];
      if (!validLanguages.includes(p.language as string)) {
        throw new Error(`container:execute language must be one of: ${validLanguages.join(', ')}`);
      }

      const validTriggers: ContainerTrigger[] = [
        'user-explicit', 'agent-subtask', 'scheduled-task', 'intelligence', 'untrusted-code',
      ];
      const trigger = (typeof p.trigger === 'string' && validTriggers.includes(p.trigger as ContainerTrigger))
        ? p.trigger as ContainerTrigger
        : 'user-explicit';

      const validNetworks: NetworkPolicy[] = ['none', 'localhost', 'dns-only', 'restricted'];
      const network = (typeof p.network === 'string' && validNetworks.includes(p.network as NetworkPolicy))
        ? p.network as NetworkPolicy
        : undefined;

      return containerEngine.executeInContainer({
        code: p.code as string,
        language: p.language as 'python' | 'bash' | 'node',
        trigger,
        description: typeof p.description === 'string' ? p.description : `Execute ${p.language} code`,
        packages: Array.isArray(p.packages) ? p.packages.filter((pkg): pkg is string => typeof pkg === 'string') : undefined,
        sourcePath: typeof p.sourcePath === 'string' ? p.sourcePath : undefined,
        limits: p.limits && typeof p.limits === 'object' ? p.limits as Partial<ResourceLimits> : undefined,
        network,
        env: p.env && typeof p.env === 'object' ? p.env as Record<string, string> : undefined,
      });
    },
  );

  // ── Cancel a running container ────────────────────────────────────
  ipcMain.handle(
    'container:cancel',
    async (_event, taskId: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('container:cancel requires a string taskId');
      }
      return containerEngine.cancelContainer(taskId);
    },
  );

  // ── Get container status ──────────────────────────────────────────
  ipcMain.handle('container:status', () => {
    return containerEngine.getStatus();
  });

  // ── Get all containers (for AgentDashboard) ───────────────────────
  ipcMain.handle('container:list', () => {
    return containerEngine.getAllContainers();
  });

  // ── Get a specific container ──────────────────────────────────────
  ipcMain.handle(
    'container:get',
    (_event, taskId: unknown) => {
      if (!taskId || typeof taskId !== 'string') {
        throw new Error('container:get requires a string taskId');
      }
      const container = containerEngine.getContainer(taskId);
      if (!container) return null;

      // Serialize for IPC (strip non-serializable fields)
      return {
        taskId: container.taskId,
        state: container.state,
        trigger: container.trigger,
        description: container.description,
        createdAt: container.createdAt,
        completedAt: container.completedAt,
        resources: container.lastResources,
        stdout: container.stdout,
        stderr: container.stderr,
        result: container.result,
        error: container.error,
        durationMs: container.completedAt
          ? container.completedAt - container.createdAt
          : Date.now() - container.createdAt,
      };
    },
  );

  // ── Check if Docker is available ──────────────────────────────────
  ipcMain.handle('container:available', () => {
    return containerEngine.isAvailable();
  });

  // ── Get active container count ────────────────────────────────────
  ipcMain.handle('container:active-count', () => {
    return containerEngine.getActiveContainers().length;
  });
}

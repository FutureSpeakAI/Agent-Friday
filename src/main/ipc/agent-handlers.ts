/**
 * Agent IPC handlers — background agents, clipboard intelligence, project awareness, document ingestion.
 */
import { ipcMain } from 'electron';
import { agentRunner } from '../agents/agent-runner';
import { clipboardIntelligence } from '../clipboard-intelligence';
import { projectAwareness } from '../project-awareness';
import { documentIngestion } from '../document-ingestion';

export function registerAgentHandlers(): void {
  // ── Background agents ───────────────────────────────────────────────
  ipcMain.handle(
    'agents:spawn',
    (_event, agentType: string, description: string, input: Record<string, unknown>) => {
      // Validate inputs
      if (!agentType || typeof agentType !== 'string') {
        throw new Error('agents:spawn requires a string agentType');
      }
      if (!description || typeof description !== 'string') {
        throw new Error('agents:spawn requires a string description');
      }
      if (description.length > 10000) {
        throw new Error('agents:spawn description too long (max 10000 chars)');
      }
      return agentRunner.spawn(agentType, description, input || {});
    },
  );

  ipcMain.handle('agents:list', (_event, status?: string) => agentRunner.list(status as any));
  ipcMain.handle('agents:get', (_event, taskId: string) => agentRunner.get(taskId));
  ipcMain.handle('agents:cancel', (_event, taskId: string) => agentRunner.cancel(taskId));
  ipcMain.handle('agents:types', () => agentRunner.getAgentTypes());

  // ── Clipboard intelligence ──────────────────────────────────────────
  ipcMain.handle('clipboard:get-recent', (_event, count?: number) => {
    return clipboardIntelligence.getRecent(count);
  });

  ipcMain.handle('clipboard:get-current', () => clipboardIntelligence.getCurrent());

  // ── Project awareness ───────────────────────────────────────────────
  ipcMain.handle('project:watch', async (_event, rootPath: string) => {
    return projectAwareness.watchProject(rootPath);
  });

  ipcMain.handle('project:list', () => projectAwareness.getProjects());

  ipcMain.handle('project:get', (_event, rootPath: string) => {
    return projectAwareness.getProject(rootPath);
  });

  // ── Document ingestion ──────────────────────────────────────────────
  ipcMain.handle('documents:pick-and-ingest', async () => documentIngestion.pickAndIngest());

  ipcMain.handle('documents:ingest-file', async (_event, filePath: string) => {
    return documentIngestion.ingestFile(filePath);
  });

  ipcMain.handle('documents:list', () => documentIngestion.getAll());

  ipcMain.handle('documents:get', (_event, id: string) => documentIngestion.getById(id));

  ipcMain.handle('documents:search', (_event, query: string) => documentIngestion.search(query));
}

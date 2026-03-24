/**
 * Agent IPC handlers — background agents, clipboard intelligence, project awareness, document ingestion.
 */
import { ipcMain } from 'electron';
import { agentRunner } from '../agents/agent-runner';
import { clipboardIntelligence } from '../clipboard-intelligence';
import { projectAwareness } from '../project-awareness';
import { documentIngestion } from '../document-ingestion';
import { assertString, assertSafePath, assertObject } from './validate';

export function registerAgentHandlers(): void {
  // ── Background agents ───────────────────────────────────────────────
  // Crypto Sprint 8: Replace ad-hoc checks with shared validators + validate input object.
  ipcMain.handle(
    'agents:spawn',
    (_event, agentType: unknown, description: unknown, input: unknown) => {
      assertString(agentType, 'agents:spawn agentType', 256);
      assertString(description, 'agents:spawn description', 10_000);
      if (input !== undefined && input !== null) {
        assertObject(input, 'agents:spawn input');
      }
      return agentRunner.spawn(agentType as string, description as string, (input || {}) as Record<string, unknown>);
    },
  );

  ipcMain.handle('agents:list', (_event, status?: unknown) => {
    if (status !== undefined && status !== null) {
      assertString(status, 'agents:list status', 50);
    }
    return agentRunner.list(status as any);
  });
  ipcMain.handle('agents:get', (_event, taskId: unknown) => {
    assertString(taskId, 'agents:get taskId', 256);
    return agentRunner.get(taskId as string);
  });
  ipcMain.handle('agents:cancel', (_event, taskId: unknown) => {
    assertString(taskId, 'agents:cancel taskId', 256);
    return agentRunner.cancel(taskId as string);
  });
  ipcMain.handle('agents:types', () => agentRunner.getAgentTypes());

  // ── Clipboard intelligence ──────────────────────────────────────────
  ipcMain.handle('clipboard:get-recent', (_event, count?: unknown) => {
    if (count !== undefined && count !== null) {
      if (typeof count !== 'number' || !Number.isFinite(count) || count < 1 || count > 1000) {
        throw new Error('clipboard:get-recent count must be a number between 1 and 1000');
      }
    }
    return clipboardIntelligence.getRecent(count as number | undefined);
  });

  ipcMain.handle('clipboard:get-current', () => clipboardIntelligence.getCurrent());

  // ── Project awareness ───────────────────────────────────────────────

  // Crypto Sprint 8 (HIGH): Validate project paths to prevent traversal / UNC attacks.
  ipcMain.handle('project:watch', async (_event, rootPath: unknown) => {
    assertSafePath(rootPath, 'project:watch rootPath');
    return projectAwareness.watchProject(rootPath as string);
  });

  ipcMain.handle('project:list', () => projectAwareness.getProjects());

  // Crypto Sprint 8 (HIGH): Validate project path.
  ipcMain.handle('project:get', (_event, rootPath: unknown) => {
    assertSafePath(rootPath, 'project:get rootPath');
    return projectAwareness.getProject(rootPath as string);
  });

  // ── Document ingestion ──────────────────────────────────────────────
  ipcMain.handle('documents:pick-and-ingest', async () => documentIngestion.pickAndIngest());

  // Crypto Sprint 8 (CRITICAL): Validate file path — no downstream validation exists.
  // Without this, renderer could pass '../../../etc/passwd' or '\\\\attacker\\share' paths.
  ipcMain.handle('documents:ingest-file', async (_event, filePath: unknown) => {
    assertSafePath(filePath, 'documents:ingest-file filePath');
    return documentIngestion.ingestFile(filePath as string);
  });

  ipcMain.handle('documents:list', () => documentIngestion.getAll());

  // Crypto Sprint 8 (MEDIUM): Validate id is a string.
  ipcMain.handle('documents:get', (_event, id: unknown) => {
    assertString(id, 'documents:get id', 256);
    return documentIngestion.getById(id as string);
  });

  // Crypto Sprint 8 (MEDIUM): Validate search query.
  ipcMain.handle('documents:search', (_event, query: unknown) => {
    assertString(query, 'documents:search query', 10_000);
    return documentIngestion.search(query as string);
  });
}

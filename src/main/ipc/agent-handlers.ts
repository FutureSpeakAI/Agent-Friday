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

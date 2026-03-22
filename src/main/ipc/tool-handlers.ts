/**
 * Tool IPC handlers — desktop tools, browser, screen capture, scheduler, ambient, sentiment, predictor, SOC.
 */
import { ipcMain, BrowserWindow } from 'electron';
import { DESKTOP_TOOL_DECLARATIONS, callDesktopTool } from '../desktop-tools';
import { BROWSER_TOOL_DECLARATIONS, executeBrowserTool } from '../browser';
import { screenCapture } from '../screen-capture';
import { taskScheduler, SCHEDULER_TOOL_DECLARATIONS } from '../scheduler';
import { ambientEngine } from '../ambient';
import { sentimentEngine } from '../sentiment';
import { predictor } from '../predictor';
import {
  pythonBridge,
  buildSocToolDeclarations,
  operateComputer,
  takeScreenshot,
  clickScreen,
  typeText,
  pressKeys,
  browserTask,
  checkDependencies,
} from '../soc-bridge';
import { gitLoader, buildGitLoaderToolDeclarations } from '../git-loader';
import {
  assertToolCallArgs,
  assertSafeUrl,
  assertString,
  assertNumber,
  assertObject,
  assertStringArray,
} from './validate';

export interface ToolHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerToolHandlers(deps: ToolHandlerDeps): void {
  // ── Desktop tools ───────────────────────────────────────────────────
  ipcMain.handle('desktop:list-tools', () => DESKTOP_TOOL_DECLARATIONS);

  // Crypto Sprint 8 (CRITICAL): Validate tool name and args before dispatching.
  ipcMain.handle(
    'desktop:call-tool',
    async (_event, toolName: unknown, args: unknown) => {
      const { validatedName, validatedArgs } = assertToolCallArgs(toolName, args, 'desktop:call-tool');
      return callDesktopTool(validatedName, validatedArgs);
    },
  );

  ipcMain.handle('desktop:focus-window', async (_event, target: string) => {
    return callDesktopTool('focus_window', { target });
  });

  ipcMain.handle('desktop:confirm-response', (_event, id: string, approved: boolean) => {
    const { handleConfirmationResponse } = require('../desktop-tools');
    handleConfirmationResponse(id, approved);
    // cLaw Security Fix: Also route to centralized consent-gate for side-effect consent requests
    const { handleConsentResponse } = require('../consent-gate');
    handleConsentResponse(id, approved);
  });

  // ── Browser tools ───────────────────────────────────────────────────
  ipcMain.handle('browser:list-tools', () => BROWSER_TOOL_DECLARATIONS);

  // Crypto Sprint 8 (CRITICAL): Validate tool name and args before dispatching.
  ipcMain.handle(
    'browser:call-tool',
    async (_event, toolName: unknown, args: unknown) => {
      const { validatedName, validatedArgs } = assertToolCallArgs(toolName, args, 'browser:call-tool');
      return executeBrowserTool(validatedName, validatedArgs);
    },
  );

  // ── Screen capture ──────────────────────────────────────────────────
  ipcMain.handle('screen-capture:start', () => {
    const win = deps.getMainWindow();
    if (win) screenCapture.start(win);
  });

  ipcMain.handle('screen-capture:stop', () => screenCapture.stop());

  // ── Scheduler ───────────────────────────────────────────────────────
  ipcMain.handle('scheduler:list-tools', () => SCHEDULER_TOOL_DECLARATIONS);

  // Crypto Sprint 8 (HIGH): Validate params is a plain object before unsafe `as any` cast.
  ipcMain.handle(
    'scheduler:create-task',
    async (_event, params: unknown) => {
      assertObject(params, 'scheduler:create-task params');
      return taskScheduler.createTask(params as any);
    },
  );

  ipcMain.handle('scheduler:list-tasks', () => taskScheduler.listTasks());

  ipcMain.handle('scheduler:delete-task', async (_event, id: string) => {
    return taskScheduler.deleteTask(id);
  });

  // ── Ambient context ─────────────────────────────────────────────────
  ipcMain.handle('ambient:get-state', () => ambientEngine.getState());
  ipcMain.handle('ambient:get-context-string', () => ambientEngine.getContextString());

  // ── Sentiment ───────────────────────────────────────────────────────
  ipcMain.handle('sentiment:analyse', (_event, text: string) => sentimentEngine.analyse(text));
  ipcMain.handle('sentiment:get-state', () => sentimentEngine.getState());
  ipcMain.handle('sentiment:get-mood-log', () => sentimentEngine.getMoodLog());

  // Push mood changes to renderer — eliminates 5s polling from MoodContext
  const sendToRenderer = (channel: string, ...args: unknown[]) => {
    const win = deps.getMainWindow();
    if (win && !win.isDestroyed()) {
      win.webContents.send(channel, ...args);
    }
  };

  sentimentEngine.on('mood-change', (state) => {
    sendToRenderer('sentiment:mood-change', state);
  });

  // ── Predictor ───────────────────────────────────────────────────────
  ipcMain.handle('predictor:record-interaction', () => predictor.recordInteraction());

  // ── Self-Operating Computer + Browser-Use ─────────────────────────
  ipcMain.handle('soc:list-tools', () => buildSocToolDeclarations());

  // Crypto Sprint 8 (HIGH): Replace unsafe `as` casts with runtime type checks + cap max_steps.
  ipcMain.handle('soc:call-tool', async (_event, toolName: unknown, args: unknown) => {
    try {
      const { validatedName, validatedArgs } = assertToolCallArgs(toolName, args, 'soc:call-tool');
      const MAX_STEPS = 100; // Hard cap to prevent runaway loops

      switch (validatedName) {
        case 'operate_computer': {
          assertString(validatedArgs.objective, 'soc:operate_computer objective', 50_000);
          const model = typeof validatedArgs.model === 'string' ? validatedArgs.model : 'gpt-4-with-ocr';
          const maxSteps = typeof validatedArgs.max_steps === 'number' && Number.isFinite(validatedArgs.max_steps)
            ? Math.min(Math.max(1, validatedArgs.max_steps), MAX_STEPS) : 10;
          return await operateComputer(validatedArgs.objective as string, model, maxSteps);
        }
        case 'take_screenshot':
          return await takeScreenshot();
        case 'click_screen': {
          assertNumber(validatedArgs.x, 'soc:click_screen x', 0, 100_000);
          assertNumber(validatedArgs.y, 'soc:click_screen y', 0, 100_000);
          return await clickScreen(validatedArgs.x as number, validatedArgs.y as number);
        }
        case 'type_text': {
          assertString(validatedArgs.text, 'soc:type_text text', 50_000);
          return await typeText(validatedArgs.text as string);
        }
        case 'press_keys': {
          assertStringArray(validatedArgs.keys, 'soc:press_keys keys', 50, 100);
          return await pressKeys(validatedArgs.keys as string[]);
        }
        case 'browser_task': {
          assertString(validatedArgs.task, 'soc:browser_task task', 50_000);
          const model = typeof validatedArgs.model === 'string' ? validatedArgs.model : 'gpt-4o';
          const maxSteps = typeof validatedArgs.max_steps === 'number' && Number.isFinite(validatedArgs.max_steps)
            ? Math.min(Math.max(1, validatedArgs.max_steps), MAX_STEPS) : 20;
          const headless = typeof validatedArgs.headless === 'boolean' ? validatedArgs.headless : false;
          return await browserTask(validatedArgs.task as string, model, maxSteps, headless);
        }
        default:
          return { error: `Unknown SOC tool: ${validatedName}` };
      }
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('soc:check-deps', async () => {
    try {
      return await checkDependencies();
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('soc:start-bridge', async () => {
    try {
      await pythonBridge.start();
      return { status: 'ok' };
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('soc:stop-bridge', async () => {
    try {
      await pythonBridge.stop();
      return { status: 'ok' };
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('soc:bridge-status', () => ({
    running: pythonBridge.isRunning(),
  }));

  // ── GitLoader ─────────────────────────────────────────────────────
  ipcMain.handle('git:list-tools', () => buildGitLoaderToolDeclarations());

  // Crypto Sprint 8 (CRITICAL): Validate URL scheme to prevent file://, ssh://, data:// protocols.
  // file:// would read arbitrary local files; ssh:// could trigger credential prompts or key theft.
  ipcMain.handle('git:load', async (_event, repoUrl: unknown, options?: Record<string, unknown>) => {
    try {
      assertSafeUrl(repoUrl, 'git:load repoUrl', 'git');
      const repo = await gitLoader.load(repoUrl as string, options || {});
      return {
        id: repo.id,
        name: repo.name,
        owner: repo.owner,
        branch: repo.branch,
        description: repo.description,
        files: repo.files.length,
        totalSize: repo.totalSize,
        loadedAt: repo.loadedAt,
      };
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('git:get-tree', (_event, repoId: string) => {
    try {
      return gitLoader.getTree(repoId);
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('git:get-file', (_event, repoId: string, filePath: string) => {
    try {
      const file = gitLoader.getFile(repoId, filePath);
      if (!file) return { error: `File not found: ${filePath}` };
      return file;
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('git:search', (_event, repoId: string, query: string, options?: Record<string, unknown>) => {
    try {
      return gitLoader.search(repoId, query, options || {});
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('git:get-readme', (_event, repoId: string) => {
    try {
      return gitLoader.getReadme(repoId);
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('git:get-summary', (_event, repoId: string) => {
    try {
      return gitLoader.getSummary(repoId);
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  ipcMain.handle('git:list-loaded', () => gitLoader.listLoaded());

  ipcMain.handle('git:unload', async (_event, repoId: string) => {
    try {
      return await gitLoader.unload(repoId);
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });

  // Crypto Sprint 8 (CRITICAL): Validate tool name, args, and URL schemes.
  ipcMain.handle('git:call-tool', async (_event, toolName: unknown, args: unknown) => {
    try {
      const { validatedName, validatedArgs } = assertToolCallArgs(toolName, args, 'git:call-tool');
      switch (validatedName) {
        case 'git_load_repo': {
          assertSafeUrl(validatedArgs.repo_url, 'git:call-tool repo_url', 'git');
          return await gitLoader.load(validatedArgs.repo_url as string, {
            branch: typeof validatedArgs.branch === 'string' ? validatedArgs.branch : undefined,
            includePatterns: Array.isArray(validatedArgs.include_patterns) ? validatedArgs.include_patterns as string[] : undefined,
            excludePatterns: Array.isArray(validatedArgs.exclude_patterns) ? validatedArgs.exclude_patterns as string[] : undefined,
          });
        }
        case 'git_get_tree':
          return gitLoader.getTree(validatedArgs.repo_id as string);
        case 'git_get_file': {
          const file = gitLoader.getFile(validatedArgs.repo_id as string, validatedArgs.file_path as string);
          return file || { error: `File not found: ${validatedArgs.file_path}` };
        }
        case 'git_search':
          return gitLoader.search(validatedArgs.repo_id as string, validatedArgs.query as string, {
            filePattern: typeof validatedArgs.file_pattern === 'string' ? validatedArgs.file_pattern : undefined,
            maxResults: typeof validatedArgs.max_results === 'number' ? validatedArgs.max_results : undefined,
          });
        case 'git_get_readme':
          return gitLoader.getReadme(validatedArgs.repo_id as string);
        case 'git_get_summary':
          return gitLoader.getSummary(validatedArgs.repo_id as string);
        case 'git_list_loaded':
          return gitLoader.listLoaded();
        case 'git_unload_repo':
          return await gitLoader.unload(validatedArgs.repo_id as string);
        default:
          return { error: `Unknown git tool: ${validatedName}` };
      }
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });
}

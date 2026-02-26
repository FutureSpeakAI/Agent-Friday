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

export interface ToolHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerToolHandlers(deps: ToolHandlerDeps): void {
  // ── Desktop tools ───────────────────────────────────────────────────
  ipcMain.handle('desktop:list-tools', () => DESKTOP_TOOL_DECLARATIONS);

  ipcMain.handle(
    'desktop:call-tool',
    async (_event, toolName: string, args: Record<string, unknown>) => {
      return callDesktopTool(toolName, args);
    },
  );

  ipcMain.handle('desktop:focus-window', async (_event, target: string) => {
    return callDesktopTool('focus_window', { target });
  });

  ipcMain.handle('desktop:confirm-response', (_event, id: string, approved: boolean) => {
    const { handleConfirmationResponse } = require('../desktop-tools');
    handleConfirmationResponse(id, approved);
  });

  // ── Browser tools ───────────────────────────────────────────────────
  ipcMain.handle('browser:list-tools', () => BROWSER_TOOL_DECLARATIONS);

  ipcMain.handle(
    'browser:call-tool',
    async (_event, toolName: string, args: Record<string, unknown>) => {
      return executeBrowserTool(toolName, args);
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

  ipcMain.handle(
    'scheduler:create-task',
    async (_event, params: Record<string, unknown>) => {
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

  // ── Predictor ───────────────────────────────────────────────────────
  ipcMain.handle('predictor:record-interaction', () => predictor.recordInteraction());

  // ── Self-Operating Computer + Browser-Use ─────────────────────────
  ipcMain.handle('soc:list-tools', () => buildSocToolDeclarations());

  ipcMain.handle('soc:call-tool', async (_event, toolName: string, args: Record<string, unknown>) => {
    try {
      switch (toolName) {
        case 'operate_computer':
          return await operateComputer(
            args.objective as string,
            (args.model as string) || 'gpt-4-with-ocr',
            (args.max_steps as number) || 10,
          );
        case 'take_screenshot':
          return await takeScreenshot();
        case 'click_screen':
          return await clickScreen(args.x as number, args.y as number);
        case 'type_text':
          return await typeText(args.text as string);
        case 'press_keys':
          return await pressKeys(args.keys as string[]);
        case 'browser_task':
          return await browserTask(
            args.task as string,
            (args.model as string) || 'gpt-4o',
            (args.max_steps as number) || 20,
            (args.headless as boolean) || false,
          );
        default:
          return { error: `Unknown SOC tool: ${toolName}` };
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

  ipcMain.handle('git:load', async (_event, repoUrl: string, options?: Record<string, unknown>) => {
    try {
      const repo = await gitLoader.load(repoUrl, options || {});
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

  ipcMain.handle('git:call-tool', async (_event, toolName: string, args: Record<string, unknown>) => {
    try {
      switch (toolName) {
        case 'git_load_repo':
          return await gitLoader.load(args.repo_url as string, {
            branch: args.branch as string,
            includePatterns: args.include_patterns as string[],
            excludePatterns: args.exclude_patterns as string[],
          });
        case 'git_get_tree':
          return gitLoader.getTree(args.repo_id as string);
        case 'git_get_file': {
          const file = gitLoader.getFile(args.repo_id as string, args.file_path as string);
          return file || { error: `File not found: ${args.file_path}` };
        }
        case 'git_search':
          return gitLoader.search(args.repo_id as string, args.query as string, {
            filePattern: args.file_pattern as string,
            maxResults: args.max_results as number,
          });
        case 'git_get_readme':
          return gitLoader.getReadme(args.repo_id as string);
        case 'git_get_summary':
          return gitLoader.getSummary(args.repo_id as string);
        case 'git_list_loaded':
          return gitLoader.listLoaded();
        case 'git_unload_repo':
          return await gitLoader.unload(args.repo_id as string);
        default:
          return { error: `Unknown git tool: ${toolName}` };
      }
    } catch (err) {
      return { error: err instanceof Error ? err.message : String(err) };
    }
  });
}

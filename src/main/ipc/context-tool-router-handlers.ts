/**
 * IPC handlers for Context Tool Router — Track III Phase 3.
 * Exposes read-only tool routing queries to the renderer.
 *
 * cLaw Gate: All channels are read-only queries. No tool execution,
 * no persistence, no data mutation.
 */

import { ipcMain } from 'electron';
import { contextToolRouter } from '../context-tool-router';

const VALID_CATEGORIES = new Set([
  'code', 'communication', 'research', 'system', 'meeting',
  'memory', 'project', 'automation', 'creative', 'trust', 'general',
]);

export function registerContextToolRouterHandlers(): void {
  // ── Tool Suggestions ─────────────────────────────────────────────
  ipcMain.handle('tool-router:suggestions', () => {
    return contextToolRouter.route();
  });

  // ── Active Category ──────────────────────────────────────────────
  ipcMain.handle('tool-router:active-category', () => {
    return contextToolRouter.getActiveCategory();
  });

  // ── Category Scores ──────────────────────────────────────────────
  ipcMain.handle('tool-router:category-scores', () => {
    return contextToolRouter.getCategoryScores();
  });

  // ── Snapshot ─────────────────────────────────────────────────────
  ipcMain.handle('tool-router:snapshot', () => {
    return contextToolRouter.getSnapshot();
  });

  // ── Context String (Full) ────────────────────────────────────────
  ipcMain.handle('tool-router:context-string', () => {
    return contextToolRouter.getContextString();
  });

  // ── Prompt Context (Budget) ──────────────────────────────────────
  ipcMain.handle('tool-router:prompt-context', () => {
    return contextToolRouter.getPromptContext();
  });

  // ── Status ───────────────────────────────────────────────────────
  ipcMain.handle('tool-router:status', () => {
    return contextToolRouter.getStatus();
  });

  // ── Register Dynamic Tools ───────────────────────────────────────
  ipcMain.handle(
    'tool-router:register-tools',
    (_event, tools: Array<{ name: string; description?: string }>) => {
      if (!Array.isArray(tools)) {
        throw new Error('tool-router:register-tools requires an array of tool declarations');
      }
      // Validate & cap
      const sanitized = tools.slice(0, 200).filter(
        t => typeof t.name === 'string' && t.name.length > 0 && t.name.length < 100,
      );
      contextToolRouter.registerToolsFromDeclarations(sanitized);
      return { registered: sanitized.length };
    },
  );

  // ── Unregister Tool ──────────────────────────────────────────────
  ipcMain.handle('tool-router:unregister-tool', (_event, name: string) => {
    if (typeof name !== 'string' || !name) {
      throw new Error('tool-router:unregister-tool requires a string name');
    }
    contextToolRouter.unregisterTool(name);
    return { unregistered: name };
  });

  // ── Config ───────────────────────────────────────────────────────
  ipcMain.handle('tool-router:config', () => {
    return contextToolRouter.getConfig();
  });
}

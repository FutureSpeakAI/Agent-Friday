/**
 * Sprint 7: IPC handlers for the vision pipeline.
 *
 * Exposes VisionProvider, ScreenContext, and ImageUnderstanding
 * to the renderer via eve.vision namespace.
 *
 * Image data (Buffer) is serialized as base64 strings over IPC.
 */

import { ipcMain, type BrowserWindow } from 'electron';
import { VisionProvider } from '../vision/vision-provider';
import { ScreenContext } from '../vision/screen-context';
import { ImageUnderstanding } from '../vision/image-understanding';
import { assertString, assertObject, assertNumber, assertStringArray } from './validate';

export interface VisionPipelineHandlerDeps {
  getMainWindow: () => BrowserWindow | null;
}

export function registerVisionPipelineHandlers(deps: VisionPipelineHandlerDeps): void {
  const vision = VisionProvider.getInstance();
  const screen = ScreenContext.getInstance();
  const imageUnderstanding = ImageUnderstanding.getInstance();

  // ── Vision Provider (model inference) ─────────────────────────────

  ipcMain.handle('vision:load-model', async (_event, name?: unknown) => {
    if (name !== undefined && name !== null) {
      assertString(name as unknown, 'vision:load-model name', 256);
    }
    return vision.loadModel(name as string | undefined);
  });

  ipcMain.handle('vision:unload-model', () => {
    vision.unloadModel();
  });

  ipcMain.handle('vision:is-ready', () => {
    return vision.isReady();
  });

  ipcMain.handle('vision:get-model-info', () => {
    return vision.getModelInfo();
  });

  ipcMain.handle('vision:describe', async (_event, imageBase64: unknown) => {
    assertString(imageBase64, 'vision:describe image', 50_000_000); // up to ~37MB base64
    const buf = Buffer.from(imageBase64 as string, 'base64');
    return vision.describe(buf);
  });

  ipcMain.handle('vision:answer', async (_event, imageBase64: unknown, question: unknown) => {
    assertString(imageBase64, 'vision:answer image', 50_000_000);
    assertString(question, 'vision:answer question', 2_000);
    const buf = Buffer.from(imageBase64 as string, 'base64');
    return vision.answer(buf, question as string);
  });

  // ── Screen Context (screenshots + auto-capture) ───────────────────

  ipcMain.handle('vision:screen:capture-screen', async () => {
    const buf = await screen.captureScreen();
    return buf ? buf.toString('base64') : null;
  });

  ipcMain.handle('vision:screen:capture-window', async (_event, windowId?: unknown) => {
    if (windowId !== undefined && windowId !== null) {
      assertNumber(windowId as unknown, 'vision:screen:capture-window windowId', 0, Number.MAX_SAFE_INTEGER);
    }
    const buf = await screen.captureWindow(windowId as number | undefined);
    return buf ? buf.toString('base64') : null;
  });

  ipcMain.handle('vision:screen:capture-region', async (_event, rect: unknown) => {
    assertObject(rect, 'vision:screen:capture-region rect');
    const r = rect as Record<string, unknown>;
    assertNumber(r.x, 'rect.x', 0, 100_000);
    assertNumber(r.y, 'rect.y', 0, 100_000);
    assertNumber(r.width, 'rect.width', 1, 100_000);
    assertNumber(r.height, 'rect.height', 1, 100_000);
    const buf = await screen.captureRegion(rect as any);
    return buf ? buf.toString('base64') : null;
  });

  ipcMain.handle('vision:screen:get-context', () => {
    return screen.getContext();
  });

  ipcMain.handle('vision:screen:start-auto-capture', (_event, intervalMs?: unknown) => {
    if (intervalMs !== undefined && intervalMs !== null) {
      assertNumber(intervalMs as unknown, 'vision:screen:start-auto-capture intervalMs', 1_000, 300_000);
    }
    screen.startAutoCapture(intervalMs as number | undefined);
  });

  ipcMain.handle('vision:screen:stop-auto-capture', () => {
    screen.stopAutoCapture();
  });

  // ── Image Understanding (high-level analysis) ─────────────────────

  ipcMain.handle('vision:understand:process-image', async (_event, imageBase64: unknown, question?: unknown) => {
    assertString(imageBase64, 'vision:understand:process-image image', 50_000_000);
    if (question !== undefined && question !== null) {
      assertString(question as unknown, 'vision:understand:process-image question', 2_000);
    }
    const buf = Buffer.from(imageBase64 as string, 'base64');
    return imageUnderstanding.processImage(buf, question as string | undefined);
  });

  ipcMain.handle('vision:understand:process-clipboard', async () => {
    return imageUnderstanding.processClipboardImage();
  });

  ipcMain.handle('vision:understand:handle-drop', async (_event, files: unknown) => {
    assertStringArray(files, 'vision:understand:handle-drop files', 20, 1_000);
    return imageUnderstanding.handleDrop(files as string[]);
  });

  ipcMain.handle('vision:understand:handle-file-select', async () => {
    return imageUnderstanding.handleFileSelect();
  });

  ipcMain.handle('vision:understand:get-last-result', () => {
    return imageUnderstanding.getLastResult();
  });

  // ── Event forwarding to renderer ──────────────────────────────────

  screen.on('context-update', (data) => {
    deps.getMainWindow()?.webContents.send('vision:event:context-update', data);
  });

  imageUnderstanding.on('image-result', (data) => {
    deps.getMainWindow()?.webContents.send('vision:event:image-result', data);
  });
}

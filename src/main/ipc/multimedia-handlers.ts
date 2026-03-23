/**
 * IPC handlers for the multimedia creation engine.
 *
 * Exposes podcast, visual, audio message, and music creation
 * plus permission management and media listing to the renderer.
 */

import { ipcMain, app } from 'electron';
import * as path from 'path';
import * as fs from 'fs/promises';
import { multimediaEngine } from '../multimedia-engine';
import { assertObject, assertString } from './validate';

export function registerMultimediaHandlers(): void {
  // ── Podcast creation ────────────────────────────────────────────
  // Crypto Sprint 20: Validate IPC inputs.
  ipcMain.handle('multimedia:create-podcast', async (_event, request: unknown) => {
    try {
      assertObject(request, 'multimedia:create-podcast request');
      const result = await multimediaEngine.generatePodcast(request as any);
      return { ok: true, result };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Multimedia] Podcast creation failed:', msg);
      return { ok: false, error: msg };
    }
  });

  // ── Visual creation (infographic, diagram, etc.) ────────────────
  ipcMain.handle('multimedia:create-visual', async (_event, request: unknown) => {
    try {
      assertObject(request, 'multimedia:create-visual request');
      const result = await multimediaEngine.generateVisual(request as any);
      return { ok: true, result };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Multimedia] Visual creation failed:', msg);
      return { ok: false, error: msg };
    }
  });

  // ── Audio message creation ──────────────────────────────────────
  ipcMain.handle('multimedia:create-audio-message', async (_event, request: unknown) => {
    try {
      assertObject(request, 'multimedia:create-audio-message request');
      const result = await multimediaEngine.createAudioMessage(request as any);
      return { ok: true, result };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Multimedia] Audio message creation failed:', msg);
      return { ok: false, error: msg };
    }
  });

  // ── Music generation ────────────────────────────────────────────
  ipcMain.handle('multimedia:create-music', async (_event, request: unknown) => {
    try {
      assertObject(request, 'multimedia:create-music request');
      const result = await multimediaEngine.generateMusic(request as any);
      return { ok: true, result };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Multimedia] Music creation failed:', msg);
      return { ok: false, error: msg };
    }
  });

  // ── Permissions ─────────────────────────────────────────────────
  ipcMain.handle('multimedia:get-permissions', () => {
    return multimediaEngine.getPermissions();
  });

  ipcMain.handle('multimedia:update-permissions', async (_event, permissions: unknown) => {
    try {
      assertObject(permissions, 'multimedia:update-permissions permissions');
      multimediaEngine.updatePermissions(permissions as any);
      return { ok: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, error: msg };
    }
  });

  ipcMain.handle('multimedia:can-create', (_event, level: unknown) => {
    assertString(level, 'multimedia:can-create level', 50);
    return multimediaEngine.canCreate(level as any);
  });

  // ── Media listing ───────────────────────────────────────────────
  ipcMain.handle('multimedia:list-media', async (_event, type?: unknown) => {
    try {
      if (type !== undefined && type !== null) {
        assertString(type, 'multimedia:list-media type', 50);
      }
      const media = await multimediaEngine.listMedia(type as any);
      return { ok: true, media };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, error: msg, media: [] };
    }
  });

  // ── Speaker presets ─────────────────────────────────────────────
  ipcMain.handle('multimedia:get-speaker-presets', () => {
    return multimediaEngine.getSpeakerPresets();
  });

  // ── Media directory path ────────────────────────────────────────
  ipcMain.handle('multimedia:get-media-dir', () => {
    return multimediaEngine.getMediaDir();
  });

  // ── Camera capture save ──────────────────────────────────────────
  ipcMain.handle('multimedia:save-capture', async (_event, dataURL: unknown, filename?: unknown) => {
    try {
      assertString(dataURL, 'multimedia:save-capture dataURL', 50_000_000);
      if (typeof dataURL !== 'string' || !dataURL.startsWith('data:image/')) {
        throw new Error('Invalid data URL: must start with data:image/');
      }

      let name: string;
      if (filename !== undefined && filename !== null) {
        assertString(filename, 'multimedia:save-capture filename', 255);
        name = filename as string;
      } else {
        name = `capture-${Date.now()}.png`;
      }

      const picturesDir = path.join(app.getPath('pictures'), 'Agent Friday');
      await fs.mkdir(picturesDir, { recursive: true });

      // Strip the data URL prefix (e.g. "data:image/png;base64,")
      const base64Data = (dataURL as string).replace(/^data:image\/\w+;base64,/, '');
      const buffer = Buffer.from(base64Data, 'base64');

      const filePath = path.join(picturesDir, name);
      await fs.writeFile(filePath, buffer);

      console.log('[Multimedia] Saved capture:', filePath);
      return filePath;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error('[Multimedia] Save capture failed:', msg);
      throw new Error(msg);
    }
  });

  console.log('[IPC] Multimedia handlers registered');
}

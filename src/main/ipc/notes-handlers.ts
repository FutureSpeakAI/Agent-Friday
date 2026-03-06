/**
 * IPC handlers for the notes store — CRUD + search operations.
 *
 * Exposes note management to the renderer process via eve.notes namespace.
 */

import { ipcMain } from 'electron';
import { notesStore } from '../notes-store';
import { assertString, assertObject, assertOptionalString } from './validate';

export function registerNotesHandlers(): void {
  ipcMain.handle('notes:list', async () => {
    return notesStore.list();
  });

  ipcMain.handle('notes:get', async (_event, id: unknown) => {
    assertString(id, 'notes:get id', 200);
    return notesStore.get(id as string);
  });

  ipcMain.handle('notes:create', async (_event, input: unknown) => {
    assertObject(input, 'notes:create input');
    const inp = input as Record<string, unknown>;
    if (inp.title !== undefined) assertOptionalString(inp.title, 'input.title', 1000);
    if (inp.content !== undefined) assertOptionalString(inp.content, 'input.content', 500_000);
    return notesStore.create({
      title: (inp.title as string) || 'Untitled Note',
      content: (inp.content as string) || '',
    });
  });

  ipcMain.handle('notes:update', async (_event, id: unknown, patch: unknown) => {
    assertString(id, 'notes:update id', 200);
    assertObject(patch, 'notes:update patch');
    const p = patch as Record<string, unknown>;
    if (p.title !== undefined) assertOptionalString(p.title, 'patch.title', 1000);
    if (p.content !== undefined) assertOptionalString(p.content, 'patch.content', 500_000);
    return notesStore.update(id as string, {
      title: p.title as string | undefined,
      content: p.content as string | undefined,
    });
  });

  ipcMain.handle('notes:delete', async (_event, id: unknown) => {
    assertString(id, 'notes:delete id', 200);
    return notesStore.delete(id as string);
  });

  ipcMain.handle('notes:search', async (_event, query: unknown) => {
    assertString(query, 'notes:search query', 1000);
    return notesStore.search(query as string);
  });
}

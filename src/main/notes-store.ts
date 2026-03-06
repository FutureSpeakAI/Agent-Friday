/**
 * notes-store.ts — Lightweight JSON-backed note storage for Agent Friday.
 *
 * Provides CRUD + search for markdown notes. Persists to a notes.json file
 * in the app's userData directory. Thread-safe via write serialisation.
 *
 * Contract consumed by FridayNotes.tsx via eve.notes namespace.
 */

import fs from 'fs/promises';
import path from 'path';
import { app } from 'electron';

/* ── Types ────────────────────────────────────────────────────────────── */

export interface Note {
  id: string;
  title: string;
  content: string;
  createdAt: string;
  updatedAt: string;
}

interface NotesData {
  version: 1;
  notes: Note[];
}

/* ── Store ────────────────────────────────────────────────────────────── */

const DATA_FILE = () => path.join(app.getPath('userData'), 'friday-notes.json');
let cache: Note[] | null = null;
let writeQueue: Promise<void> = Promise.resolve();

function genId(): string {
  return `note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

async function load(): Promise<Note[]> {
  if (cache) return cache;
  try {
    const raw = await fs.readFile(DATA_FILE(), 'utf-8');
    const data: NotesData = JSON.parse(raw);
    cache = data.notes ?? [];
  } catch {
    cache = [];
  }
  return cache;
}

function enqueueWrite(): void {
  writeQueue = writeQueue.then(async () => {
    if (!cache) return;
    const data: NotesData = { version: 1, notes: cache };
    await fs.writeFile(DATA_FILE(), JSON.stringify(data, null, 2), 'utf-8');
  }).catch(() => {
    /* swallow write errors — data still in memory */
  });
}

/* ── Public API ───────────────────────────────────────────────────────── */

export const notesStore = {
  /** List all notes (title + metadata — content truncated to 120 chars for listing). */
  async list(): Promise<Note[]> {
    const notes = await load();
    return notes.map((n) => ({
      ...n,
      content: n.content.slice(0, 120),
    }));
  },

  /** Get a single note by ID (full content). */
  async get(id: string): Promise<Note | null> {
    const notes = await load();
    return notes.find((n) => n.id === id) ?? null;
  },

  /** Create a new note. Returns the created note with generated ID. */
  async create(input: { title: string; content: string }): Promise<Note> {
    const notes = await load();
    const now = new Date().toISOString();
    const note: Note = {
      id: genId(),
      title: input.title || 'Untitled Note',
      content: input.content || '',
      createdAt: now,
      updatedAt: now,
    };
    notes.unshift(note);
    enqueueWrite();
    return note;
  },

  /** Update an existing note. Returns the updated note or null if not found. */
  async update(
    id: string,
    patch: { title?: string; content?: string },
  ): Promise<Note | null> {
    const notes = await load();
    const idx = notes.findIndex((n) => n.id === id);
    if (idx === -1) return null;
    const note = notes[idx];
    if (patch.title !== undefined) note.title = patch.title;
    if (patch.content !== undefined) note.content = patch.content;
    note.updatedAt = new Date().toISOString();
    enqueueWrite();
    return note;
  },

  /** Delete a note by ID. Returns true if deleted, false if not found. */
  async delete(id: string): Promise<boolean> {
    const notes = await load();
    const idx = notes.findIndex((n) => n.id === id);
    if (idx === -1) return false;
    notes.splice(idx, 1);
    enqueueWrite();
    return true;
  },

  /** Full-text search across note titles and content. */
  async search(query: string): Promise<Note[]> {
    const notes = await load();
    const lower = query.toLowerCase();
    return notes
      .filter(
        (n) =>
          n.title.toLowerCase().includes(lower) ||
          n.content.toLowerCase().includes(lower),
      )
      .map((n) => ({ ...n, content: n.content.slice(0, 120) }));
  },
};

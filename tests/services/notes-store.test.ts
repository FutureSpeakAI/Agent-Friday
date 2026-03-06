/**
 * notes-store.ts — Unit tests for JSON-backed note storage.
 *
 * Tests CRUD operations, search, content truncation, and write serialisation
 * by mocking the file system and Electron app paths.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// ── Mocks ──────────────────────────────────────────────────────────

const mocks = vi.hoisted(() => ({
  readFile: vi.fn(),
  writeFile: vi.fn(),
  dateNow: vi.fn(),
  mathRandom: vi.fn(),
}));

vi.mock('fs/promises', () => ({
  default: {
    readFile: mocks.readFile,
    writeFile: mocks.writeFile,
  },
  readFile: mocks.readFile,
  writeFile: mocks.writeFile,
}));

vi.mock('electron', () => ({
  app: { getPath: () => '/mock/userData' },
}));

// ── Import (after mocks) ──────────────────────────────────────────

// We need to reimport fresh for each test to reset cache
let notesStore: typeof import('../../src/main/notes-store').notesStore;

// ── Helpers ────────────────────────────────────────────────────────

function seedNotes(notes: Array<{
  id: string; title: string; content: string;
  createdAt: string; updatedAt: string;
}>): void {
  mocks.readFile.mockResolvedValue(
    JSON.stringify({ version: 1, notes }),
  );
  mocks.writeFile.mockResolvedValue(undefined);
}

const NOTE_A = {
  id: 'note-1000-aaaaaa',
  title: 'Shopping List',
  content: 'Eggs, milk, bread, butter, cheese, apples, oranges, and much more from the grocery store down the street that has everything we need for the week',
  createdAt: '2024-01-01T00:00:00.000Z',
  updatedAt: '2024-01-01T00:00:00.000Z',
};

const NOTE_B = {
  id: 'note-2000-bbbbbb',
  title: 'Meeting Notes',
  content: 'Discussed project timeline and deliverables for Q2',
  createdAt: '2024-01-02T00:00:00.000Z',
  updatedAt: '2024-01-03T00:00:00.000Z',
};

// ── Tests ──────────────────────────────────────────────────────────

describe('notesStore.list', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    const mod = await import('../../src/main/notes-store');
    notesStore = mod.notesStore;
  });

  it('returns all notes with content truncated to 120 chars', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    const notes = await notesStore.list();
    expect(notes).toHaveLength(2);
    expect(notes[0].content.length).toBeLessThanOrEqual(120);
    expect(notes[0].title).toBe('Shopping List');
    expect(notes[1].title).toBe('Meeting Notes');
  });

  it('returns empty array when no data file exists', async () => {
    mocks.readFile.mockRejectedValue(new Error('ENOENT'));
    const notes = await notesStore.list();
    expect(notes).toEqual([]);
  });

  it('returns empty array for corrupted JSON', async () => {
    mocks.readFile.mockResolvedValue('not valid json{{{');
    const notes = await notesStore.list();
    expect(notes).toEqual([]);
  });
});

describe('notesStore.get', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    const mod = await import('../../src/main/notes-store');
    notesStore = mod.notesStore;
  });

  it('returns full note by ID', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    const note = await notesStore.get('note-1000-aaaaaa');
    expect(note).not.toBeNull();
    expect(note!.title).toBe('Shopping List');
    expect(note!.content).toBe(NOTE_A.content); // Full content, not truncated
  });

  it('returns null for non-existent ID', async () => {
    seedNotes([NOTE_A]);
    const note = await notesStore.get('note-nonexistent');
    expect(note).toBeNull();
  });
});

describe('notesStore.create', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    const mod = await import('../../src/main/notes-store');
    notesStore = mod.notesStore;
  });

  it('creates a note with generated ID and timestamps', async () => {
    seedNotes([]);
    mocks.writeFile.mockResolvedValue(undefined);

    const note = await notesStore.create({
      title: 'New Note',
      content: 'Hello world',
    });

    expect(note.id).toMatch(/^note-\d+-[a-z0-9]+$/);
    expect(note.title).toBe('New Note');
    expect(note.content).toBe('Hello world');
    expect(note.createdAt).toBeTruthy();
    expect(note.updatedAt).toBeTruthy();
    expect(note.createdAt).toBe(note.updatedAt);
  });

  it('persists to disk after creation', async () => {
    seedNotes([]);
    mocks.writeFile.mockResolvedValue(undefined);

    await notesStore.create({ title: 'Persisted', content: 'Data' });

    // Wait for the async write queue to flush
    await new Promise((r) => setTimeout(r, 50));

    expect(mocks.writeFile).toHaveBeenCalled();
    const writtenData = JSON.parse(mocks.writeFile.mock.calls[0][1]);
    expect(writtenData.version).toBe(1);
    expect(writtenData.notes).toHaveLength(1);
    expect(writtenData.notes[0].title).toBe('Persisted');
  });

  it('prepends new note to the beginning of the list', async () => {
    seedNotes([NOTE_A]);
    mocks.writeFile.mockResolvedValue(undefined);

    const created = await notesStore.create({ title: 'First', content: '' });
    const all = await notesStore.list();
    expect(all[0].id).toBe(created.id);
  });
});

describe('notesStore.update', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    const mod = await import('../../src/main/notes-store');
    notesStore = mod.notesStore;
  });

  it('patches title and content of an existing note', async () => {
    seedNotes([NOTE_A]);
    mocks.writeFile.mockResolvedValue(undefined);

    const updated = await notesStore.update('note-1000-aaaaaa', {
      title: 'Updated Title',
      content: 'Updated content',
    });

    expect(updated).not.toBeNull();
    expect(updated!.title).toBe('Updated Title');
    expect(updated!.content).toBe('Updated content');
    expect(updated!.updatedAt).not.toBe(NOTE_A.updatedAt);
  });

  it('patches only title when content is not provided', async () => {
    seedNotes([NOTE_A]);
    mocks.writeFile.mockResolvedValue(undefined);

    const updated = await notesStore.update('note-1000-aaaaaa', {
      title: 'Only Title Changed',
    });

    expect(updated!.title).toBe('Only Title Changed');
    expect(updated!.content).toBe(NOTE_A.content); // unchanged
  });

  it('returns null when updating non-existent note', async () => {
    seedNotes([NOTE_A]);
    const result = await notesStore.update('does-not-exist', { title: 'Nope' });
    expect(result).toBeNull();
  });
});

describe('notesStore.delete', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    const mod = await import('../../src/main/notes-store');
    notesStore = mod.notesStore;
  });

  it('deletes an existing note and returns true', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    mocks.writeFile.mockResolvedValue(undefined);

    const result = await notesStore.delete('note-1000-aaaaaa');
    expect(result).toBe(true);

    const remaining = await notesStore.list();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].title).toBe('Meeting Notes');
  });

  it('returns false when deleting non-existent note', async () => {
    seedNotes([NOTE_A]);
    const result = await notesStore.delete('does-not-exist');
    expect(result).toBe(false);
  });
});

describe('notesStore.search', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    vi.resetModules();
    const mod = await import('../../src/main/notes-store');
    notesStore = mod.notesStore;
  });

  it('finds notes matching title', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    const results = await notesStore.search('Shopping');
    expect(results).toHaveLength(1);
    expect(results[0].title).toBe('Shopping List');
  });

  it('finds notes matching content', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    const results = await notesStore.search('deliverables');
    expect(results).toHaveLength(1);
    expect(results[0].title).toBe('Meeting Notes');
  });

  it('is case-insensitive', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    const results = await notesStore.search('SHOPPING');
    expect(results).toHaveLength(1);
  });

  it('returns empty array when nothing matches', async () => {
    seedNotes([NOTE_A, NOTE_B]);
    const results = await notesStore.search('quantum physics');
    expect(results).toEqual([]);
  });

  it('truncates content in search results', async () => {
    seedNotes([NOTE_A]);
    const results = await notesStore.search('Shopping');
    expect(results[0].content.length).toBeLessThanOrEqual(120);
  });
});

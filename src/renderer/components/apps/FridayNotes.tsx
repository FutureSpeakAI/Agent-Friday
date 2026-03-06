/**
 * FridayNotes.tsx — Notes app for Agent Friday
 *
 * IPC: window.eve.notes?.list(), .get(id), .create(), .update(), .delete(), .search()
 * Falls back to local state if backend not available — fully functional either way.
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

interface NotesProps {
  visible: boolean;
  onClose: () => void;
}

interface Note {
  id: string;
  title: string;
  content: string;
  updatedAt: string;
}

function genId(): string {
  return `note-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return d;
  }
}

export default function FridayNotes({ visible, onClose }: NotesProps) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [usingLocal, setUsingLocal] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load notes
  const loadNotes = useCallback(async () => {
    setLoading(true);
    try {
      const result = await (window as any).eve?.notes?.list();
      if (Array.isArray(result)) {
        setNotes(result);
        setUsingLocal(false);
      } else {
        setUsingLocal(true);
      }
    } catch {
      setUsingLocal(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    if (visible) loadNotes();
  }, [visible, loadNotes]);

  // Select note
  const selectNote = useCallback(async (note: Note) => {
    setActiveId(note.id);
    setTitle(note.title);

    // Try to get full content from backend
    if (!usingLocal) {
      try {
        const full = await (window as any).eve?.notes?.get(note.id);
        if (full) {
          setContent(full.content || '');
          return;
        }
      } catch { /* fall through */ }
    }
    setContent(note.content);
  }, [usingLocal]);

  // Auto-save (debounced)
  const autoSave = useCallback((id: string, newTitle: string, newContent: string) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      const now = new Date().toISOString();

      if (usingLocal) {
        setNotes((prev) =>
          prev.map((n) =>
            n.id === id ? { ...n, title: newTitle, content: newContent, updatedAt: now } : n
          )
        );
      } else {
        try {
          await (window as any).eve?.notes?.update(id, { title: newTitle, content: newContent });
        } catch {
          // Fallback to local update
          setNotes((prev) =>
            prev.map((n) =>
              n.id === id ? { ...n, title: newTitle, content: newContent, updatedAt: now } : n
            )
          );
        }
      }
    }, 600);
  }, [usingLocal]);

  const handleTitleChange = useCallback((val: string) => {
    setTitle(val);
    if (activeId) autoSave(activeId, val, content);
  }, [activeId, content, autoSave]);

  const handleContentChange = useCallback((val: string) => {
    setContent(val);
    if (activeId) autoSave(activeId, title, val);
  }, [activeId, title, autoSave]);

  // Create note
  const createNote = useCallback(async () => {
    const newNote: Note = {
      id: genId(),
      title: 'Untitled Note',
      content: '',
      updatedAt: new Date().toISOString(),
    };

    if (!usingLocal) {
      try {
        const created = await (window as any).eve?.notes?.create({
          title: newNote.title,
          content: newNote.content,
        });
        if (created?.id) newNote.id = created.id;
      } catch { /* use generated id */ }
    }

    setNotes((prev) => [newNote, ...prev]);
    setActiveId(newNote.id);
    setTitle(newNote.title);
    setContent('');
  }, [usingLocal]);

  // Delete note
  const deleteNote = useCallback(async (id: string) => {
    if (!usingLocal) {
      try {
        await (window as any).eve?.notes?.delete(id);
      } catch { /* proceed with local delete */ }
    }

    setNotes((prev) => prev.filter((n) => n.id !== id));
    if (activeId === id) {
      setActiveId(null);
      setTitle('');
      setContent('');
    }
  }, [activeId, usingLocal]);

  // Search / filter
  const filteredNotes = search.trim()
    ? notes.filter(
        (n) =>
          n.title.toLowerCase().includes(search.toLowerCase()) ||
          n.content.toLowerCase().includes(search.toLowerCase())
      )
    : notes;

  const activeNote = notes.find((n) => n.id === activeId);

  return (
    <AppShell visible={visible} onClose={onClose} title="Notes" icon="📝" width={900}>
      {usingLocal && !loading && (
        <div style={s.localNotice}>
          <span>💾</span>
          <span>Using local storage — backend not available. Notes will persist in memory.</span>
        </div>
      )}

      <div style={s.layout}>
        {/* Left Panel — Note List */}
        <div style={s.leftPanel}>
          {/* Search */}
          <div style={s.searchWrap}>
            <input
              style={s.searchInput}
              placeholder="Search notes..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {/* Create */}
          <button
            style={s.createBtn}
            onClick={createNote}
            onMouseEnter={(e) => {
              (e.target as HTMLElement).style.background = 'rgba(0,240,255,0.15)';
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLElement).style.background = 'rgba(0,240,255,0.08)';
            }}
          >
            + New Note
          </button>

          {/* List */}
          <div style={s.noteList}>
            {loading && (
              <div style={s.listEmpty}>Loading...</div>
            )}
            {!loading && filteredNotes.length === 0 && (
              <div style={s.listEmpty}>
                {search ? 'No matches found' : 'No notes yet'}
              </div>
            )}
            {filteredNotes.map((note) => (
              <div
                key={note.id}
                style={{
                  ...s.noteItem,
                  ...(activeId === note.id ? s.noteItemActive : {}),
                }}
                onClick={() => selectNote(note)}
              >
                <div style={s.noteItemTitle}>
                  {note.title || 'Untitled'}
                </div>
                <div style={s.noteItemDate}>{formatDate(note.updatedAt)}</div>
                <div style={s.noteItemPreview}>
                  {note.content.slice(0, 60) || 'Empty note'}
                </div>
                <button
                  style={s.deleteBtn}
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteNote(note.id);
                  }}
                  title="Delete note"
                >
                  🗑
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Right Panel — Editor */}
        <div style={s.rightPanel}>
          {!activeNote ? (
            <div style={s.editorEmpty}>
              <div style={{ fontSize: 40 }}>📝</div>
              <div style={s.editorEmptyText}>Select a note or create a new one</div>
            </div>
          ) : (
            <>
              <input
                style={s.titleInput}
                value={title}
                onChange={(e) => handleTitleChange(e.target.value)}
                placeholder="Note title..."
              />
              <textarea
                style={s.contentArea}
                value={content}
                onChange={(e) => handleContentChange(e.target.value)}
                placeholder="Start writing..."
              />
              <div style={s.editorFooter}>
                <span style={s.footerDate}>
                  Last saved: {formatDate(activeNote.updatedAt)}
                </span>
                <span style={s.footerCount}>
                  {content.length} chars | {content.split(/\s+/).filter(Boolean).length} words
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  localNotice: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 12px',
    background: 'rgba(249,115,22,0.06)',
    border: '1px solid rgba(249,115,22,0.15)',
    borderRadius: 8, color: '#f97316', fontSize: 11,
  },
  layout: {
    display: 'flex', gap: 0, flex: 1,
    minHeight: 420,
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12, overflow: 'hidden',
  },
  /* Left Panel */
  leftPanel: {
    width: '30%', minWidth: 220,
    display: 'flex', flexDirection: 'column',
    borderRight: '1px solid rgba(255,255,255,0.07)',
    background: 'rgba(0,0,0,0.2)',
  },
  searchWrap: { padding: '10px 10px 0' },
  searchInput: {
    width: '100%', padding: '8px 12px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8, color: '#F8FAFC',
    fontSize: 12, outline: 'none',
    fontFamily: "'Inter', system-ui, sans-serif",
    boxSizing: 'border-box' as const,
  },
  createBtn: {
    margin: '8px 10px',
    padding: '8px 0',
    background: 'rgba(0,240,255,0.08)',
    border: '1px solid rgba(0,240,255,0.25)',
    borderRadius: 8, color: '#00f0ff',
    fontSize: 12, fontWeight: 600,
    cursor: 'pointer', transition: 'background 0.15s',
  },
  noteList: {
    flex: 1, overflowY: 'auto',
    display: 'flex', flexDirection: 'column',
  },
  listEmpty: {
    color: '#4a4a62', fontSize: 12, fontStyle: 'italic',
    textAlign: 'center', padding: 24,
  },
  noteItem: {
    padding: '10px 12px',
    cursor: 'pointer',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    position: 'relative',
    transition: 'background 0.12s',
  },
  noteItemActive: {
    background: 'rgba(0,240,255,0.06)',
    borderLeft: '3px solid #00f0ff',
  },
  noteItemTitle: {
    color: '#F8FAFC', fontSize: 13, fontWeight: 600,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
    paddingRight: 24,
  },
  noteItemDate: {
    color: '#4a4a62', fontSize: 10, marginTop: 2,
  },
  noteItemPreview: {
    color: '#8888a0', fontSize: 11, marginTop: 4,
    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
  },
  deleteBtn: {
    position: 'absolute', top: 8, right: 8,
    background: 'none', border: 'none',
    fontSize: 12, cursor: 'pointer',
    opacity: 0.4, padding: 2,
  },
  /* Right Panel */
  rightPanel: {
    flex: 1, display: 'flex', flexDirection: 'column',
    background: 'rgba(0,0,0,0.1)',
  },
  editorEmpty: {
    flex: 1, display: 'flex', flexDirection: 'column',
    alignItems: 'center', justifyContent: 'center', gap: 12,
  },
  editorEmptyText: { color: '#4a4a62', fontSize: 14 },
  titleInput: {
    padding: '14px 18px',
    background: 'transparent',
    border: 'none',
    borderBottom: '1px solid rgba(255,255,255,0.05)',
    color: '#F8FAFC', fontSize: 18, fontWeight: 700,
    outline: 'none',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  contentArea: {
    flex: 1, padding: '14px 18px',
    background: 'transparent',
    border: 'none', resize: 'none',
    color: '#F8FAFC', fontSize: 13,
    lineHeight: 1.7, outline: 'none',
    fontFamily: "'JetBrains Mono', monospace",
  },
  editorFooter: {
    display: 'flex', justifyContent: 'space-between',
    padding: '8px 18px',
    borderTop: '1px solid rgba(255,255,255,0.05)',
  },
  footerDate: { color: '#4a4a62', fontSize: 10 },
  footerCount: {
    color: '#4a4a62', fontSize: 10,
    fontFamily: "'JetBrains Mono', monospace",
  },
};

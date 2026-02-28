import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';

interface QuickActionsProps {
  visible: boolean;
  onClose: () => void;
  onSendText: (text: string) => void;
  isConnected: boolean;
}

interface ActionItem {
  id: string;
  label: string;
  description: string;
  icon: string;
  category: 'command' | 'memory' | 'agent' | 'document' | 'system';
  action: () => void | Promise<void>;
}

export default function QuickActions({ visible, onClose, onSendText, isConnected }: QuickActionsProps) {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Build the action catalog
  const actions: ActionItem[] = useMemo(() => [
    // Voice commands
    {
      id: 'ask',
      label: 'Ask Friday',
      description: 'Send a text message to Friday',
      icon: '💬',
      category: 'command' as const,
      action: () => {
        const msg = query.replace(/^(ask|tell|say)\s+/i, '');
        if (msg.trim()) onSendText(msg);
        onClose();
      },
    },
    {
      id: 'remind',
      label: 'Set a reminder',
      description: 'Ask Friday to remind you about something',
      icon: '⏰',
      category: 'command' as const,
      action: () => {
        onSendText(`Set a reminder: ${query.replace(/^remind\s*(me)?\s*/i, '')}`);
        onClose();
      },
    },
    {
      id: 'research',
      label: 'Research a topic',
      description: 'Dispatch a research agent to investigate',
      icon: '🔍',
      category: 'agent' as const,
      action: () => {
        onSendText(`Research this topic in depth: ${query.replace(/^research\s*/i, '')}`);
        onClose();
      },
    },
    {
      id: 'summarise',
      label: 'Summarise',
      description: 'Summarise a document, article, or topic',
      icon: '📝',
      category: 'agent' as const,
      action: () => {
        onSendText(`Summarise: ${query.replace(/^summar(ise|ize)\s*/i, '')}`);
        onClose();
      },
    },
    // Memory actions
    {
      id: 'remember',
      label: 'Remember this',
      description: 'Save a fact to long-term memory',
      icon: '🧠',
      category: 'memory' as const,
      action: () => {
        onSendText(`Remember this: ${query.replace(/^remember\s*/i, '')}`);
        onClose();
      },
    },
    {
      id: 'recall',
      label: 'Recall memory',
      description: 'Search Friday\'s memory for something',
      icon: '💡',
      category: 'memory' as const,
      action: () => {
        onSendText(`What do you remember about: ${query.replace(/^recall\s*/i, '')}`);
        onClose();
      },
    },
    // Document actions
    {
      id: 'ingest',
      label: 'Ingest document',
      description: 'Import a file into Friday\'s document library',
      icon: '📄',
      category: 'document' as const,
      action: async () => {
        try {
          await window.eve.documents.pickAndIngest();
        } catch {
          // cancelled
        }
        onClose();
      },
    },
    {
      id: 'search-docs',
      label: 'Search documents',
      description: 'Search across all ingested documents',
      icon: '📚',
      category: 'document' as const,
      action: () => {
        onSendText(`Search my documents for: ${query.replace(/^search\s*(docs|documents)?\s*/i, '')}`);
        onClose();
      },
    },
    // System actions
    {
      id: 'dashboard',
      label: 'Open Dashboard',
      description: 'Open the command center (Ctrl+Shift+D)',
      icon: '◈',
      category: 'system' as const,
      action: () => {
        // Simulate Ctrl+Shift+D
        window.dispatchEvent(new KeyboardEvent('keydown', {
          code: 'KeyD', ctrlKey: true, shiftKey: true, bubbles: true,
        }));
        onClose();
      },
    },
    {
      id: 'settings',
      label: 'Open Settings',
      description: 'Configure Friday\'s behaviour',
      icon: '⚙',
      category: 'system' as const,
      action: () => {
        // Trigger settings — parent will handle
        window.dispatchEvent(new CustomEvent('eve:open-settings'));
        onClose();
      },
    },
    {
      id: 'watch-project',
      label: 'Watch project',
      description: 'Start tracking a project directory',
      icon: '◆',
      category: 'document' as const,
      action: () => {
        onSendText(`Watch my project at: ${query.replace(/^watch\s*(project)?\s*/i, '')}`);
        onClose();
      },
    },
    {
      id: 'clear-chat',
      label: 'Clear chat history',
      description: 'Clear the conversation history from view',
      icon: '🗑',
      category: 'system' as const,
      action: () => {
        window.dispatchEvent(new CustomEvent('eve:clear-chat'));
        onClose();
      },
    },
  ], [query, onSendText, onClose]);

  // Fuzzy filter
  const filtered = useMemo(() => {
    if (!query.trim()) return actions;
    const q = query.toLowerCase();
    return actions.filter(
      (a) =>
        a.label.toLowerCase().includes(q) ||
        a.description.toLowerCase().includes(q) ||
        a.category.includes(q) ||
        a.id.includes(q)
    );
  }, [query, actions]);

  // Reset selection on filter change
  useEffect(() => {
    setSelectedIndex(0);
  }, [filtered.length]);

  // Auto-focus
  useEffect(() => {
    if (visible) {
      setQuery('');
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [visible]);

  // Keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const item = filtered[selectedIndex];
        if (item) item.action();
        return;
      }
    },
    [filtered, selectedIndex, onClose]
  );

  // Scroll selected into view
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const child = list.children[selectedIndex] as HTMLElement;
    if (child) {
      child.scrollIntoView({ block: 'nearest' });
    }
  }, [selectedIndex]);

  if (!visible) return null;

  const CATEGORY_COLORS: Record<string, string> = {
    command: '#00f0ff',
    memory: '#a78bfa',
    agent: '#f59e0b',
    document: '#22c55e',
    system: '#666680',
  };

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.palette} onClick={(e) => e.stopPropagation()} onKeyDown={handleKeyDown}>
        {/* Search input */}
        <div style={styles.inputRow}>
          <span style={styles.searchIcon}>⌘</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={isConnected ? 'Type a command or ask Friday...' : 'Friday is disconnected — commands only'}
            style={styles.input}
          />
          <span style={styles.escHint}>ESC</span>
        </div>

        {/* Results */}
        <div ref={listRef} style={styles.list}>
          {filtered.length === 0 ? (
            <div style={styles.noResults}>
              {query.trim() ? (
                <>
                  No matching command — press Enter to ask Friday: <strong>"{query}"</strong>
                </>
              ) : (
                'Start typing to filter commands...'
              )}
            </div>
          ) : (
            filtered.map((item, i) => (
              <div
                key={item.id}
                style={{
                  ...styles.item,
                  ...(i === selectedIndex ? styles.itemSelected : {}),
                }}
                onClick={() => item.action()}
                onMouseEnter={() => setSelectedIndex(i)}
              >
                <span style={styles.itemIcon}>{item.icon}</span>
                <div style={styles.itemBody}>
                  <span style={styles.itemLabel}>{item.label}</span>
                  <span style={styles.itemDesc}>{item.description}</span>
                </div>
                <span
                  style={{
                    ...styles.itemCategory,
                    color: CATEGORY_COLORS[item.category] || '#666',
                    background: `${CATEGORY_COLORS[item.category] || '#666'}18`,
                  }}
                >
                  {item.category}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div style={styles.footer}>
          <span style={styles.footerHint}>↑↓ navigate</span>
          <span style={styles.footerHint}>↵ select</span>
          <span style={styles.footerHint}>esc close</span>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0, 0, 0, 0.5)',
    backdropFilter: 'blur(6px)',
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'center',
    paddingTop: '15vh',
    zIndex: 120,
    animation: 'fadeIn 0.15s ease',
  },
  palette: {
    width: 560,
    maxWidth: '92vw',
    background: 'rgba(12, 12, 20, 0.98)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 16,
    overflow: 'hidden',
    boxShadow: '0 20px 60px rgba(0, 0, 0, 0.6), 0 0 40px rgba(0, 240, 255, 0.06)',
  },
  inputRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '14px 18px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
  },
  searchIcon: {
    fontSize: 14,
    color: '#555568',
    flexShrink: 0,
  },
  input: {
    flex: 1,
    background: 'none',
    border: 'none',
    outline: 'none',
    color: '#e0e0e8',
    fontSize: 15,
    fontFamily: 'inherit',
  },
  escHint: {
    fontSize: 10,
    color: '#444',
    background: 'rgba(255, 255, 255, 0.05)',
    padding: '2px 6px',
    borderRadius: 4,
    fontWeight: 600,
    fontFamily: 'monospace',
  },
  list: {
    maxHeight: 320,
    overflowY: 'auto',
    padding: '6px 0',
  },
  item: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '10px 18px',
    cursor: 'pointer',
    transition: 'background 0.1s',
  },
  itemSelected: {
    background: 'rgba(0, 240, 255, 0.06)',
  },
  itemIcon: {
    fontSize: 16,
    width: 24,
    textAlign: 'center' as const,
    flexShrink: 0,
  },
  itemBody: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 2,
  },
  itemLabel: {
    fontSize: 13,
    color: '#e0e0e8',
    fontWeight: 600,
  },
  itemDesc: {
    fontSize: 11,
    color: '#666680',
  },
  itemCategory: {
    fontSize: 9,
    fontWeight: 700,
    padding: '2px 8px',
    borderRadius: 4,
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    flexShrink: 0,
  },
  noResults: {
    padding: '20px 18px',
    fontSize: 13,
    color: '#555568',
    textAlign: 'center',
  },
  footer: {
    display: 'flex',
    gap: 16,
    justifyContent: 'center',
    padding: '8px 18px',
    borderTop: '1px solid rgba(255, 255, 255, 0.04)',
  },
  footerHint: {
    fontSize: 10,
    color: '#444',
    fontFamily: "'JetBrains Mono', monospace",
  },
};

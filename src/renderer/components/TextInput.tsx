import React, { useState, useRef, useEffect, useCallback } from 'react';

interface TextInputProps {
  onSend: (text: string) => void;
  isConnected?: boolean;
  /** True when the local voice conversation loop (Ollama) is active */
  isLocalActive?: boolean;
}

const MAX_ROWS = 5;
const LINE_HEIGHT = 20; // px per line

export default function TextInput({ onSend, isConnected, isLocalActive }: TextInputProps) {
  const [value, setValue] = useState('');
  const [focused, setFocused] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea to content (1–5 rows)
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = `${LINE_HEIGHT}px`; // reset to 1 line to measure
    const scrollH = el.scrollHeight;
    const maxH = LINE_HEIGHT * MAX_ROWS;
    el.style.height = `${Math.min(scrollH, maxH)}px`;
  }, []);

  useEffect(() => {
    autoResize();
  }, [value, autoResize]);

  // Tab key focuses the input from anywhere
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Tab' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        // Don't capture Tab if user is in another input/textarea or settings panel
        const target = e.target as HTMLElement;
        if (target.tagName === 'INPUT' || (target.tagName === 'TEXTAREA' && target !== textareaRef.current)) {
          return;
        }
        e.preventDefault();
        textareaRef.current?.focus();
      }
    };
    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, []);

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      textareaRef.current?.blur();
      return;
    }
    // Enter sends, Shift+Enter adds newline
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div style={styles.container}>
      <div
        style={{
          ...styles.inputRow,
          borderColor: focused
            ? 'rgba(0, 240, 255, 0.3)'
            : 'rgba(0, 240, 255, 0.08)',
          background: focused
            ? 'rgba(255,255,255,0.05)'
            : 'rgba(255,255,255,0.02)',
        }}
      >
        {/* Connection status dot */}
        <span
          style={{
            ...styles.statusDot,
            background: isLocalActive
              ? '#00f0ff'
              : isConnected
                ? '#22c55e'
                : '#555568',
            boxShadow: isLocalActive
              ? '0 0 6px rgba(0, 240, 255, 0.4)'
              : isConnected
                ? '0 0 6px rgba(34, 197, 94, 0.4)'
                : 'none',
          }}
          title={
            isLocalActive ? 'Local AI (Ollama)' : isConnected ? 'Cloud AI (Gemini)' : 'Disconnected'
          }
        />
        <span style={{ ...styles.prompt, color: focused ? '#00f0ff' : '#333348' }}>›</span>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder="Type a message... (Tab to focus)"
          style={styles.textarea}
          autoComplete="off"
          spellCheck={false}
          rows={1}
        />
        <button
          type="button"
          onClick={handleSubmit}
          style={{
            ...styles.sendBtn,
            opacity: value.trim() ? 1 : 0.2,
          }}
          disabled={!value.trim()}
          title="Send (Enter)"
        >
          ↵
        </button>
      </div>
      {focused && (
        <div style={styles.hint}>
          <span style={styles.hintKey}>Enter</span> send
          <span style={styles.hintSep}>·</span>
          <span style={styles.hintKey}>Shift+Enter</span> newline
          <span style={styles.hintSep}>·</span>
          <span style={styles.hintKey}>Esc</span> unfocus
          {(isLocalActive || isConnected) && (
            <>
              <span style={styles.hintSep}>·</span>
              <span style={{
                fontSize: 9,
                color: isLocalActive ? 'rgba(0, 240, 255, 0.5)' : 'rgba(34, 197, 94, 0.5)',
                fontWeight: 500,
              }}>
                {isLocalActive ? 'local' : 'cloud'}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    width: '100%',
    padding: '0 4px',
  },
  inputRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    border: '1px solid',
    borderRadius: 12,
    padding: '8px 14px',
    backdropFilter: 'blur(8px)',
    transition: 'border-color 0.2s, background 0.2s',
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: '50%',
    flexShrink: 0,
    marginTop: 7, // vertically center with first line
    transition: 'background 0.3s, box-shadow 0.3s',
  },
  prompt: {
    fontSize: 18,
    fontWeight: 700,
    lineHeight: `${LINE_HEIGHT}px`,
    flexShrink: 0,
    transition: 'color 0.2s',
  },
  textarea: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#e0e0e8',
    fontSize: 14,
    fontFamily: 'inherit',
    letterSpacing: '0.01em',
    lineHeight: `${LINE_HEIGHT}px`,
    resize: 'none',
    overflow: 'auto',
    height: LINE_HEIGHT,
    maxHeight: LINE_HEIGHT * MAX_ROWS,
    padding: 0,
  },
  sendBtn: {
    background: 'rgba(0, 240, 255, 0.1)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 16,
    fontWeight: 600,
    width: 32,
    height: 32,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    transition: 'opacity 0.15s',
  },
  hint: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    fontSize: 10,
    color: '#444458',
    letterSpacing: '0.03em',
  },
  hintKey: {
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 3,
    padding: '1px 5px',
    fontSize: 9,
    fontWeight: 600,
    color: '#666680',
  },
  hintSep: {
    color: '#333345',
  },
};

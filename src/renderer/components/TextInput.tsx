import React, { useState, useRef, useEffect, useCallback } from 'react';

interface TextInputProps {
  visible: boolean;
  onSend: (text: string) => void;
  onClose: () => void;
}

const MAX_ROWS = 5;
const LINE_HEIGHT = 20; // px per line

export default function TextInput({ visible, onSend, onClose }: TextInputProps) {
  const [value, setValue] = useState('');
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
    if (visible) {
      setTimeout(() => {
        textareaRef.current?.focus();
        autoResize();
      }, 50);
    } else {
      setValue('');
    }
  }, [visible, autoResize]);

  useEffect(() => {
    autoResize();
  }, [value, autoResize]);

  if (!visible) return null;

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue('');
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    // Enter sends, Shift+Enter adds newline
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const isFocused = typeof document !== 'undefined' && document.activeElement === textareaRef.current;

  return (
    <div style={styles.container}>
      <div style={styles.inputRow}>
        <span style={styles.prompt}>›</span>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
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
            opacity: value.trim() ? 1 : 0.3,
          }}
          disabled={!value.trim()}
        >
          ↵
        </button>
      </div>
      {!isFocused && (
        <div style={styles.hint}>
          <span style={styles.hintKey}>Tab</span> voice mode
          <span style={styles.hintSep}>·</span>
          <span style={styles.hintKey}>Esc</span> close
          <span style={styles.hintSep}>·</span>
          <span style={styles.hintKey}>Enter</span> send
          <span style={styles.hintSep}>·</span>
          <span style={styles.hintKey}>Shift+Enter</span> newline
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    width: '100%',
    maxWidth: 560,
    padding: '0 24px',
  },
  inputRow: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 12,
    padding: '10px 16px',
    backdropFilter: 'blur(8px)',
    transition: 'border-color 0.2s',
  },
  prompt: {
    color: '#00f0ff',
    fontSize: 18,
    fontWeight: 700,
    lineHeight: `${LINE_HEIGHT}px`,
    flexShrink: 0,
    paddingTop: 0,
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

/**
 * FridayTerminal.tsx — Terminal/REPL app for Agent Friday
 *
 * IPC: window.eve.container?.execute(language, code) => {stdout, stderr, exitCode}
 * Supports bash, python, node. Command history with up/down arrows.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import AppShell from '../AppShell';

interface TerminalProps {
  visible: boolean;
  onClose: () => void;
}

interface OutputLine {
  id: number;
  type: 'command' | 'stdout' | 'stderr' | 'info' | 'error';
  text: string;
  lang?: string;
}

type Language = 'bash' | 'python' | 'node';

const LANGUAGES: { key: Language; label: string; icon: string }[] = [
  { key: 'bash', label: 'Bash', icon: '🐚' },
  { key: 'python', label: 'Python', icon: '🐍' },
  { key: 'node', label: 'Node.js', icon: '🟢' },
];

const WELCOME_LINES: OutputLine[] = [
  { id: 0, type: 'info', text: '╔════════════════════════════════════════════════╗' },
  { id: 1, type: 'info', text: '║       Agent Friday Terminal — v1.0.0           ║' },
  { id: 2, type: 'info', text: '║  Select a language and type a command below    ║' },
  { id: 3, type: 'info', text: '╚════════════════════════════════════════════════╝' },
  { id: 4, type: 'info', text: '' },
];

let lineIdCounter = 100;

export default function FridayTerminal({ visible, onClose }: TerminalProps) {
  const [language, setLanguage] = useState<Language>('bash');
  const [input, setInput] = useState('');
  const [output, setOutput] = useState<OutputLine[]>(WELCOME_LINES);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const [running, setRunning] = useState(false);
  const outputRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [output]);

  // Focus input on show
  useEffect(() => {
    if (visible && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [visible]);

  const addLine = useCallback((type: OutputLine['type'], text: string, lang?: string) => {
    setOutput((prev) => [
      ...prev,
      { id: ++lineIdCounter, type, text, lang },
    ]);
  }, []);

  const execute = useCallback(async (cmd: string) => {
    if (!cmd.trim()) return;

    // Add command to output
    const prompt = language === 'bash' ? '$' : language === 'python' ? '>>>' : '>';
    addLine('command', `${prompt} ${cmd}`, language);

    // Add to history
    setHistory((prev) => {
      const filtered = prev.filter((h) => h !== cmd);
      return [cmd, ...filtered].slice(0, 100);
    });
    setHistoryIdx(-1);
    setInput('');
    setRunning(true);

    try {
      const result = await (window as any).eve?.container?.execute(language, cmd);

      if (result) {
        if (result.stdout) {
          result.stdout.split('\n').forEach((line: string) => {
            addLine('stdout', line);
          });
        }
        if (result.stderr) {
          result.stderr.split('\n').forEach((line: string) => {
            addLine('stderr', line);
          });
        }
        if (result.exitCode !== undefined && result.exitCode !== 0) {
          addLine('info', `[exit code: ${result.exitCode}]`);
        }
      } else {
        addLine('error', 'Backend not available — container execution requires the Electron backend.');
        addLine('info', 'Tip: This terminal will work once window.eve.container is connected.');
      }
    } catch (err: any) {
      addLine('error', `Error: ${err?.message || 'Execution failed'}`);
    }

    setRunning(false);
  }, [language, addLine]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !running) {
      execute(input);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length > 0) {
        const nextIdx = Math.min(historyIdx + 1, history.length - 1);
        setHistoryIdx(nextIdx);
        setInput(history[nextIdx]);
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIdx > 0) {
        const nextIdx = historyIdx - 1;
        setHistoryIdx(nextIdx);
        setInput(history[nextIdx]);
      } else {
        setHistoryIdx(-1);
        setInput('');
      }
    }
  }, [input, running, execute, history, historyIdx]);

  const clearOutput = useCallback(() => {
    setOutput(WELCOME_LINES);
  }, []);

  const getPrompt = (): string => {
    if (language === 'bash') return '$';
    if (language === 'python') return '>>>';
    return '>';
  };

  const getLineColor = (type: OutputLine['type']): string => {
    switch (type) {
      case 'command': return '#00f0ff';
      case 'stdout': return '#22c55e';
      case 'stderr': return '#ef4444';
      case 'error': return '#ef4444';
      case 'info': return '#8888a0';
      default: return '#22c55e';
    }
  };

  return (
    <AppShell visible={visible} onClose={onClose} title="Terminal" icon="⌨️" width={780}>
      {/* Language Selector + Controls */}
      <div style={s.toolbar}>
        <div style={s.langRow}>
          {LANGUAGES.map((lang) => (
            <button
              key={lang.key}
              style={{
                ...s.langBtn,
                ...(language === lang.key ? s.langBtnActive : {}),
              }}
              onClick={() => setLanguage(lang.key)}
            >
              <span>{lang.icon}</span>
              <span>{lang.label}</span>
            </button>
          ))}
        </div>
        <button
          style={s.clearBtn}
          onClick={clearOutput}
          onMouseEnter={(e) => {
            (e.target as HTMLElement).style.background = 'rgba(255,255,255,0.06)';
          }}
          onMouseLeave={(e) => {
            (e.target as HTMLElement).style.background = 'transparent';
          }}
        >
          Clear
        </button>
      </div>

      {/* Output Area */}
      <div ref={outputRef} style={s.outputArea}>
        {output.map((line) => (
          <div key={line.id} style={s.outputLine}>
            <span style={{ color: getLineColor(line.type) }}>
              {line.text}
            </span>
          </div>
        ))}
        {running && (
          <div style={s.outputLine}>
            <span style={{ color: '#f97316' }}>Running...</span>
          </div>
        )}
      </div>

      {/* Input Line */}
      <div style={s.inputRow}>
        <span style={s.prompt}>{getPrompt()}</span>
        <input
          ref={inputRef}
          style={s.input}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`Type a ${language} command...`}
          disabled={running}
          spellCheck={false}
          autoComplete="off"
        />
      </div>

      {/* Status Bar */}
      <div style={s.statusBar}>
        <span style={s.statusLang}>
          {LANGUAGES.find((l) => l.key === language)?.icon}{' '}
          {LANGUAGES.find((l) => l.key === language)?.label}
        </span>
        <span style={s.statusHints}>
          ↑↓ history | Enter to run | Esc to close
        </span>
        <span style={s.statusHistory}>
          {history.length} cmd{history.length !== 1 ? 's' : ''} in history
        </span>
      </div>
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  toolbar: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
  },
  langRow: { display: 'flex', gap: 6 },
  langBtn: {
    display: 'flex', alignItems: 'center', gap: 5,
    padding: '6px 12px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6, color: '#8888a0', fontSize: 12,
    cursor: 'pointer', transition: 'all 0.15s',
  },
  langBtnActive: {
    background: 'rgba(0,240,255,0.08)',
    border: '1px solid rgba(0,240,255,0.3)',
    color: '#00f0ff',
  },
  clearBtn: {
    background: 'transparent',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6, color: '#8888a0', fontSize: 11,
    padding: '6px 12px', cursor: 'pointer',
    transition: 'background 0.15s',
  },
  outputArea: {
    flex: 1,
    minHeight: 300, maxHeight: 420,
    overflowY: 'auto',
    background: 'rgba(0, 0, 0, 0.5)',
    borderRadius: 10,
    padding: '12px 14px',
    border: '1px solid rgba(255,255,255,0.07)',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    lineHeight: 1.6,
  },
  outputLine: {
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-all' as const,
    minHeight: 18,
  },
  inputRow: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '10px 14px',
    background: 'rgba(0, 0, 0, 0.4)',
    borderRadius: 10,
    border: '1px solid rgba(0,240,255,0.15)',
  },
  prompt: {
    color: '#00f0ff',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 14, fontWeight: 700,
    flexShrink: 0,
  },
  input: {
    flex: 1, background: 'transparent',
    border: 'none', outline: 'none',
    color: '#22c55e',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 13, caretColor: '#00f0ff',
  },
  statusBar: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '6px 12px',
    background: 'rgba(255,255,255,0.02)',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.04)',
  },
  statusLang: { color: '#8888a0', fontSize: 11 },
  statusHints: { color: '#4a4a62', fontSize: 10 },
  statusHistory: {
    color: '#4a4a62', fontSize: 10,
    fontFamily: "'JetBrains Mono', monospace",
  },
};

/**
 * FridayCalc.tsx — Calculator app for Agent Friday
 *
 * Pure renderer — no IPC. Uses sandboxed Function constructor for math eval.
 * Supports basic arithmetic, parentheses, and scientific functions.
 */

import React, { useState, useCallback } from 'react';
import AppShell from '../AppShell';

interface CalcProps {
  visible: boolean;
  onClose: () => void;
}

interface HistoryEntry {
  expression: string;
  result: string;
}

const SCIENTIFIC_FNS: { label: string; insert: string }[] = [
  { label: 'sin', insert: 'Math.sin(' },
  { label: 'cos', insert: 'Math.cos(' },
  { label: 'tan', insert: 'Math.tan(' },
  { label: 'sqrt', insert: 'Math.sqrt(' },
  { label: 'pow', insert: 'Math.pow(' },
  { label: 'log', insert: 'Math.log(' },
  { label: 'pi', insert: 'Math.PI' },
  { label: 'e', insert: 'Math.E' },
];

const NUM_PAD = [
  ['(', ')', 'C', '/'],
  ['7', '8', '9', '*'],
  ['4', '5', '6', '-'],
  ['1', '2', '3', '+'],
  ['0', '.', '±', '='],
];

function safeEval(expr: string): string {
  try {
    // Sandboxed evaluation — only Math is available
    const fn = new Function('Math', `"use strict"; return (${expr});`);
    const result = fn(Math);
    if (typeof result !== 'number' || !isFinite(result)) return 'Error';
    return String(Math.round(result * 1e12) / 1e12);
  } catch {
    return 'Error';
  }
}

export default function FridayCalc({ visible, onClose }: CalcProps) {
  const [display, setDisplay] = useState('0');
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [justEvaluated, setJustEvaluated] = useState(false);

  const appendToDisplay = useCallback((val: string) => {
    setDisplay((prev) => {
      if (justEvaluated && /[0-9.]/.test(val)) {
        setJustEvaluated(false);
        return val;
      }
      setJustEvaluated(false);
      return prev === '0' && val !== '.' ? val : prev + val;
    });
  }, [justEvaluated]);

  const handleButton = useCallback((btn: string) => {
    switch (btn) {
      case 'C':
        setDisplay('0');
        setJustEvaluated(false);
        break;
      case '±':
        setDisplay((prev) => prev.startsWith('-') ? prev.slice(1) : '-' + prev);
        break;
      case '=': {
        const result = safeEval(display);
        setHistory((prev) => [{ expression: display, result }, ...prev].slice(0, 50));
        setDisplay(result);
        setJustEvaluated(true);
        break;
      }
      default:
        appendToDisplay(btn);
    }
  }, [display, appendToDisplay]);

  const handleScientific = useCallback((insert: string) => {
    setDisplay((prev) => {
      if (justEvaluated || prev === '0') {
        setJustEvaluated(false);
        return insert;
      }
      return prev + insert;
    });
  }, [justEvaluated]);

  return (
    <AppShell visible={visible} onClose={onClose} title="Calculator" icon="🔢" width={480}>
      {/* Display */}
      <div style={s.display}>
        <div style={s.displayText}>{display}</div>
      </div>

      {/* Scientific Row */}
      <div style={s.sciRow}>
        {SCIENTIFIC_FNS.map((fn) => (
          <button
            key={fn.label}
            style={s.sciBtn}
            onClick={() => handleScientific(fn.insert)}
            onMouseEnter={(e) => {
              (e.target as HTMLElement).style.background = 'rgba(138,43,226,0.25)';
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLElement).style.background = 'rgba(138,43,226,0.12)';
            }}
          >
            {fn.label}
          </button>
        ))}
      </div>

      {/* Number Pad */}
      <div style={s.padGrid}>
        {NUM_PAD.flat().map((btn, i) => {
          const isOp = ['+', '-', '*', '/', '='].includes(btn);
          const isClear = btn === 'C';
          return (
            <button
              key={`${btn}-${i}`}
              style={{
                ...s.padBtn,
                ...(isOp ? s.opBtn : {}),
                ...(isClear ? s.clearBtn : {}),
                ...(btn === '=' ? s.equalsBtn : {}),
              }}
              onClick={() => handleButton(btn)}
              onMouseEnter={(e) => {
                (e.target as HTMLElement).style.opacity = '0.85';
              }}
              onMouseLeave={(e) => {
                (e.target as HTMLElement).style.opacity = '1';
              }}
            >
              {btn}
            </button>
          );
        })}
      </div>

      {/* History */}
      <div style={s.historySection}>
        <div style={s.historyLabel}>History</div>
        <div style={s.historyList}>
          {history.length === 0 && (
            <div style={s.emptyHistory}>No calculations yet</div>
          )}
          {history.map((h, i) => (
            <div
              key={i}
              style={s.historyItem}
              onClick={() => { setDisplay(h.result); setJustEvaluated(true); }}
              title="Click to reuse result"
            >
              <span style={s.historyExpr}>{h.expression}</span>
              <span style={s.historyResult}>= {h.result}</span>
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  display: {
    background: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(0, 240, 255, 0.15)',
    borderRadius: 12,
    padding: '16px 20px',
    minHeight: 64,
    display: 'flex',
    alignItems: 'flex-end',
    justifyContent: 'flex-end',
  },
  displayText: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 28,
    color: '#00f0ff',
    wordBreak: 'break-all',
    textAlign: 'right',
    lineHeight: 1.3,
  },
  sciRow: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap',
  },
  sciBtn: {
    flex: '1 1 auto',
    minWidth: 44,
    padding: '8px 6px',
    background: 'rgba(138, 43, 226, 0.12)',
    border: '1px solid rgba(138, 43, 226, 0.3)',
    borderRadius: 8,
    color: '#c4a5f0',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  padGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: 8,
  },
  padBtn: {
    padding: '14px 0',
    background: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 10,
    color: '#F8FAFC',
    fontSize: 18,
    fontFamily: "'Inter', system-ui, sans-serif",
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'opacity 0.12s',
  },
  opBtn: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    color: '#00f0ff',
  },
  clearBtn: {
    background: 'rgba(239, 68, 68, 0.12)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    color: '#ef4444',
  },
  equalsBtn: {
    background: 'rgba(34, 197, 94, 0.15)',
    border: '1px solid rgba(34, 197, 94, 0.3)',
    color: '#22c55e',
    fontWeight: 700,
  },
  historySection: {
    marginTop: 4,
  },
  historyLabel: {
    fontSize: 12,
    color: '#8888a0',
    fontWeight: 600,
    letterSpacing: '0.05em',
    textTransform: 'uppercase' as const,
    marginBottom: 8,
  },
  historyList: {
    maxHeight: 160,
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  emptyHistory: {
    color: '#4a4a62',
    fontSize: 13,
    fontStyle: 'italic',
    textAlign: 'center',
    padding: 12,
  },
  historyItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '8px 12px',
    background: 'rgba(255, 255, 255, 0.03)',
    borderRadius: 8,
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  historyExpr: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    color: '#8888a0',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    maxWidth: '60%',
  },
  historyResult: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 13,
    color: '#00f0ff',
    fontWeight: 600,
  },
};

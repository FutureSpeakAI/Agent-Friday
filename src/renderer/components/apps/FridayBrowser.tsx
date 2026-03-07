/**
 * FridayBrowser.tsx — Browser & MCP tool runner for Agent Friday
 *
 * IPC: window.eve.browser.*, window.eve.mcp.*
 * Features: List browser/MCP tools, execute with JSON args, result display, history
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

interface ToolDef {
  name: string;
  description: string;
  source: 'browser' | 'mcp';
}

interface HistoryEntry {
  id: number;
  toolName: string;
  args: string;
  result: string;
  success: boolean;
  timestamp: number;
}

interface FridayBrowserProps {
  visible: boolean;
  onClose: () => void;
}

let historyId = 0;

export default function FridayBrowser({ visible, onClose }: FridayBrowserProps) {
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTool, setSelectedTool] = useState<ToolDef | null>(null);
  const [argsInput, setArgsInput] = useState('{}');
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [filter, setFilter] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  const loadTools = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [browserTools, mcpTools] = await Promise.allSettled([
        window.eve.browser.listTools(),
        window.eve.mcp.listTools(),
      ]);

      const combined: ToolDef[] = [];

      if (browserTools.status === 'fulfilled' && Array.isArray(browserTools.value)) {
        browserTools.value.forEach((t: any) =>
          combined.push({ name: t.name, description: t.description || '', source: 'browser' }),
        );
      }
      if (mcpTools.status === 'fulfilled' && Array.isArray(mcpTools.value)) {
        mcpTools.value.forEach((t: any) =>
          combined.push({ name: t.name, description: t.description || '', source: 'mcp' }),
        );
      }

      setTools(combined);
    } catch (err: any) {
      setError(err?.message || 'Failed to load tools');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    loadTools();
  }, [visible, loadTools]);

  const handleExecute = async () => {
    if (!selectedTool) return;
    let parsedArgs: any;
    try {
      parsedArgs = JSON.parse(argsInput);
    } catch {
      setError('Invalid JSON in arguments');
      return;
    }

    try {
      setExecuting(true);
      setError(null);
      setResult(null);

      let res: any;
      if (selectedTool.source === 'browser') {
        res = await window.eve.browser.callTool(selectedTool.name, parsedArgs);
      } else {
        res = await window.eve.mcp.callTool(selectedTool.name, parsedArgs);
      }

      const resultStr = typeof res === 'string' ? res : JSON.stringify(res, null, 2);
      setResult(resultStr);
      setHistory((prev) => [
        {
          id: ++historyId,
          toolName: selectedTool.name,
          args: argsInput,
          result: resultStr,
          success: true,
          timestamp: Date.now(),
        },
        ...prev,
      ].slice(0, 50));
    } catch (err: any) {
      const errMsg = err?.message || 'Execution failed';
      setResult(`ERROR: ${errMsg}`);
      setHistory((prev) => [
        {
          id: ++historyId,
          toolName: selectedTool.name,
          args: argsInput,
          result: errMsg,
          success: false,
          timestamp: Date.now(),
        },
        ...prev,
      ].slice(0, 50));
    } finally {
      setExecuting(false);
    }
  };

  const filteredTools = filter
    ? tools.filter(
        (t) =>
          t.name.toLowerCase().includes(filter.toLowerCase()) ||
          t.description.toLowerCase().includes(filter.toLowerCase()),
      )
    : tools;

  const browserCount = tools.filter((t) => t.source === 'browser').length;
  const mcpCount = tools.filter((t) => t.source === 'mcp').length;

  return (
    <AppShell visible={visible} onClose={onClose} icon="🌐" title="Browser Tools" width={960}>
      <ContextBar appId="friday-browser" />
      {error && <div style={s.errorBar}>{error}</div>}

      {/* Stats Bar */}
      <div style={s.statsBar}>
        <span style={s.stat}>
          <span style={s.statLabel}>Browser</span>
          <span style={s.statVal}>{browserCount}</span>
        </span>
        <span style={s.stat}>
          <span style={s.statLabel}>MCP</span>
          <span style={s.statVal}>{mcpCount}</span>
        </span>
        <span style={s.stat}>
          <span style={s.statLabel}>History</span>
          <span style={s.statVal}>{history.length}</span>
        </span>
        <div style={{ flex: 1 }} />
        <button
          style={showHistory ? { ...s.histToggle, ...s.histToggleActive } : s.histToggle}
          onClick={() => setShowHistory(!showHistory)}
        >
          {showHistory ? 'Tools' : 'History'}
        </button>
      </div>

      {showHistory ? (
        /* History View */
        <div style={s.historyList}>
          {history.length === 0 && <div style={s.empty}>No executions yet</div>}
          {history.map((h) => (
            <div
              key={h.id}
              style={s.historyItem}
              onClick={() => {
                const tool = tools.find((t) => t.name === h.toolName);
                if (tool) {
                  setSelectedTool(tool);
                  setArgsInput(h.args);
                  setResult(h.result);
                  setShowHistory(false);
                }
              }}
            >
              <div style={s.historyTop}>
                <span style={s.historyName}>{h.toolName}</span>
                <span style={{ ...s.historyStatus, color: h.success ? '#22c55e' : '#ef4444' }}>
                  {h.success ? 'OK' : 'ERR'}
                </span>
              </div>
              <div style={s.historyTime}>
                {new Date(h.timestamp).toLocaleTimeString()}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={s.mainLayout}>
          {/* Tool List */}
          <div style={s.toolListPane}>
            <input
              style={s.searchInput}
              placeholder="Filter tools..."
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <div style={s.toolList}>
              {loading && <div style={s.empty}>Loading tools...</div>}
              {!loading && filteredTools.length === 0 && (
                <div style={s.empty}>No tools found</div>
              )}
              {filteredTools.map((tool) => (
                <div
                  key={`${tool.source}-${tool.name}`}
                  style={
                    selectedTool?.name === tool.name && selectedTool?.source === tool.source
                      ? { ...s.toolItem, ...s.toolItemActive }
                      : s.toolItem
                  }
                  onClick={() => {
                    setSelectedTool(tool);
                    setArgsInput('{}');
                    setResult(null);
                  }}
                >
                  <div style={s.toolItemTop}>
                    <span style={s.toolName}>{tool.name}</span>
                    <span
                      style={{
                        ...s.sourceBadge,
                        background:
                          tool.source === 'browser'
                            ? 'rgba(0, 240, 255, 0.12)'
                            : 'rgba(138, 43, 226, 0.12)',
                        color: tool.source === 'browser' ? '#00f0ff' : '#8A2BE2',
                      }}
                    >
                      {tool.source}
                    </span>
                  </div>
                  <div style={s.toolDesc}>{tool.description || 'No description'}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Execution Panel */}
          <div style={s.execPane}>
            {!selectedTool ? (
              <div style={s.emptyExec}>Select a tool to execute</div>
            ) : (
              <>
                <div style={s.execHeader}>
                  <div style={s.execTitle}>{selectedTool.name}</div>
                  <div style={s.execDesc}>{selectedTool.description}</div>
                </div>

                <div style={s.argsSection}>
                  <label style={s.label}>Arguments (JSON)</label>
                  <textarea
                    style={s.argsInput}
                    value={argsInput}
                    onChange={(e) => setArgsInput(e.target.value)}
                    spellCheck={false}
                  />
                </div>

                <button
                  style={s.execBtn}
                  onClick={handleExecute}
                  disabled={executing}
                >
                  {executing ? 'Executing...' : 'Execute Tool'}
                </button>

                {result !== null && (
                  <div style={s.resultSection} ref={resultRef}>
                    <label style={s.label}>Result</label>
                    <pre style={s.resultPre}>{result}</pre>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      <button style={s.refreshBtn} onClick={loadTools} disabled={loading}>
        {loading ? 'Refreshing...' : '↻ Refresh Tools'}
      </button>
    </AppShell>
  );
}

const s: Record<string, React.CSSProperties> = {
  errorBar: {
    background: 'rgba(239, 68, 68, 0.1)',
    border: '1px solid rgba(239, 68, 68, 0.3)',
    borderRadius: 8,
    padding: '10px 16px',
    color: '#ef4444',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  statsBar: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '8px 0',
  },
  stat: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  statLabel: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  statVal: {
    color: '#00f0ff',
    fontSize: 13,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
  },
  histToggle: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '6px 16px',
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'all 0.15s',
  },
  histToggleActive: {
    borderColor: 'rgba(0, 240, 255, 0.3)',
    color: '#00f0ff',
    background: 'rgba(0, 240, 255, 0.06)',
  },
  mainLayout: {
    display: 'flex',
    gap: 14,
    flex: 1,
    minHeight: 380,
  },
  toolListPane: {
    width: 320,
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  searchInput: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#F8FAFC',
    padding: '9px 14px',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
  },
  toolList: {
    overflowY: 'auto',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    maxHeight: 400,
    flex: 1,
  },
  toolItem: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 8,
    padding: '10px 14px',
    cursor: 'pointer',
    transition: 'border-color 0.15s',
  },
  toolItemActive: {
    borderColor: 'rgba(0, 240, 255, 0.3)',
    background: 'rgba(0, 240, 255, 0.04)',
  },
  toolItemTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  toolName: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'JetBrains Mono', monospace",
  },
  sourceBadge: {
    fontSize: 10,
    fontWeight: 600,
    borderRadius: 4,
    padding: '2px 6px',
    fontFamily: "'JetBrains Mono', monospace",
    textTransform: 'uppercase',
    letterSpacing: '0.03em',
  },
  toolDesc: {
    color: '#8888a0',
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.4,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    display: '-webkit-box',
    WebkitLineClamp: 2,
    WebkitBoxOrient: 'vertical',
  },
  execPane: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    minWidth: 0,
  },
  emptyExec: {
    color: '#4a4a62',
    fontSize: 14,
    textAlign: 'center',
    padding: '80px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  execHeader: {
    borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
    paddingBottom: 12,
  },
  execTitle: {
    color: '#00f0ff',
    fontSize: 16,
    fontWeight: 600,
    fontFamily: "'JetBrains Mono', monospace",
    marginBottom: 4,
  },
  execDesc: {
    color: '#8888a0',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.5,
  },
  argsSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  label: {
    color: '#8888a0',
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  argsInput: {
    background: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: 8,
    color: '#F8FAFC',
    padding: '12px 14px',
    fontSize: 13,
    fontFamily: "'JetBrains Mono', monospace",
    outline: 'none',
    minHeight: 80,
    resize: 'vertical',
    width: '100%',
    boxSizing: 'border-box',
  },
  execBtn: {
    background: 'linear-gradient(135deg, #00f0ff, #0090cc)',
    border: 'none',
    borderRadius: 8,
    color: '#000',
    padding: '10px 0',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    width: '100%',
  },
  resultSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    flex: 1,
    minHeight: 0,
  },
  resultPre: {
    background: 'rgba(0, 0, 0, 0.3)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: 8,
    color: '#c8c8d8',
    padding: 14,
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
    overflow: 'auto',
    maxHeight: 200,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    margin: 0,
    lineHeight: 1.5,
  },
  historyList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    maxHeight: 420,
    overflowY: 'auto',
  },
  historyItem: {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 8,
    padding: '10px 14px',
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
  historyTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 2,
  },
  historyName: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 500,
    fontFamily: "'JetBrains Mono', monospace",
  },
  historyStatus: {
    fontSize: 11,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
  },
  historyTime: {
    color: '#4a4a62',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
  },
  empty: {
    color: '#4a4a62',
    fontSize: 13,
    textAlign: 'center',
    padding: '40px 0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  refreshBtn: {
    background: 'none',
    border: '1px solid rgba(255, 255, 255, 0.07)',
    borderRadius: 8,
    color: '#8888a0',
    padding: '8px 16px',
    fontSize: 12,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    alignSelf: 'center',
  },
};

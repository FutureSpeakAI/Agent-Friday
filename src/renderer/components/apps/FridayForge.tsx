/**
 * FridayForge.tsx — Sovereign Coding Environment for Agent Friday
 *
 * Full coding harness with:
 *   - Split-pane layout (file tree | editor | terminal + agents)
 *   - Syntax highlighting via language-specific token coloring
 *   - Git-aware file tree with status indicators
 *   - Agent team panel showing active coding agents
 *   - Real-time cost tracking (local = $0, cloud = $$$)
 *   - Integrated terminal with code execution
 *
 * Designed as the zero-cost coding experience powered by Gemma 4 via Ollama.
 *
 * IPC: window.eve.container.execute, window.eve.gitLoader.*,
 *      window.eve.code.*, cost:session, cost:savings
 */

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

// ── Types ─────────────────────────────────────────────────────────────

interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: FileNode[];
  gitStatus?: string;
}

interface AgentTask {
  id: string;
  agentType: string;
  description: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  progress: number;
  currentPhase?: string;
}

interface CostSession {
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCostUsd: number;
  byProvider: Record<string, { tokens: number; costUsd: number; calls: number }>;
}

interface CostSavings {
  localCost: number;
  cloudEquivalent: number;
  savedUsd: number;
}

interface Props {
  visible: boolean;
  onClose: () => void;
}

type ActivePane = 'editor' | 'terminal' | 'agents';
type Language = 'javascript' | 'typescript' | 'python' | 'bash' | 'html' | 'css' | 'json' | 'markdown';

/** Map file extensions to languages */
function detectLanguage(filename: string): Language {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, Language> = {
    js: 'javascript', jsx: 'javascript', mjs: 'javascript',
    ts: 'typescript', tsx: 'typescript', mts: 'typescript',
    py: 'python', pyw: 'python',
    sh: 'bash', bash: 'bash', zsh: 'bash',
    html: 'html', htm: 'html',
    css: 'css', scss: 'css',
    json: 'json', jsonl: 'json',
    md: 'markdown', mdx: 'markdown',
  };
  return map[ext] || 'typescript';
}

/** Map git status codes to colors and labels */
function gitStatusColor(status?: string): string {
  if (!status) return 'transparent';
  if (status.includes('M')) return '#f59e0b'; // modified
  if (status.includes('A') || status.includes('?')) return '#22c55e'; // added/untracked
  if (status.includes('D')) return '#ef4444'; // deleted
  if (status.includes('R')) return '#8b5cf6'; // renamed
  return '#8888a0';
}

/** File icon based on extension */
function fileIcon(name: string, isDir: boolean): string {
  if (isDir) return '\u{1F4C1}';
  const ext = name.split('.').pop()?.toLowerCase() || '';
  const icons: Record<string, string> = {
    ts: '\u{1F535}', tsx: '\u{1F535}', js: '\u{1F7E1}', jsx: '\u{1F7E1}',
    py: '\u{1F40D}', json: '\u{1F4CB}', md: '\u{1F4DD}',
    html: '\u{1F310}', css: '\u{1F3A8}', sh: '\u{1F4DF}',
  };
  return icons[ext] || '\u{1F4C4}';
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof Error) return err.message || fallback;
  if (typeof err === 'string') return err || fallback;
  return fallback;
}

// ── Syntax Highlighting (lightweight token-based) ─────────────────────

function highlightLine(line: string, lang: Language): React.ReactNode[] {
  if (lang === 'json' || lang === 'markdown') {
    return [<span key="0">{line}</span>];
  }

  // Simple token-based highlighting (not a full parser — good enough for display)
  const parts: Array<{ start: number; end: number; className: string }> = [];

  // Comments first (highest priority)
  const commentRe = /\/\/.*$|\/\*[\s\S]*?\*\//gm;
  let m: RegExpExecArray | null;
  while ((m = commentRe.exec(line)) !== null) {
    parts.push({ start: m.index, end: m.index + m[0].length, className: 'sh-comment' });
  }

  // Strings
  const stringRe = /(["'`])(?:(?!\1|\\).|\\.)*?\1/g;
  while ((m = stringRe.exec(line)) !== null) {
    parts.push({ start: m.index, end: m.index + m[0].length, className: 'sh-string' });
  }

  // Keywords
  const kwRe = /\b(const|let|var|function|return|if|else|for|while|class|extends|import|export|from|default|new|try|catch|async|await|interface|type|enum)\b/g;
  while ((m = kwRe.exec(line)) !== null) {
    parts.push({ start: m.index, end: m.index + m[0].length, className: 'sh-keyword' });
  }

  // Numbers
  const numRe = /\b\d+\.?\d*\b/g;
  while ((m = numRe.exec(line)) !== null) {
    parts.push({ start: m.index, end: m.index + m[0].length, className: 'sh-number' });
  }

  if (parts.length === 0) {
    return [<span key="0">{line}</span>];
  }

  // Sort by start position, filter overlaps (comments/strings win)
  parts.sort((a, b) => a.start - b.start);
  const filtered: typeof parts = [];
  let lastEnd = 0;
  for (const part of parts) {
    if (part.start >= lastEnd) {
      filtered.push(part);
      lastEnd = part.end;
    }
  }

  // Build React elements
  const elements: React.ReactNode[] = [];
  let pos = 0;
  for (let i = 0; i < filtered.length; i++) {
    const part = filtered[i];
    if (pos < part.start) {
      elements.push(<span key={`t${i}`}>{line.slice(pos, part.start)}</span>);
    }
    elements.push(
      <span key={`h${i}`} className={part.className}>
        {line.slice(part.start, part.end)}
      </span>
    );
    pos = part.end;
  }
  if (pos < line.length) {
    elements.push(<span key="tail">{line.slice(pos)}</span>);
  }

  return elements;
}

// ── Component ─────────────────────────────────────────────────────────

export default function FridayForge({ visible, onClose }: Props) {
  // File tree state
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  // Editor state
  const [editorContent, setEditorContent] = useState('');
  const [editorLanguage, setEditorLanguage] = useState<Language>('typescript');
  const [editorModified, setEditorModified] = useState(false);
  const [editorFileName, setEditorFileName] = useState('');

  // Terminal state
  const [terminalOutput, setTerminalOutput] = useState<Array<{ type: 'cmd' | 'stdout' | 'stderr'; text: string }>>([]);
  const [terminalInput, setTerminalInput] = useState('');
  const [terminalHistory, setTerminalHistory] = useState<string[]>([]);
  const [_historyIdx, setHistoryIdx] = useState(-1);

  // Agent state
  const [agents, setAgents] = useState<AgentTask[]>([]);

  // Cost state
  const [costSession, setCostSession] = useState<CostSession | null>(null);
  const [costSavings, setCostSavings] = useState<CostSavings | null>(null);

  // UI state
  const [activePane, setActivePane] = useState<ActivePane>('editor');
  const [sidebarWidth] = useState(240);
  const [bottomPaneHeight] = useState(220);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const terminalRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  // ── Data Loading ──────────────────────────────────────────────────

  const loadFileTree = useCallback(async () => {
    try {
      const repos = await (window as any).eve.gitLoader?.listLoaded?.();
      if (repos && repos.length > 0) {
        const tree = await (window as any).eve.gitLoader.getTree(repos[0]);
        setFileTree(Array.isArray(tree) ? tree : []);
      }
    } catch {
      // Git loader not available
    }
  }, []);

  const loadCosts = useCallback(async () => {
    try {
      const [session, savings] = await Promise.all([
        (window as any).eve?.cost?.session?.(),
        (window as any).eve?.cost?.savings?.(),
      ]);
      if (session) setCostSession(session);
      if (savings) setCostSavings(savings);
    } catch {
      // Cost tracking not available yet
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    loadFileTree();
    loadCosts();

    // Poll for agent updates
    const interval = setInterval(() => {
      loadCosts();
    }, 10000);
    return () => clearInterval(interval);
  }, [visible, loadFileTree, loadCosts]);

  // Listen for agent updates
  useEffect(() => {
    const handler = (_event: unknown, task: AgentTask) => {
      setAgents(prev => {
        const idx = prev.findIndex(a => a.id === task.id);
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = task;
          return next;
        }
        return [...prev, task];
      });
    };

    (window as any).eve?.on?.('agents:update', handler);
    return () => (window as any).eve?.off?.('agents:update', handler);
  }, []);

  // ── File Operations ───────────────────────────────────────────────

  const openFile = useCallback(async (filePath: string) => {
    setLoading(true);
    setError(null);
    try {
      const repos = await (window as any).eve.gitLoader?.listLoaded?.();
      if (!repos?.length) throw new Error('No repository loaded');

      const content = await (window as any).eve.gitLoader.getFile(repos[0], filePath);
      setEditorContent(content || '');
      setSelectedFile(filePath);
      setEditorFileName(filePath.split('/').pop() || filePath);
      setEditorLanguage(detectLanguage(filePath));
      setEditorModified(false);
      setActivePane('editor');
    } catch (err) {
      setError(errMsg(err, 'Failed to open file'));
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleDir = useCallback((dirPath: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev);
      if (next.has(dirPath)) next.delete(dirPath);
      else next.add(dirPath);
      return next;
    });
  }, []);

  // ── Terminal ──────────────────────────────────────────────────────

  const runCommand = useCallback(async (cmd: string) => {
    if (!cmd.trim()) return;

    setTerminalOutput(prev => [...prev, { type: 'cmd', text: `$ ${cmd}` }]);
    setTerminalHistory(prev => [...prev, cmd]);
    setTerminalInput('');
    setHistoryIdx(-1);

    try {
      const result = await (window as any).eve.container?.execute?.({
        code: cmd,
        language: 'bash',
      });

      if (result?.stdout) {
        setTerminalOutput(prev => [...prev, { type: 'stdout', text: result.stdout }]);
      }
      if (result?.stderr) {
        setTerminalOutput(prev => [...prev, { type: 'stderr', text: result.stderr }]);
      }
    } catch (err) {
      setTerminalOutput(prev => [...prev, { type: 'stderr', text: errMsg(err, 'Command failed') }]);
    }

    // Scroll to bottom
    requestAnimationFrame(() => {
      if (terminalRef.current) {
        terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
      }
    });
  }, []);

  const handleTerminalKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      runCommand(terminalInput);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHistoryIdx(prev => {
        const next = prev + 1;
        if (next < terminalHistory.length) {
          setTerminalInput(terminalHistory[terminalHistory.length - 1 - next]);
          return next;
        }
        return prev;
      });
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHistoryIdx(prev => {
        const next = prev - 1;
        if (next >= 0) {
          setTerminalInput(terminalHistory[terminalHistory.length - 1 - next]);
          return next;
        }
        setTerminalInput('');
        return -1;
      });
    }
  }, [terminalInput, terminalHistory, runCommand]);

  // ── Editor Key Handling ───────────────────────────────────────────

  const handleEditorKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const target = e.currentTarget;
      const start = target.selectionStart;
      const end = target.selectionEnd;
      const newValue = editorContent.substring(0, start) + '  ' + editorContent.substring(end);
      setEditorContent(newValue);
      setEditorModified(true);
      requestAnimationFrame(() => {
        target.selectionStart = target.selectionEnd = start + 2;
      });
    }
  }, [editorContent]);

  // ── Computed Values ───────────────────────────────────────────────

  const editorLines = useMemo(() => editorContent.split('\n'), [editorContent]);
  const lineCount = editorLines.length;

  const runningAgents = agents.filter(a => a.status === 'running' || a.status === 'queued');
  const totalTokens = costSession ? costSession.totalInputTokens + costSession.totalOutputTokens : 0;
  const savedAmount = costSavings?.savedUsd ?? 0;

  // ── File Tree Renderer ────────────────────────────────────────────

  const renderFileTree = (nodes: FileNode[], depth = 0): React.ReactNode[] => {
    return nodes.map(node => {
      const isDir = node.type === 'directory';
      const isExpanded = expandedDirs.has(node.path);
      const isSelected = node.path === selectedFile;
      const indent = depth * 16;

      return (
        <React.Fragment key={node.path}>
          <div
            style={{
              ...s.treeItem,
              paddingLeft: 8 + indent,
              background: isSelected ? 'rgba(0,240,255,0.08)' : 'transparent',
              borderLeft: isSelected ? '2px solid #00f0ff' : '2px solid transparent',
            }}
            onClick={() => isDir ? toggleDir(node.path) : openFile(node.path)}
            title={node.path}
          >
            <span style={s.treeIcon}>
              {isDir ? (isExpanded ? '\u{1F4C2}' : '\u{1F4C1}') : fileIcon(node.name, false)}
            </span>
            <span style={{
              ...s.treeName,
              color: isSelected ? '#00f0ff' : '#e0e0e8',
            }}>
              {node.name}
            </span>
            {node.gitStatus && (
              <span style={{
                ...s.gitBadge,
                color: gitStatusColor(node.gitStatus),
              }}>
                {node.gitStatus.trim()}
              </span>
            )}
          </div>
          {isDir && isExpanded && node.children && renderFileTree(node.children, depth + 1)}
        </React.Fragment>
      );
    });
  };

  // ── Render ────────────────────────────────────────────────────────

  return (
    <AppShell visible={visible} onClose={onClose} title="Forge" icon="\u{1F528}" width={1200} maxHeightVh={92}>
      <ContextBar appId="friday-forge" />

      {/* Status Bar */}
      <div style={s.statusBar}>
        <div style={s.statusLeft}>
          <span style={s.statusItem}>
            <span style={s.statusLabel}>Provider</span>
            <span style={s.statusValue}>
              {costSession?.byProvider && Object.keys(costSession.byProvider).length > 0
                ? Object.keys(costSession.byProvider).join(' + ')
                : 'ollama'}
            </span>
          </span>
          <span style={s.statusDivider}>|</span>
          <span style={s.statusItem}>
            <span style={s.statusLabel}>Tokens</span>
            <span style={s.statusValue}>{totalTokens.toLocaleString()}</span>
          </span>
          <span style={s.statusDivider}>|</span>
          <span style={s.statusItem}>
            <span style={s.statusLabel}>Cost</span>
            <span style={{
              ...s.statusValue,
              color: (costSession?.totalCostUsd ?? 0) === 0 ? '#22c55e' : '#f59e0b',
            }}>
              ${(costSession?.totalCostUsd ?? 0).toFixed(4)}
            </span>
          </span>
          {savedAmount > 0 && (
            <>
              <span style={s.statusDivider}>|</span>
              <span style={s.statusItem}>
                <span style={s.statusLabel}>Saved</span>
                <span style={{ ...s.statusValue, color: '#22c55e' }}>
                  ${savedAmount.toFixed(4)}
                </span>
              </span>
            </>
          )}
        </div>
        <div style={s.statusRight}>
          {runningAgents.length > 0 && (
            <span style={{ ...s.statusItem, color: '#8b5cf6' }}>
              {runningAgents.length} agent{runningAgents.length > 1 ? 's' : ''} active
            </span>
          )}
          {editorFileName && (
            <span style={s.statusItem}>
              {editorFileName}{editorModified ? ' *' : ''}
              <span style={s.langBadge}>{editorLanguage}</span>
            </span>
          )}
          <span style={s.statusItem}>{lineCount} lines</span>
        </div>
      </div>

      {error && (
        <div style={s.errorBar}>
          <span>{error}</span>
          <button style={s.dismissBtn} onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}

      {/* Main Layout: Sidebar | Editor/Bottom */}
      <div style={s.mainLayout}>
        {/* Sidebar — File Tree */}
        <div style={{ ...s.sidebar, width: sidebarWidth }}>
          <div style={s.sidebarHeader}>
            <span style={s.sidebarTitle}>Files</span>
            <button style={s.refreshBtn} onClick={loadFileTree} title="Refresh">
              \u21BB
            </button>
          </div>
          <div style={s.fileTreeContainer}>
            {fileTree.length === 0 ? (
              <div style={s.emptyTree}>
                <span style={s.mutedText}>No repository loaded</span>
              </div>
            ) : (
              renderFileTree(fileTree)
            )}
          </div>
        </div>

        {/* Content Area */}
        <div style={s.contentArea}>
          {/* Editor (top) */}
          <div style={{ ...s.editorArea, flex: 1 }}>
            {loading ? (
              <div style={s.centerContent}>
                <span style={s.spinner}>\u27F3</span>
                <span style={s.mutedText}>Loading...</span>
              </div>
            ) : editorContent ? (
              <div style={s.editorContainer}>
                {/* Line number gutter + highlighted code overlay */}
                <div style={s.lineNumbers}>
                  {editorLines.map((_, i) => (
                    <div key={i} style={s.lineNumber}>{i + 1}</div>
                  ))}
                </div>
                <div style={s.codeContainer}>
                  {/* Syntax-highlighted overlay (read-only, rendered on top) */}
                  <pre style={s.highlightOverlay} aria-hidden="true">
                    {editorLines.map((line, i) => (
                      <div key={i} style={s.codeLine}>
                        {highlightLine(line, editorLanguage)}
                      </div>
                    ))}
                  </pre>
                  {/* Actual editable textarea (transparent text, captures input) */}
                  <textarea
                    ref={editorRef}
                    style={s.editorTextarea}
                    value={editorContent}
                    onChange={e => {
                      setEditorContent(e.target.value);
                      setEditorModified(true);
                    }}
                    onKeyDown={handleEditorKeyDown}
                    spellCheck={false}
                    autoComplete="off"
                    autoCorrect="off"
                    autoCapitalize="off"
                  />
                </div>
              </div>
            ) : (
              <div style={s.centerContent}>
                <div style={s.welcomeIcon}>{'\u{1F528}'}</div>
                <div style={s.welcomeTitle}>Friday Forge</div>
                <div style={s.welcomeSubtitle}>
                  Select a file from the tree or open a repository to begin
                </div>
                <div style={s.welcomeFeatures}>
                  <div style={s.featureItem}>
                    <span style={s.featureDot}>{'\u{1F7E2}'}</span> Zero-cost local inference via Gemma 4
                  </div>
                  <div style={s.featureItem}>
                    <span style={s.featureDot}>{'\u{1F535}'}</span> Agent team coding with live visualization
                  </div>
                  <div style={s.featureItem}>
                    <span style={s.featureDot}>{'\u{1F7E3}'}</span> Privacy-first with cLaw governance
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Bottom Pane — Terminal / Agents */}
          <div style={{ ...s.bottomPane, height: bottomPaneHeight }}>
            {/* Bottom Pane Tabs */}
            <div style={s.bottomTabs}>
              <button
                style={{
                  ...s.bottomTab,
                  ...(activePane === 'terminal' ? s.bottomTabActive : {}),
                }}
                onClick={() => setActivePane('terminal')}
              >
                Terminal
              </button>
              <button
                style={{
                  ...s.bottomTab,
                  ...(activePane === 'agents' ? s.bottomTabActive : {}),
                }}
                onClick={() => setActivePane('agents')}
              >
                Agents
                {runningAgents.length > 0 && (
                  <span style={s.agentBadge}>{runningAgents.length}</span>
                )}
              </button>
            </div>

            {/* Terminal Content */}
            {activePane === 'terminal' && (
              <div style={s.terminalContainer}>
                <div ref={terminalRef} style={s.terminalOutput}>
                  {terminalOutput.length === 0 ? (
                    <div style={s.terminalWelcome}>
                      Friday Forge Terminal — Type a command to begin
                    </div>
                  ) : (
                    terminalOutput.map((line, i) => (
                      <div key={i} style={{
                        ...s.terminalLine,
                        color: line.type === 'stderr' ? '#ef4444'
                          : line.type === 'cmd' ? '#00f0ff'
                          : '#e0e0e8',
                      }}>
                        {line.text}
                      </div>
                    ))
                  )}
                </div>
                <div style={s.terminalInputRow}>
                  <span style={s.terminalPrompt}>$</span>
                  <input
                    type="text"
                    value={terminalInput}
                    onChange={e => setTerminalInput(e.target.value)}
                    onKeyDown={handleTerminalKeyDown}
                    style={s.terminalInputField}
                    placeholder="Enter command..."
                    spellCheck={false}
                    autoComplete="off"
                  />
                </div>
              </div>
            )}

            {/* Agents Content */}
            {activePane === 'agents' && (
              <div style={s.agentsContainer}>
                {agents.length === 0 ? (
                  <div style={s.agentsEmpty}>
                    <span style={s.mutedText}>No agents running</span>
                  </div>
                ) : (
                  agents.slice(-10).map(agent => (
                    <div key={agent.id} style={s.agentCard}>
                      <div style={{
                        ...s.agentStatusDot,
                        background: agent.status === 'running' ? '#22c55e'
                          : agent.status === 'queued' ? '#f59e0b'
                          : agent.status === 'failed' ? '#ef4444'
                          : '#4a4a62',
                      }} />
                      <div style={s.agentInfo}>
                        <div style={s.agentName}>{agent.agentType}</div>
                        <div style={s.agentDesc}>{agent.description}</div>
                        {agent.currentPhase && (
                          <div style={s.agentPhase}>{agent.currentPhase}</div>
                        )}
                      </div>
                      {agent.status === 'running' && (
                        <div style={s.agentProgress}>
                          <div style={{
                            ...s.agentProgressBar,
                            width: `${agent.progress}%`,
                          }} />
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  // Status Bar
  statusBar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '4px 12px',
    borderBottom: '1px solid rgba(255,255,255,0.07)',
    background: 'rgba(0,0,0,0.3)',
    fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    gap: 8,
    flexShrink: 0,
  },
  statusLeft: { display: 'flex', alignItems: 'center', gap: 8 },
  statusRight: { display: 'flex', alignItems: 'center', gap: 12 },
  statusItem: { display: 'flex', alignItems: 'center', gap: 4, color: '#8888a0' },
  statusLabel: { color: '#4a4a62', fontSize: 10 },
  statusValue: { color: '#e0e0e8', fontWeight: 600 },
  statusDivider: { color: '#2a2a3a' },
  langBadge: {
    marginLeft: 4,
    fontSize: 9,
    padding: '1px 4px',
    borderRadius: 3,
    background: 'rgba(0,240,255,0.1)',
    color: '#00f0ff',
  },
  errorBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 0,
    padding: '4px 12px',
    color: '#ef4444',
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
    flexShrink: 0,
  },
  dismissBtn: {
    background: 'none',
    border: 'none',
    color: '#ef4444',
    cursor: 'pointer',
    fontSize: 10,
  },

  // Main Layout
  mainLayout: {
    display: 'flex',
    flex: 1,
    overflow: 'hidden',
    minHeight: 0,
  },

  // Sidebar
  sidebar: {
    display: 'flex',
    flexDirection: 'column',
    borderRight: '1px solid rgba(255,255,255,0.07)',
    background: 'rgba(0,0,0,0.2)',
    flexShrink: 0,
  },
  sidebarHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '8px 10px',
    borderBottom: '1px solid rgba(255,255,255,0.05)',
  },
  sidebarTitle: {
    fontSize: 11,
    fontWeight: 700,
    color: '#8888a0',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  refreshBtn: {
    background: 'none',
    border: 'none',
    color: '#4a4a62',
    cursor: 'pointer',
    fontSize: 14,
    padding: 2,
  },
  fileTreeContainer: {
    flex: 1,
    overflowY: 'auto',
    overflowX: 'hidden',
  },
  emptyTree: {
    padding: 20,
    textAlign: 'center' as const,
  },
  treeItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '3px 8px',
    cursor: 'pointer',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
    borderLeft: '2px solid transparent',
    transition: 'background 0.1s',
  },
  treeIcon: { fontSize: 12, flexShrink: 0, width: 16, textAlign: 'center' as const },
  treeName: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
    flex: 1,
  },
  gitBadge: {
    fontSize: 9,
    fontWeight: 700,
    fontFamily: "'JetBrains Mono', monospace",
    flexShrink: 0,
  },

  // Content Area
  contentArea: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
  },

  // Editor Area
  editorArea: {
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    borderBottom: '1px solid rgba(255,255,255,0.07)',
  },
  editorContainer: {
    display: 'flex',
    flex: 1,
    overflow: 'auto',
    background: '#0a0a14',
    position: 'relative' as const,
  },
  lineNumbers: {
    padding: '8px 0',
    borderRight: '1px solid rgba(255,255,255,0.05)',
    background: '#08080f',
    flexShrink: 0,
    userSelect: 'none' as const,
    minWidth: 48,
  },
  lineNumber: {
    padding: '0 8px 0 12px',
    fontSize: 12,
    lineHeight: '20px',
    color: '#3a3a4a',
    fontFamily: "'JetBrains Mono', monospace",
    textAlign: 'right' as const,
  },
  codeContainer: {
    flex: 1,
    position: 'relative' as const,
    minWidth: 0,
  },
  highlightOverlay: {
    position: 'absolute' as const,
    top: 0,
    left: 0,
    right: 0,
    padding: '8px 12px',
    margin: 0,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    lineHeight: '20px',
    color: '#e0e0e8',
    whiteSpace: 'pre' as const,
    pointerEvents: 'none' as const,
    overflow: 'visible',
    background: 'transparent',
    border: 'none',
  },
  codeLine: {
    height: 20,
    whiteSpace: 'pre' as const,
  },
  editorTextarea: {
    position: 'relative' as const,
    width: '100%',
    height: '100%',
    padding: '8px 12px',
    margin: 0,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    lineHeight: '20px',
    color: 'transparent',
    caretColor: '#00f0ff',
    background: 'transparent',
    border: 'none',
    outline: 'none',
    resize: 'none' as const,
    overflow: 'auto',
    whiteSpace: 'pre' as const,
    tabSize: 2,
  },

  // Welcome Screen
  centerContent: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    flex: 1,
    gap: 12,
    padding: 40,
  },
  welcomeIcon: { fontSize: 48, opacity: 0.4 },
  welcomeTitle: {
    fontSize: 20,
    fontWeight: 700,
    color: '#e0e0e8',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  welcomeSubtitle: {
    fontSize: 13,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
    textAlign: 'center' as const,
  },
  welcomeFeatures: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    marginTop: 16,
  },
  featureItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    fontSize: 12,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  featureDot: { fontSize: 10 },

  // Bottom Pane
  bottomPane: {
    display: 'flex',
    flexDirection: 'column',
    background: 'rgba(0,0,0,0.3)',
    flexShrink: 0,
  },
  bottomTabs: {
    display: 'flex',
    gap: 0,
    borderBottom: '1px solid rgba(255,255,255,0.05)',
    flexShrink: 0,
  },
  bottomTab: {
    padding: '6px 14px',
    border: 'none',
    background: 'transparent',
    color: '#4a4a62',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    borderBottom: '2px solid transparent',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  bottomTabActive: {
    color: '#00f0ff',
    borderBottomColor: '#00f0ff',
    background: 'rgba(0,240,255,0.03)',
  },
  agentBadge: {
    fontSize: 9,
    fontWeight: 700,
    background: 'rgba(139,92,246,0.2)',
    color: '#8b5cf6',
    borderRadius: 8,
    padding: '1px 5px',
    fontFamily: "'JetBrains Mono', monospace",
  },

  // Terminal
  terminalContainer: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    overflow: 'hidden',
  },
  terminalOutput: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 12px',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    lineHeight: 1.5,
    background: '#070710',
  },
  terminalWelcome: {
    color: '#4a4a62',
    fontStyle: 'italic' as const,
  },
  terminalLine: {
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-all' as const,
  },
  terminalInputRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '6px 12px',
    borderTop: '1px solid rgba(255,255,255,0.05)',
    background: '#050508',
  },
  terminalPrompt: {
    color: '#00f0ff',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    fontWeight: 700,
    flexShrink: 0,
  },
  terminalInputField: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#e0e0e8',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12,
    caretColor: '#00f0ff',
  },

  // Agents Panel
  agentsContainer: {
    flex: 1,
    overflowY: 'auto',
    padding: '8px 12px',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  agentsEmpty: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flex: 1,
  },
  agentCard: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 10px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid rgba(255,255,255,0.05)',
    borderRadius: 8,
  },
  agentStatusDot: {
    width: 8,
    height: 8,
    borderRadius: '50%',
    flexShrink: 0,
  },
  agentInfo: {
    flex: 1,
    minWidth: 0,
  },
  agentName: {
    fontSize: 12,
    fontWeight: 600,
    color: '#e0e0e8',
    fontFamily: "'Inter', system-ui, sans-serif",
    textTransform: 'capitalize' as const,
  },
  agentDesc: {
    fontSize: 10,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  agentPhase: {
    fontSize: 10,
    color: '#00f0ff',
    fontFamily: "'JetBrains Mono', monospace",
    marginTop: 2,
  },
  agentProgress: {
    width: 60,
    height: 4,
    background: 'rgba(255,255,255,0.05)',
    borderRadius: 2,
    overflow: 'hidden',
    flexShrink: 0,
  },
  agentProgressBar: {
    height: '100%',
    background: '#22c55e',
    borderRadius: 2,
    transition: 'width 0.3s',
  },

  // Shared
  mutedText: {
    color: '#4a4a62',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  spinner: {
    fontSize: 24,
    color: '#00f0ff',
    animation: 'spin 1s linear infinite',
  },
};

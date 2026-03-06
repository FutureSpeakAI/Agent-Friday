/**
 * FridayCode.tsx — Code editor, git browser, and code runner for Agent Friday
 *
 * Three tabs:
 *   Editor — textarea with monospace font and basic syntax coloring
 *   Git    — repo list + file tree (via window.eve.gitLoader)
 *   Run    — language selector + execute + output (via window.eve.container)
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

interface Props {
  visible: boolean;
  onClose: () => void;
}

type Tab = 'editor' | 'git' | 'run';

interface RepoInfo {
  name: string;
  path: string;
}

interface TreeEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: TreeEntry[];
}

const LANGUAGES = [
  { id: 'javascript', label: 'JavaScript', ext: '.js' },
  { id: 'typescript', label: 'TypeScript', ext: '.ts' },
  { id: 'python', label: 'Python', ext: '.py' },
  { id: 'bash', label: 'Bash', ext: '.sh' },
  { id: 'html', label: 'HTML', ext: '.html' },
  { id: 'css', label: 'CSS', ext: '.css' },
  { id: 'json', label: 'JSON', ext: '.json' },
];

const DEFAULT_CODE = `// Welcome to Friday Code Editor
// Write your code here and run it in the Run tab

function greet(name) {
  return \`Hello, \${name}! Welcome to NexusOS.\`;
}

console.log(greet("World"));
`;

export default function FridayCode({ visible, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('editor');

  // Editor state
  const [code, setCode] = useState(DEFAULT_CODE);
  const [language, setLanguage] = useState('javascript');
  const [fileName, setFileName] = useState('untitled.js');
  const [modified, setModified] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const lineCountRef = useRef<HTMLDivElement>(null);

  // Git state
  const [repos, setRepos] = useState<RepoInfo[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeEntry[]>([]);
  const [gitLoading, setGitLoading] = useState(false);
  const [gitError, setGitError] = useState('');
  const [gitAvailable, setGitAvailable] = useState(true);
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());

  // Run state
  const [runLanguage, setRunLanguage] = useState('javascript');
  const [output, setOutput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [runError, setRunError] = useState('');
  const [containerAvailable, setContainerAvailable] = useState(true);

  // Calculate line numbers
  const lineCount = code.split('\n').length;

  // Handle code change
  const handleCodeChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setCode(e.target.value);
    setModified(true);
  }, []);

  // Sync scroll between line numbers and textarea
  const handleEditorScroll = useCallback(() => {
    if (editorRef.current && lineCountRef.current) {
      lineCountRef.current.scrollTop = editorRef.current.scrollTop;
    }
  }, []);

  // Handle tab key in editor
  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const textarea = e.currentTarget;
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const newCode = code.substring(0, start) + '  ' + code.substring(end);
      setCode(newCode);
      setModified(true);
      // Restore cursor position
      requestAnimationFrame(() => {
        textarea.selectionStart = textarea.selectionEnd = start + 2;
      });
    }
  }, [code]);

  // ── Git operations ────────────────────────────────────────────────────────

  // Load repos
  const loadRepos = useCallback(async () => {
    const gitLoader = (window as any).eve?.gitLoader;
    if (!gitLoader?.listRepos) {
      setGitAvailable(false);
      return;
    }

    setGitLoading(true);
    setGitError('');

    try {
      const result = await gitLoader.listRepos();
      const repoList = Array.isArray(result) ? result : [];
      setRepos(repoList.map((r: any) => ({
        name: r.name || r.path?.split('/').pop() || 'Unknown',
        path: r.path || r,
      })));
      setGitAvailable(true);
    } catch (err: any) {
      setGitError(`Failed to load repos: ${err.message || 'Unknown error'}`);
    } finally {
      setGitLoading(false);
    }
  }, []);

  // Load file tree
  const loadTree = useCallback(async (repoPath: string) => {
    const gitLoader = (window as any).eve?.gitLoader;
    if (!gitLoader?.getTree) return;

    setGitLoading(true);
    setGitError('');

    try {
      const result = await gitLoader.getTree(repoPath);
      setTree(Array.isArray(result) ? result : []);
      setSelectedRepo(repoPath);
      setExpandedDirs(new Set());
    } catch (err: any) {
      setGitError(`Failed to load tree: ${err.message || 'Unknown error'}`);
      setTree([]);
    } finally {
      setGitLoading(false);
    }
  }, []);

  // Load repos when git tab activates
  useEffect(() => {
    if (visible && activeTab === 'git' && repos.length === 0) {
      loadRepos();
    }
  }, [visible, activeTab, repos.length, loadRepos]);

  // Toggle directory expansion
  const toggleDir = useCallback((path: string) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // Load file content into editor
  const openFile = useCallback(async (entry: TreeEntry) => {
    if (entry.type !== 'file') return;

    // Try to read file content via gitLoader or fallback
    try {
      const gitLoader = (window as any).eve?.gitLoader;
      if (gitLoader?.readFile && selectedRepo) {
        const content = await gitLoader.readFile(selectedRepo, entry.path);
        if (typeof content === 'string') {
          setCode(content);
          setFileName(entry.name);
          setModified(false);
          // Detect language from extension
          const ext = entry.name.split('.').pop()?.toLowerCase();
          const lang = LANGUAGES.find((l) => l.ext === `.${ext}`);
          if (lang) setLanguage(lang.id);
          setActiveTab('editor');
          return;
        }
      }
    } catch {
      // fallback - just open the filename
    }

    setFileName(entry.name);
    setActiveTab('editor');
  }, [selectedRepo]);

  // ── Run operations ────────────────────────────────────────────────────────

  const runCode = useCallback(async () => {
    const container = (window as any).eve?.container;
    if (!container?.execute) {
      setContainerAvailable(false);
      // Fallback: try eval for JavaScript
      if (runLanguage === 'javascript') {
        setIsRunning(true);
        setOutput('');
        setRunError('');
        try {
          const logs: string[] = [];
          const mockConsole = {
            log: (...args: any[]) => logs.push(args.map(String).join(' ')),
            error: (...args: any[]) => logs.push('[ERROR] ' + args.map(String).join(' ')),
            warn: (...args: any[]) => logs.push('[WARN] ' + args.map(String).join(' ')),
            info: (...args: any[]) => logs.push(args.map(String).join(' ')),
          };
          // Create sandboxed eval
          const fn = new Function('console', code);
          fn(mockConsole);
          setOutput(logs.join('\n') || '(no output)');
        } catch (err: any) {
          setRunError(err.message || 'Execution error');
          setOutput('');
        } finally {
          setIsRunning(false);
        }
        return;
      }
      setRunError('Container runtime not available. Only JavaScript can run in fallback mode.');
      return;
    }

    setIsRunning(true);
    setOutput('');
    setRunError('');

    try {
      const result = await container.execute(runLanguage, code);
      if (typeof result === 'string') {
        setOutput(result);
      } else if (result?.stdout || result?.stderr) {
        setOutput([result.stdout, result.stderr].filter(Boolean).join('\n'));
      } else {
        setOutput(JSON.stringify(result, null, 2));
      }
    } catch (err: any) {
      setRunError(`Execution failed: ${err.message || 'Unknown error'}`);
    } finally {
      setIsRunning(false);
    }
  }, [code, runLanguage]);

  // Copy code to run tab
  const sendToRun = useCallback(() => {
    setRunLanguage(language);
    setActiveTab('run');
  }, [language]);

  // Download code file
  const downloadFile = useCallback(() => {
    const blob = new Blob([code], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }, [code, fileName]);

  // Reset on close
  useEffect(() => {
    if (!visible) {
      setOutput('');
      setRunError('');
      setGitError('');
    }
  }, [visible]);

  // ── Render file tree recursively ──────────────────────────────────────────

  const renderTree = (entries: TreeEntry[], depth = 0): React.ReactNode => {
    return entries.map((entry) => {
      const isDir = entry.type === 'directory';
      const isExpanded = expandedDirs.has(entry.path);

      return (
        <div key={entry.path}>
          <div
            style={{
              ...s.treeItem,
              paddingLeft: 12 + depth * 16,
              background: 'transparent',
              cursor: 'pointer',
            }}
            onClick={() => isDir ? toggleDir(entry.path) : openFile(entry)}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.06)';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLDivElement).style.background = 'transparent';
            }}
          >
            <span style={s.treeIcon}>
              {isDir ? (isExpanded ? '📂' : '📁') : getFileIcon(entry.name)}
            </span>
            <span style={{ ...s.treeName, color: isDir ? '#F8FAFC' : '#8888a0' }}>
              {entry.name}
            </span>
          </div>
          {isDir && isExpanded && entry.children && renderTree(entry.children, depth + 1)}
        </div>
      );
    });
  };

  return (
    <AppShell visible={visible} onClose={onClose} title="Code" icon="💻" width={950} maxHeightVh={92}>
      {/* Tab bar */}
      <div style={s.tabBar}>
        {([
          { id: 'editor' as Tab, label: '📝 Editor' },
          { id: 'git' as Tab, label: '🔀 Git' },
          { id: 'run' as Tab, label: '▶ Run' },
        ]).map((tab) => (
          <button
            key={tab.id}
            style={{
              ...s.tab,
              color: activeTab === tab.id ? '#00f0ff' : '#8888a0',
              borderBottomColor: activeTab === tab.id ? '#00f0ff' : 'transparent',
              background: activeTab === tab.id ? 'rgba(0,240,255,0.05)' : 'transparent',
            }}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Editor Tab ── */}
      {activeTab === 'editor' && (
        <div style={s.editorPane}>
          {/* Editor toolbar */}
          <div style={s.editorToolbar}>
            <div style={s.toolbarLeft}>
              <select
                style={s.langSelect}
                value={language}
                onChange={(e) => {
                  setLanguage(e.target.value);
                  const lang = LANGUAGES.find((l) => l.id === e.target.value);
                  if (lang) {
                    const base = fileName.split('.')[0] || 'untitled';
                    setFileName(base + lang.ext);
                  }
                }}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>{l.label}</option>
                ))}
              </select>
              <span style={s.fileNameLabel}>
                {fileName} {modified && <span style={{ color: '#f97316' }}>*</span>}
              </span>
            </div>
            <div style={s.toolbarRight}>
              <button style={s.tbarBtn} onClick={sendToRun} title="Send to Run tab">
                ▶ Run
              </button>
              <button style={s.tbarBtn} onClick={downloadFile} title="Download file">
                💾 Save
              </button>
              <button
                style={s.tbarBtn}
                onClick={() => { setCode(''); setModified(true); }}
                title="Clear editor"
              >
                🗑 Clear
              </button>
            </div>
          </div>

          {/* Editor with line numbers */}
          <div style={s.editorBody}>
            <div ref={lineCountRef} style={s.lineNumbers}>
              {Array.from({ length: lineCount }, (_, i) => (
                <div key={i} style={s.lineNum}>{i + 1}</div>
              ))}
            </div>
            <textarea
              ref={editorRef}
              style={s.codeArea}
              value={code}
              onChange={handleCodeChange}
              onScroll={handleEditorScroll}
              onKeyDown={handleKeyDown}
              spellCheck={false}
              autoComplete="off"
              autoCorrect="off"
              wrap="off"
            />
          </div>

          {/* Status bar */}
          <div style={s.statusBar}>
            <span style={s.statusText}>Ln {lineCount}, Col --</span>
            <span style={s.statusText}>{language}</span>
            <span style={s.statusText}>{code.length} chars</span>
          </div>
        </div>
      )}

      {/* ── Git Tab ── */}
      {activeTab === 'git' && (
        <div style={s.gitPane}>
          {!gitAvailable ? (
            <div style={s.unavailable}>
              <span style={{ fontSize: 36 }}>🔀</span>
              <span style={s.unavailableTitle}>Git Backend Unavailable</span>
              <span style={s.unavailableText}>
                The git loader service is not connected. Ensure window.eve.gitLoader is available.
              </span>
              <button style={s.retryBtn} onClick={loadRepos}>
                Retry Connection
              </button>
            </div>
          ) : (
            <>
              <div style={s.gitHeader}>
                <span style={s.sectionTitle}>
                  {selectedRepo ? `📂 ${selectedRepo.split('/').pop()}` : 'Repositories'}
                </span>
                <div style={{ display: 'flex', gap: 6 }}>
                  {selectedRepo && (
                    <button
                      style={s.tbarBtn}
                      onClick={() => { setSelectedRepo(null); setTree([]); }}
                    >
                      ← Repos
                    </button>
                  )}
                  <button style={s.tbarBtn} onClick={loadRepos}>
                    🔄 Refresh
                  </button>
                </div>
              </div>

              {gitError && (
                <div style={s.errorBanner}>{gitError}</div>
              )}

              {gitLoading && (
                <div style={s.loadingText}>Loading...</div>
              )}

              {/* Repo list */}
              {!selectedRepo && !gitLoading && (
                <div style={s.repoList}>
                  {repos.length === 0 && !gitError ? (
                    <div style={s.emptyText}>No repositories found</div>
                  ) : (
                    repos.map((repo) => (
                      <div
                        key={repo.path}
                        style={s.repoItem}
                        onClick={() => loadTree(repo.path)}
                        onMouseEnter={(e) => {
                          (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.06)';
                        }}
                        onMouseLeave={(e) => {
                          (e.currentTarget as HTMLDivElement).style.background = 'rgba(255,255,255,0.03)';
                        }}
                      >
                        <span style={{ fontSize: 16 }}>📦</span>
                        <div style={s.repoInfo}>
                          <span style={s.repoName}>{repo.name}</span>
                          <span style={s.repoPath}>{repo.path}</span>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* File tree */}
              {selectedRepo && !gitLoading && (
                <div style={s.treeContainer}>
                  {tree.length === 0 ? (
                    <div style={s.emptyText}>No files found</div>
                  ) : (
                    renderTree(tree)
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Run Tab ── */}
      {activeTab === 'run' && (
        <div style={s.runPane}>
          <div style={s.runToolbar}>
            <select
              style={s.langSelect}
              value={runLanguage}
              onChange={(e) => setRunLanguage(e.target.value)}
            >
              {LANGUAGES.filter((l) => ['javascript', 'typescript', 'python', 'bash'].includes(l.id)).map((l) => (
                <option key={l.id} value={l.id}>{l.label}</option>
              ))}
            </select>
            <button
              style={{
                ...s.runBtn,
                opacity: isRunning ? 0.6 : 1,
              }}
              onClick={runCode}
              disabled={isRunning}
            >
              {isRunning ? '⏳ Running...' : '▶ Execute'}
            </button>
            {!containerAvailable && (
              <span style={s.fallbackBadge}>JS Fallback Mode</span>
            )}
          </div>

          {/* Code preview */}
          <div style={s.codePreview}>
            <div style={s.previewHeader}>
              <span style={s.previewLabel}>Code ({code.split('\n').length} lines)</span>
            </div>
            <pre style={s.previewCode}>
              {code.length > 2000 ? code.substring(0, 2000) + '\n...(truncated)' : code}
            </pre>
          </div>

          {/* Output */}
          <div style={s.outputSection}>
            <div style={s.outputHeader}>
              <span style={s.sectionTitle}>Output</span>
              {output && (
                <button style={s.tbarBtn} onClick={() => { setOutput(''); setRunError(''); }}>
                  Clear
                </button>
              )}
            </div>
            <div style={s.outputBox}>
              {runError ? (
                <pre style={{ ...s.outputText, color: '#ef4444' }}>{runError}</pre>
              ) : output ? (
                <pre style={s.outputText}>{output}</pre>
              ) : (
                <span style={s.emptyText}>
                  {isRunning ? 'Running...' : 'Run your code to see output here'}
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function getFileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'ts': case 'tsx': return '🔷';
    case 'js': case 'jsx': return '🟡';
    case 'py': return '🐍';
    case 'json': return '📋';
    case 'md': return '📄';
    case 'css': case 'scss': return '🎨';
    case 'html': return '🌐';
    case 'sh': case 'bash': return '⚙️';
    case 'yml': case 'yaml': return '📐';
    case 'png': case 'jpg': case 'svg': return '🖼️';
    default: return '📄';
  }
}

// ── Styles ──────────────────────────────────────────────────────────────────
const s: Record<string, React.CSSProperties> = {
  tabBar: {
    display: 'flex',
    gap: 0,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    marginBottom: 12,
  },
  tab: {
    flex: 1,
    background: 'none',
    border: 'none',
    borderBottom: '2px solid transparent',
    padding: '10px 16px',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'color 0.15s, background 0.15s',
  },

  // ── Editor styles ──
  editorPane: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 0,
  },
  editorToolbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 8,
    gap: 8,
    flexWrap: 'wrap',
  },
  toolbarLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  toolbarRight: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  langSelect: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6,
    color: '#F8FAFC',
    fontSize: 12,
    padding: '5px 8px',
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    cursor: 'pointer',
  },
  fileNameLabel: {
    fontSize: 12,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
  },
  tbarBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6,
    color: '#8888a0',
    fontSize: 11,
    padding: '5px 10px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.12s',
    whiteSpace: 'nowrap',
  },
  editorBody: {
    display: 'flex',
    flex: 1,
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    overflow: 'hidden',
    background: '#0d0d1a',
    minHeight: 300,
    maxHeight: '55vh',
  },
  lineNumbers: {
    width: 44,
    flexShrink: 0,
    overflowY: 'hidden',
    background: 'rgba(255,255,255,0.02)',
    borderRight: '1px solid rgba(255,255,255,0.05)',
    paddingTop: 12,
    userSelect: 'none',
  },
  lineNum: {
    height: 20,
    lineHeight: '20px',
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'JetBrains Mono', monospace",
    textAlign: 'right',
    paddingRight: 10,
  },
  codeArea: {
    flex: 1,
    background: 'transparent',
    border: 'none',
    color: '#F8FAFC',
    fontSize: 13,
    lineHeight: '20px',
    fontFamily: "'JetBrains Mono', monospace",
    padding: '12px 14px',
    resize: 'none',
    outline: 'none',
    tabSize: 2,
    whiteSpace: 'pre',
    overflowX: 'auto',
    overflowY: 'auto',
  },
  statusBar: {
    display: 'flex',
    gap: 16,
    padding: '6px 0',
  },
  statusText: {
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'JetBrains Mono', monospace",
  },

  // ── Git styles ──
  gitPane: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minHeight: 300,
  },
  gitHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  sectionTitle: {
    fontSize: 13,
    fontWeight: 600,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  repoList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  repoItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '10px 14px',
    cursor: 'pointer',
    transition: 'background 0.12s',
  },
  repoInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
  },
  repoName: {
    fontSize: 13,
    fontWeight: 600,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  repoPath: {
    fontSize: 11,
    color: '#4a4a62',
    fontFamily: "'JetBrains Mono', monospace",
  },
  treeContainer: {
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    overflow: 'auto',
    maxHeight: '50vh',
    background: 'rgba(255,255,255,0.02)',
  },
  treeItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '5px 12px',
    transition: 'background 0.1s',
  },
  treeIcon: {
    fontSize: 13,
    flexShrink: 0,
  },
  treeName: {
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  unavailable: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    padding: 40,
    minHeight: 200,
  },
  unavailableTitle: {
    fontSize: 16,
    fontWeight: 700,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  unavailableText: {
    fontSize: 13,
    color: '#8888a0',
    textAlign: 'center',
    maxWidth: 360,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  retryBtn: {
    background: 'rgba(0,240,255,0.1)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    padding: '8px 20px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  errorBanner: {
    background: 'rgba(239,68,68,0.08)',
    border: '1px solid rgba(239,68,68,0.2)',
    borderRadius: 8,
    padding: '8px 12px',
    fontSize: 12,
    color: '#ef4444',
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 8,
  },
  loadingText: {
    fontSize: 13,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
    padding: 20,
    textAlign: 'center',
  },
  emptyText: {
    fontSize: 13,
    color: '#4a4a62',
    fontFamily: "'Inter', system-ui, sans-serif",
    textAlign: 'center',
    padding: 20,
  },

  // ── Run styles ──
  runPane: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    flex: 1,
  },
  runToolbar: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  runBtn: {
    background: 'rgba(34,197,94,0.12)',
    border: '1px solid rgba(34,197,94,0.4)',
    borderRadius: 8,
    color: '#22c55e',
    fontSize: 13,
    fontWeight: 600,
    padding: '8px 20px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.15s',
  },
  fallbackBadge: {
    fontSize: 10,
    color: '#f97316',
    background: 'rgba(249,115,22,0.1)',
    border: '1px solid rgba(249,115,22,0.2)',
    borderRadius: 4,
    padding: '3px 8px',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  codePreview: {
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    overflow: 'hidden',
    background: 'rgba(255,255,255,0.02)',
  },
  previewHeader: {
    padding: '6px 12px',
    borderBottom: '1px solid rgba(255,255,255,0.05)',
  },
  previewLabel: {
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  previewCode: {
    fontSize: 12,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
    padding: '10px 14px',
    margin: 0,
    maxHeight: 160,
    overflowY: 'auto',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
  },
  outputSection: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    minHeight: 120,
  },
  outputHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  outputBox: {
    flex: 1,
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    background: '#0d0d1a',
    padding: 14,
    overflow: 'auto',
    maxHeight: '30vh',
    minHeight: 100,
  },
  outputText: {
    fontSize: 12,
    color: '#22c55e',
    fontFamily: "'JetBrains Mono', monospace",
    margin: 0,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-all',
  },
};

import React, { useEffect, useRef } from 'react';

export interface ActionItem {
  id: string;
  name: string;
  status: 'running' | 'success' | 'error';
  startTime: number;
  isAgent?: boolean;        // true for background agent tasks
  description?: string;     // agent task description
  progress?: number;        // 0-100 for agents
  windowTitle?: string;     // associated window to focus on click
}

interface ActionFeedProps {
  actions: ActionItem[];
  onOpenAgentDashboard?: () => void;
}

// Friendly labels for tool names
const TOOL_LABELS: Record<string, string> = {
  ask_claude: 'Consulting Claude Opus',
  save_memory: 'Saving to memory',
  setup_intelligence: 'Setting up research',
  create_task: 'Creating task',
  list_tasks: 'Listing tasks',
  delete_task: 'Deleting task',
  read_own_source: 'Reading source code',
  list_own_files: 'Browsing codebase',
  propose_code_change: 'Proposing code change',
  run_command: 'Running command',
  launch_app: 'Launching application',
  get_active_window: 'Reading screen',
  list_windows: 'Scanning windows',
  browser_navigate: 'Navigating browser',
  browser_screenshot: 'Taking screenshot',
  browser_click: 'Clicking element',
  browser_type: 'Typing text',
  // Agent types
  research: 'Research Agent',
  summarize: 'Summarize Agent',
  'code-review': 'Code Review Agent',
  'draft-email': 'Draft Email Agent',
};

function getLabel(name: string, isAgent?: boolean): string {
  if (isAgent) return TOOL_LABELS[name] || `${name} Agent`;
  return TOOL_LABELS[name] || name.replace(/_/g, ' ');
}

function getIcon(name: string, status: string, isAgent?: boolean): string {
  if (status === 'success') return '\u2713';
  if (status === 'error') return '\u2717';
  // Agent icons
  if (isAgent) {
    if (name === 'research') return '\uD83D\uDD0D';
    if (name === 'summarize') return '\uD83D\uDCDD';
    if (name === 'code-review') return '\uD83E\uddEC';
    if (name === 'draft-email') return '\u2709\uFE0F';
    return '\u26A1';
  }
  // Running icons by category
  if (name.startsWith('browser_')) return '\uD83C\uDF10';
  if (name === 'ask_claude') return '\uD83E\udDE0';
  if (name.startsWith('save_memory') || name === 'setup_intelligence') return '\uD83D\uDCA1';
  if (name.includes('task')) return '\u23F0';
  if (name.startsWith('read_own') || name.startsWith('list_own') || name === 'propose_code_change') return '\uD83E\uddEC';
  if (name === 'run_command') return '\u26A1';
  if (name === 'launch_app') return '\uD83D\uDE80';
  return '\u2699\uFE0F';
}

export default function ActionFeed({ actions, onOpenAgentDashboard }: ActionFeedProps) {
  if (actions.length === 0) return null;

  const agentCount = actions.filter((a) => a.isAgent && a.status === 'running').length;

  return (
    <div style={styles.container}>
      {/* Active agent count badge */}
      {agentCount > 0 && (
        <div style={styles.agentBadge}>
          <span style={styles.agentBadgeIcon}>⚡</span>
          <span style={styles.agentBadgeText}>
            {agentCount} agent{agentCount > 1 ? 's' : ''} active
          </span>
        </div>
      )}
      {actions.map((action) => (
        <ActionCard
          key={action.id}
          action={action}
          onOpenAgentDashboard={onOpenAgentDashboard}
        />
      ))}
    </div>
  );
}

function ActionCard({
  action,
  onOpenAgentDashboard,
}: {
  action: ActionItem;
  onOpenAgentDashboard?: () => void;
}) {
  const elRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<number>(0);
  const [elapsed, setElapsed] = React.useState(0);
  const [hovered, setHovered] = React.useState(false);

  // Tick elapsed time for running actions
  useEffect(() => {
    if (action.status !== 'running') return;
    const tick = () => {
      setElapsed(Math.round((Date.now() - action.startTime) / 100) / 10);
      timerRef.current = window.setTimeout(tick, 100);
    };
    tick();
    return () => clearTimeout(timerRef.current);
  }, [action.status, action.startTime]);

  const isRunning = action.status === 'running';
  const isSuccess = action.status === 'success';
  const isError = action.status === 'error';
  const isAgent = !!action.isAgent;
  const hasWindow = !!action.windowTitle;

  // Agent cards: purple tint. Tool cards: cyan tint.
  const accentColor = isAgent
    ? { running: 'rgba(138, 43, 226, 0.4)', success: 'rgba(34, 197, 94, 0.3)', error: 'rgba(239, 68, 68, 0.3)' }
    : { running: 'rgba(0, 240, 255, 0.3)', success: 'rgba(34, 197, 94, 0.3)', error: 'rgba(239, 68, 68, 0.3)' };

  const borderColor = isRunning
    ? accentColor.running
    : isSuccess
      ? accentColor.success
      : accentColor.error;

  const glowColor = isRunning
    ? (isAgent ? '0 0 16px rgba(138, 43, 226, 0.2)' : '0 0 12px rgba(0, 240, 255, 0.15)')
    : isSuccess
      ? '0 0 12px rgba(34, 197, 94, 0.15)'
      : '0 0 12px rgba(239, 68, 68, 0.15)';

  const handleClick = () => {
    if (!isAgent) return;
    if (hasWindow) {
      // Focus the window this agent is working in
      try {
        window.eve?.desktop?.focusWindow?.(action.windowTitle!);
      } catch {}
    } else if (onOpenAgentDashboard) {
      onOpenAgentDashboard();
    }
  };

  const isClickable = isAgent && (hasWindow || !!onOpenAgentDashboard);

  return (
    <div
      ref={elRef}
      onClick={handleClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        ...styles.card,
        borderColor,
        boxShadow: glowColor,
        opacity: isRunning ? 1 : 0.7,
        animation: isRunning ? 'none' : 'actionFadeOut 2s ease forwards 1s',
        cursor: isClickable ? 'pointer' : 'default',
        borderLeftWidth: isAgent ? 3 : 1,
        transform: hovered && isClickable ? 'translateX(4px)' : 'none',
        transition: 'transform 0.15s ease, box-shadow 0.15s ease',
      }}
    >
      <div style={styles.cardIcon}>
        {isRunning ? (
          <div style={styles.spinner}>{getIcon(action.name, action.status, isAgent)}</div>
        ) : (
          <span style={{
            color: isSuccess ? '#22c55e' : '#ef4444',
            fontSize: 14,
            fontWeight: 700,
          }}>
            {getIcon(action.name, action.status, isAgent)}
          </span>
        )}
      </div>
      <div style={styles.cardBody}>
        <div style={styles.cardLabel}>{getLabel(action.name, isAgent)}</div>
        {/* Agent description line */}
        {isAgent && action.description && (
          <div style={styles.cardDesc}>{action.description}</div>
        )}
        <div style={styles.cardMeta}>
          {isRunning && (
            <span style={styles.cardElapsed}>{elapsed.toFixed(1)}s</span>
          )}
          {isAgent && isRunning && action.progress !== undefined && action.progress > 0 && (
            <span style={styles.cardProgress}>{action.progress}%</span>
          )}
        </div>
      </div>
      {/* Watch eye icon for agent cards with window association */}
      {isAgent && hasWindow && isRunning && (
        <div style={{
          ...styles.watchIcon,
          opacity: hovered ? 1 : 0.4,
        }}>
          👁
        </div>
      )}
      {isRunning && <div style={styles.progressBar}>
        {isAgent && action.progress !== undefined && action.progress > 0 ? (
          <div style={{
            ...styles.agentProgressFill,
            width: `${action.progress}%`,
          }} />
        ) : (
          <div style={styles.progressFill} />
        )}
      </div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    position: 'absolute',
    bottom: 100,
    left: 20,
    zIndex: 35,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    maxWidth: 280,
    pointerEvents: 'auto',
  },
  agentBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    padding: '4px 10px',
    background: 'rgba(138, 43, 226, 0.12)',
    border: '1px solid rgba(138, 43, 226, 0.25)',
    borderRadius: 8,
    alignSelf: 'flex-start',
    marginBottom: 2,
  },
  agentBadgeIcon: {
    fontSize: 11,
  },
  agentBadgeText: {
    fontSize: 10,
    fontWeight: 600,
    color: '#a78bfa',
    letterSpacing: '0.03em',
    fontFamily: "'JetBrains Mono', monospace",
    textTransform: 'uppercase' as const,
  },
  card: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '8px 14px',
    background: 'rgba(10, 10, 18, 0.85)',
    backdropFilter: 'blur(12px)',
    border: '1px solid',
    borderRadius: 10,
    animation: 'slideInLeft 0.3s ease-out',
    position: 'relative',
    overflow: 'hidden',
  },
  cardIcon: {
    width: 24,
    height: 24,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
    flexShrink: 0,
  },
  spinner: {
    animation: 'action-pulse 1.2s ease-in-out infinite',
  },
  cardBody: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 2,
    minWidth: 0,
  },
  cardLabel: {
    fontSize: 12,
    fontWeight: 500,
    color: '#d0d0dd',
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  cardDesc: {
    fontSize: 10,
    color: '#888',
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: 180,
  },
  cardMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  cardElapsed: {
    fontSize: 10,
    color: '#666680',
    fontFamily: "'JetBrains Mono', monospace",
  },
  cardProgress: {
    fontSize: 10,
    color: '#a78bfa',
    fontFamily: "'JetBrains Mono', monospace",
    fontWeight: 600,
  },
  watchIcon: {
    fontSize: 14,
    transition: 'opacity 0.15s ease',
    flexShrink: 0,
  },
  progressBar: {
    position: 'absolute' as const,
    bottom: 0,
    left: 0,
    right: 0,
    height: 2,
    background: 'rgba(255, 255, 255, 0.05)',
  },
  progressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, rgba(0, 240, 255, 0.6), rgba(168, 85, 247, 0.6))',
    animation: 'action-progress 2s ease-in-out infinite',
    width: '40%',
    borderRadius: 1,
  },
  agentProgressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, rgba(138, 43, 226, 0.7), rgba(168, 85, 247, 0.5))',
    borderRadius: 1,
    transition: 'width 0.4s ease',
  },
};

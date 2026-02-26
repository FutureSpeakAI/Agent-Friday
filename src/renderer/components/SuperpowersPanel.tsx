/**
 * SuperpowersPanel — Full-screen overlay for managing loaded programs (Superpowers).
 *
 * Shows a list of installed superpowers with safety scores, toggle controls,
 * per-tool management, permissions editor, usage stats, and install flow.
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';

/* ── Types (mirror main-process types for renderer) ────────────────────── */

interface SuperpowerTool {
  name: string;
  description: string;
  enabled: boolean;
  invocations: number;
  lastUsed: number | null;
  avgLatencyMs: number;
  errors: number;
}

interface SuperpowerPermissions {
  networkDomains: string[];
  filesystemAccess: 'none' | 'scratch' | 'readonly' | 'readwrite';
  memoryAccess: boolean;
  maxCpuMs: number;
  maxMemoryMb: number;
}

type SuperpowerStatus = 'active' | 'disabled' | 'analyzing' | 'installing' | 'error' | 'pending';
type NasseRisk = 'low' | 'medium' | 'high';

interface NasseScore {
  score: number;
  risk: NasseRisk;
  findings: string[];
  scannedAt: number;
  autoApproved: boolean;
}

interface ImprovementInfo {
  analyzed: boolean;
  analysisDate: number | null;
  improvementsFound: number;
  improvementsApplied: number;
  categories: string[];
}

interface ForkInfo {
  hasFork: boolean;
  forkUrl: string | null;
  prsOpened: number;
  prsMerged: number;
  lastSyncDate: number | null;
}

interface Superpower {
  id: string;
  name: string;
  owner: string;
  description: string;
  repoUrl: string;
  status: SuperpowerStatus;
  enabled: boolean;
  installedAt: number;
  lastUsed: number | null;
  tools: SuperpowerTool[];
  nasse: NasseScore | null;
  permissions: SuperpowerPermissions;
  improvement: ImprovementInfo;
  fork: ForkInfo;
  totalInvocations: number;
  totalErrors: number;
  avgLatencyMs: number;
}

interface UsageStats {
  totalInvocations: number;
  totalErrors: number;
  avgLatencyMs: number;
  toolStats: Array<{ name: string; invocations: number; errors: number; avgLatencyMs: number }>;
  lastUsed: number | null;
}

/* ── Props ──────────────────────────────────────────────────────────────── */

interface SuperpowersPanelProps {
  visible: boolean;
  onClose: () => void;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

const eve = (window as any).eve;

function timeAgo(ts: number | null): string {
  if (!ts) return 'Never';
  const diff = Date.now() - ts;
  if (diff < 60_000) return 'Just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function formatMs(ms: number): string {
  if (ms < 1) return '0ms';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const RISK_COLORS: Record<NasseRisk, string> = {
  low: '#22c55e',
  medium: '#f59e0b',
  high: '#ef4444',
};

const STATUS_LABELS: Record<SuperpowerStatus, { label: string; color: string }> = {
  active: { label: 'ACTIVE', color: '#22c55e' },
  disabled: { label: 'DISABLED', color: '#666680' },
  analyzing: { label: 'SCANNING', color: '#f59e0b' },
  installing: { label: 'INSTALLING', color: '#00f0ff' },
  error: { label: 'ERROR', color: '#ef4444' },
  pending: { label: 'PENDING', color: '#666680' },
};

const FS_ACCESS_LABELS: Record<string, string> = {
  none: 'No filesystem access',
  scratch: 'Scratch directory only',
  readonly: 'Read-only access',
  readwrite: 'Full read/write access',
};

/* ── Main Component ────────────────────────────────────────────────────── */

export default function SuperpowersPanel({ visible, onClose }: SuperpowersPanelProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const [superpowers, setSuperpowers] = useState<Superpower[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailStats, setDetailStats] = useState<UsageStats | null>(null);
  const [view, setView] = useState<'list' | 'detail' | 'install'>('list');
  const [installUrl, setInstallUrl] = useState('');
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState('');
  const [loading, setLoading] = useState(true);

  // Load superpowers list
  const loadSuperpowers = useCallback(async () => {
    try {
      const list = await eve.superpowers.list();
      setSuperpowers(list || []);
    } catch (err) {
      console.warn('[SuperpowersPanel] Failed to load:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (visible) {
      loadSuperpowers();
      setTimeout(() => overlayRef.current?.focus(), 50);
    } else {
      setView('list');
      setSelectedId(null);
      setDetailStats(null);
    }
  }, [visible, loadSuperpowers]);

  // Load detail stats when selecting a superpower
  useEffect(() => {
    if (selectedId && view === 'detail') {
      eve.superpowers.usageStats(selectedId).then((stats: UsageStats | null) => {
        setDetailStats(stats);
      }).catch(() => setDetailStats(null));
    }
  }, [selectedId, view]);

  if (!visible) return null;

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      if (view === 'detail' || view === 'install') {
        setView('list');
        setSelectedId(null);
      } else {
        onClose();
      }
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    await eve.superpowers.toggle(id, enabled);
    loadSuperpowers();
  };

  const handleToolToggle = async (superpowerId: string, toolName: string, enabled: boolean) => {
    await eve.superpowers.toggleTool(superpowerId, toolName, enabled);
    loadSuperpowers();
  };

  const handleInstall = async () => {
    if (!installUrl.trim()) return;
    setInstalling(true);
    setInstallError('');
    try {
      const result = await eve.superpowers.install(installUrl.trim());
      if (result?.error) {
        setInstallError(result.error);
      } else {
        setInstallUrl('');
        setView('list');
        loadSuperpowers();
      }
    } catch (err: any) {
      setInstallError(err?.message || String(err));
    } finally {
      setInstalling(false);
    }
  };

  const handleUninstall = async (id: string) => {
    await eve.superpowers.uninstall(id);
    setView('list');
    setSelectedId(null);
    loadSuperpowers();
  };

  const selected = selectedId ? superpowers.find((s) => s.id === selectedId) : null;

  return (
    <div ref={overlayRef} style={styles.overlay} onKeyDown={handleKeyDown} tabIndex={-1}>
      <style>{scrollbarCSS}</style>
      <div style={styles.panel}>
        {/* ── Header ──────────────────────────────────────────── */}
        <div style={styles.header}>
          {view !== 'list' && (
            <button
              onClick={() => { setView('list'); setSelectedId(null); }}
              style={styles.backBtn}
              title="Back to list"
            >
              ←
            </button>
          )}
          <span style={styles.headerIcon}>⚡</span>
          <span style={styles.headerTitle}>
            {view === 'list' ? 'Superpowers' : view === 'install' ? 'Add Superpower' : selected?.name || 'Details'}
          </span>
          <span style={styles.headerCount}>
            {view === 'list' && `${superpowers.filter((s) => s.enabled).length}/${superpowers.length} active`}
          </span>
          {view === 'list' && (
            <button
              onClick={() => setView('install')}
              style={styles.addBtn}
              title="Add Superpower"
            >
              + Add
            </button>
          )}
          <button onClick={onClose} style={styles.closeBtn} title="Close">✕</button>
        </div>

        {/* ── Content ─────────────────────────────────────────── */}
        <div className="sp-scroll" style={styles.content}>
          {view === 'list' && (
            <ListView
              superpowers={superpowers}
              loading={loading}
              onSelect={(id) => { setSelectedId(id); setView('detail'); }}
              onToggle={handleToggle}
            />
          )}
          {view === 'detail' && selected && (
            <DetailView
              superpower={selected}
              stats={detailStats}
              onToggle={handleToggle}
              onToolToggle={handleToolToggle}
              onUninstall={handleUninstall}
            />
          )}
          {view === 'install' && (
            <InstallView
              url={installUrl}
              onUrlChange={setInstallUrl}
              onInstall={handleInstall}
              installing={installing}
              error={installError}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/* ── ListView ──────────────────────────────────────────────────────────── */

function ListView({
  superpowers,
  loading,
  onSelect,
  onToggle,
}: {
  superpowers: Superpower[];
  loading: boolean;
  onSelect: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  if (loading) {
    return <div style={styles.emptyState}>Loading superpowers...</div>;
  }

  if (superpowers.length === 0) {
    return (
      <div style={styles.emptyState}>
        <div style={styles.emptyIcon}>⚡</div>
        <div style={styles.emptyTitle}>No Superpowers Installed</div>
        <div style={styles.emptyDesc}>
          Superpowers extend your agent's capabilities by loading programs from GitHub repositories.
          Click "+ Add" to install your first superpower.
        </div>
      </div>
    );
  }

  return (
    <div style={styles.cardList}>
      {superpowers.map((sp) => (
        <SuperpowerCard
          key={sp.id}
          superpower={sp}
          onSelect={() => onSelect(sp.id)}
          onToggle={(enabled) => onToggle(sp.id, enabled)}
        />
      ))}
    </div>
  );
}

/* ── SuperpowerCard ────────────────────────────────────────────────────── */

function SuperpowerCard({
  superpower: sp,
  onSelect,
  onToggle,
}: {
  superpower: Superpower;
  onSelect: () => void;
  onToggle: (enabled: boolean) => void;
}) {
  const statusInfo = STATUS_LABELS[sp.status];

  return (
    <div style={styles.card} onClick={onSelect}>
      {/* Left: info */}
      <div style={styles.cardBody}>
        <div style={styles.cardTopRow}>
          <span style={styles.cardName}>{sp.name}</span>
          <SafetyBadge nasse={sp.nasse} />
        </div>
        <div style={styles.cardOwner}>{sp.owner}/{sp.name}</div>
        <div style={styles.cardDesc}>
          {sp.description || 'No description available'}
        </div>
        <div style={styles.cardMeta}>
          <span style={{ ...styles.statusPill, color: statusInfo.color, borderColor: statusInfo.color + '40' }}>
            {statusInfo.label}
          </span>
          <span style={styles.cardStat}>{sp.tools.length} tools</span>
          <span style={styles.cardStat}>{sp.totalInvocations} calls</span>
          <span style={styles.cardStat}>Last: {timeAgo(sp.lastUsed)}</span>
        </div>
      </div>

      {/* Right: toggle */}
      <div style={styles.cardToggle} onClick={(e) => e.stopPropagation()}>
        <ToggleSwitch
          enabled={sp.enabled}
          onChange={onToggle}
          disabled={sp.status === 'installing' || sp.status === 'analyzing'}
        />
      </div>
    </div>
  );
}

/* ── SafetyBadge ───────────────────────────────────────────────────────── */

function SafetyBadge({ nasse }: { nasse: NasseScore | null }) {
  if (!nasse) return <span style={styles.badgeUnscanned}>UNSCANNED</span>;

  const color = RISK_COLORS[nasse.risk];
  return (
    <span
      style={{
        ...styles.badge,
        color,
        borderColor: color + '50',
        background: color + '12',
      }}
      title={`NASSE Score: ${(nasse.score * 100).toFixed(0)}% risk\n${nasse.findings.join('\n')}`}
    >
      {nasse.risk === 'low' ? '✓' : nasse.risk === 'medium' ? '⚠' : '✗'} {nasse.risk.toUpperCase()}
    </span>
  );
}

/* ── ToggleSwitch ──────────────────────────────────────────────────────── */

function ToggleSwitch({
  enabled,
  onChange,
  disabled = false,
}: {
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={() => !disabled && onChange(!enabled)}
      style={{
        ...styles.toggle,
        background: enabled ? '#22c55e30' : 'rgba(255,255,255,0.06)',
        borderColor: enabled ? '#22c55e50' : 'rgba(255,255,255,0.1)',
        opacity: disabled ? 0.4 : 1,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
      title={enabled ? 'Disable' : 'Enable'}
    >
      <div
        style={{
          ...styles.toggleKnob,
          transform: enabled ? 'translateX(18px)' : 'translateX(0)',
          background: enabled ? '#22c55e' : '#555568',
        }}
      />
    </button>
  );
}

/* ── DetailView ────────────────────────────────────────────────────────── */

function DetailView({
  superpower: sp,
  stats,
  onToggle,
  onToolToggle,
  onUninstall,
}: {
  superpower: Superpower;
  stats: UsageStats | null;
  onToggle: (id: string, enabled: boolean) => void;
  onToolToggle: (superpowerId: string, toolName: string, enabled: boolean) => void;
  onUninstall: (id: string) => void;
}) {
  const [showConfirmUninstall, setShowConfirmUninstall] = useState(false);
  const [detailTab, setDetailTab] = useState<'tools' | 'permissions' | 'safety' | 'stats'>('tools');

  return (
    <div style={styles.detailRoot}>
      {/* Detail header */}
      <div style={styles.detailHeader}>
        <div style={styles.detailInfo}>
          <div style={styles.detailName}>{sp.name}</div>
          <div style={styles.detailOwner}>{sp.owner}/{sp.name}</div>
          <div style={styles.detailDesc}>{sp.description}</div>
          <div style={styles.detailMeta}>
            <SafetyBadge nasse={sp.nasse} />
            <span style={styles.detailMetaItem}>Installed {timeAgo(sp.installedAt)}</span>
            <a
              href={sp.repoUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={styles.detailLink}
              onClick={(e) => e.stopPropagation()}
            >
              GitHub →
            </a>
          </div>
        </div>
        <div style={styles.detailActions}>
          <ToggleSwitch enabled={sp.enabled} onChange={(en) => onToggle(sp.id, en)} />
        </div>
      </div>

      {/* Tabs */}
      <div style={styles.detailTabs}>
        {(['tools', 'permissions', 'safety', 'stats'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setDetailTab(tab)}
            style={{
              ...styles.detailTab,
              color: detailTab === tab ? '#00f0ff' : '#666680',
              borderBottomColor: detailTab === tab ? '#00f0ff' : 'transparent',
            }}
          >
            {tab === 'tools' ? `Tools (${sp.tools.length})` :
             tab === 'permissions' ? 'Permissions' :
             tab === 'safety' ? 'Safety' : 'Usage Stats'}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={styles.detailContent}>
        {detailTab === 'tools' && (
          <ToolsTab superpower={sp} onToolToggle={onToolToggle} />
        )}
        {detailTab === 'permissions' && (
          <PermissionsTab superpower={sp} />
        )}
        {detailTab === 'safety' && (
          <SafetyTab superpower={sp} />
        )}
        {detailTab === 'stats' && (
          <StatsTab superpower={sp} stats={stats} />
        )}
      </div>

      {/* Danger zone */}
      <div style={styles.dangerZone}>
        {!showConfirmUninstall ? (
          <button
            onClick={() => setShowConfirmUninstall(true)}
            style={styles.uninstallBtn}
          >
            Uninstall Superpower
          </button>
        ) : (
          <div style={styles.confirmRow}>
            <span style={styles.confirmText}>Remove {sp.name}? This cannot be undone.</span>
            <button onClick={() => onUninstall(sp.id)} style={styles.confirmYes}>Remove</button>
            <button onClick={() => setShowConfirmUninstall(false)} style={styles.confirmNo}>Cancel</button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── ToolsTab ──────────────────────────────────────────────────────────── */

function ToolsTab({
  superpower: sp,
  onToolToggle,
}: {
  superpower: Superpower;
  onToolToggle: (superpowerId: string, toolName: string, enabled: boolean) => void;
}) {
  if (sp.tools.length === 0) {
    return (
      <div style={styles.tabEmpty}>
        No tools extracted yet. Tools are discovered when the agent first uses this superpower.
      </div>
    );
  }

  return (
    <div style={styles.toolList}>
      {sp.tools.map((tool) => (
        <div key={tool.name} style={styles.toolRow}>
          <div style={styles.toolInfo}>
            <div style={styles.toolName}>{tool.name}</div>
            <div style={styles.toolDesc}>{tool.description || 'No description'}</div>
            <div style={styles.toolMeta}>
              {tool.invocations} calls · {tool.errors} errors · avg {formatMs(tool.avgLatencyMs)}
            </div>
          </div>
          <ToggleSwitch
            enabled={tool.enabled}
            onChange={(en) => onToolToggle(sp.id, tool.name, en)}
          />
        </div>
      ))}
    </div>
  );
}

/* ── PermissionsTab ────────────────────────────────────────────────────── */

function PermissionsTab({ superpower: sp }: { superpower: Superpower }) {
  const p = sp.permissions;
  return (
    <div style={styles.permGrid}>
      <PermRow label="Filesystem" value={FS_ACCESS_LABELS[p.filesystemAccess] || p.filesystemAccess} />
      <PermRow label="Network Domains" value={p.networkDomains.length === 0 ? 'None (blocked)' : p.networkDomains.join(', ')} />
      <PermRow label="Memory Access" value={p.memoryAccess ? 'Can read agent memory' : 'No memory access'} />
      <PermRow label="CPU Limit" value={`${p.maxCpuMs}ms per invocation`} />
      <PermRow label="Memory Limit" value={`${p.maxMemoryMb} MB per invocation`} />
    </div>
  );
}

function PermRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.permRow}>
      <span style={styles.permLabel}>{label}</span>
      <span style={styles.permValue}>{value}</span>
    </div>
  );
}

/* ── SafetyTab ─────────────────────────────────────────────────────────── */

function SafetyTab({ superpower: sp }: { superpower: Superpower }) {
  const n = sp.nasse;
  if (!n) {
    return <div style={styles.tabEmpty}>Safety scan has not been run yet.</div>;
  }

  const pct = Math.round(n.score * 100);
  const color = RISK_COLORS[n.risk];

  return (
    <div style={styles.safetyRoot}>
      {/* Score gauge */}
      <div style={styles.safetyGauge}>
        <div style={styles.safetyScoreRing}>
          <svg width="80" height="80" viewBox="0 0 80 80">
            <circle cx="40" cy="40" r="35" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="6" />
            <circle
              cx="40" cy="40" r="35"
              fill="none"
              stroke={color}
              strokeWidth="6"
              strokeDasharray={`${n.score * 220} 220`}
              strokeLinecap="round"
              transform="rotate(-90 40 40)"
              style={{ transition: 'stroke-dasharray 0.5s ease' }}
            />
          </svg>
          <div style={{ ...styles.safetyScoreText, color }}>{pct}%</div>
        </div>
        <div style={styles.safetyScoreInfo}>
          <div style={{ ...styles.safetyRiskLabel, color }}>
            {n.risk.toUpperCase()} RISK
          </div>
          <div style={styles.safetyScoreDetail}>
            {n.autoApproved ? 'Auto-approved (low risk)' : 'Manual approval required'}
          </div>
          <div style={styles.safetyScanDate}>
            Scanned {timeAgo(n.scannedAt)}
          </div>
        </div>
      </div>

      {/* Findings */}
      <div style={styles.findingsHeader}>Findings</div>
      <div style={styles.findingsList}>
        {n.findings.map((f, i) => (
          <div key={i} style={styles.findingRow}>
            <span style={{ ...styles.findingDot, background: color }} />
            <span style={styles.findingText}>{f}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── StatsTab ──────────────────────────────────────────────────────────── */

function StatsTab({ superpower: sp, stats }: { superpower: Superpower; stats: UsageStats | null }) {
  return (
    <div style={styles.statsRoot}>
      <div style={styles.statsGrid}>
        <StatBox label="Total Calls" value={String(sp.totalInvocations)} />
        <StatBox label="Errors" value={String(sp.totalErrors)} accent={sp.totalErrors > 0 ? '#ef4444' : undefined} />
        <StatBox label="Avg Latency" value={formatMs(sp.avgLatencyMs)} />
        <StatBox label="Last Used" value={timeAgo(sp.lastUsed)} />
      </div>

      {stats?.toolStats && stats.toolStats.length > 0 && (
        <>
          <div style={styles.statsToolHeader}>Per-Tool Breakdown</div>
          <div style={styles.statsToolList}>
            {stats.toolStats.map((t) => (
              <div key={t.name} style={styles.statsToolRow}>
                <span style={styles.statsToolName}>{t.name}</span>
                <span style={styles.statsToolVal}>{t.invocations} calls</span>
                <span style={styles.statsToolVal}>{t.errors} err</span>
                <span style={styles.statsToolVal}>{formatMs(t.avgLatencyMs)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function StatBox({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div style={styles.statBox}>
      <div style={{ ...styles.statValue, color: accent || '#e0e0e8' }}>{value}</div>
      <div style={styles.statLabel}>{label}</div>
    </div>
  );
}

/* ── InstallView ───────────────────────────────────────────────────────── */

function InstallView({
  url,
  onUrlChange,
  onInstall,
  installing,
  error,
}: {
  url: string;
  onUrlChange: (v: string) => void;
  onInstall: () => void;
  installing: boolean;
  error: string;
}) {
  return (
    <div style={styles.installRoot}>
      <div style={styles.installIcon}>⚡</div>
      <div style={styles.installTitle}>Add a New Superpower</div>
      <div style={styles.installDesc}>
        Enter a GitHub repository URL. The repository will be cloned, analyzed for safety,
        and its tools will be available to your agent.
      </div>

      <div style={styles.installField}>
        <input
          type="text"
          value={url}
          onChange={(e) => onUrlChange(e.target.value)}
          placeholder="https://github.com/owner/repo"
          style={styles.installInput}
          onKeyDown={(e) => e.key === 'Enter' && onInstall()}
          autoFocus
        />
      </div>

      {error && <div style={styles.installError}>{error}</div>}

      <button
        onClick={onInstall}
        disabled={installing || !url.trim()}
        style={{
          ...styles.installBtn,
          opacity: installing || !url.trim() ? 0.4 : 1,
        }}
      >
        {installing ? 'Installing...' : 'Install Superpower'}
      </button>

      <div style={styles.installHint}>
        Tip: Your agent can also install superpowers by voice — just say
        "load the repository at github.com/..."
      </div>
    </div>
  );
}

/* ── Scrollbar CSS ─────────────────────────────────────────────────────── */

const scrollbarCSS = `
  .sp-scroll::-webkit-scrollbar { width: 6px; }
  .sp-scroll::-webkit-scrollbar-track { background: transparent; }
  .sp-scroll::-webkit-scrollbar-thumb { background: rgba(0, 240, 255, 0.15); border-radius: 3px; }
  .sp-scroll::-webkit-scrollbar-thumb:hover { background: rgba(0, 240, 255, 0.3); }
`;

/* ── Styles ─────────────────────────────────────────────────────────────── */

const styles: Record<string, React.CSSProperties> = {
  // Overlay
  overlay: {
    position: 'fixed',
    inset: 0,
    background: 'rgba(0,0,0,0.6)',
    backdropFilter: 'blur(8px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 100,
    outline: 'none',
  },

  // Panel
  panel: {
    width: 760,
    maxHeight: '85vh',
    background: '#111118',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    position: 'relative',
  },

  // Header
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '18px 24px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    flexShrink: 0,
  },
  headerIcon: { fontSize: 18, color: '#00f0ff' },
  headerTitle: { fontSize: 15, fontWeight: 600, color: '#e0e0e8' },
  headerCount: { flex: 1, fontSize: 12, color: '#555568', fontFamily: "'Fira Code', monospace" },
  closeBtn: {
    background: 'none',
    border: 'none',
    color: '#555568',
    fontSize: 16,
    cursor: 'pointer',
    padding: '4px 8px',
    borderRadius: 4,
  },
  backBtn: {
    background: 'none',
    border: '1px solid rgba(255,255,255,0.1)',
    color: '#888',
    fontSize: 14,
    cursor: 'pointer',
    padding: '2px 8px',
    borderRadius: 4,
    marginRight: 4,
  },
  addBtn: {
    background: 'rgba(0, 240, 255, 0.08)',
    border: '1px solid rgba(0, 240, 255, 0.2)',
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    padding: '6px 14px',
    borderRadius: 6,
    letterSpacing: '0.03em',
  },

  // Content area
  content: {
    flex: 1,
    overflowY: 'auto' as const,
    padding: '20px 24px',
    minHeight: 0,
  },

  // Empty state
  emptyState: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    padding: '60px 40px',
    textAlign: 'center' as const,
    color: '#555568',
    fontSize: 13,
  },
  emptyIcon: { fontSize: 40, marginBottom: 16, opacity: 0.3 },
  emptyTitle: { fontSize: 16, fontWeight: 600, color: '#888', marginBottom: 8 },
  emptyDesc: { fontSize: 13, color: '#555568', lineHeight: '1.6', maxWidth: 400 },

  // Card list
  cardList: { display: 'flex', flexDirection: 'column' as const, gap: 10 },
  card: {
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    padding: '16px 18px',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 10,
    cursor: 'pointer',
    transition: 'border-color 0.15s, background 0.15s',
  },
  cardBody: { flex: 1, minWidth: 0 },
  cardTopRow: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 },
  cardName: { fontSize: 14, fontWeight: 600, color: '#e0e0e8' },
  cardOwner: { fontSize: 11, color: '#555568', fontFamily: "'Fira Code', monospace", marginBottom: 4 },
  cardDesc: {
    fontSize: 12,
    color: '#888',
    lineHeight: '1.4',
    overflow: 'hidden' as const,
    textOverflow: 'ellipsis' as const,
    whiteSpace: 'nowrap' as const,
    marginBottom: 8,
  },
  cardMeta: { display: 'flex', alignItems: 'center', gap: 10 },
  cardStat: { fontSize: 11, color: '#555568' },
  statusPill: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.06em',
    padding: '2px 8px',
    borderRadius: 4,
    border: '1px solid',
  },
  cardToggle: { flexShrink: 0 },

  // Toggle
  toggle: {
    width: 40,
    height: 22,
    borderRadius: 11,
    border: '1px solid',
    padding: 2,
    display: 'flex',
    alignItems: 'center',
    transition: 'background 0.2s, border-color 0.2s',
  },
  toggleKnob: {
    width: 16,
    height: 16,
    borderRadius: '50%',
    transition: 'transform 0.2s, background 0.2s',
  },

  // Badge
  badge: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '0.06em',
    padding: '2px 8px',
    borderRadius: 4,
    border: '1px solid',
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
  },
  badgeUnscanned: {
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: '0.06em',
    padding: '2px 8px',
    borderRadius: 4,
    border: '1px solid rgba(255,255,255,0.1)',
    color: '#555568',
  },

  // Detail view
  detailRoot: { display: 'flex', flexDirection: 'column' as const, gap: 0 },
  detailHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 16,
    paddingBottom: 16,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    marginBottom: 0,
  },
  detailInfo: { flex: 1 },
  detailName: { fontSize: 18, fontWeight: 700, color: '#e0e0e8', marginBottom: 2 },
  detailOwner: { fontSize: 12, color: '#555568', fontFamily: "'Fira Code', monospace", marginBottom: 6 },
  detailDesc: { fontSize: 13, color: '#888', lineHeight: '1.5', marginBottom: 10 },
  detailMeta: { display: 'flex', alignItems: 'center', gap: 12 },
  detailMetaItem: { fontSize: 11, color: '#555568' },
  detailLink: { fontSize: 11, color: '#00f0ff', textDecoration: 'none' },
  detailActions: { flexShrink: 0, paddingTop: 4 },

  // Detail tabs
  detailTabs: {
    display: 'flex',
    gap: 0,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    flexShrink: 0,
  },
  detailTab: {
    background: 'none',
    border: 'none',
    borderBottom: '2px solid transparent',
    color: '#666680',
    fontSize: 12,
    fontWeight: 600,
    padding: '14px 16px',
    cursor: 'pointer',
    letterSpacing: '0.03em',
    transition: 'color 0.15s, border-color 0.15s',
  },
  detailContent: { paddingTop: 16 },

  // Tools tab
  tabEmpty: { fontSize: 13, color: '#555568', padding: '24px 0', textAlign: 'center' as const },
  toolList: { display: 'flex', flexDirection: 'column' as const, gap: 8 },
  toolRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '12px 14px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid rgba(255,255,255,0.04)',
    borderRadius: 8,
  },
  toolInfo: { flex: 1, minWidth: 0 },
  toolName: { fontSize: 13, fontWeight: 600, color: '#e0e0e8', fontFamily: "'Fira Code', monospace" },
  toolDesc: { fontSize: 11, color: '#888', marginTop: 2 },
  toolMeta: { fontSize: 10, color: '#555568', marginTop: 4 },

  // Permissions tab
  permGrid: { display: 'flex', flexDirection: 'column' as const, gap: 12 },
  permRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '10px 14px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid rgba(255,255,255,0.04)',
    borderRadius: 8,
  },
  permLabel: { fontSize: 12, fontWeight: 600, color: '#888' },
  permValue: { fontSize: 12, color: '#e0e0e8', fontFamily: "'Fira Code', monospace" },

  // Safety tab
  safetyRoot: { display: 'flex', flexDirection: 'column' as const, gap: 16 },
  safetyGauge: { display: 'flex', alignItems: 'center', gap: 20 },
  safetyScoreRing: { position: 'relative' as const, width: 80, height: 80 },
  safetyScoreText: {
    position: 'absolute' as const,
    inset: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 18,
    fontWeight: 700,
    fontFamily: "'Fira Code', monospace",
  },
  safetyScoreInfo: { flex: 1 },
  safetyRiskLabel: { fontSize: 14, fontWeight: 700, letterSpacing: '0.06em', marginBottom: 4 },
  safetyScoreDetail: { fontSize: 12, color: '#888' },
  safetyScanDate: { fontSize: 11, color: '#555568', marginTop: 4 },
  findingsHeader: {
    fontSize: 12,
    fontWeight: 700,
    color: '#888',
    letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
    paddingTop: 8,
    borderTop: '1px solid rgba(255,255,255,0.04)',
  },
  findingsList: { display: 'flex', flexDirection: 'column' as const, gap: 6 },
  findingRow: { display: 'flex', alignItems: 'center', gap: 8 },
  findingDot: { width: 6, height: 6, borderRadius: '50%', flexShrink: 0 },
  findingText: { fontSize: 12, color: '#e0e0e8' },

  // Stats tab
  statsRoot: { display: 'flex', flexDirection: 'column' as const, gap: 16 },
  statsGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, 1fr)',
    gap: 10,
  },
  statBox: {
    padding: '14px 12px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid rgba(255,255,255,0.04)',
    borderRadius: 8,
    textAlign: 'center' as const,
  },
  statValue: { fontSize: 18, fontWeight: 700, fontFamily: "'Fira Code', monospace", marginBottom: 4 },
  statLabel: { fontSize: 10, color: '#555568', letterSpacing: '0.04em', textTransform: 'uppercase' as const },
  statsToolHeader: {
    fontSize: 12,
    fontWeight: 700,
    color: '#888',
    letterSpacing: '0.06em',
    textTransform: 'uppercase' as const,
  },
  statsToolList: { display: 'flex', flexDirection: 'column' as const, gap: 4 },
  statsToolRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '8px 12px',
    background: 'rgba(255,255,255,0.02)',
    borderRadius: 6,
    fontSize: 12,
  },
  statsToolName: { flex: 1, fontWeight: 600, color: '#e0e0e8', fontFamily: "'Fira Code', monospace" },
  statsToolVal: { color: '#555568', fontSize: 11 },

  // Install view
  installRoot: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    justifyContent: 'center',
    padding: '40px 20px',
    textAlign: 'center' as const,
  },
  installIcon: { fontSize: 40, marginBottom: 16, opacity: 0.4 },
  installTitle: { fontSize: 18, fontWeight: 700, color: '#e0e0e8', marginBottom: 8 },
  installDesc: { fontSize: 13, color: '#888', lineHeight: '1.6', maxWidth: 440, marginBottom: 24 },
  installField: { width: '100%', maxWidth: 500, marginBottom: 12 },
  installInput: {
    width: '100%',
    padding: '12px 16px',
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    color: '#e0e0e8',
    fontSize: 14,
    fontFamily: "'Fira Code', monospace",
    outline: 'none',
    boxSizing: 'border-box' as const,
  },
  installError: {
    fontSize: 12,
    color: '#ef4444',
    marginBottom: 12,
    maxWidth: 500,
  },
  installBtn: {
    background: 'rgba(0, 240, 255, 0.1)',
    border: '1px solid rgba(0, 240, 255, 0.3)',
    color: '#00f0ff',
    fontSize: 14,
    fontWeight: 600,
    padding: '10px 28px',
    borderRadius: 8,
    cursor: 'pointer',
    letterSpacing: '0.03em',
    transition: 'opacity 0.15s',
    marginBottom: 20,
  },
  installHint: {
    fontSize: 11,
    color: '#444460',
    fontStyle: 'italic' as const,
    maxWidth: 400,
    lineHeight: '1.5',
  },

  // Danger zone
  dangerZone: {
    marginTop: 24,
    paddingTop: 16,
    borderTop: '1px solid rgba(255,255,255,0.04)',
  },
  uninstallBtn: {
    background: 'rgba(239, 68, 68, 0.06)',
    border: '1px solid rgba(239, 68, 68, 0.15)',
    color: '#ef4444',
    fontSize: 12,
    fontWeight: 600,
    padding: '8px 16px',
    borderRadius: 6,
    cursor: 'pointer',
    width: '100%',
  },
  confirmRow: { display: 'flex', alignItems: 'center', gap: 10 },
  confirmText: { flex: 1, fontSize: 12, color: '#ef4444' },
  confirmYes: {
    background: '#ef4444',
    border: 'none',
    color: '#fff',
    fontSize: 12,
    fontWeight: 600,
    padding: '6px 14px',
    borderRadius: 6,
    cursor: 'pointer',
  },
  confirmNo: {
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    color: '#888',
    fontSize: 12,
    padding: '6px 14px',
    borderRadius: 6,
    cursor: 'pointer',
  },
};

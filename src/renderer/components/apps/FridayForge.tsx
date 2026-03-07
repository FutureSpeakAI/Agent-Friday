/**
 * FridayForge.tsx — Agent Friday Superpower Forge
 *
 * Browse the registry, manage installed superpowers, and
 * discover capability gaps with auto-recommendations.
 *
 * IPC: window.eve.ecosystem.*, window.eve.superpowers.*, window.eve.capabilityGaps.*
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

interface RegistryEntry {
  id: string;
  name: string;
  description?: string;
  version?: string;
  author?: string;
  category?: string;
  rating?: number;
  downloads?: number;
  tags?: string[];
}

interface InstalledPower {
  id: string;
  name: string;
  description?: string;
  version?: string;
  enabled: boolean;
  status?: string;
  installedAt?: string;
}

interface CapabilityGap {
  id: string;
  description: string;
  severity?: string;
  category?: string;
  recommendation?: string;
  recommendedPowerId?: string;
}

interface Props {
  visible: boolean;
  onClose: () => void;
}

type Tab = 'store' | 'installed' | 'gaps';

export default function FridayForge({ visible, onClose }: Props) {
  const [registry, setRegistry] = useState<RegistryEntry[]>([]);
  const [installed, setInstalled] = useState<InstalledPower[]>([]);
  const [gaps, setGaps] = useState<CapabilityGap[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>('store');
  const [search, setSearch] = useState('');
  const [searching, setSearching] = useState(false);
  const [actionInProgress, setActionInProgress] = useState<string | null>(null);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadStore = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await (window as any).eve.ecosystem.searchRegistry({});
      setRegistry(Array.isArray(result) ? result : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load registry');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadInstalled = useCallback(async () => {
    try {
      const result = await (window as any).eve.superpowers.list();
      setInstalled(Array.isArray(result) ? result : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load installed superpowers');
    }
  }, []);

  const loadGaps = useCallback(async () => {
    try {
      const result = await (window as any).eve.capabilityGaps.top();
      setGaps(Array.isArray(result) ? result : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load capability gaps');
    }
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([loadStore(), loadInstalled(), loadGaps()]);
    } catch {
      // Individual loaders handle their own errors
    } finally {
      setLoading(false);
    }
  }, [loadStore, loadInstalled, loadGaps]);

  useEffect(() => {
    if (visible) loadAll();
  }, [visible, loadAll]);

  const handleSearch = useCallback(async (query: string) => {
    if (!query.trim()) {
      loadStore();
      return;
    }
    setSearching(true);
    try {
      const result = await (window as any).eve.ecosystem.searchRegistry({ query: query.trim() });
      setRegistry(Array.isArray(result) ? result : []);
    } catch (err: any) {
      setError(err?.message || 'Search failed');
    } finally {
      setSearching(false);
    }
  }, [loadStore]);

  const onSearchChange = (value: string) => {
    setSearch(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => handleSearch(value), 400);
  };

  const handleInstall = async (id: string) => {
    setActionInProgress(id);
    setError(null);
    try {
      await (window as any).eve.superpowers.install(id);
      await loadInstalled();
    } catch (err: any) {
      setError(err?.message || 'Install failed');
    } finally {
      setActionInProgress(null);
    }
  };

  const handleUninstall = async (id: string) => {
    setActionInProgress(id);
    setError(null);
    try {
      await (window as any).eve.superpowers.uninstall(id);
      await loadInstalled();
    } catch (err: any) {
      setError(err?.message || 'Uninstall failed');
    } finally {
      setActionInProgress(null);
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    setActionInProgress(id);
    setError(null);
    try {
      await (window as any).eve.superpowers.toggle(id, enabled);
      await loadInstalled();
    } catch (err: any) {
      setError(err?.message || 'Toggle failed');
    } finally {
      setActionInProgress(null);
    }
  };

  const isInstalled = (id: string): boolean =>
    installed.some((p) => p.id === id);

  const getSeverityColor = (severity?: string): string => {
    switch (severity?.toLowerCase()) {
      case 'critical': case 'high': return '#ef4444';
      case 'medium': return '#f97316';
      case 'low': return '#22c55e';
      default: return '#8888a0';
    }
  };

  const renderStars = (rating?: number): string => {
    if (!rating) return '';
    const full = Math.floor(rating);
    const half = rating - full >= 0.5 ? 1 : 0;
    return '\u2605'.repeat(full) + (half ? '\u00BD' : '') + '\u2606'.repeat(5 - full - half);
  };

  const tabs: { key: Tab; label: string; count: number }[] = [
    { key: 'store', label: 'Store', count: registry.length },
    { key: 'installed', label: 'My Powers', count: installed.length },
    { key: 'gaps', label: 'Gaps', count: gaps.length },
  ];

  return (
    <AppShell visible={visible} onClose={onClose} title="Forge" icon="🛠️" width={980}>
      <ContextBar appId="friday-forge" />
      {/* Tab Bar */}
      <div style={s.tabBar}>
        {tabs.map((t) => (
          <button
            key={t.key}
            style={{
              ...s.tab,
              ...(activeTab === t.key ? s.tabActive : {}),
            }}
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
            <span style={s.tabBadge}>{t.count}</span>
          </button>
        ))}
      </div>

      {error && (
        <div style={s.errorBar}>
          <span>{error}</span>
          <button style={s.dismissBtn} onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {loading ? (
        <div style={s.center}>
          <span style={s.spinner}>⟳</span>
          <span style={s.secondaryText}>Loading forge...</span>
        </div>
      ) : activeTab === 'store' ? (
        /* ── Store Tab ── */
        <div style={s.storeContainer}>
          <div style={s.searchRow}>
            <input
              type="text"
              placeholder="Search superpowers..."
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              style={s.searchInput}
            />
            {searching && <span style={s.searchingLabel}>Searching...</span>}
          </div>

          {registry.length === 0 ? (
            <div style={s.center}>
              <div style={s.emptyIcon}>🔍</div>
              <span style={s.mutedText}>No superpowers found</span>
            </div>
          ) : (
            <div style={s.storeGrid}>
              {registry.map((entry) => {
                const alreadyInstalled = isInstalled(entry.id);
                const busy = actionInProgress === entry.id;
                return (
                  <div key={entry.id} style={s.storeCard}>
                    <div style={s.storeCardHeader}>
                      <div style={{ flex: 1 }}>
                        <div style={s.storeName}>{entry.name}</div>
                        {entry.category && (
                          <span style={s.categoryPill}>{entry.category}</span>
                        )}
                      </div>
                      {entry.version && (
                        <span style={s.versionBadge}>v{entry.version}</span>
                      )}
                    </div>
                    {entry.description && (
                      <div style={s.storeDesc}>{entry.description}</div>
                    )}
                    <div style={s.storeMeta}>
                      {entry.author && (
                        <span style={s.storeAuthor}>by {entry.author}</span>
                      )}
                      {entry.rating !== undefined && (
                        <span style={s.storeRating}>
                          {renderStars(entry.rating)}
                        </span>
                      )}
                      {entry.downloads !== undefined && (
                        <span style={s.storeDownloads}>
                          {entry.downloads.toLocaleString()} installs
                        </span>
                      )}
                    </div>
                    {entry.tags && entry.tags.length > 0 && (
                      <div style={s.tagsRow}>
                        {entry.tags.slice(0, 4).map((t) => (
                          <span key={t} style={s.tag}>{t}</span>
                        ))}
                      </div>
                    )}
                    <button
                      style={alreadyInstalled ? s.installedBtn : s.installBtn}
                      onClick={() => !alreadyInstalled && handleInstall(entry.id)}
                      disabled={alreadyInstalled || busy}
                    >
                      {busy
                        ? 'Installing...'
                        : alreadyInstalled
                        ? 'Installed'
                        : 'Install'}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : activeTab === 'installed' ? (
        /* ── Installed Tab ── */
        <div style={s.installedContainer}>
          {installed.length === 0 ? (
            <div style={s.center}>
              <div style={s.emptyIcon}>⚡</div>
              <span style={s.mutedText}>No superpowers installed</span>
              <button style={s.actionBtn} onClick={() => setActiveTab('store')}>
                Browse Store
              </button>
            </div>
          ) : (
            installed.map((power) => {
              const busy = actionInProgress === power.id;
              return (
                <div key={power.id} style={s.powerCard}>
                  <div style={s.powerHeader}>
                    <div
                      style={{
                        ...s.powerStatusDot,
                        background: power.enabled ? '#22c55e' : '#4a4a62',
                      }}
                    />
                    <div style={{ flex: 1 }}>
                      <div style={s.powerName}>{power.name}</div>
                      {power.description && (
                        <div style={s.powerDesc}>{power.description}</div>
                      )}
                      <div style={s.powerMeta}>
                        {power.version && (
                          <span style={s.versionBadge}>v{power.version}</span>
                        )}
                        {power.installedAt && (
                          <span style={s.mutedText}>
                            Installed: {new Date(power.installedAt).toLocaleDateString()}
                          </span>
                        )}
                        {power.status && (
                          <span
                            style={{
                              ...s.statusPill,
                              color:
                                power.status === 'error'
                                  ? '#ef4444'
                                  : power.status === 'running'
                                  ? '#22c55e'
                                  : '#8888a0',
                            }}
                          >
                            {power.status}
                          </span>
                        )}
                      </div>
                    </div>
                    <div style={s.powerActions}>
                      <button
                        style={{
                          ...s.toggleSwitch,
                          background: power.enabled
                            ? 'rgba(34,197,94,0.15)'
                            : 'rgba(255,255,255,0.03)',
                          borderColor: power.enabled
                            ? 'rgba(34,197,94,0.4)'
                            : 'rgba(255,255,255,0.07)',
                        }}
                        onClick={() => handleToggle(power.id, !power.enabled)}
                        disabled={busy}
                        title={power.enabled ? 'Disable' : 'Enable'}
                      >
                        <div
                          style={{
                            ...s.toggleKnob,
                            transform: power.enabled
                              ? 'translateX(16px)'
                              : 'translateX(0)',
                            background: power.enabled ? '#22c55e' : '#4a4a62',
                          }}
                        />
                      </button>
                      <button
                        style={s.uninstallBtn}
                        onClick={() => handleUninstall(power.id)}
                        disabled={busy}
                        title="Uninstall"
                      >
                        {busy ? '...' : 'Uninstall'}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      ) : (
        /* ── Gaps Tab ── */
        <div style={s.gapsContainer}>
          {gaps.length === 0 ? (
            <div style={s.center}>
              <div style={s.emptyIcon}>✨</div>
              <span style={s.mutedText}>No capability gaps detected</span>
              <span style={s.secondaryText}>
                Your agent is fully equipped
              </span>
            </div>
          ) : (
            gaps.map((gap) => {
              const busy = actionInProgress === gap.recommendedPowerId;
              return (
                <div key={gap.id} style={s.gapCard}>
                  <div style={s.gapHeader}>
                    <div
                      style={{
                        ...s.severityBadge,
                        color: getSeverityColor(gap.severity),
                        borderColor: `${getSeverityColor(gap.severity)}44`,
                        background: `${getSeverityColor(gap.severity)}11`,
                      }}
                    >
                      {gap.severity || 'unknown'}
                    </div>
                    {gap.category && (
                      <span style={s.gapCategory}>{gap.category}</span>
                    )}
                  </div>
                  <div style={s.gapDesc}>{gap.description}</div>
                  {gap.recommendation && (
                    <div style={s.gapRecommendation}>
                      <span style={s.recLabel}>Recommendation:</span>
                      <span style={s.recText}>{gap.recommendation}</span>
                    </div>
                  )}
                  {gap.recommendedPowerId && (
                    <button
                      style={s.installRecommendedBtn}
                      onClick={() => handleInstall(gap.recommendedPowerId!)}
                      disabled={
                        busy || isInstalled(gap.recommendedPowerId)
                      }
                    >
                      {busy
                        ? 'Installing...'
                        : isInstalled(gap.recommendedPowerId)
                        ? 'Already Installed'
                        : 'Install Recommended'}
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>
      )}
    </AppShell>
  );
}

/* ── Styles ─────────────────────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  tabBar: {
    display: 'flex',
    gap: 4,
    borderBottom: '1px solid rgba(255,255,255,0.07)',
    paddingBottom: 12,
    marginBottom: 4,
  },
  tab: {
    background: 'transparent',
    border: '1px solid transparent',
    borderRadius: 8,
    padding: '6px 14px',
    color: '#8888a0',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0,240,255,0.08)',
    borderColor: 'rgba(0,240,255,0.3)',
    color: '#00f0ff',
  },
  tabBadge: {
    fontSize: 10,
    fontWeight: 700,
    background: 'rgba(255,255,255,0.06)',
    borderRadius: 10,
    padding: '1px 6px',
    fontFamily: "'JetBrains Mono', monospace",
  },
  errorBar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    background: 'rgba(239,68,68,0.1)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: 8,
    padding: '6px 12px',
    color: '#ef4444',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  dismissBtn: {
    background: 'none',
    border: 'none',
    color: '#ef4444',
    cursor: 'pointer',
    fontSize: 11,
  },
  storeContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    flex: 1,
  },
  searchRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  searchInput: {
    flex: 1,
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 12px',
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
  },
  searchingLabel: {
    fontSize: 12,
    color: '#00f0ff',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  storeGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
    gap: 10,
    overflowY: 'auto',
    flex: 1,
  },
  storeCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 12,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  storeCardHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 8,
  },
  storeName: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 700,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginBottom: 3,
  },
  categoryPill: {
    fontSize: 10,
    fontWeight: 700,
    color: '#8A2BE2',
    background: 'rgba(138,43,226,0.12)',
    padding: '2px 8px',
    borderRadius: 4,
    fontFamily: "'Inter', system-ui, sans-serif",
    textTransform: 'uppercase' as const,
  },
  versionBadge: {
    fontSize: 10,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
    background: 'rgba(255,255,255,0.06)',
    padding: '2px 6px',
    borderRadius: 4,
    flexShrink: 0,
  },
  storeDesc: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.4,
  },
  storeMeta: {
    display: 'flex',
    gap: 10,
    fontSize: 11,
    fontFamily: "'Inter', system-ui, sans-serif",
    flexWrap: 'wrap',
  },
  storeAuthor: { color: '#4a4a62' },
  storeRating: { color: '#f97316' },
  storeDownloads: { color: '#4a4a62' },
  tagsRow: {
    display: 'flex',
    gap: 4,
    flexWrap: 'wrap',
  },
  tag: {
    fontSize: 10,
    color: '#8A2BE2',
    background: 'rgba(138,43,226,0.1)',
    padding: '1px 6px',
    borderRadius: 3,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  installBtn: {
    marginTop: 'auto',
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '6px 14px',
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    alignSelf: 'flex-start',
  },
  installedBtn: {
    marginTop: 'auto',
    background: 'rgba(34,197,94,0.08)',
    border: '1px solid rgba(34,197,94,0.2)',
    borderRadius: 8,
    padding: '6px 14px',
    color: '#22c55e',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'default',
    fontFamily: "'Inter', system-ui, sans-serif",
    alignSelf: 'flex-start',
  },
  installedContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
    flex: 1,
    overflowY: 'auto',
  },
  powerCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: '12px 14px',
  },
  powerHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: 10,
  },
  powerStatusDot: {
    width: 10,
    height: 10,
    borderRadius: '50%',
    marginTop: 4,
    flexShrink: 0,
  },
  powerName: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 600,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  powerDesc: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
    marginTop: 2,
  },
  powerMeta: {
    display: 'flex',
    gap: 10,
    alignItems: 'center',
    marginTop: 6,
    flexWrap: 'wrap',
  },
  statusPill: {
    fontSize: 11,
    fontWeight: 600,
    textTransform: 'capitalize' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  powerActions: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    flexShrink: 0,
  },
  toggleSwitch: {
    width: 36,
    height: 20,
    borderRadius: 10,
    border: '1px solid',
    position: 'relative',
    cursor: 'pointer',
    padding: 2,
    transition: 'all 0.2s',
  },
  toggleKnob: {
    width: 14,
    height: 14,
    borderRadius: '50%',
    transition: 'transform 0.2s, background 0.2s',
  },
  uninstallBtn: {
    background: 'rgba(239,68,68,0.08)',
    border: '1px solid rgba(239,68,68,0.2)',
    borderRadius: 6,
    padding: '4px 10px',
    color: '#ef4444',
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  gapsContainer: {
    display: 'flex',
    flexDirection: 'column',
    gap: 10,
    flex: 1,
    overflowY: 'auto',
  },
  gapCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 10,
    padding: 14,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  gapHeader: {
    display: 'flex',
    gap: 8,
    alignItems: 'center',
  },
  severityBadge: {
    fontSize: 10,
    fontWeight: 700,
    border: '1px solid',
    borderRadius: 6,
    padding: '2px 8px',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  gapCategory: {
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  gapDesc: {
    color: '#F8FAFC',
    fontSize: 13,
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.4,
  },
  gapRecommendation: {
    background: 'rgba(0,240,255,0.05)',
    border: '1px solid rgba(0,240,255,0.15)',
    borderRadius: 8,
    padding: '8px 10px',
    display: 'flex',
    flexDirection: 'column',
    gap: 3,
  },
  recLabel: {
    fontSize: 10,
    fontWeight: 700,
    color: '#00f0ff',
    letterSpacing: '0.04em',
    textTransform: 'uppercase' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  recText: {
    fontSize: 12,
    color: '#F8FAFC',
    fontFamily: "'Inter', system-ui, sans-serif",
    lineHeight: 1.4,
  },
  installRecommendedBtn: {
    alignSelf: 'flex-start',
    background: 'rgba(34,197,94,0.12)',
    border: '1px solid rgba(34,197,94,0.3)',
    borderRadius: 8,
    padding: '6px 14px',
    color: '#22c55e',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  actionBtn: {
    marginTop: 8,
    background: 'rgba(0,240,255,0.12)',
    border: '1px solid rgba(0,240,255,0.3)',
    borderRadius: 8,
    padding: '6px 16px',
    color: '#00f0ff',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    padding: 40,
    flex: 1,
  },
  spinner: {
    fontSize: 28,
    color: '#00f0ff',
    animation: 'spin 1s linear infinite',
  },
  secondaryText: {
    color: '#8888a0',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  mutedText: {
    color: '#4a4a62',
    fontSize: 12,
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  emptyIcon: { fontSize: 32, opacity: 0.5 },
};

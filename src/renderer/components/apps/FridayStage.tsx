/**
 * FridayStage.tsx — The Stage: Unified Creative Output Hub
 *
 * Track G of the Polymath Update (v3.0.0).
 * Single-pane creative command centre that:
 *   1. Accepts a natural-language creative prompt
 *   2. Classifies intent via polymath_classify
 *   3. Dispatches to the right creative connector via polymath_dispatch
 *   4. Pushes results to stage_push_output
 *   5. Renders outputs with domain-appropriate viewers
 *
 * IPC bridge: window.eve.connectors.callTool(name, args)
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import AppShell from '../AppShell';

// ── Types ────────────────────────────────────────────────────────────────────

interface StageProps {
  visible: boolean;
  onClose: () => void;
}

type StageTab = 'create' | 'gallery' | 'pipelines';

type StageDomain =
  | 'image' | 'video' | 'music' | 'sfx'
  | 'speech' | 'podcast' | 'code' | 'document';

interface StageOutputItem {
  id: string;
  domain: StageDomain;
  renderer: string;
  title: string;
  source_tool: string;
  file_path?: string;
  url?: string;
  thumbnail?: string;
  pinned: boolean;
  created_at: string;
}

interface ClassifyResult {
  domains: Array<{ domain: string; confidence: number }>;
}

interface PipelineInfo {
  id: string;
  template?: string;
  status: string;
  progress: number;
  steps_total: number;
  steps_completed: number;
  created_at: string;
}

// ── Connector bridge ─────────────────────────────────────────────────────────

async function callConnector(
  toolName: string,
  args: Record<string, unknown>,
): Promise<any> {
  try {
    const eve = (window as any).eve;
    if (!eve?.connectors?.callTool) {
      return { error: 'Connector bridge not available' };
    }
    return await eve.connectors.callTool(toolName, args);
  } catch (err: any) {
    return { error: err?.message ?? String(err) };
  }
}

async function callAndParse(
  toolName: string,
  args: Record<string, unknown>,
): Promise<{ data?: any; error?: string }> {
  const r = await callConnector(toolName, args);
  if (r?.error) return { error: r.error };
  if (r?.result) {
    try {
      return { data: JSON.parse(r.result) };
    } catch {
      return { data: r.result };
    }
  }
  return { error: 'No result returned' };
}

// ── Domain helpers ───────────────────────────────────────────────────────────

const DOMAIN_ICONS: Record<StageDomain, string> = {
  image: '🖼️',
  video: '🎬',
  music: '🎵',
  sfx: '💥',
  speech: '🗣️',
  podcast: '🎙️',
  code: '💻',
  document: '📄',
};

const DOMAIN_COLORS: Record<StageDomain, string> = {
  image: '#00f0ff',
  video: '#8A2BE2',
  music: '#22c55e',
  sfx: '#f59e0b',
  speech: '#3b82f6',
  podcast: '#ec4899',
  code: '#a78bfa',
  document: '#94a3b8',
};

// ── Component ────────────────────────────────────────────────────────────────

export default function FridayStage({ visible, onClose }: StageProps) {
  const [tab, setTab] = useState<StageTab>('create');
  const [prompt, setPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [classified, setClassified] = useState<ClassifyResult | null>(null);
  const [outputs, setOutputs] = useState<StageOutputItem[]>([]);
  const [pipelines, setPipelines] = useState<PipelineInfo[]>([]);
  const [selectedOutput, setSelectedOutput] = useState<StageOutputItem | null>(null);
  const [domainFilter, setDomainFilter] = useState<StageDomain | 'all'>('all');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // ── Load outputs on mount / tab switch ──────────────────────────────────
  const refreshOutputs = useCallback(async () => {
    const args: Record<string, unknown> = { limit: 50 };
    if (domainFilter !== 'all') args.domain = domainFilter;
    const { data } = await callAndParse('stage_list_outputs', args);
    if (data?.outputs) setOutputs(data.outputs);
  }, [domainFilter]);

  const refreshPipelines = useCallback(async () => {
    const { data } = await callAndParse('polymath_pipeline_list', {});
    if (data?.pipelines) setPipelines(data.pipelines);
  }, []);

  useEffect(() => {
    if (!visible) return;
    refreshOutputs();
    refreshPipelines();
  }, [visible, tab, refreshOutputs, refreshPipelines]);

  // ── Classify on typing (debounced) ─────────────────────────────────────
  useEffect(() => {
    if (!prompt.trim() || prompt.length < 5) {
      setClassified(null);
      return;
    }
    const timer = setTimeout(async () => {
      const { data } = await callAndParse('polymath_classify', { prompt });
      if (data?.domains) setClassified(data);
    }, 400);
    return () => clearTimeout(timer);
  }, [prompt]);

  // ── Create handler ─────────────────────────────────────────────────────
  const handleCreate = useCallback(async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    setError(null);
    setStatus('Classifying intent…');

    try {
      // Step 1: classify
      const { data: classData, error: classErr } = await callAndParse(
        'polymath_classify',
        { prompt },
      );
      if (classErr) throw new Error(classErr);

      const topDomain = classData?.domains?.[0]?.domain;
      if (!topDomain) throw new Error('Could not classify creative intent');

      setStatus(`Dispatching to ${topDomain}…`);

      // Step 2: dispatch
      const { data: dispatchData, error: dispatchErr } = await callAndParse(
        'polymath_dispatch',
        { domain: topDomain, prompt },
      );
      if (dispatchErr) throw new Error(dispatchErr);

      setStatus('Recording output…');

      // Step 3: push to stage
      await callConnector('stage_push_output', {
        domain: topDomain,
        title: prompt.slice(0, 80),
        source_tool: dispatchData?.tool ?? `polymath_dispatch:${topDomain}`,
        prompt,
        metadata: dispatchData?.suggested_args ?? {},
      });

      setStatus(null);
      setPrompt('');
      setClassified(null);
      await refreshOutputs();
    } catch (err: any) {
      setError(err?.message ?? String(err));
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [prompt, refreshOutputs]);

  // ── Pin toggle ─────────────────────────────────────────────────────────
  const togglePin = useCallback(async (id: string, currentlyPinned: boolean) => {
    await callConnector('stage_pin_output', { id, pinned: !currentlyPinned });
    await refreshOutputs();
  }, [refreshOutputs]);

  // ── Render ─────────────────────────────────────────────────────────────

  const tabs: { key: StageTab; label: string; icon: string }[] = [
    { key: 'create', label: 'Create', icon: '✨' },
    { key: 'gallery', label: 'Gallery', icon: '🎭' },
    { key: 'pipelines', label: 'Pipelines', icon: '⚡' },
  ];

  return (
    <AppShell visible={visible} onClose={onClose} title="The Stage" icon="🎭" width={960} maxHeightVh={90}>
      {/* ── Tabs ────────────────────────────────────────────────────── */}
      <div style={s.tabs}>
        {tabs.map(t => (
          <button
            key={t.key}
            style={{
              ...s.tab,
              ...(tab === t.key ? s.tabActive : {}),
            }}
            onClick={() => setTab(t.key)}
          >
            <span>{t.icon}</span> {t.label}
          </button>
        ))}
      </div>

      {/* ── Create Tab ──────────────────────────────────────────────── */}
      {tab === 'create' && (
        <div style={s.section}>
          <div style={s.promptRow}>
            <textarea
              ref={inputRef}
              style={s.promptInput}
              placeholder="Describe what you want to create… image, video, music, code, anything creative"
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleCreate();
                }
              }}
              rows={3}
              disabled={loading}
            />
            <button
              style={{
                ...s.createBtn,
                opacity: loading || !prompt.trim() ? 0.5 : 1,
              }}
              onClick={handleCreate}
              disabled={loading || !prompt.trim()}
            >
              {loading ? '⏳' : '🚀'} Create
            </button>
          </div>

          {/* Classification chips */}
          {classified && classified.domains.length > 0 && (
            <div style={s.chips}>
              {classified.domains.slice(0, 4).map((d, i) => (
                <span
                  key={d.domain}
                  style={{
                    ...s.chip,
                    borderColor: DOMAIN_COLORS[d.domain as StageDomain] ?? '#555',
                    opacity: 1 - i * 0.2,
                  }}
                >
                  {DOMAIN_ICONS[d.domain as StageDomain] ?? '🔮'}{' '}
                  {d.domain}{' '}
                  <span style={s.chipScore}>{(d.confidence * 100).toFixed(0)}%</span>
                </span>
              ))}
            </div>
          )}

          {/* Status / Error */}
          {status && <div style={s.statusMsg}>{status}</div>}
          {error && <div style={s.errorMsg}>⚠️ {error}</div>}

          {/* Recent outputs preview */}
          <div style={s.recentHeader}>
            <span style={s.sectionTitle}>Recent Creations</span>
          </div>
          {outputs.length === 0 ? (
            <div style={s.empty}>No creative outputs yet — type a prompt above to get started</div>
          ) : (
            <div style={s.grid}>
              {outputs.slice(0, 8).map(o => (
                <div
                  key={o.id}
                  style={s.card}
                  onClick={() => setSelectedOutput(o)}
                >
                  <div style={{
                    ...s.cardBadge,
                    backgroundColor: DOMAIN_COLORS[o.domain] ?? '#555',
                  }}>
                    {DOMAIN_ICONS[o.domain] ?? '🔮'}
                  </div>
                  <div style={s.cardTitle}>{o.title}</div>
                  <div style={s.cardMeta}>
                    {o.domain} · {o.source_tool}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Gallery Tab ─────────────────────────────────────────────── */}
      {tab === 'gallery' && (
        <div style={s.section}>
          <div style={s.filterRow}>
            <button
              style={{ ...s.filterBtn, ...(domainFilter === 'all' ? s.filterActive : {}) }}
              onClick={() => setDomainFilter('all')}
            >
              All
            </button>
            {(Object.keys(DOMAIN_ICONS) as StageDomain[]).map(d => (
              <button
                key={d}
                style={{ ...s.filterBtn, ...(domainFilter === d ? s.filterActive : {}) }}
                onClick={() => setDomainFilter(d)}
              >
                {DOMAIN_ICONS[d]} {d}
              </button>
            ))}
          </div>

          {outputs.length === 0 ? (
            <div style={s.empty}>No outputs match the current filter</div>
          ) : (
            <div style={s.grid}>
              {outputs.map(o => (
                <div
                  key={o.id}
                  style={{ ...s.card, ...(o.pinned ? s.cardPinned : {}) }}
                  onClick={() => setSelectedOutput(o)}
                >
                  <div style={s.cardTop}>
                    <div style={{
                      ...s.cardBadge,
                      backgroundColor: DOMAIN_COLORS[o.domain] ?? '#555',
                    }}>
                      {DOMAIN_ICONS[o.domain] ?? '🔮'}
                    </div>
                    <button
                      style={s.pinBtn}
                      onClick={e => { e.stopPropagation(); togglePin(o.id, o.pinned); }}
                      title={o.pinned ? 'Unpin' : 'Pin'}
                    >
                      {o.pinned ? '📌' : '📍'}
                    </button>
                  </div>
                  <div style={s.cardTitle}>{o.title}</div>
                  <div style={s.cardMeta}>
                    {o.domain} · {new Date(o.created_at).toLocaleDateString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Pipelines Tab ───────────────────────────────────────────── */}
      {tab === 'pipelines' && (
        <div style={s.section}>
          <div style={s.sectionTitle}>Active Pipelines</div>
          {pipelines.length === 0 ? (
            <div style={s.empty}>
              No active pipelines — use multi-step creative workflows to see them here
            </div>
          ) : (
            <div style={s.pipelineList}>
              {pipelines.map(p => (
                <div key={p.id} style={s.pipelineCard}>
                  <div style={s.pipelineName}>
                    {p.template ?? 'Custom Pipeline'}{' '}
                    <span style={s.pipelineStatus}>{p.status}</span>
                  </div>
                  <div style={s.progressBar}>
                    <div
                      style={{
                        ...s.progressFill,
                        width: `${(p.progress ?? 0) * 100}%`,
                      }}
                    />
                  </div>
                  <div style={s.pipelineMeta}>
                    {p.steps_completed}/{p.steps_total} steps ·{' '}
                    {new Date(p.created_at).toLocaleTimeString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Output detail modal ─────────────────────────────────────── */}
      {selectedOutput && (
        <div style={s.detailOverlay} onClick={() => setSelectedOutput(null)}>
          <div style={s.detailPanel} onClick={e => e.stopPropagation()}>
            <div style={s.detailHeader}>
              <span>
                {DOMAIN_ICONS[selectedOutput.domain]}{' '}
                {selectedOutput.title}
              </span>
              <button style={s.detailClose} onClick={() => setSelectedOutput(null)}>✕</button>
            </div>
            <div style={s.detailBody}>
              <div style={s.detailRow}>
                <span style={s.detailLabel}>Domain</span>
                <span style={{
                  ...s.domainBadge,
                  borderColor: DOMAIN_COLORS[selectedOutput.domain],
                }}>
                  {selectedOutput.domain}
                </span>
              </div>
              <div style={s.detailRow}>
                <span style={s.detailLabel}>Renderer</span>
                <span>{selectedOutput.renderer}</span>
              </div>
              <div style={s.detailRow}>
                <span style={s.detailLabel}>Source Tool</span>
                <span style={s.mono}>{selectedOutput.source_tool}</span>
              </div>
              {selectedOutput.file_path && (
                <div style={s.detailRow}>
                  <span style={s.detailLabel}>File</span>
                  <span style={s.mono}>{selectedOutput.file_path}</span>
                </div>
              )}
              {selectedOutput.url && (
                <div style={s.detailRow}>
                  <span style={s.detailLabel}>URL</span>
                  <span style={s.mono}>{selectedOutput.url}</span>
                </div>
              )}
              <div style={s.detailRow}>
                <span style={s.detailLabel}>Created</span>
                <span>{new Date(selectedOutput.created_at).toLocaleString()}</span>
              </div>
              <div style={s.detailRow}>
                <span style={s.detailLabel}>Pinned</span>
                <button
                  style={s.pinToggle}
                  onClick={() => {
                    togglePin(selectedOutput.id, selectedOutput.pinned);
                    setSelectedOutput({ ...selectedOutput, pinned: !selectedOutput.pinned });
                  }}
                >
                  {selectedOutput.pinned ? '📌 Pinned' : '📍 Pin this'}
                </button>
              </div>

              {/* Domain-specific preview placeholder */}
              <div style={s.previewArea}>
                {selectedOutput.thumbnail ? (
                  <img
                    src={selectedOutput.thumbnail}
                    alt={selectedOutput.title}
                    style={s.previewImg}
                  />
                ) : (
                  <div style={s.previewPlaceholder}>
                    <span style={{ fontSize: 48 }}>{DOMAIN_ICONS[selectedOutput.domain]}</span>
                    <span style={{ marginTop: 8, color: '#888' }}>
                      {selectedOutput.renderer} preview
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </AppShell>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const s: Record<string, React.CSSProperties> = {
  tabs: {
    display: 'flex',
    gap: 4,
    marginBottom: 16,
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    paddingBottom: 8,
  },
  tab: {
    background: 'transparent',
    border: '1px solid transparent',
    borderRadius: 8,
    color: '#888',
    fontSize: 13,
    padding: '6px 14px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    transition: 'all 0.15s',
  },
  tabActive: {
    background: 'rgba(0, 240, 255, 0.08)',
    borderColor: 'rgba(0, 240, 255, 0.3)',
    color: '#00f0ff',
  },
  section: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 12,
  },
  promptRow: {
    display: 'flex',
    gap: 10,
    alignItems: 'flex-start',
  },
  promptInput: {
    flex: 1,
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 12,
    color: '#f0f0f0',
    fontSize: 14,
    padding: '12px 14px',
    resize: 'none' as const,
    fontFamily: "'Inter', system-ui, sans-serif",
    outline: 'none',
    lineHeight: 1.5,
  },
  createBtn: {
    background: 'linear-gradient(135deg, #00f0ff 0%, #8A2BE2 100%)',
    border: 'none',
    borderRadius: 12,
    color: '#fff',
    fontSize: 14,
    fontWeight: 600,
    padding: '12px 20px',
    cursor: 'pointer',
    whiteSpace: 'nowrap' as const,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    transition: 'opacity 0.15s',
  },
  chips: {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap' as const,
  },
  chip: {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid',
    borderRadius: 20,
    padding: '3px 10px',
    fontSize: 12,
    color: '#ccc',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
  },
  chipScore: {
    color: '#666',
    fontSize: 10,
  },
  statusMsg: {
    color: '#00f0ff',
    fontSize: 13,
    padding: '4px 0',
  },
  errorMsg: {
    color: '#f87171',
    fontSize: 13,
    padding: '4px 0',
  },
  recentHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginTop: 8,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: 600,
    color: '#888',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
  },
  empty: {
    textAlign: 'center' as const,
    color: '#555',
    fontSize: 13,
    padding: '32px 16px',
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
    gap: 10,
  },
  card: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 12,
    padding: 14,
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  cardPinned: {
    borderColor: 'rgba(0, 240, 255, 0.2)',
    background: 'rgba(0, 240, 255, 0.03)',
  },
  cardTop: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  cardBadge: {
    width: 28,
    height: 28,
    borderRadius: 8,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 14,
    marginBottom: 8,
  },
  cardTitle: {
    fontSize: 13,
    fontWeight: 500,
    color: '#e0e0e0',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
    marginBottom: 4,
  },
  cardMeta: {
    fontSize: 11,
    color: '#666',
  },
  pinBtn: {
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    fontSize: 14,
    padding: 2,
  },
  filterRow: {
    display: 'flex',
    gap: 6,
    flexWrap: 'wrap' as const,
    marginBottom: 4,
  },
  filterBtn: {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16,
    color: '#888',
    fontSize: 11,
    padding: '4px 10px',
    cursor: 'pointer',
    transition: 'all 0.15s',
  },
  filterActive: {
    background: 'rgba(0, 240, 255, 0.1)',
    borderColor: 'rgba(0, 240, 255, 0.3)',
    color: '#00f0ff',
  },
  pipelineList: {
    display: 'flex',
    flexDirection: 'column' as const,
    gap: 8,
  },
  pipelineCard: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 10,
    padding: 12,
  },
  pipelineName: {
    fontSize: 13,
    fontWeight: 500,
    color: '#e0e0e0',
    marginBottom: 8,
  },
  pipelineStatus: {
    fontSize: 11,
    color: '#888',
    marginLeft: 8,
  },
  progressBar: {
    height: 4,
    background: 'rgba(255,255,255,0.06)',
    borderRadius: 2,
    overflow: 'hidden',
    marginBottom: 6,
  },
  progressFill: {
    height: '100%',
    background: 'linear-gradient(90deg, #00f0ff, #8A2BE2)',
    borderRadius: 2,
    transition: 'width 0.3s ease',
  },
  pipelineMeta: {
    fontSize: 11,
    color: '#666',
  },
  // ── Detail overlay ─────────────────────────────────────────────────────
  detailOverlay: {
    position: 'fixed' as const,
    inset: 0,
    background: 'rgba(0,0,0,0.6)',
    backdropFilter: 'blur(8px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 200,
  },
  detailPanel: {
    background: 'rgba(12, 12, 20, 0.98)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 16,
    width: 520,
    maxWidth: '90vw',
    maxHeight: '80vh',
    overflow: 'auto',
  },
  detailHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '14px 18px',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    fontSize: 14,
    fontWeight: 600,
    color: '#fff',
  },
  detailClose: {
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8,
    color: '#888',
    fontSize: 12,
    width: 28,
    height: 28,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  detailBody: {
    padding: '14px 18px 18px',
  },
  detailRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '6px 0',
    borderBottom: '1px solid rgba(255,255,255,0.03)',
    fontSize: 13,
    color: '#ccc',
  },
  detailLabel: {
    color: '#888',
    fontSize: 12,
    fontWeight: 500,
  },
  domainBadge: {
    border: '1px solid',
    borderRadius: 12,
    padding: '2px 8px',
    fontSize: 11,
  },
  mono: {
    fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
    fontSize: 12,
    color: '#a0a0a0',
    maxWidth: 280,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap' as const,
  },
  pinToggle: {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 8,
    color: '#ccc',
    fontSize: 12,
    padding: '3px 10px',
    cursor: 'pointer',
  },
  previewArea: {
    marginTop: 14,
    background: 'rgba(0,0,0,0.3)',
    border: '1px solid rgba(255,255,255,0.06)',
    borderRadius: 12,
    minHeight: 180,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  previewImg: {
    maxWidth: '100%',
    maxHeight: 300,
    objectFit: 'contain' as const,
  },
  previewPlaceholder: {
    display: 'flex',
    flexDirection: 'column' as const,
    alignItems: 'center',
    gap: 4,
    padding: 32,
  },
};

/**
 * FridayMonitor.tsx — System Monitor app for Agent Friday
 *
 * IPC: window.eve.system?.getStats(), window.eve.system?.getProcesses()
 * Auto-refreshes every 3 seconds. Graceful fallback if backend not available.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import AppShell from '../AppShell';
import ContextBar from '../ContextBar';

interface MonitorProps {
  visible: boolean;
  onClose: () => void;
}

interface SystemStats {
  cpuPercent: number;
  memUsedMB: number;
  memTotalMB: number;
  diskUsedGB: number;
  diskTotalGB: number;
  uptime: number;
}

interface ProcessInfo {
  name: string;
  pid: number;
  cpu: number;
  mem: number;
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const parts: string[] = [];
  if (d > 0) parts.push(`${d}d`);
  if (h > 0) parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(' ');
}

function formatMB(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

function GaugeBar({ label, value, max, unit, color }: {
  label: string; value: number; max: number; unit: string; color: string;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div style={s.gauge}>
      <div style={s.gaugeHeader}>
        <span style={s.gaugeLabel}>{label}</span>
        <span style={{ ...s.gaugeValue, color }}>{pct.toFixed(1)}%</span>
      </div>
      <div style={s.gaugeTrack}>
        <div
          style={{
            ...s.gaugeFill,
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${color}, ${color}88)`,
            transition: 'width 0.6s ease',
          }}
        />
      </div>
      <div style={s.gaugeDetail}>
        {unit === '%' ? `${value.toFixed(1)}%` : `${formatMB(value)} / ${formatMB(max)}`}
      </div>
    </div>
  );
}

export default function FridayMonitor({ visible, onClose }: MonitorProps) {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [processes, setProcesses] = useState<ProcessInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const sysStats = await (window as any).eve?.system?.getStats();
      if (sysStats) {
        setStats(sysStats);
        setError(null);
      } else {
        setError('Backend not available');
      }
    } catch {
      setError('Backend not available — system stats require the Electron backend');
    }

    try {
      const procList = await (window as any).eve?.system?.getProcesses();
      if (Array.isArray(procList)) {
        setProcesses(procList.slice(0, 30));
      }
    } catch {
      // Silently ignore — processes are optional
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    fetchData();
    timerRef.current = setInterval(fetchData, 3000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [visible, fetchData]);

  return (
    <AppShell visible={visible} onClose={onClose} title="System Monitor" icon="📊" width={720}>
      <ContextBar appId="friday-monitor" />
      {loading && !stats && !error && (
        <div style={s.center}>
          <span style={s.spinner}>⟳</span>
          <span style={s.loadingText}>Loading system stats...</span>
        </div>
      )}

      {error && !stats && (
        <div style={s.errorBox}>
          <div style={s.errorIcon}>⚠️</div>
          <div style={s.errorTitle}>Backend Not Available</div>
          <div style={s.errorMsg}>
            System monitoring requires IPC backend (window.eve.system).
            Stats will appear once the backend is connected.
          </div>
        </div>
      )}

      {stats && (
        <>
          {/* Gauges */}
          <div style={s.gaugesRow}>
            <GaugeBar label="CPU" value={stats.cpuPercent} max={100} unit="%" color="#00f0ff" />
            <GaugeBar label="Memory" value={stats.memUsedMB} max={stats.memTotalMB} unit="mb" color="#8A2BE2" />
            <GaugeBar label="Disk" value={stats.diskUsedGB * 1024} max={stats.diskTotalGB * 1024} unit="mb" color="#f97316" />
          </div>

          {/* Uptime */}
          <div style={s.uptimeBar}>
            <span style={s.uptimeLabel}>Uptime</span>
            <span style={s.uptimeValue}>{formatUptime(stats.uptime)}</span>
          </div>

          {/* Process Table */}
          <div style={s.sectionLabel}>Processes ({processes.length})</div>
          <div style={s.tableWrap}>
            <table style={s.table}>
              <thead>
                <tr>
                  <th style={{ ...s.th, width: '50%' }}>Name</th>
                  <th style={{ ...s.th, width: '15%' }}>PID</th>
                  <th style={{ ...s.th, width: '17%' }}>CPU %</th>
                  <th style={{ ...s.th, width: '18%' }}>Memory</th>
                </tr>
              </thead>
              <tbody>
                {processes.length === 0 && (
                  <tr>
                    <td colSpan={4} style={s.emptyRow}>No process data</td>
                  </tr>
                )}
                {processes.map((p, i) => (
                  <tr key={`${p.pid}-${i}`} style={i % 2 === 0 ? s.evenRow : undefined}>
                    <td style={s.td}>{p.name}</td>
                    <td style={{ ...s.td, color: '#8888a0' }}>{p.pid}</td>
                    <td style={{ ...s.td, color: p.cpu > 50 ? '#ef4444' : '#00f0ff' }}>
                      {p.cpu.toFixed(1)}%
                    </td>
                    <td style={{ ...s.td, color: '#8A2BE2' }}>{formatMB(p.mem)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </AppShell>
  );
}

/* ── Styles ───────────────────────────────────────────────── */
const s: Record<string, React.CSSProperties> = {
  center: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    padding: 40,
  },
  spinner: {
    fontSize: 32,
    color: '#00f0ff',
    animation: 'spin 1s linear infinite',
  },
  loadingText: { color: '#8888a0', fontSize: 14 },
  errorBox: {
    background: 'rgba(239, 68, 68, 0.06)',
    border: '1px solid rgba(239, 68, 68, 0.2)',
    borderRadius: 12,
    padding: 24,
    textAlign: 'center',
  },
  errorIcon: { fontSize: 32, marginBottom: 8 },
  errorTitle: { color: '#ef4444', fontSize: 16, fontWeight: 700, marginBottom: 6 },
  errorMsg: { color: '#8888a0', fontSize: 13, lineHeight: 1.5 },
  gaugesRow: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: 14,
  },
  gauge: {
    background: 'rgba(255,255,255,0.03)',
    borderRadius: 12,
    padding: 14,
    border: '1px solid rgba(255,255,255,0.07)',
  },
  gaugeHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  gaugeLabel: {
    color: '#F8FAFC',
    fontSize: 13,
    fontWeight: 600,
  },
  gaugeValue: {
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 14,
    fontWeight: 700,
  },
  gaugeTrack: {
    height: 8,
    background: 'rgba(255,255,255,0.06)',
    borderRadius: 4,
    overflow: 'hidden',
  },
  gaugeFill: {
    height: '100%',
    borderRadius: 4,
  },
  gaugeDetail: {
    marginTop: 6,
    fontSize: 11,
    color: '#8888a0',
    fontFamily: "'JetBrains Mono', monospace",
  },
  uptimeBar: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 16px',
    background: 'rgba(255,255,255,0.03)',
    borderRadius: 10,
    border: '1px solid rgba(255,255,255,0.07)',
  },
  uptimeLabel: { color: '#8888a0', fontSize: 13, fontWeight: 600 },
  uptimeValue: {
    color: '#22c55e',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 14,
    fontWeight: 600,
  },
  sectionLabel: {
    fontSize: 12,
    fontWeight: 600,
    color: '#8888a0',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.05em',
  },
  tableWrap: {
    borderRadius: 10,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)',
    maxHeight: 260,
    overflowY: 'auto',
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse' as const,
    fontSize: 12,
    fontFamily: "'JetBrains Mono', monospace",
  },
  th: {
    textAlign: 'left' as const,
    padding: '8px 12px',
    color: '#8888a0',
    fontWeight: 600,
    fontSize: 11,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.04em',
    borderBottom: '1px solid rgba(255,255,255,0.07)',
    background: 'rgba(255,255,255,0.03)',
    position: 'sticky' as const,
    top: 0,
  },
  td: {
    padding: '6px 12px',
    color: '#F8FAFC',
    borderBottom: '1px solid rgba(255,255,255,0.03)',
    whiteSpace: 'nowrap' as const,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: 200,
  },
  evenRow: { background: 'rgba(255,255,255,0.02)' },
  emptyRow: {
    padding: 20,
    textAlign: 'center' as const,
    color: '#4a4a62',
    fontStyle: 'italic',
  },
};

/**
 * HudOverlay.tsx — Holographic HUD Overlay
 *
 * Designed by Gemini, wired for Agent Friday.
 * Renders the ethereal HUD elements over the 3D desktop visualization:
 *   - Corner optical arcs with glow dots
 *   - Header: title, laws status, clock
 *   - Left sidebar: API provider status panel (collapsible)
 *   - Right sidebar: Telemetry bars → App tray toggle
 *   - Footer: evolution name + build info
 *   - Evolution control panel (near-invisible until hovered)
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useMood } from '../contexts/MoodContext';
import { EVOLUTION_PATH } from './DesktopViz';
import AppLaunchpad from './AppLaunchpad';
import type { SemanticState } from './FridayCore';
import '../styles/desktop-viz.css';

// ── Types ────────────────────────────────────────────────────────────────────

export interface ApiStatus {
  gemini: 'connected' | 'connecting' | 'offline' | 'no-key';
  claude: 'connected' | 'ready' | 'offline' | 'no-key';
  elevenlabs: 'connected' | 'ready' | 'offline' | 'no-key';
  openrouter: 'connected' | 'ready' | 'offline' | 'no-key';
  browser: 'ready' | 'unavailable';
}

export interface HudOverlayProps {
  apiStatus: ApiStatus;
  semanticState: SemanticState;
  evolutionIndex: number;
  onEvolutionChange?: (index: number) => void;
  /** Universal app opener — called with app registry ID */
  onOpenApp: (id: string) => void;
  clockStr?: string;
  /** Dev mode — shows extra controls */
  devMode?: boolean;
}

// ── Status helpers ───────────────────────────────────────────────────────────

function apiDotColor(status: string): 'green' | 'yellow' | 'red' {
  if (status === 'connected' || status === 'ready') return 'green';
  if (status === 'connecting') return 'yellow';
  return 'red';
}

function apiLabel(key: string): string {
  const labels: Record<string, string> = {
    gemini: 'GEMINI',
    claude: 'CLAUDE',
    elevenlabs: 'VOICE',
    openrouter: 'ROUTER',
    browser: 'BROWSER',
  };
  return labels[key] || key.toUpperCase();
}

// ── Component ────────────────────────────────────────────────────────────────

export default function HudOverlay({
  apiStatus,
  semanticState,
  evolutionIndex,
  onEvolutionChange,
  onOpenApp,
  clockStr,
  devMode = false,
}: HudOverlayProps) {
  const mood = useMood();
  const [apiPanelCollapsed, setApiPanelCollapsed] = useState(false);
  const [appTrayOpen, setAppTrayOpen] = useState(false);

  // Telemetry bars — randomized widths that shift subtly over time
  const [telemetryWidths, setTelemetryWidths] = useState<number[]>(() =>
    Array.from({ length: 12 }, () => 10 + Math.random() * 30),
  );
  useEffect(() => {
    const id = setInterval(() => {
      setTelemetryWidths((prev) =>
        prev.map((w) => Math.max(5, Math.min(40, w + (Math.random() - 0.5) * 8))),
      );
    }, 2000);
    return () => clearInterval(id);
  }, []);

  // Current mood name for header display
  const moodLabel = mood.currentMood.toUpperCase();

  // Evolution info
  const currentEvo = EVOLUTION_PATH[evolutionIndex] || EVOLUTION_PATH[0];

  // Status text
  const statusText = useMemo(() => {
    switch (semanticState) {
      case 'EXECUTING': return 'EXECUTING';
      case 'REASONING': return 'REASONING';
      case 'SUB_AGENTS': return 'AGENTS ACTIVE';
      default: return 'LISTENING';
    }
  }, [semanticState]);

  // Laws status (Asimov-style readout)
  const lawsStatus = useMemo(() => {
    const geminiOk = apiStatus.gemini === 'connected';
    return {
      color: geminiOk ? 'green' as const : apiStatus.gemini === 'connecting' ? 'yellow' as const : 'red' as const,
      text: geminiOk ? 'LAWS: ACTIVE' : 'LAWS: STANDBY',
    };
  }, [apiStatus.gemini]);

  return (
    <div id="hud-overlay">
      {/* ── Corner Arcs ── */}
      <div className="hud-corner top-left"><div className="dot" /></div>
      <div className="hud-corner top-right"><div className="dot" /></div>
      <div className="hud-corner bottom-left"><div className="dot" /></div>
      <div className="hud-corner bottom-right"><div className="dot" /></div>

      {/* ── Header ── */}
      <div className="hud-header">
        <div className="hud-title-container">
          <span className="hud-main-title">AGENT FRIDAY</span>
          <span>{statusText} · {moodLabel}</span>
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'flex-start', gap: 16 }}>
          <div>
            <div className="hud-laws">
              <span className={`status-dot ${lawsStatus.color}`} />
              <span>{lawsStatus.text}</span>
            </div>
            {clockStr && <div style={{ marginTop: 4, letterSpacing: '3px' }}>{clockStr}</div>}
          </div>
          {/* Settings gear — always visible for discoverability */}
          <button
            onClick={() => onOpenApp('settings')}
            title="Settings"
            aria-label="Open settings"
            style={{
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid rgba(255, 255, 255, 0.06)',
              borderRadius: 8,
              width: 32,
              height: 32,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              color: 'rgba(255, 255, 255, 0.35)',
              fontSize: 16,
              transition: 'all 0.2s ease',
              flexShrink: 0,
              marginTop: 2,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = '#00f0ff';
              e.currentTarget.style.borderColor = 'rgba(0, 240, 255, 0.3)';
              e.currentTarget.style.background = 'rgba(0, 240, 255, 0.06)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'rgba(255, 255, 255, 0.35)';
              e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.06)';
              e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)';
            }}
          >
            {'⚙'}
          </button>
        </div>
      </div>

      {/* ── API Provider Panel (left sidebar) ── */}
      <div
        className={`hud-api-panel ${apiPanelCollapsed ? 'collapsed' : ''}`}
        onClick={() => setApiPanelCollapsed(!apiPanelCollapsed)}
        title={apiPanelCollapsed ? 'Expand API status' : 'Collapse'}
      >
        {Object.entries(apiStatus).map(([key, status]) => (
          <div className="api-item" key={key}>
            <span className={`status-dot ${apiDotColor(status)}`} />
            <span className="api-name">{apiLabel(key)}</span>
          </div>
        ))}
      </div>

      {/* ── Telemetry Bars + App Tray trigger (right sidebar) ── */}
      <div
        className="hud-side-panel"
        onClick={() => setAppTrayOpen(!appTrayOpen)}
        title="Toggle app tray"
      >
        {telemetryWidths.map((w, i) => (
          <div
            key={i}
            className="telemetry-bar"
            style={{ width: `${w}px`, opacity: 0.3 + (w / 40) * 0.7 }}
          />
        ))}
      </div>

      {/* ── App Tray — full categorized launchpad ── */}
      <div id="desktop-app-tray" className={appTrayOpen ? 'open' : ''}>
        <AppLaunchpad onOpenApp={(id) => { setAppTrayOpen(false); onOpenApp(id); }} />
      </div>

      {/* ── Footer ── */}
      <div className="hud-footer">
        <div>
          <div>FORM: {currentEvo.name}</div>
          <div style={{ opacity: 0.5, marginTop: 4 }}>PHASE {evolutionIndex + 1} / {EVOLUTION_PATH.length}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div>Agent Friday · v3.1.1</div>
          <a
            className="discord-link"
            href="#"
            onClick={(e) => { e.preventDefault(); window.eve?.shell?.openPath?.('https://discord.gg/8af2bFqn'); }}
            style={{ marginTop: 4, display: 'inline-block' }}
          >
            JOIN DISCORD
          </a>
        </div>
      </div>

      {/* ── Evolution Control Panel (near-invisible until hovered) ── */}
      <div id="evolution-control-panel">
        {/* Structure column */}
        <div className="panel-section">
          <div className="panel-title">STRUCTURE</div>
          {EVOLUTION_PATH.map((evo, idx) => (
            <button
              key={evo.id}
              className={`evo-btn ${idx === evolutionIndex ? 'active' : ''}`}
              onClick={() => onEvolutionChange?.(idx)}
            >
              {evo.name}
            </button>
          ))}
        </div>

        {/* Mood override column (dev only) */}
        {devMode && (
          <div className="panel-section">
            <div className="panel-title">MOOD</div>
            {['LISTENING', 'REASONING', 'EXECUTING', 'EXCITED', 'CALM'].map((m) => (
              <button
                key={m}
                className={`evo-btn mood-btn ${semanticState === m ? 'active' : ''}`}
                disabled
              >
                {m}
              </button>
            ))}
            <button
              className={`evo-btn dev-btn ${devMode ? 'active' : ''}`}
              disabled
            >
              DEV MODE
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

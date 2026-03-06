/**
 * FridayCanvas.tsx — Drawing canvas app for Agent Friday
 *
 * Pure HTML5 Canvas drawing with tools: pen, eraser, line, rectangle, circle, fill.
 * Includes undo/redo, color picker, brush size, and PNG export.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import AppShell from '../AppShell';

interface Props {
  visible: boolean;
  onClose: () => void;
}

type Tool = 'pen' | 'eraser' | 'line' | 'rectangle' | 'circle' | 'fill';

interface Point { x: number; y: number }

const PRESET_COLORS = [
  '#FFFFFF', '#F8FAFC', '#ef4444', '#f97316', '#eab308',
  '#22c55e', '#00f0ff', '#3b82f6', '#8A2BE2', '#ec4899',
  '#000000', '#4a4a62', '#8888a0', '#6b7280', '#a3a3a3',
];

const CANVAS_BG = '#1a1a2e';

const TOOL_DEFS: { id: Tool; icon: string; label: string }[] = [
  { id: 'pen', icon: '✏️', label: 'Pen' },
  { id: 'eraser', icon: '🧹', label: 'Eraser' },
  { id: 'line', icon: '📏', label: 'Line' },
  { id: 'rectangle', icon: '⬜', label: 'Rect' },
  { id: 'circle', icon: '⭕', label: 'Circle' },
  { id: 'fill', icon: '🪣', label: 'Fill' },
];

export default function FridayCanvas({ visible, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [tool, setTool] = useState<Tool>('pen');
  const [color, setColor] = useState('#00f0ff');
  const [brushSize, setBrushSize] = useState(3);
  const [hexInput, setHexInput] = useState('#00f0ff');

  // Undo / Redo stacks (ImageData snapshots)
  const undoStackRef = useRef<ImageData[]>([]);
  const redoStackRef = useRef<ImageData[]>([]);
  const [undoCount, setUndoCount] = useState(0);
  const [redoCount, setRedoCount] = useState(0);

  // Drawing state
  const isDrawingRef = useRef(false);
  const startPointRef = useRef<Point>({ x: 0, y: 0 });
  const lastPointRef = useRef<Point>({ x: 0, y: 0 });
  const snapshotRef = useRef<ImageData | null>(null);

  // Initialize canvas
  const initCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const rect = container.getBoundingClientRect();
    canvas.width = Math.floor(rect.width);
    canvas.height = Math.floor(rect.height);

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.fillStyle = CANVAS_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // Save initial state
    undoStackRef.current = [ctx.getImageData(0, 0, canvas.width, canvas.height)];
    redoStackRef.current = [];
    setUndoCount(0);
    setRedoCount(0);
  }, []);

  useEffect(() => {
    if (visible) {
      // Slight delay so the shell renders and we get correct bounds
      const t = setTimeout(initCanvas, 50);
      return () => clearTimeout(t);
    }
  }, [visible, initCanvas]);

  // Save snapshot before a stroke
  const saveSnapshot = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    snapshotRef.current = ctx.getImageData(0, 0, canvas.width, canvas.height);
  }, []);

  // Push current state onto undo stack
  const pushUndo = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    undoStackRef.current.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    if (undoStackRef.current.length > 50) undoStackRef.current.shift();
    redoStackRef.current = [];
    setUndoCount(undoStackRef.current.length - 1);
    setRedoCount(0);
  }, []);

  // Get canvas-local coordinates from mouse event
  const getCanvasPoint = useCallback((e: React.MouseEvent): Point => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left) * (canvas.width / rect.width),
      y: (e.clientY - rect.top) * (canvas.height / rect.height),
    };
  }, []);

  // Flood fill algorithm
  const floodFill = useCallback((startX: number, startY: number, fillColor: string) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    const imageData = ctx.getImageData(0, 0, w, h);
    const data = imageData.data;

    // Parse fill color
    const temp = document.createElement('canvas').getContext('2d')!;
    temp.fillStyle = fillColor;
    temp.fillRect(0, 0, 1, 1);
    const fc = temp.getImageData(0, 0, 1, 1).data;

    const sx = Math.floor(startX);
    const sy = Math.floor(startY);
    if (sx < 0 || sx >= w || sy < 0 || sy >= h) return;

    const idx = (sy * w + sx) * 4;
    const tr = data[idx], tg = data[idx + 1], tb = data[idx + 2], ta = data[idx + 3];

    if (tr === fc[0] && tg === fc[1] && tb === fc[2] && ta === fc[3]) return;

    const tolerance = 30;
    const match = (i: number) =>
      Math.abs(data[i] - tr) <= tolerance &&
      Math.abs(data[i + 1] - tg) <= tolerance &&
      Math.abs(data[i + 2] - tb) <= tolerance &&
      Math.abs(data[i + 3] - ta) <= tolerance;

    const stack: number[] = [sx, sy];
    const visited = new Uint8Array(w * h);

    while (stack.length > 0) {
      const cy = stack.pop()!;
      const cx = stack.pop()!;
      const ci = cy * w + cx;

      if (cx < 0 || cx >= w || cy < 0 || cy >= h) continue;
      if (visited[ci]) continue;
      const pi = ci * 4;
      if (!match(pi)) continue;

      visited[ci] = 1;
      data[pi] = fc[0];
      data[pi + 1] = fc[1];
      data[pi + 2] = fc[2];
      data[pi + 3] = fc[3];

      stack.push(cx + 1, cy);
      stack.push(cx - 1, cy);
      stack.push(cx, cy + 1);
      stack.push(cx, cy - 1);
    }

    ctx.putImageData(imageData, 0, 0);
  }, []);

  // ── Mouse handlers ────────────────────────────────────────────────────────

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    const point = getCanvasPoint(e);
    isDrawingRef.current = true;
    startPointRef.current = point;
    lastPointRef.current = point;
    saveSnapshot();

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    if (tool === 'fill') {
      pushUndo();
      floodFill(point.x, point.y, color);
      isDrawingRef.current = false;
      pushUndo();
      return;
    }

    if (tool === 'pen' || tool === 'eraser') {
      ctx.beginPath();
      ctx.moveTo(point.x, point.y);
      ctx.strokeStyle = tool === 'eraser' ? CANVAS_BG : color;
      ctx.lineWidth = tool === 'eraser' ? brushSize * 3 : brushSize;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      // Draw a dot for single click
      ctx.lineTo(point.x + 0.1, point.y + 0.1);
      ctx.stroke();
    }
  }, [tool, color, brushSize, getCanvasPoint, saveSnapshot, pushUndo, floodFill]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isDrawingRef.current) return;
    const point = getCanvasPoint(e);
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    if (tool === 'pen' || tool === 'eraser') {
      ctx.strokeStyle = tool === 'eraser' ? CANVAS_BG : color;
      ctx.lineWidth = tool === 'eraser' ? brushSize * 3 : brushSize;
      ctx.lineTo(point.x, point.y);
      ctx.stroke();
      lastPointRef.current = point;
    } else if (tool === 'line' || tool === 'rectangle' || tool === 'circle') {
      // Restore snapshot, draw preview shape
      if (snapshotRef.current) {
        ctx.putImageData(snapshotRef.current, 0, 0);
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = brushSize;
      ctx.beginPath();

      const start = startPointRef.current;
      if (tool === 'line') {
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(point.x, point.y);
      } else if (tool === 'rectangle') {
        ctx.rect(start.x, start.y, point.x - start.x, point.y - start.y);
      } else if (tool === 'circle') {
        const rx = Math.abs(point.x - start.x) / 2;
        const ry = Math.abs(point.y - start.y) / 2;
        const cx = start.x + (point.x - start.x) / 2;
        const cy = start.y + (point.y - start.y) / 2;
        ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
      }
      ctx.stroke();
    }
  }, [tool, color, brushSize, getCanvasPoint]);

  const handleMouseUp = useCallback(() => {
    if (!isDrawingRef.current) return;
    isDrawingRef.current = false;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.closePath();

    pushUndo();
  }, [pushUndo]);

  // Undo
  const undo = useCallback(() => {
    if (undoStackRef.current.length <= 1) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const current = undoStackRef.current.pop()!;
    redoStackRef.current.push(current);

    const prev = undoStackRef.current[undoStackRef.current.length - 1];
    ctx.putImageData(prev, 0, 0);

    setUndoCount(undoStackRef.current.length - 1);
    setRedoCount(redoStackRef.current.length);
  }, []);

  // Redo
  const redo = useCallback(() => {
    if (redoStackRef.current.length === 0) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const next = redoStackRef.current.pop()!;
    undoStackRef.current.push(next);
    ctx.putImageData(next, 0, 0);

    setUndoCount(undoStackRef.current.length - 1);
    setRedoCount(redoStackRef.current.length);
  }, []);

  // Clear canvas
  const clearCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.fillStyle = CANVAS_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    pushUndo();
  }, [pushUndo]);

  // Export as PNG
  const exportPNG = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const link = document.createElement('a');
    link.download = `canvas-${Date.now()}.png`;
    link.href = canvas.toDataURL('image/png');
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, []);

  // Handle hex input change
  const handleHexChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setHexInput(val);
    if (/^#[0-9a-fA-F]{6}$/.test(val)) {
      setColor(val);
    }
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    if (!visible) return;
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
        e.preventDefault();
        if (e.shiftKey) redo(); else undo();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [visible, undo, redo]);

  return (
    <AppShell visible={visible} onClose={onClose} title="Canvas" icon="🎨" width={960} maxHeightVh={92}>
      <div style={s.layout}>
        {/* Tool sidebar */}
        <div style={s.sidebar}>
          {/* Tools */}
          <div style={s.sideSection}>
            <span style={s.sideLabel}>Tools</span>
            <div style={s.toolGrid}>
              {TOOL_DEFS.map((t) => (
                <button
                  key={t.id}
                  style={{
                    ...s.toolBtn,
                    background: tool === t.id ? 'rgba(0,240,255,0.1)' : 'rgba(255,255,255,0.03)',
                    borderColor: tool === t.id ? 'rgba(0,240,255,0.3)' : 'rgba(255,255,255,0.07)',
                    color: tool === t.id ? '#00f0ff' : '#8888a0',
                  }}
                  onClick={() => setTool(t.id)}
                  title={t.label}
                >
                  <span style={{ fontSize: 16 }}>{t.icon}</span>
                  <span style={{ fontSize: 10 }}>{t.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Colors */}
          <div style={s.sideSection}>
            <span style={s.sideLabel}>Color</span>
            <div style={s.colorGrid}>
              {PRESET_COLORS.map((c) => (
                <button
                  key={c}
                  style={{
                    ...s.colorSwatch,
                    background: c,
                    outline: color === c ? '2px solid #00f0ff' : '1px solid rgba(255,255,255,0.1)',
                    outlineOffset: color === c ? 2 : 0,
                  }}
                  onClick={() => { setColor(c); setHexInput(c); }}
                  title={c}
                />
              ))}
            </div>
            <input
              style={s.hexInput}
              value={hexInput}
              onChange={handleHexChange}
              placeholder="#00f0ff"
              maxLength={7}
            />
            <div style={{ ...s.colorPreview, background: color }} />
          </div>

          {/* Brush size */}
          <div style={s.sideSection}>
            <span style={s.sideLabel}>Size: {brushSize}px</span>
            <input
              type="range"
              min={1}
              max={40}
              value={brushSize}
              onChange={(e) => setBrushSize(Number(e.target.value))}
              style={s.slider}
            />
          </div>

          {/* Actions */}
          <div style={s.sideSection}>
            <div style={s.actionBtns}>
              <button style={s.actionBtn} onClick={undo} disabled={undoCount === 0} title="Undo (Ctrl+Z)">
                ↩ Undo
              </button>
              <button style={s.actionBtn} onClick={redo} disabled={redoCount === 0} title="Redo (Ctrl+Shift+Z)">
                ↪ Redo
              </button>
              <button style={{ ...s.actionBtn, color: '#ef4444' }} onClick={clearCanvas}>
                🗑 Clear
              </button>
              <button style={{ ...s.actionBtn, color: '#22c55e' }} onClick={exportPNG}>
                📤 Export PNG
              </button>
            </div>
          </div>
        </div>

        {/* Canvas */}
        <div ref={containerRef} style={s.canvasContainer}>
          <canvas
            ref={canvasRef}
            style={s.canvas}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
          />
        </div>
      </div>
    </AppShell>
  );
}

// ── Styles ──────────────────────────────────────────────────────────────────
const s: Record<string, React.CSSProperties> = {
  layout: {
    display: 'flex',
    gap: 16,
    height: '70vh',
    minHeight: 400,
  },
  sidebar: {
    width: 150,
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
    overflowY: 'auto',
  },
  sideSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  sideLabel: {
    fontSize: 11,
    fontWeight: 600,
    color: '#8888a0',
    textTransform: 'uppercase' as const,
    letterSpacing: '0.06em',
    fontFamily: "'Inter', system-ui, sans-serif",
  },
  toolGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(2, 1fr)',
    gap: 4,
  },
  toolBtn: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: 2,
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 8,
    padding: '8px 4px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    transition: 'background 0.12s',
    background: 'none',
  },
  colorGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(5, 1fr)',
    gap: 3,
  },
  colorSwatch: {
    width: '100%',
    aspectRatio: '1',
    border: 'none',
    borderRadius: 4,
    cursor: 'pointer',
    minHeight: 20,
  },
  hexInput: {
    width: '100%',
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6,
    color: '#F8FAFC',
    fontSize: 12,
    padding: '5px 8px',
    fontFamily: "'JetBrains Mono', monospace",
    outline: 'none',
    boxSizing: 'border-box' as const,
  },
  colorPreview: {
    height: 16,
    borderRadius: 4,
    border: '1px solid rgba(255,255,255,0.1)',
  },
  slider: {
    width: '100%',
    accentColor: '#00f0ff',
    cursor: 'pointer',
  },
  actionBtns: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  actionBtn: {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.07)',
    borderRadius: 6,
    color: '#8888a0',
    fontSize: 11,
    fontWeight: 500,
    padding: '6px 8px',
    cursor: 'pointer',
    fontFamily: "'Inter', system-ui, sans-serif",
    textAlign: 'left' as const,
    transition: 'background 0.12s',
  },
  canvasContainer: {
    flex: 1,
    borderRadius: 12,
    overflow: 'hidden',
    border: '1px solid rgba(255,255,255,0.07)',
    background: CANVAS_BG,
    cursor: 'crosshair',
    position: 'relative',
  },
  canvas: {
    display: 'block',
    width: '100%',
    height: '100%',
  },
};

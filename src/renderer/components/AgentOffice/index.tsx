/**
 * AgentOffice — Pixel-art agent office visualization.
 *
 * A canvas-based view that shows agents as pixel characters working
 * in a small tech office. Agents sit at desks when active (typing),
 * wander during idle moments, and show thought bubbles with their
 * current chain-of-thought.
 *
 * Renders in its own BrowserWindow, loaded via ?office=true.
 */

import React, { useRef, useEffect, useCallback, useState } from 'react';
import {
  getCharacterSprites,
  getCachedCanvas,
  clearCanvasCache,
  getFurnitureSprite,
  DESK_SPRITE,
} from './sprites';

/* ── Types (mirrored from office-types.ts — renderer can't import from main) ── */

const TileType = { VOID: 0, FLOOR: 1, WALL: 2 } as const;
const CharState = { IDLE: 'idle', WALK: 'walk', TYPE: 'type' } as const;
const Dir = { DOWN: 0, LEFT: 1, RIGHT: 2, UP: 3 } as const;

const TILE = 32;
const ZOOM = 2;
const WALK_SPEED = 64; // px/sec
const WALK_FRAME_DUR = 0.15;
const TYPE_FRAME_DUR = 0.4;
const WANDER_PAUSE_MIN = 4;
const WANDER_PAUSE_MAX = 10;
const WANDER_MOVES_MIN = 2;
const WANDER_MOVES_MAX = 5;
const SEAT_REST_MIN = 30;
const SEAT_REST_MAX = 90;
const SPAWN_DUR = 0.5;
const BUBBLE_DUR = 4;
const BUBBLE_FADE = 0.5;

interface Seat {
  id: string;
  col: number;
  row: number;
  facingDir: number;
  assigned: boolean;
  assignedTo: string | null;
}

interface FurnitureInst {
  type: string;
  col: number;
  row: number;
  zY: number;
}

interface OfficeLayout {
  cols: number;
  rows: number;
  tiles: number[];
  seats: Seat[];
  furniture: FurnitureInst[];
}

interface OfficeChar {
  id: string;
  name: string;
  x: number;
  y: number;
  tileCol: number;
  tileRow: number;
  state: string;
  dir: number;
  frame: number;
  frameTimer: number;
  path: Array<{ col: number; row: number }>;
  moveProgress: number;
  isActive: boolean;
  currentTool: string | null;
  seatId: string | null;
  palette: number;
  hueShift: number;
  bubbleText: string | null;
  bubbleTimer: number;
  bubbleType: string | null;
  spawnEffect: string | null;
  spawnTimer: number;
  role: string;
  teamId: string | null;
  wanderTimer: number;
  wanderCount: number;
  wanderLimit: number;
  seatTimer: number;
}

/* ── BFS Pathfinding (renderer-side) ──────────────────────────────── */

function isWalkable(
  col: number,
  row: number,
  layout: OfficeLayout,
  blocked: Set<string>
): boolean {
  if (col < 0 || row < 0 || col >= layout.cols || row >= layout.rows) return false;
  const tile = layout.tiles[row * layout.cols + col];
  if (tile === TileType.WALL || tile === TileType.VOID) return false;
  if (blocked.has(`${col},${row}`)) return false;
  return true;
}

function findPath(
  fc: number, fr: number, tc: number, tr: number,
  layout: OfficeLayout, blocked: Set<string>
): Array<{ col: number; row: number }> {
  if (fc === tc && fr === tr) return [];
  const key = (c: number, r: number) => `${c},${r}`;
  const visited = new Set<string>();
  const parent = new Map<string, string>();
  const queue: Array<{ col: number; row: number }> = [{ col: fc, row: fr }];
  visited.add(key(fc, fr));
  const dirs = [{ dc: 0, dr: -1 }, { dc: 0, dr: 1 }, { dc: -1, dr: 0 }, { dc: 1, dr: 0 }];

  while (queue.length > 0) {
    const cur = queue.shift()!;
    if (cur.col === tc && cur.row === tr) {
      const path: Array<{ col: number; row: number }> = [];
      let k = key(tc, tr);
      while (k !== key(fc, fr)) {
        const [c, r] = k.split(',').map(Number);
        path.unshift({ col: c, row: r });
        k = parent.get(k)!;
      }
      return path;
    }
    for (const { dc, dr } of dirs) {
      const nc = cur.col + dc, nr = cur.row + dr;
      const nk = key(nc, nr);
      if (!visited.has(nk) && isWalkable(nc, nr, layout, blocked)) {
        visited.add(nk);
        parent.set(nk, key(cur.col, cur.row));
        queue.push({ col: nc, row: nr });
      }
    }
  }
  return [];
}

function getWalkableTiles(layout: OfficeLayout, blocked: Set<string>): Array<{ col: number; row: number }> {
  const result: Array<{ col: number; row: number }> = [];
  for (let r = 0; r < layout.rows; r++) {
    for (let c = 0; c < layout.cols; c++) {
      if (isWalkable(c, r, layout, blocked)) result.push({ col: c, row: r });
    }
  }
  return result;
}

/* ── Component ────────────────────────────────────────────────────── */

const AgentOffice: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef<{
    layout: OfficeLayout | null;
    characters: Map<string, OfficeChar>;
    seats: Map<string, Seat>;
    blocked: Set<string>;
    lastTime: number;
  }>({
    layout: null,
    characters: new Map(),
    seats: new Map(),
    blocked: new Set(),
    lastTime: 0,
  });
  const animFrameRef = useRef<number>(0);
  const [ready, setReady] = useState(false);

  /* ── Receive initial state from main process ────────────────── */
  useEffect(() => {
    const eve = (window as any).eve;
    if (!eve) return;

    // Request initial state
    eve.office?.getState?.().then((snapshot: any) => {
      if (!snapshot) return;
      const s = stateRef.current;
      s.layout = snapshot.layout;

      // Build seat map
      s.seats.clear();
      for (const seat of snapshot.layout.seats) {
        s.seats.set(seat.id, { ...seat });
      }

      // Build blocked tiles
      s.blocked.clear();
      for (const f of snapshot.layout.furniture) {
        if (['desk', 'bookshelf', 'cooler', 'whiteboard'].includes(f.type)) {
          s.blocked.add(`${f.col},${f.row}`);
        }
      }

      // Hydrate characters
      s.characters.clear();
      for (const ch of snapshot.characters) {
        s.characters.set(ch.id, hydrateChar(ch));
      }

      setReady(true);
    });

    // Listen for IPC events from office-manager
    const listeners: Array<() => void> = [];

    const onFullState = (_e: any, data: any) => {
      const s = stateRef.current;
      s.layout = data.layout;
      s.seats.clear();
      for (const seat of data.layout.seats) {
        s.seats.set(seat.id, { ...seat });
      }
      s.blocked.clear();
      for (const f of data.layout.furniture) {
        if (['desk', 'bookshelf', 'cooler', 'whiteboard'].includes(f.type)) {
          s.blocked.add(`${f.col},${f.row}`);
        }
      }
      s.characters.clear();
      for (const ch of data.characters) {
        s.characters.set(ch.id, hydrateChar(ch));
      }
      setReady(true);
    };

    const onSpawned = (_e: any, ch: any) => {
      stateRef.current.characters.set(ch.id, hydrateChar(ch));
    };

    const onThought = (_e: any, data: { id: string; text: string; phase?: string }) => {
      const ch = stateRef.current.characters.get(data.id);
      if (ch) {
        ch.bubbleText = data.text;
        ch.bubbleType = 'thought';
        ch.bubbleTimer = BUBBLE_DUR;
      }
    };

    const onPhase = (_e: any, data: { id: string; phase: string }) => {
      const ch = stateRef.current.characters.get(data.id);
      if (ch) ch.currentTool = data.phase;
    };

    const onCompleted = (_e: any, data: { id: string; result?: string }) => {
      const ch = stateRef.current.characters.get(data.id);
      if (ch) {
        ch.isActive = false;
        ch.spawnEffect = 'despawn';
        ch.spawnTimer = SPAWN_DUR;
        ch.bubbleText = '✓ Done';
        ch.bubbleType = 'done';
        ch.bubbleTimer = 3;
      }
    };

    const onStopped = (_e: any, data: { id: string }) => {
      const ch = stateRef.current.characters.get(data.id);
      if (ch) {
        ch.isActive = false;
        ch.spawnEffect = 'despawn';
        ch.spawnTimer = SPAWN_DUR;
        ch.bubbleText = '✕ Stopped';
        ch.bubbleType = 'error';
        ch.bubbleTimer = 2;
      }
    };

    const onRemoved = (_e: any, data: { id: string }) => {
      stateRef.current.characters.delete(data.id);
    };

    // Register via IPC
    if (eve.office?.onFullState) listeners.push(eve.office.onFullState(onFullState));
    if (eve.office?.onSpawned) listeners.push(eve.office.onSpawned(onSpawned));
    if (eve.office?.onThought) listeners.push(eve.office.onThought(onThought));
    if (eve.office?.onPhase) listeners.push(eve.office.onPhase(onPhase));
    if (eve.office?.onCompleted) listeners.push(eve.office.onCompleted(onCompleted));
    if (eve.office?.onStopped) listeners.push(eve.office.onStopped(onStopped));
    if (eve.office?.onRemoved) listeners.push(eve.office.onRemoved(onRemoved));

    return () => {
      for (const unsub of listeners) unsub?.();
    };
  }, []);

  /* ── Game Loop ──────────────────────────────────────────────── */
  const gameLoop = useCallback((timestamp: number) => {
    const s = stateRef.current;
    if (!s.layout) {
      animFrameRef.current = requestAnimationFrame(gameLoop);
      return;
    }

    // Delta time (capped at 100ms to prevent teleporting on tab switch)
    const dt = s.lastTime > 0 ? Math.min((timestamp - s.lastTime) / 1000, 0.1) : 0;
    s.lastTime = timestamp;

    // Update all characters
    for (const ch of s.characters.values()) {
      updateCharacter(ch, dt, s.layout, s.seats, s.blocked);
    }

    // Render
    render(canvasRef.current, s.layout, s.characters, s.seats);

    animFrameRef.current = requestAnimationFrame(gameLoop);
  }, []);

  useEffect(() => {
    if (!ready) return;
    stateRef.current.lastTime = 0;
    animFrameRef.current = requestAnimationFrame(gameLoop);
    return () => cancelAnimationFrame(animFrameRef.current);
  }, [ready, gameLoop]);

  /* ── Canvas sizing ──────────────────────────────────────────── */
  useEffect(() => {
    const handleResize = () => {
      const canvas = canvasRef.current;
      const layout = stateRef.current.layout;
      if (!canvas || !layout) return;
      canvas.width = layout.cols * TILE * ZOOM;
      canvas.height = layout.rows * TILE * ZOOM;
      clearCanvasCache();
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [ready]);

  return (
    <div style={{
      width: '100vw',
      height: '100vh',
      background: '#0a0e1c',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      overflow: 'hidden',
      fontFamily: "'Segoe UI', sans-serif",
      WebkitAppRegion: 'drag' as any,
    }}>
      {/* Title bar area */}
      <div style={{
        width: '100%',
        height: 28,
        flexShrink: 0,
      }} />

      {/* Canvas container */}
      <div style={{
        flex: 1,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        WebkitAppRegion: 'no-drag' as any,
      }}>
        <canvas
          ref={canvasRef}
          style={{
            imageRendering: 'pixelated',
            maxWidth: '100%',
            maxHeight: '100%',
            borderRadius: 4,
            boxShadow: '0 0 40px rgba(0, 240, 255, 0.08)',
          }}
        />
      </div>

      {/* Status bar */}
      <div style={{
        width: '100%',
        height: 24,
        background: 'rgba(0, 240, 255, 0.05)',
        borderTop: '1px solid rgba(0, 240, 255, 0.1)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 12px',
        fontSize: 11,
        color: 'rgba(0, 240, 255, 0.5)',
        gap: 16,
        flexShrink: 0,
        WebkitAppRegion: 'no-drag' as any,
      }}>
        <span>AGENT OFFICE</span>
        <span>{stateRef.current.characters.size} agent{stateRef.current.characters.size !== 1 ? 's' : ''}</span>
      </div>
    </div>
  );
};

export default AgentOffice;

/* ══════════════════════════════════════════════════════════════════════
   CHARACTER STATE MACHINE UPDATE
   ══════════════════════════════════════════════════════════════════════ */

function updateCharacter(
  ch: OfficeChar,
  dt: number,
  layout: OfficeLayout,
  seats: Map<string, Seat>,
  blocked: Set<string>
): void {
  // Spawn/despawn effect
  if (ch.spawnEffect) {
    ch.spawnTimer -= dt;
    if (ch.spawnTimer <= 0) {
      if (ch.spawnEffect === 'spawn') {
        ch.spawnEffect = null;
        // After spawn, walk to seat if assigned
        if (ch.seatId && ch.isActive) {
          const seat = seats.get(ch.seatId);
          if (seat) {
            ch.path = findPath(ch.tileCol, ch.tileRow, seat.col, seat.row, layout, blocked);
            if (ch.path.length > 0) {
              ch.state = CharState.WALK;
              ch.moveProgress = 0;
            } else {
              // Already at seat
              ch.state = CharState.TYPE;
              ch.dir = seat.facingDir;
            }
          }
        }
      } else {
        // Despawn complete — character will be removed by IPC
        ch.spawnEffect = null;
      }
    }
    return;
  }

  // Bubble timer
  if (ch.bubbleTimer > 0) {
    ch.bubbleTimer -= dt;
    if (ch.bubbleTimer <= 0) {
      ch.bubbleText = null;
      ch.bubbleType = null;
    }
  }

  // State machine
  switch (ch.state) {
    case CharState.TYPE:
      updateTyping(ch, dt, layout, seats, blocked);
      break;
    case CharState.WALK:
      updateWalking(ch, dt, layout, seats, blocked);
      break;
    case CharState.IDLE:
      updateIdle(ch, dt, layout, seats, blocked);
      break;
  }
}

function updateTyping(
  ch: OfficeChar,
  dt: number,
  layout: OfficeLayout,
  seats: Map<string, Seat>,
  blocked: Set<string>
): void {
  ch.frameTimer += dt;
  if (ch.frameTimer >= TYPE_FRAME_DUR) {
    ch.frameTimer = 0;
    ch.frame = (ch.frame + 1) % 2;
  }

  // If still active, keep typing. After a while, maybe wander.
  if (ch.isActive) {
    ch.seatTimer += dt;
    if (ch.seatTimer >= SEAT_REST_MIN + Math.random() * (SEAT_REST_MAX - SEAT_REST_MIN)) {
      ch.seatTimer = 0;
      // Briefly wander
      ch.state = CharState.IDLE;
      ch.wanderTimer = 0;
      ch.wanderCount = 0;
      ch.wanderLimit = WANDER_MOVES_MIN + Math.floor(Math.random() * (WANDER_MOVES_MAX - WANDER_MOVES_MIN));
    }
  } else {
    // Agent finished — go idle
    ch.state = CharState.IDLE;
    ch.wanderTimer = 1;
    ch.wanderCount = 0;
    ch.wanderLimit = 99; // Keep wandering until removed
  }
}

function updateWalking(
  ch: OfficeChar,
  dt: number,
  layout: OfficeLayout,
  seats: Map<string, Seat>,
  blocked: Set<string>
): void {
  if (ch.path.length === 0) {
    // Arrived at destination
    if (ch.isActive && ch.seatId) {
      const seat = seats.get(ch.seatId);
      if (seat && ch.tileCol === seat.col && ch.tileRow === seat.row) {
        ch.state = CharState.TYPE;
        ch.dir = seat.facingDir;
        ch.frame = 0;
        ch.frameTimer = 0;
        ch.seatTimer = 0;
        return;
      }
    }
    ch.state = CharState.IDLE;
    ch.wanderTimer = WANDER_PAUSE_MIN + Math.random() * (WANDER_PAUSE_MAX - WANDER_PAUSE_MIN);
    return;
  }

  // Move toward next tile in path
  const next = ch.path[0];
  const targetX = next.col * TILE + TILE / 2;
  const targetY = next.row * TILE + TILE / 2;

  // Update direction
  const dx = next.col - ch.tileCol;
  const dy = next.row - ch.tileRow;
  if (dy < 0) ch.dir = Dir.UP;
  else if (dy > 0) ch.dir = Dir.DOWN;
  else if (dx < 0) ch.dir = Dir.LEFT;
  else if (dx > 0) ch.dir = Dir.RIGHT;

  // Lerp movement
  ch.moveProgress += (WALK_SPEED * dt) / TILE;
  if (ch.moveProgress >= 1) {
    ch.moveProgress = 0;
    ch.x = targetX;
    ch.y = targetY;
    ch.tileCol = next.col;
    ch.tileRow = next.row;
    ch.path.shift();
  } else {
    const prevX = ch.tileCol * TILE + TILE / 2;
    const prevY = ch.tileRow * TILE + TILE / 2;
    ch.x = prevX + (targetX - prevX) * ch.moveProgress;
    ch.y = prevY + (targetY - prevY) * ch.moveProgress;
  }

  // Walk animation
  ch.frameTimer += dt;
  if (ch.frameTimer >= WALK_FRAME_DUR) {
    ch.frameTimer = 0;
    ch.frame = (ch.frame + 1) % 4;
  }
}

function updateIdle(
  ch: OfficeChar,
  dt: number,
  layout: OfficeLayout,
  seats: Map<string, Seat>,
  blocked: Set<string>
): void {
  ch.wanderTimer -= dt;
  if (ch.wanderTimer > 0) return;

  // Time to move somewhere
  ch.wanderCount++;

  if (ch.wanderCount > ch.wanderLimit && ch.isActive && ch.seatId) {
    // Go back to seat
    const seat = seats.get(ch.seatId);
    if (seat) {
      ch.path = findPath(ch.tileCol, ch.tileRow, seat.col, seat.row, layout, blocked);
      if (ch.path.length > 0) {
        ch.state = CharState.WALK;
        ch.frame = 0;
        ch.frameTimer = 0;
        ch.moveProgress = 0;
        return;
      }
    }
  }

  // Pick a random walkable tile nearby
  const walkable = getWalkableTiles(layout, blocked);
  const nearby = walkable.filter(
    (t) => Math.abs(t.col - ch.tileCol) + Math.abs(t.row - ch.tileRow) <= 4
  );
  const target = nearby.length > 0
    ? nearby[Math.floor(Math.random() * nearby.length)]
    : walkable[Math.floor(Math.random() * walkable.length)];

  if (target) {
    ch.path = findPath(ch.tileCol, ch.tileRow, target.col, target.row, layout, blocked);
    if (ch.path.length > 0) {
      ch.state = CharState.WALK;
      ch.frame = 0;
      ch.frameTimer = 0;
      ch.moveProgress = 0;
    } else {
      ch.wanderTimer = WANDER_PAUSE_MIN;
    }
  } else {
    ch.wanderTimer = WANDER_PAUSE_MIN;
  }
}

/* ══════════════════════════════════════════════════════════════════════
   CANVAS RENDERING
   ══════════════════════════════════════════════════════════════════════ */

function render(
  canvas: HTMLCanvasElement | null,
  layout: OfficeLayout,
  characters: Map<string, OfficeChar>,
  seats: Map<string, Seat>
): void {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = layout.cols * TILE * ZOOM;
  const h = layout.rows * TILE * ZOOM;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;

  ctx.clearRect(0, 0, w, h);
  ctx.imageSmoothingEnabled = false;

  // Draw floor + walls
  drawTiles(ctx, layout);

  // Collect all renderable items with z-sort values
  const items: Array<{ zY: number; draw: () => void }> = [];

  // Furniture
  for (const furn of layout.furniture) {
    items.push({
      zY: furn.zY || furn.row * TILE + TILE / 2,
      draw: () => drawFurniture(ctx, furn),
    });
  }

  // Characters
  for (const ch of characters.values()) {
    items.push({
      zY: ch.y,
      draw: () => drawCharacter(ctx, ch),
    });
  }

  // Z-sort: lower Y drawn first (further from camera)
  items.sort((a, b) => a.zY - b.zY);
  for (const item of items) {
    item.draw();
  }

  // Draw bubbles on top of everything
  for (const ch of characters.values()) {
    if (ch.bubbleText && ch.bubbleTimer > 0) {
      drawBubble(ctx, ch);
    }
  }

  // Draw name labels
  for (const ch of characters.values()) {
    drawNameLabel(ctx, ch);
  }
}

/* ── Tile rendering ───────────────────────────────────────────── */

const FLOOR_COLOR = '#1a1e30';
const FLOOR_ALT_COLOR = '#1c2034';
const WALL_COLOR = '#2a2e42';
const WALL_DARK = '#1e2236';

function drawTiles(ctx: CanvasRenderingContext2D, layout: OfficeLayout): void {
  for (let r = 0; r < layout.rows; r++) {
    for (let c = 0; c < layout.cols; c++) {
      const tile = layout.tiles[r * layout.cols + c];
      const x = c * TILE * ZOOM;
      const y = r * TILE * ZOOM;
      const s = TILE * ZOOM;

      if (tile === TileType.WALL) {
        ctx.fillStyle = WALL_COLOR;
        ctx.fillRect(x, y, s, s);
        // Inner shadow
        ctx.fillStyle = WALL_DARK;
        ctx.fillRect(x + 2, y + 2, s - 4, s - 4);
      } else if (tile === TileType.FLOOR) {
        ctx.fillStyle = (r + c) % 2 === 0 ? FLOOR_COLOR : FLOOR_ALT_COLOR;
        ctx.fillRect(x, y, s, s);
        // Subtle grid lines
        ctx.strokeStyle = 'rgba(0, 240, 255, 0.03)';
        ctx.lineWidth = 1;
        ctx.strokeRect(x + 0.5, y + 0.5, s - 1, s - 1);
      }
    }
  }
}

/* ── Furniture rendering ──────────────────────────────────────── */

function drawFurniture(ctx: CanvasRenderingContext2D, furn: FurnitureInst): void {
  const sprite = getFurnitureSprite(furn.type);
  if (!sprite) return;

  const cached = getCachedCanvas(sprite, ZOOM);
  const x = furn.col * TILE * ZOOM + (TILE * ZOOM - cached.width) / 2;
  const y = furn.row * TILE * ZOOM + (TILE * ZOOM - cached.height) / 2;
  ctx.drawImage(cached, Math.round(x), Math.round(y));
}

/* ── Character rendering ──────────────────────────────────────── */

function drawCharacter(ctx: CanvasRenderingContext2D, ch: OfficeChar): void {
  const sprites = getCharacterSprites(ch.palette, ch.hueShift);

  let spriteData;
  if (ch.state === CharState.TYPE) {
    const frames = sprites.typing[ch.dir] || sprites.typing[0];
    spriteData = frames[ch.frame % frames.length];
  } else {
    const frames = sprites.walk[ch.dir] || sprites.walk[0];
    if (ch.state === CharState.IDLE) {
      spriteData = frames[1]; // Standing frame
    } else {
      spriteData = frames[ch.frame % frames.length];
    }
  }

  if (!spriteData) return;

  const cached = getCachedCanvas(spriteData, ZOOM);

  // Position: center sprite on character position
  const drawX = ch.x * ZOOM - cached.width / 2;
  const drawY = ch.y * ZOOM - cached.height + 8 * ZOOM; // Anchor at feet

  // Spawn/despawn effect
  if (ch.spawnEffect) {
    const progress = ch.spawnTimer / SPAWN_DUR;
    const alpha = ch.spawnEffect === 'spawn' ? 1 - progress : progress;
    ctx.globalAlpha = Math.max(0, Math.min(1, alpha));

    // Scale effect
    const scale = ch.spawnEffect === 'spawn' ? 0.5 + alpha * 0.5 : 1 + (1 - alpha) * 0.5;
    ctx.save();
    ctx.translate(ch.x * ZOOM, ch.y * ZOOM);
    ctx.scale(scale, scale);
    ctx.drawImage(cached, -cached.width / 2, -cached.height + 8 * ZOOM);
    ctx.restore();

    ctx.globalAlpha = 1;
    return;
  }

  ctx.drawImage(cached, Math.round(drawX), Math.round(drawY));
}

/* ── Bubble rendering ─────────────────────────────────────────── */

function drawBubble(ctx: CanvasRenderingContext2D, ch: OfficeChar): void {
  if (!ch.bubbleText) return;

  const text = ch.bubbleText;
  const bx = ch.x * ZOOM;
  const by = ch.y * ZOOM - 24 * ZOOM;

  ctx.font = `${10 * ZOOM}px 'Consolas', 'Courier New', monospace`;
  const metrics = ctx.measureText(text);
  const tw = metrics.width;
  const pad = 4 * ZOOM;
  const bw = tw + pad * 2;
  const bh = 12 * ZOOM + pad * 2;

  // Fade
  let alpha = 1;
  if (ch.bubbleTimer < BUBBLE_FADE) {
    alpha = ch.bubbleTimer / BUBBLE_FADE;
  }
  ctx.globalAlpha = alpha * 0.9;

  // Background
  const bgColor = ch.bubbleType === 'error' ? 'rgba(204, 50, 50, 0.85)'
    : ch.bubbleType === 'done' ? 'rgba(50, 180, 80, 0.85)'
    : 'rgba(10, 14, 28, 0.9)';
  const borderColor = ch.bubbleType === 'error' ? 'rgba(255, 100, 100, 0.6)'
    : ch.bubbleType === 'done' ? 'rgba(100, 255, 130, 0.6)'
    : 'rgba(0, 240, 255, 0.3)';

  const rx = bx - bw / 2;
  const ry = by - bh;

  ctx.fillStyle = bgColor;
  ctx.strokeStyle = borderColor;
  ctx.lineWidth = 1;
  roundRect(ctx, rx, ry, bw, bh, 4 * ZOOM);
  ctx.fill();
  ctx.stroke();

  // Triangle pointer
  ctx.fillStyle = bgColor;
  ctx.beginPath();
  ctx.moveTo(bx - 4 * ZOOM, ry + bh);
  ctx.lineTo(bx, ry + bh + 4 * ZOOM);
  ctx.lineTo(bx + 4 * ZOOM, ry + bh);
  ctx.closePath();
  ctx.fill();

  // Text
  ctx.fillStyle = ch.bubbleType === 'thought' ? 'rgba(0, 240, 255, 0.9)' : '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, bx, ry + bh / 2);

  ctx.globalAlpha = 1;
  ctx.textAlign = 'start';
  ctx.textBaseline = 'alphabetic';
}

/* ── Name label ───────────────────────────────────────────────── */

function drawNameLabel(ctx: CanvasRenderingContext2D, ch: OfficeChar): void {
  const x = ch.x * ZOOM;
  const y = ch.y * ZOOM + 4 * ZOOM;

  ctx.font = `${8 * ZOOM}px 'Consolas', 'Courier New', monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.fillStyle = 'rgba(0, 240, 255, 0.4)';
  ctx.fillText(ch.name, x, y);
  ctx.textAlign = 'start';
  ctx.textBaseline = 'alphabetic';
}

/* ── Helpers ──────────────────────────────────────────────────── */

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number, r: number
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function hydrateChar(data: any): OfficeChar {
  return {
    id: data.id,
    name: data.name || 'Agent',
    x: data.x ?? 0,
    y: data.y ?? 0,
    tileCol: data.tileCol ?? 0,
    tileRow: data.tileRow ?? 0,
    state: data.state || CharState.IDLE,
    dir: data.dir ?? Dir.DOWN,
    frame: data.frame ?? 0,
    frameTimer: 0,
    path: [],
    moveProgress: 0,
    isActive: data.isActive ?? true,
    currentTool: data.currentTool ?? null,
    seatId: data.seatId ?? null,
    palette: data.palette ?? 0,
    hueShift: data.hueShift ?? 0,
    bubbleText: data.bubbleText ?? null,
    bubbleTimer: data.bubbleText ? BUBBLE_DUR : 0,
    bubbleType: data.bubbleType ?? null,
    spawnEffect: data.spawnEffect ?? 'spawn',
    spawnTimer: SPAWN_DUR,
    role: data.role || 'solo',
    teamId: data.teamId ?? null,
    wanderTimer: 0,
    wanderCount: 0,
    wanderLimit: WANDER_MOVES_MIN + Math.floor(Math.random() * (WANDER_MOVES_MAX - WANDER_MOVES_MIN)),
    seatTimer: 0,
  };
}

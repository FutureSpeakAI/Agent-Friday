/**
 * office-manager.ts — Manages the Agent Office visualization window.
 *
 * Opens a secondary BrowserWindow when agents start working,
 * and forwards agent lifecycle events to the office renderer.
 */

import { BrowserWindow, ipcMain, screen } from 'electron';
import path from 'path';
import { app } from 'electron';
import { buildDefaultLayout } from './office-layout';
import {
  OfficeLayout,
  Character,
  Seat,
  CharacterState,
  Direction,
  TILE_SIZE,
  SPAWN_DURATION_SEC,
} from './office-types';

const isDev = !app.isPackaged;

/* ── Constants ──────────────────────────────────────────────────── */
const PALETTE_COUNT = 6;
const HUE_SHIFT_MIN = 45;
const HUE_SHIFT_RANGE = 270;

/* ── Office State (main-process side) ───────────────────────────── */

class AgentOfficeManager {
  private officeWindow: BrowserWindow | null = null;
  private mainWindow: BrowserWindow | null = null;
  private layout: OfficeLayout;
  private characters: Map<string, Character> = new Map();
  private seats: Map<string, Seat> = new Map();
  private blockedTiles: Set<string> = new Set();
  private serverPort = 3333;

  constructor() {
    this.layout = buildDefaultLayout();
    // Initialize seats from layout
    for (const seat of this.layout.seats) {
      this.seats.set(seat.id, { ...seat });
    }
    // Build blocked tiles from furniture
    for (const f of this.layout.furniture) {
      if (f.type === 'desk' || f.type === 'bookshelf' || f.type === 'cooler' || f.type === 'whiteboard') {
        this.blockedTiles.add(`${f.col},${f.row}`);
      }
    }
    this.registerIPC();
  }

  setMainWindow(win: BrowserWindow) {
    this.mainWindow = win;
  }

  setServerPort(port: number) {
    this.serverPort = port;
  }

  /* ── Window Lifecycle ─────────────────────────────────────────── */

  private async openWindow(): Promise<void> {
    if (this.officeWindow && !this.officeWindow.isDestroyed()) {
      this.officeWindow.show();
      this.officeWindow.focus();
      return;
    }

    const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workAreaSize;

    this.officeWindow = new BrowserWindow({
      width: Math.min(720, screenW - 100),
      height: Math.min(520, screenH - 100),
      minWidth: 480,
      minHeight: 360,
      frame: false,
      transparent: false,
      backgroundColor: '#0a0e1c',
      title: 'Agent Office',
      show: false,
      titleBarStyle: 'hidden',
      titleBarOverlay: {
        color: '#0a0e1c',
        symbolColor: '#00f0ff',
        height: 28,
      },
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    this.officeWindow.once('ready-to-show', () => {
      this.officeWindow?.show();
    });

    // Load with office query param so renderer knows to show office view
    const baseUrl = isDev ? 'http://localhost:5199' : `http://localhost:${this.serverPort}`;
    await this.officeWindow.loadURL(`${baseUrl}?office=true`);

    this.officeWindow.on('closed', () => {
      this.officeWindow = null;
    });

    // Send initial state after a small delay to let renderer mount
    setTimeout(() => {
      this.sendFullState();
    }, 500);
  }

  closeWindow(): void {
    if (this.officeWindow && !this.officeWindow.isDestroyed()) {
      this.officeWindow.close();
      this.officeWindow = null;
    }
  }

  isOpen(): boolean {
    return this.officeWindow !== null && !this.officeWindow.isDestroyed();
  }

  /* ── Agent Lifecycle Events ───────────────────────────────────── */

  agentSpawned(agentId: string, name: string, role: string, teamId?: string): void {
    // Auto-open window
    if (!this.isOpen()) {
      this.openWindow();
    }

    // Find a free seat
    const seatId = this.findFreeSeat();
    if (seatId) {
      const seat = this.seats.get(seatId)!;
      seat.assigned = true;
      seat.assignedTo = agentId;
    }

    // Pick diverse palette
    const { palette, hueShift } = this.pickDiversePalette();

    // Determine spawn position
    const seat = seatId ? this.seats.get(seatId)! : null;
    const spawnCol = seat ? seat.col : 7;
    const spawnRow = seat ? seat.row : 5;

    const character: Character = {
      id: agentId,
      name,
      x: spawnCol * TILE_SIZE + TILE_SIZE / 2,
      y: spawnRow * TILE_SIZE + TILE_SIZE / 2,
      tileCol: spawnCol,
      tileRow: spawnRow,
      state: CharacterState.IDLE,
      dir: seat ? seat.facingDir : Direction.DOWN,
      frame: 0,
      frameTimer: 0,
      path: [],
      moveProgress: 0,
      isActive: true,
      currentTool: null,
      seatId: seatId,
      palette,
      hueShift,
      bubbleText: null,
      bubbleTimer: 0,
      bubbleType: null,
      isSubAgent: role === 'sub-agent',
      parentId: null,
      teamId: teamId || null,
      role,
      spawnEffect: 'spawn',
      spawnTimer: SPAWN_DURATION_SEC,
      wanderTimer: 0,
      wanderCount: 0,
      wanderLimit: 2 + Math.floor(Math.random() * 4),
      seatTimer: 0,
    };

    this.characters.set(agentId, character);
    this.emitToOffice('office:agent-spawned', this.serializeCharacter(character));
  }

  agentCompleted(agentId: string, result?: string): void {
    const ch = this.characters.get(agentId);
    if (!ch) return;

    ch.isActive = false;
    ch.spawnEffect = 'despawn';
    ch.spawnTimer = SPAWN_DURATION_SEC;
    ch.bubbleText = result ? '✓' : '✓ Done';
    ch.bubbleType = 'done';
    ch.bubbleTimer = 3;

    this.emitToOffice('office:agent-completed', { id: agentId, result: result?.slice(0, 80) });

    // Remove after despawn animation
    setTimeout(() => {
      this.removeAgent(agentId);
    }, SPAWN_DURATION_SEC * 1000 + 500);
  }

  agentStopped(agentId: string): void {
    const ch = this.characters.get(agentId);
    if (!ch) return;

    ch.isActive = false;
    ch.spawnEffect = 'despawn';
    ch.spawnTimer = SPAWN_DURATION_SEC;
    ch.bubbleText = '✕ Stopped';
    ch.bubbleType = 'error';
    ch.bubbleTimer = 2;

    this.emitToOffice('office:agent-stopped', { id: agentId });

    setTimeout(() => {
      this.removeAgent(agentId);
    }, SPAWN_DURATION_SEC * 1000 + 500);
  }

  agentThought(agentId: string, text: string, phase?: string): void {
    const ch = this.characters.get(agentId);
    if (!ch) return;

    // Show thought as bubble
    ch.bubbleText = text.length > 60 ? text.slice(0, 57) + '…' : text;
    ch.bubbleType = 'thought';
    ch.bubbleTimer = 4;

    this.emitToOffice('office:agent-thought', { id: agentId, text: ch.bubbleText, phase });
  }

  agentPhase(agentId: string, phase: string): void {
    const ch = this.characters.get(agentId);
    if (!ch) return;

    ch.currentTool = phase;
    this.emitToOffice('office:agent-phase', { id: agentId, phase });
  }

  private removeAgent(agentId: string): void {
    const ch = this.characters.get(agentId);
    if (ch?.seatId) {
      const seat = this.seats.get(ch.seatId);
      if (seat) {
        seat.assigned = false;
        seat.assignedTo = null;
      }
    }
    this.characters.delete(agentId);
    this.emitToOffice('office:agent-removed', { id: agentId });

    // Close window if no agents left
    if (this.characters.size === 0) {
      setTimeout(() => {
        if (this.characters.size === 0) {
          // Keep window open but could auto-close after a delay
          // this.closeWindow();
        }
      }, 5000);
    }
  }

  /* ── IPC Registration ─────────────────────────────────────────── */

  private registerIPC(): void {
    ipcMain.handle('office:get-state', () => {
      return {
        layout: this.layout,
        characters: [...this.characters.values()].map((c) => this.serializeCharacter(c)),
      };
    });

    ipcMain.handle('office:is-open', () => this.isOpen());

    ipcMain.on('office:request-open', () => {
      this.openWindow();
    });

    ipcMain.on('office:request-close', () => {
      this.closeWindow();
    });
  }

  /* ── Helpers ──────────────────────────────────────────────────── */

  private findFreeSeat(): string | null {
    for (const [id, seat] of this.seats) {
      if (!seat.assigned) return id;
    }
    return null;
  }

  private pickDiversePalette(): { palette: number; hueShift: number } {
    const counts = new Array(PALETTE_COUNT).fill(0) as number[];
    for (const ch of this.characters.values()) {
      if (!ch.isSubAgent) counts[ch.palette]++;
    }
    const minCount = Math.min(...counts);
    const available: number[] = [];
    for (let i = 0; i < PALETTE_COUNT; i++) {
      if (counts[i] === minCount) available.push(i);
    }
    const palette = available[Math.floor(Math.random() * available.length)];
    const hueShift = minCount > 0 ? HUE_SHIFT_MIN + Math.floor(Math.random() * HUE_SHIFT_RANGE) : 0;
    return { palette, hueShift };
  }

  private serializeCharacter(ch: Character) {
    return {
      id: ch.id,
      name: ch.name,
      x: ch.x,
      y: ch.y,
      tileCol: ch.tileCol,
      tileRow: ch.tileRow,
      state: ch.state,
      dir: ch.dir,
      frame: ch.frame,
      palette: ch.palette,
      hueShift: ch.hueShift,
      isActive: ch.isActive,
      currentTool: ch.currentTool,
      seatId: ch.seatId,
      bubbleText: ch.bubbleText,
      bubbleType: ch.bubbleType,
      spawnEffect: ch.spawnEffect,
      role: ch.role,
      teamId: ch.teamId,
    };
  }

  private sendFullState(): void {
    if (!this.officeWindow || this.officeWindow.isDestroyed()) return;
    this.officeWindow.webContents.send('office:full-state', {
      layout: this.layout,
      characters: [...this.characters.values()].map((c) => this.serializeCharacter(c)),
    });
  }

  private emitToOffice(channel: string, data: unknown): void {
    if (this.officeWindow && !this.officeWindow.isDestroyed()) {
      this.officeWindow.webContents.send(channel, data);
    }
    // Also emit to main window for the agent panel
    if (this.mainWindow && !this.mainWindow.isDestroyed()) {
      this.mainWindow.webContents.send(channel, data);
    }
  }
}

export const officeManager = new AgentOfficeManager();

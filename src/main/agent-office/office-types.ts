/**
 * office-types.ts — Type definitions for the pixel-art agent office visualization.
 *
 * Ported and adapted from pablodelucca/pixel-agents for Electron.
 */

/* ── Tile System ─────────────────────────────────────────────────────── */

export const TileType = {
  VOID: 0,
  FLOOR: 1,
  WALL: 2,
} as const;

export type TileTypeVal = (typeof TileType)[keyof typeof TileType];

/* ── Character State Machine ─────────────────────────────────────────── */

export const CharacterState = {
  IDLE: 'idle',
  WALK: 'walk',
  TYPE: 'type',
} as const;

export type CharacterStateVal = (typeof CharacterState)[keyof typeof CharacterState];

export const Direction = {
  DOWN: 0,
  LEFT: 1,
  RIGHT: 2,
  UP: 3,
} as const;

export type DirectionVal = (typeof Direction)[keyof typeof Direction];

/* ── Sprite Data ─────────────────────────────────────────────────────── */

/** 2D array of hex color strings ('' = transparent pixel) */
export type SpriteData = string[][];

export interface CharacterPalette {
  skin: string;
  hair: string;
  shirt: string;
  pants: string;
  shoes: string;
  eyes: string;
}

/* ── Character ───────────────────────────────────────────────────────── */

export interface Character {
  id: string;
  name: string;

  // Position (pixel coords)
  x: number;
  y: number;
  tileCol: number;
  tileRow: number;

  // State machine
  state: CharacterStateVal;
  dir: DirectionVal;
  frame: number;
  frameTimer: number;

  // Movement
  path: Array<{ col: number; row: number }>;
  moveProgress: number; // 0-1 lerp between tiles

  // Work state
  isActive: boolean;
  currentTool: string | null; // 'typing' or 'reading' etc.

  // Seat assignment
  seatId: string | null;

  // Visual
  palette: number; // 0-5
  hueShift: number;

  // Bubble
  bubbleText: string | null;
  bubbleTimer: number;
  bubbleType: 'thought' | 'task' | 'done' | 'error' | null;

  // Sub-agent tracking
  isSubAgent: boolean;
  parentId: string | null;
  teamId: string | null;
  role: string;

  // Spawn/despawn
  spawnEffect: 'spawn' | 'despawn' | null;
  spawnTimer: number;

  // Wander behavior
  wanderTimer: number;
  wanderCount: number;
  wanderLimit: number;
  seatTimer: number;
}

/* ── Seat ─────────────────────────────────────────────────────────────── */

export interface Seat {
  id: string;
  col: number;
  row: number;
  facingDir: DirectionVal;
  assigned: boolean;
  assignedTo: string | null;
}

/* ── Furniture ───────────────────────────────────────────────────────── */

export type FurnitureType = 'desk' | 'chair' | 'monitor' | 'plant' | 'bookshelf' | 'whiteboard' | 'cooler';

export interface FurnitureInstance {
  type: FurnitureType;
  col: number;
  row: number;
  zY: number;
}

/* ── Office Layout ───────────────────────────────────────────────────── */

export interface OfficeLayout {
  cols: number;
  rows: number;
  tiles: TileTypeVal[];         // Flat array indexed [row * cols + col]
  seats: Seat[];
  furniture: FurnitureInstance[];
}

/* ── Office State (for IPC) ──────────────────────────────────────────── */

export interface OfficeSnapshot {
  characters: Array<{
    id: string;
    name: string;
    x: number;
    y: number;
    state: CharacterStateVal;
    dir: DirectionVal;
    frame: number;
    palette: number;
    hueShift: number;
    isActive: boolean;
    bubbleText: string | null;
    bubbleType: string | null;
    spawnEffect: string | null;
    role: string;
    teamId: string | null;
  }>;
  layout: OfficeLayout;
}

/* ── Animation Constants ─────────────────────────────────────────────── */

export const TILE_SIZE = 32;
export const WALK_SPEED_PX_PER_SEC = 64;
export const WALK_FRAME_DURATION_SEC = 0.15;
export const TYPE_FRAME_DURATION_SEC = 0.4;
export const WANDER_PAUSE_MIN_SEC = 4;
export const WANDER_PAUSE_MAX_SEC = 10;
export const WANDER_MOVES_MIN = 2;
export const WANDER_MOVES_MAX = 5;
export const SEAT_REST_MIN_SEC = 30;
export const SEAT_REST_MAX_SEC = 90;
export const SPAWN_DURATION_SEC = 0.5;
export const BUBBLE_DURATION_SEC = 4;
export const BUBBLE_FADE_SEC = 0.5;
export const CHARACTER_SITTING_OFFSET_PX = 12;

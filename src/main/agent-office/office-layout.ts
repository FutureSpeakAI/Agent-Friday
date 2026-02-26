/**
 * office-layout.ts — Default office layout for the agent visualization.
 *
 * A small tech-office with 8 desk stations and open space for wandering.
 * Layout grows dynamically as more agents are spawned.
 */

import { OfficeLayout, TileType, Direction, Seat, FurnitureInstance } from './office-types';

/**
 * Build the default office layout.
 * 12 columns × 10 rows with 8 desk stations.
 */
export function buildDefaultLayout(): OfficeLayout {
  const cols = 14;
  const rows = 10;

  // Initialize all as floor
  const tiles = new Array(cols * rows).fill(TileType.FLOOR);

  // Add walls around the perimeter
  for (let c = 0; c < cols; c++) {
    tiles[0 * cols + c] = TileType.WALL;           // Top wall
    tiles[(rows - 1) * cols + c] = TileType.WALL;  // Bottom wall
  }
  for (let r = 0; r < rows; r++) {
    tiles[r * cols + 0] = TileType.WALL;            // Left wall
    tiles[r * cols + (cols - 1)] = TileType.WALL;   // Right wall
  }

  // 8 desk stations in 2 rows of 4
  const seats: Seat[] = [
    // Row 1 (top) — facing down
    { id: 'seat-0', col: 2, row: 2, facingDir: Direction.DOWN, assigned: false, assignedTo: null },
    { id: 'seat-1', col: 5, row: 2, facingDir: Direction.DOWN, assigned: false, assignedTo: null },
    { id: 'seat-2', col: 8, row: 2, facingDir: Direction.DOWN, assigned: false, assignedTo: null },
    { id: 'seat-3', col: 11, row: 2, facingDir: Direction.DOWN, assigned: false, assignedTo: null },
    // Row 2 (bottom) — facing up
    { id: 'seat-4', col: 2, row: 6, facingDir: Direction.UP, assigned: false, assignedTo: null },
    { id: 'seat-5', col: 5, row: 6, facingDir: Direction.UP, assigned: false, assignedTo: null },
    { id: 'seat-6', col: 8, row: 6, facingDir: Direction.UP, assigned: false, assignedTo: null },
    { id: 'seat-7', col: 11, row: 6, facingDir: Direction.UP, assigned: false, assignedTo: null },
  ];

  // Furniture — desks at each station, decorative items
  const furniture: FurnitureInstance[] = [
    // Desks (one tile in front of each seat)
    { type: 'desk', col: 2, row: 3, zY: 3 * 32 + 16 },
    { type: 'desk', col: 5, row: 3, zY: 3 * 32 + 16 },
    { type: 'desk', col: 8, row: 3, zY: 3 * 32 + 16 },
    { type: 'desk', col: 11, row: 3, zY: 3 * 32 + 16 },
    { type: 'desk', col: 2, row: 5, zY: 5 * 32 + 16 },
    { type: 'desk', col: 5, row: 5, zY: 5 * 32 + 16 },
    { type: 'desk', col: 8, row: 5, zY: 5 * 32 + 16 },
    { type: 'desk', col: 11, row: 5, zY: 5 * 32 + 16 },

    // Monitors on desks
    { type: 'monitor', col: 2, row: 3, zY: 3 * 32 + 8 },
    { type: 'monitor', col: 5, row: 3, zY: 3 * 32 + 8 },
    { type: 'monitor', col: 8, row: 3, zY: 3 * 32 + 8 },
    { type: 'monitor', col: 11, row: 3, zY: 3 * 32 + 8 },
    { type: 'monitor', col: 2, row: 5, zY: 5 * 32 + 8 },
    { type: 'monitor', col: 5, row: 5, zY: 5 * 32 + 8 },
    { type: 'monitor', col: 8, row: 5, zY: 5 * 32 + 8 },
    { type: 'monitor', col: 11, row: 5, zY: 5 * 32 + 8 },

    // Decorative
    { type: 'plant', col: 1, row: 1, zY: 1 * 32 + 16 },
    { type: 'plant', col: 12, row: 1, zY: 1 * 32 + 16 },
    { type: 'cooler', col: 7, row: 8, zY: 8 * 32 + 16 },
    { type: 'bookshelf', col: 1, row: 4, zY: 4 * 32 + 16 },
    { type: 'whiteboard', col: 7, row: 1, zY: 1 * 32 + 16 },
  ];

  return { cols, rows, tiles, seats, furniture };
}

/**
 * Check if a tile is walkable (floor and not occupied by furniture/wall).
 */
export function isWalkable(
  col: number,
  row: number,
  layout: OfficeLayout,
  blockedTiles: Set<string>
): boolean {
  if (col < 0 || row < 0 || col >= layout.cols || row >= layout.rows) return false;
  const tile = layout.tiles[row * layout.cols + col];
  if (tile === TileType.WALL || tile === TileType.VOID) return false;
  if (blockedTiles.has(`${col},${row}`)) return false;
  return true;
}

/**
 * Get all walkable tiles in the layout.
 */
export function getWalkableTiles(
  layout: OfficeLayout,
  blockedTiles: Set<string>
): Array<{ col: number; row: number }> {
  const result: Array<{ col: number; row: number }> = [];
  for (let r = 0; r < layout.rows; r++) {
    for (let c = 0; c < layout.cols; c++) {
      if (isWalkable(c, r, layout, blockedTiles)) {
        result.push({ col: c, row: r });
      }
    }
  }
  return result;
}

/**
 * BFS pathfinding from one tile to another.
 * Returns array of tile coordinates (excluding start, including end).
 */
export function findPath(
  fromCol: number,
  fromRow: number,
  toCol: number,
  toRow: number,
  layout: OfficeLayout,
  blockedTiles: Set<string>
): Array<{ col: number; row: number }> {
  if (fromCol === toCol && fromRow === toRow) return [];

  const key = (c: number, r: number) => `${c},${r}`;
  const visited = new Set<string>();
  const parent = new Map<string, string>();

  const queue: Array<{ col: number; row: number }> = [{ col: fromCol, row: fromRow }];
  visited.add(key(fromCol, fromRow));

  const dirs = [
    { dc: 0, dr: -1 }, // up
    { dc: 0, dr: 1 },  // down
    { dc: -1, dr: 0 }, // left
    { dc: 1, dr: 0 },  // right
  ];

  while (queue.length > 0) {
    const current = queue.shift()!;

    if (current.col === toCol && current.row === toRow) {
      // Reconstruct path
      const path: Array<{ col: number; row: number }> = [];
      let k = key(toCol, toRow);
      while (k !== key(fromCol, fromRow)) {
        const [c, r] = k.split(',').map(Number);
        path.unshift({ col: c, row: r });
        k = parent.get(k)!;
      }
      return path;
    }

    for (const { dc, dr } of dirs) {
      const nc = current.col + dc;
      const nr = current.row + dr;
      const nk = key(nc, nr);

      if (!visited.has(nk) && isWalkable(nc, nr, layout, blockedTiles)) {
        visited.add(nk);
        parent.set(nk, key(current.col, current.row));
        queue.push({ col: nc, row: nr });
      }
    }
  }

  // No path found
  return [];
}

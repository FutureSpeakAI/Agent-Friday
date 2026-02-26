/**
 * sprites.ts — Pixel-art sprite data for the Agent Office visualization.
 *
 * Contains character sprite templates (16x24), furniture sprites,
 * palette definitions, and sprite caching/rendering utilities.
 * Adapted from pablodelucca/pixel-agents.
 */

/* ── Types ──────────────────────────────────────────────────────── */

export type SpriteData = string[][];

export interface CharacterSprites {
  walk: Record<number, SpriteData[]>;    // direction → [frame0, frame1, frame2, frame3]
  typing: Record<number, SpriteData[]>;  // direction → [frame0, frame1]
}

interface CharPalette {
  skin: string;
  shirt: string;
  pants: string;
  hair: string;
  shoes: string;
}

/* ── Transparent Pixel ─────────────────────────────────────────── */

const _ = '';

/* ── Character Palettes (6 distinct agents) ────────────────────── */

export const CHARACTER_PALETTES: CharPalette[] = [
  { skin: '#FFCC99', shirt: '#4488CC', pants: '#334466', hair: '#553322', shoes: '#222222' },
  { skin: '#FFCC99', shirt: '#CC4444', pants: '#333333', hair: '#FFD700', shoes: '#222222' },
  { skin: '#DEB887', shirt: '#44AA66', pants: '#334444', hair: '#222222', shoes: '#333333' },
  { skin: '#FFCC99', shirt: '#AA55CC', pants: '#443355', hair: '#AA4422', shoes: '#222222' },
  { skin: '#DEB887', shirt: '#CCAA33', pants: '#444433', hair: '#553322', shoes: '#333333' },
  { skin: '#FFCC99', shirt: '#FF8844', pants: '#443322', hair: '#111111', shoes: '#222222' },
];

/* ── Template Keys ─────────────────────────────────────────────── */

const H = 'H'; // hair
const K = 'K'; // skin
const S = 'S'; // shirt
const P = 'P'; // pants
const O = 'O'; // shoes
const E = '#FFFFFF'; // eyes

type TC = string; // template cell

/* ── Template Resolution ───────────────────────────────────────── */

function resolveTemplate(template: TC[][], palette: CharPalette): SpriteData {
  return template.map(row =>
    row.map(cell => {
      if (cell === _) return '';
      if (cell === E) return E;
      if (cell === H) return palette.hair;
      if (cell === K) return palette.skin;
      if (cell === S) return palette.shirt;
      if (cell === P) return palette.pants;
      if (cell === O) return palette.shoes;
      return cell;
    })
  );
}

function flipHorizontal(template: TC[][]): TC[][] {
  return template.map(row => [...row].reverse());
}

/* ══════════════════════════════════════════════════════════════════
   CHARACTER SPRITE TEMPLATES (16×24)
   ══════════════════════════════════════════════════════════════════ */

// ── DOWN walk frames ────────────────────────────────────────────
const WALK_DOWN_1: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,E,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,P,P,_,_,_,_,P,P,_,_,_,_],
  [_,_,_,_,P,P,_,_,_,_,P,P,_,_,_,_],
  [_,_,_,_,O,O,_,_,_,_,_,O,O,_,_,_],
  [_,_,_,_,O,O,_,_,_,_,_,O,O,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const WALK_DOWN_2: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,E,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const WALK_DOWN_3: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,E,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,O,O,_,_,_,_,_,_,P,P,_,_,_],
  [_,_,_,O,O,_,_,_,_,_,_,P,P,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,O,O,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,O,O,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

// ── UP walk frames ──────────────────────────────────────────────
const WALK_UP_1: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,P,P,_,_,_,_,P,P,_,_,_,_],
  [_,_,_,_,P,P,_,_,_,_,P,P,_,_,_,_],
  [_,_,_,O,O,_,_,_,_,_,_,O,O,_,_,_],
  [_,_,_,O,O,_,_,_,_,_,_,O,O,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const WALK_UP_2: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const WALK_UP_3: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,P,P,_,_,_,_,_,_,O,O,_,_,_],
  [_,_,_,P,P,_,_,_,_,_,_,O,O,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,O,O,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,O,O,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

// ── RIGHT walk frames ───────────────────────────────────────────
const WALK_RIGHT_1: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,_,_,P,P,_,_,_,_],
  [_,_,_,_,_,_,P,P,_,_,P,P,_,_,_,_],
  [_,_,_,_,_,_,O,O,_,_,_,O,O,_,_,_],
  [_,_,_,_,_,_,O,O,_,_,_,O,O,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const WALK_RIGHT_2: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,_,O,O,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,O,O,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

// ── TYPING frames (seated, facing down) ─────────────────────────
const TYPE_DOWN_1: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,E,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,K,K,S,S,S,S,S,S,K,K,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const TYPE_DOWN_2: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,E,K,K,E,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,K,K,S,S,S,S,S,S,K,K,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

// ── TYPING frames (facing up - back of head) ────────────────────
const TYPE_UP_1: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,K,K,S,S,S,S,S,S,K,K,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

const TYPE_UP_2: TC[][] = [
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,H,H,H,H,_,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,H,H,H,H,H,H,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,K,K,K,K,K,K,_,_,_,_,_],
  [_,_,_,_,_,_,S,S,S,S,_,_,_,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,S,S,S,S,S,S,S,S,_,_,_,_],
  [_,_,_,_,K,S,S,S,S,S,S,K,_,_,_,_],
  [_,_,_,K,K,S,S,S,S,S,S,K,K,_,_,_],
  [_,_,_,_,_,S,S,S,S,S,S,_,_,_,_,_],
  [_,_,_,_,_,_,P,P,P,P,_,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,P,P,P,P,_,_,_,_,_],
  [_,_,_,_,_,P,P,_,_,P,P,_,_,_,_,_],
  [_,_,_,_,_,O,O,_,_,O,O,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
];

/* ══════════════════════════════════════════════════════════════════
   FURNITURE SPRITES
   ══════════════════════════════════════════════════════════════════ */

export const DESK_SPRITE: SpriteData = (() => {
  const W = '#8B6914', L = '#A07828', Sf = '#B8922E', D = '#6B4E0A';
  const rows: string[][] = [];
  rows.push(new Array(16).fill(_));
  rows.push([_, W,W,W,W,W,W,W,W,W,W,W,W,W,W, _]);
  for (let r = 0; r < 3; r++) rows.push([_, W, ...new Array(12).fill(r < 1 ? L : Sf), W, _]);
  rows.push([_, D, ...new Array(12).fill(W), D, _]);
  for (let r = 0; r < 4; r++) rows.push([_, W, ...new Array(12).fill(Sf), W, _]);
  rows.push([_, D, ...new Array(12).fill(W), D, _]);
  for (let r = 0; r < 2; r++) rows.push([_, W, ...new Array(12).fill(r > 0 ? L : Sf), W, _]);
  rows.push([_, ...new Array(14).fill(W), _]);
  // Legs
  const legRow = new Array(16).fill(_) as string[];
  legRow[1] = D; legRow[2] = D; legRow[13] = D; legRow[14] = D;
  rows.push([...legRow]); rows.push([...legRow]);
  rows.push(new Array(16).fill(_));
  return rows;
})();

export const MONITOR_SPRITE: SpriteData = (() => {
  const F = '#555555', Sc = '#3A3A5C', B = '#6688CC', D = '#444444';
  return [
    [_,_,_,F,F,F,F,F,F,F,F,F,F,_,_,_],
    [_,_,_,F,Sc,Sc,Sc,Sc,Sc,Sc,Sc,Sc,F,_,_,_],
    [_,_,_,F,Sc,B,B,B,B,B,B,Sc,F,_,_,_],
    [_,_,_,F,Sc,B,B,B,B,B,B,Sc,F,_,_,_],
    [_,_,_,F,Sc,B,B,B,B,B,B,Sc,F,_,_,_],
    [_,_,_,F,Sc,B,B,B,B,B,B,Sc,F,_,_,_],
    [_,_,_,F,Sc,B,B,B,B,B,B,Sc,F,_,_,_],
    [_,_,_,F,Sc,B,B,B,B,B,B,Sc,F,_,_,_],
    [_,_,_,F,Sc,Sc,Sc,Sc,Sc,Sc,Sc,Sc,F,_,_,_],
    [_,_,_,F,F,F,F,F,F,F,F,F,F,_,_,_],
    [_,_,_,_,_,_,_,D,D,_,_,_,_,_,_,_],
    [_,_,_,_,_,_,_,D,D,_,_,_,_,_,_,_],
    [_,_,_,_,_,_,D,D,D,D,_,_,_,_,_,_],
    [_,_,_,_,_,D,D,D,D,D,D,_,_,_,_,_],
    [_,_,_,_,_,D,D,D,D,D,D,_,_,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  ];
})();

export const PLANT_SPRITE: SpriteData = (() => {
  const G = '#3D8B37', D = '#2D6B27', T = '#6B4E0A', P = '#B85C3A', R = '#8B4422';
  return [
    [_,_,_,_,_,_,G,G,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,G,G,G,G,_,_,_,_,_,_,_],
    [_,_,_,_,G,G,D,G,G,G,_,_,_,_,_,_],
    [_,_,_,G,G,D,G,G,D,G,G,_,_,_,_,_],
    [_,_,G,G,G,G,G,G,G,G,G,G,_,_,_,_],
    [_,G,G,D,G,G,G,G,G,G,D,G,G,_,_,_],
    [_,G,G,G,G,D,G,G,D,G,G,G,G,_,_,_],
    [_,_,G,G,G,G,G,G,G,G,G,G,_,_,_,_],
    [_,_,_,G,G,G,D,G,G,G,G,_,_,_,_,_],
    [_,_,_,_,_,_,T,T,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,_,T,T,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,R,R,R,R,R,_,_,_,_,_,_],
    [_,_,_,_,R,P,P,P,P,P,R,_,_,_,_,_],
    [_,_,_,_,R,P,P,P,P,P,R,_,_,_,_,_],
    [_,_,_,_,R,P,P,P,P,P,R,_,_,_,_,_],
    [_,_,_,_,_,R,R,R,R,R,_,_,_,_,_,_],
  ];
})();

export const COOLER_SPRITE: SpriteData = (() => {
  const W = '#CCDDEE', L = '#88BBDD', D = '#999999', B = '#666666';
  return [
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,D,D,D,D,D,D,_,_,_,_,_],
    [_,_,_,_,D,L,L,L,L,L,L,D,_,_,_,_],
    [_,_,_,_,D,L,L,L,L,L,L,D,_,_,_,_],
    [_,_,_,_,D,L,L,L,L,L,L,D,_,_,_,_],
    [_,_,_,_,_,D,D,D,D,D,D,_,_,_,_,_],
    [_,_,_,_,_,D,W,W,W,W,D,_,_,_,_,_],
    [_,_,_,_,_,D,W,W,W,W,D,_,_,_,_,_],
    [_,_,_,_,_,D,W,W,W,W,D,_,_,_,_,_],
    [_,_,_,_,D,D,W,W,W,W,D,D,_,_,_,_],
    [_,_,_,_,D,W,W,W,W,W,W,D,_,_,_,_],
    [_,_,_,_,D,D,D,D,D,D,D,D,_,_,_,_],
    [_,_,_,_,_,D,B,B,B,B,D,_,_,_,_,_],
    [_,_,_,_,D,D,B,B,B,B,D,D,_,_,_,_],
    [_,_,_,_,D,D,D,D,D,D,D,D,_,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  ];
})();

export const BOOKSHELF_SPRITE: SpriteData = (() => {
  const W = '#8B6914', D = '#6B4E0A', R = '#CC4444', B = '#4477AA', G = '#44AA66', Y = '#CCAA33', P = '#9955AA';
  return [
    [_,W,W,W,W,W,W,W,W,W,W,W,W,W,W,_],
    [W,D,D,D,D,D,D,D,D,D,D,D,D,D,D,W],
    [W,D,R,R,B,B,G,G,Y,Y,R,R,B,B,D,W],
    [W,D,R,R,B,B,G,G,Y,Y,R,R,B,B,D,W],
    [W,D,R,R,B,B,G,G,Y,Y,R,R,B,B,D,W],
    [W,W,W,W,W,W,W,W,W,W,W,W,W,W,W,W],
    [W,D,D,D,D,D,D,D,D,D,D,D,D,D,D,W],
    [W,D,P,P,Y,Y,B,B,G,G,P,P,R,R,D,W],
    [W,D,P,P,Y,Y,B,B,G,G,P,P,R,R,D,W],
    [W,D,P,P,Y,Y,B,B,G,G,P,P,R,R,D,W],
    [W,W,W,W,W,W,W,W,W,W,W,W,W,W,W,W],
    [W,D,D,D,D,D,D,D,D,D,D,D,D,D,D,W],
    [W,D,G,G,R,R,P,P,B,B,Y,Y,G,G,D,W],
    [W,D,G,G,R,R,P,P,B,B,Y,Y,G,G,D,W],
    [W,D,G,G,R,R,P,P,B,B,Y,Y,G,G,D,W],
    [W,W,W,W,W,W,W,W,W,W,W,W,W,W,W,W],
  ];
})();

export const WHITEBOARD_SPRITE: SpriteData = (() => {
  const F = '#AAAAAA', W = '#EEEEFF', M = '#CC4444', B = '#4477AA';
  return [
    [_,F,F,F,F,F,F,F,F,F,F,F,F,F,F,_],
    [F,W,W,W,W,W,W,W,W,W,W,W,W,W,W,F],
    [F,W,W,M,M,W,W,W,W,B,B,W,W,W,W,F],
    [F,W,W,W,W,W,W,W,W,W,W,W,W,W,W,F],
    [F,W,W,W,M,M,M,W,W,W,W,B,B,W,W,F],
    [F,W,M,M,W,W,W,W,B,B,W,W,W,W,W,F],
    [F,W,W,W,W,W,B,B,W,W,W,W,M,M,W,F],
    [F,W,W,W,W,W,W,W,W,W,W,W,W,W,W,F],
    [F,W,W,W,W,W,W,W,W,W,W,W,W,W,W,F],
    [_,F,F,F,F,F,F,F,F,F,F,F,F,F,F,_],
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_,_,_,_,_,_],
  ];
})();

/* ══════════════════════════════════════════════════════════════════
   SPRITE BUILDING + CACHING
   ══════════════════════════════════════════════════════════════════ */

const spriteCache = new Map<string, CharacterSprites>();

export function getCharacterSprites(paletteIndex: number, hueShift = 0): CharacterSprites {
  const cacheKey = `${paletteIndex}:${hueShift}`;
  const cached = spriteCache.get(cacheKey);
  if (cached) return cached;

  const palette = CHARACTER_PALETTES[paletteIndex % CHARACTER_PALETTES.length];
  const p = hueShift > 0 ? shiftPalette(palette, hueShift) : palette;

  // Walk frames: 4 frames per direction (1, 2=standing, 3=mirror, 2 again)
  const walkDown = [
    resolveTemplate(WALK_DOWN_1, p),
    resolveTemplate(WALK_DOWN_2, p),
    resolveTemplate(WALK_DOWN_3, p),
    resolveTemplate(WALK_DOWN_2, p),
  ];
  const walkUp = [
    resolveTemplate(WALK_UP_1, p),
    resolveTemplate(WALK_UP_2, p),
    resolveTemplate(WALK_UP_3, p),
    resolveTemplate(WALK_UP_2, p),
  ];
  const walkRight = [
    resolveTemplate(WALK_RIGHT_1, p),
    resolveTemplate(WALK_RIGHT_2, p),
    resolveTemplate(WALK_RIGHT_1, p), // mirror
    resolveTemplate(WALK_RIGHT_2, p),
  ];
  const walkLeft = walkRight.map(s => flipSpriteH(s));

  // Typing frames: 2 frames per direction
  const typeDown = [resolveTemplate(TYPE_DOWN_1, p), resolveTemplate(TYPE_DOWN_2, p)];
  const typeUp = [resolveTemplate(TYPE_UP_1, p), resolveTemplate(TYPE_UP_2, p)];
  const typeRight = typeDown; // Reuse front-facing type for side (simplified)
  const typeLeft = typeDown;

  const sprites: CharacterSprites = {
    walk: { 0: walkDown, 1: walkLeft, 2: walkRight, 3: walkUp },
    typing: { 0: typeDown, 1: typeLeft, 2: typeRight, 3: typeUp },
  };

  spriteCache.set(cacheKey, sprites);
  return sprites;
}

function flipSpriteH(sprite: SpriteData): SpriteData {
  return sprite.map(row => [...row].reverse());
}

/* ── Hue Shift ─────────────────────────────────────────────────── */

function shiftPalette(palette: CharPalette, degrees: number): CharPalette {
  return {
    skin: shiftColor(palette.skin, degrees),
    shirt: shiftColor(palette.shirt, degrees),
    pants: shiftColor(palette.pants, degrees),
    hair: shiftColor(palette.hair, degrees),
    shoes: shiftColor(palette.shoes, degrees),
  };
}

function shiftColor(hex: string, degrees: number): string {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;

  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0, s = 0;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
    else if (max === g) h = ((b - r) / d + 2) / 6;
    else h = ((r - g) / d + 4) / 6;
  }

  h = ((h * 360 + degrees) % 360) / 360;
  if (h < 0) h += 1;

  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };

  let rr: number, gg: number, bb: number;
  if (s === 0) {
    rr = gg = bb = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    rr = hue2rgb(p, q, h + 1 / 3);
    gg = hue2rgb(p, q, h);
    bb = hue2rgb(p, q, h - 1 / 3);
  }

  const toHex = (v: number) => Math.round(v * 255).toString(16).padStart(2, '0');
  return `#${toHex(rr)}${toHex(gg)}${toHex(bb)}`;
}

/* ══════════════════════════════════════════════════════════════════
   CANVAS SPRITE RENDERING CACHE
   ══════════════════════════════════════════════════════════════════ */

const canvasCache = new Map<string, HTMLCanvasElement>();

/** Render a SpriteData to an offscreen canvas at given zoom, with caching */
export function getCachedCanvas(sprite: SpriteData, zoom: number): HTMLCanvasElement {
  // Build a simple hash from sprite dimensions + first few pixels
  const h = sprite.length;
  const w = h > 0 ? sprite[0].length : 0;
  const sample = sprite.length > 2 ? sprite[2].join('').slice(0, 20) : '';
  const key = `${w}x${h}:${zoom}:${sample}`;

  const cached = canvasCache.get(key);
  if (cached) return cached;

  const canvas = document.createElement('canvas');
  canvas.width = w * zoom;
  canvas.height = h * zoom;
  const ctx = canvas.getContext('2d')!;

  for (let r = 0; r < h; r++) {
    for (let c = 0; c < w; c++) {
      const color = sprite[r][c];
      if (!color) continue;
      ctx.fillStyle = color;
      ctx.fillRect(c * zoom, r * zoom, zoom, zoom);
    }
  }

  canvasCache.set(key, canvas);
  return canvas;
}

/** Clear sprite cache (call on zoom change) */
export function clearCanvasCache(): void {
  canvasCache.clear();
}

/* ── Furniture sprite lookup ───────────────────────────────────── */

const FURNITURE_SPRITES: Record<string, SpriteData> = {
  desk: DESK_SPRITE,
  monitor: MONITOR_SPRITE,
  plant: PLANT_SPRITE,
  bookshelf: BOOKSHELF_SPRITE,
  cooler: COOLER_SPRITE,
  whiteboard: WHITEBOARD_SPRITE,
};

export function getFurnitureSprite(type: string): SpriteData | null {
  return FURNITURE_SPRITES[type] || null;
}

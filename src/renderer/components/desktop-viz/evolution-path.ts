/**
 * desktop-viz/evolution-path.ts — The 13-stage evolution path.
 */

export interface EvolutionStage {
  id: string;
  name: string;
}

export const EVOLUTION_PATH: EvolutionStage[] = [
  { id: 'CUBES',        name: 'GENESIS LATTICE' },
  { id: 'ICOSAHEDRON',  name: 'SACRED SPHERE' },
  { id: 'NETWORK',      name: 'SHANNON NETWORK' },
  { id: 'DOME',         name: 'GEODESIC CATHEDRAL' },
  { id: 'ASTROLABE',    name: 'LOVELACE ASTROLABE' },
  { id: 'TESSERACT',    name: 'VON NEUMANN TESSERACT' },
  { id: 'QUANTUM',      name: 'DIRAC PROBABILITY' },
  { id: 'MANDELBROT',   name: 'MANDELBROT SET' },
  { id: 'MOBIUS',       name: 'TURING MOBIUS' },
  { id: 'GRID',         name: 'OCEAN OF LIGHT' },
  { id: 'CABLES',       name: 'FIBONACCI NERVE' },
  { id: 'NONE',         name: 'TRANSCENDENCE' },
  { id: 'EDEN',         name: 'GIGA EARTH (REZ)' },
];

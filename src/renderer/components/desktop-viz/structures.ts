/**
 * desktop-viz/structures.ts — 13 evolution structure builders.
 *
 * Each builder returns { group, ...refs } where refs are mutable objects
 * needed by the animation loop.
 */

import * as THREE from 'three';
import { createMaterial } from './materials';

// ── Shared context passed to every builder ──────────────────────────────────

export interface BuildContext {
  glowTexture: THREE.CanvasTexture;
  cloudTexture: THREE.CanvasTexture;
}

// ── Return types ────────────────────────────────────────────────────────────

export interface CubesResult {
  group: THREE.Group;
  coreCubes: THREE.Mesh[];
}

export interface IcosahedronResult {
  group: THREE.Group;
}

export interface DomeResult {
  group: THREE.Group;
  cathedralRings: THREE.Mesh[];
  abyssParticles: THREE.Points;
}

export interface CablesResult {
  group: THREE.Group;
}

export interface GridResult {
  group: THREE.Group;
  gridOcean: THREE.Points;
}

export interface MandelbrotResult {
  group: THREE.Group;
  mandelbrotSystem: THREE.Points;
}

export interface AstrolabeResult {
  group: THREE.Group;
  astrolabeRings: THREE.Group[];
}

export interface TesseractResult {
  group: THREE.Group;
  tesseractLines: THREE.LineSegments;
}

export interface NetworkResult {
  group: THREE.Group;
  shannonNodes: { pos: THREE.Vector3; velocity: THREE.Vector3 }[];
  shannonLines: THREE.LineSegments;
}

export interface MobiusResult {
  group: THREE.Group;
  mobiusSystem: THREE.Points;
}

export interface QuantumResult {
  group: THREE.Group;
  quantumRings: THREE.LineLoop[];
}

export interface NoneResult {
  group: THREE.Group;
  matrixLines: THREE.Line[];
}

export interface EdenResult {
  group: THREE.Group;
  edenDebris: THREE.Object3D[];
  edenPlayer: THREE.Group;
  edenLady: THREE.Object3D;
}

// ── All refs collected ──────────────────────────────────────────────────────

export interface AllStructureRefs {
  structures: Record<string, THREE.Group>;
  coreCubes: THREE.Mesh[];
  matrixLines: THREE.Line[];
  gridOcean: THREE.Points | null;
  mandelbrotSystem: THREE.Points | null;
  tesseractLines: THREE.LineSegments | null;
  astrolabeRings: THREE.Group[];
  shannonNodes: { pos: THREE.Vector3; velocity: THREE.Vector3 }[];
  shannonLines: THREE.LineSegments | null;
  mobiusSystem: THREE.Points | null;
  quantumRings: THREE.LineLoop[];
  abyssParticles: THREE.Points | null;
  cathedralRings: THREE.Mesh[];
  edenDebris: THREE.Object3D[];
  edenPlayer: THREE.Group | null;
  edenLady: THREE.Object3D | null;
}

// ── 1. CUBES — 3x3x3 grid logo with 15% random dropout ────────────────────

export function buildCubes(_ctx: BuildContext): CubesResult {
  const group = new THREE.Group();
  const coreCubes: THREE.Mesh[] = [];

  const boxMat = createMaterial('mesh', 0x00ffff, 0.85, false);  // Solid faces, NOT additive
  const edgeMat = createMaterial('line', 0x00ffff, 0.8, true);
  edgeMat.userData.isAccent = true;
  const boxGeo = new THREE.BoxGeometry(1.4, 1.4, 1.4);
  const edgeGeo = new THREE.EdgesGeometry(boxGeo);
  const gridSize = 3;
  const spacing = 1.6;
  const cubeOffset = (gridSize * spacing) / 2 - (spacing / 2);
  for (let x = 0; x < gridSize; x++) {
    for (let y = 0; y < gridSize; y++) {
      for (let z = 0; z < gridSize; z++) {
        if (Math.random() > 0.85) continue;  // 15% dropout
        const mesh = new THREE.Mesh(boxGeo, boxMat.clone());
        mesh.add(new THREE.LineSegments(edgeGeo, edgeMat.clone()));
        const posX = (x * spacing) - cubeOffset;
        const posY = (y * spacing) - cubeOffset;
        const posZ = (z * spacing) - cubeOffset;
        mesh.position.set(posX, posY, posZ);
        const dir = new THREE.Vector3(posX, posY, posZ).normalize();
        if (dir.length() === 0) dir.set(0, 1, 0);
        mesh.userData = { baseX: posX, baseY: posY, baseZ: posZ, dir, rx: Math.random() * Math.PI * 2, speed: 0.5 + Math.random() * 0.5 };
        group.add(mesh);
        coreCubes.push(mesh);
      }
    }
  }
  group.scale.set(1.5, 1.5, 1.5);

  return { group, coreCubes };
}

// ── 2. ICOSAHEDRON ─────────────────────────────────────────────────────────

export function buildIcosahedron(_ctx: BuildContext): IcosahedronResult {
  const group = new THREE.Group();
  group.add(new THREE.Mesh(new THREE.IcosahedronGeometry(5.0, 3), createMaterial('mesh', 0x00ffff, 0.15, true, true)));
  const midIco = createMaterial('mesh', 0x00ffff, 0.3, true, true);
  midIco.userData.isAccent = true;
  group.add(new THREE.Mesh(new THREE.IcosahedronGeometry(3.5, 2), midIco));
  group.add(new THREE.Mesh(new THREE.IcosahedronGeometry(2.0, 1), createMaterial('mesh', 0xffffff, 0.6, true, true)));

  return { group };
}

// ── 3. DOME (Cathedral) ────────────────────────────────────────────────────

export function buildDome(ctx: BuildContext): DomeResult {
  const group = new THREE.Group();
  const cathedralRings: THREE.Mesh[] = [];

  group.add(new THREE.Mesh(new THREE.SphereGeometry(35, 48, 32, 0, Math.PI * 2, 0, Math.PI / 2), createMaterial('mesh', 0x00ffff, 0.1, true, true)));
  for (let i = 0; i < 8; i++) {
    const angle = (i / 8) * Math.PI * 2;
    const pillar = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.8, 50, 8), createMaterial('mesh', 0x00ffff, 0.15, true, true));
    pillar.position.set(Math.cos(angle) * 18, -10, Math.sin(angle) * 18);
    group.add(pillar);
  }
  const chMat = createMaterial('mesh', 0xffffff, 0.5, true, true);
  chMat.userData.isAccent = true;
  for (let i = 0; i < 6; i++) {
    const crystal = new THREE.Mesh(new THREE.OctahedronGeometry(4 - i * 0.5, 1), chMat.clone());
    crystal.position.y = 25 - i * 4;
    crystal.rotation.y = i * Math.PI / 4;
    group.add(crystal);
    cathedralRings.push(crystal);
  }
  const abyssGeo = new THREE.BufferGeometry();
  const aPts: number[] = [];
  for (let i = 0; i < 2000; i++) {
    const r = Math.random() * 40;
    const theta = Math.random() * Math.PI * 2;
    const depth = -10 - Math.pow(r, 1.2) * 0.4;
    aPts.push(r * Math.cos(theta), depth, r * Math.sin(theta));
  }
  abyssGeo.setAttribute('position', new THREE.Float32BufferAttribute(aPts, 3));
  const abyssParticles = new THREE.Points(abyssGeo, new THREE.PointsMaterial({
    size: 0.5, map: ctx.glowTexture, color: 0x00ffff, transparent: true,
    opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  abyssParticles.userData = { isAccent: true };
  group.add(abyssParticles);

  return { group, cathedralRings, abyssParticles };
}

// ── 4. CABLES (Fibonacci Nerve) ────────────────────────────────────────────

export function buildCables(_ctx: BuildContext): CablesResult {
  const group = new THREE.Group();
  const cableMat = createMaterial('mesh', 0x00ffff, 0.15, true, false);
  cableMat.userData.isAccent = true;
  const phiFib = Math.PI * (3.0 - Math.sqrt(5.0));
  for (let i = 0; i < 80; i++) {
    const y = 1 - (i / 79) * 2;
    const radius = Math.sqrt(1 - y * y);
    const theta = phiFib * i;
    const start = new THREE.Vector3(Math.cos(theta) * radius * 30, y * 30, Math.sin(theta) * radius * 30);
    const end = new THREE.Vector3(0, 0, 0);
    const mid = start.clone().lerp(end, 0.5).applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.PI / 1.5);
    group.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3([start, mid, end]), 50, 0.15, 6, false), cableMat.clone()));
  }

  return { group };
}

// ── 5. GRID (Ocean of Light) ───────────────────────────────────────────────

export function buildGrid(ctx: BuildContext): GridResult {
  const group = new THREE.Group();
  const oceanGeo = new THREE.PlaneGeometry(100, 100, 80, 80);
  oceanGeo.rotateX(-Math.PI / 2);
  const gridOcean = new THREE.Points(oceanGeo, new THREE.PointsMaterial({
    size: 0.35, map: ctx.glowTexture, transparent: true, opacity: 0.5,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  (gridOcean.material as THREE.PointsMaterial).userData = { baseOpacity: 0.5, isAccent: true };
  gridOcean.userData.baseY = new Float32Array(oceanGeo.attributes.position.count);
  for (let i = 0; i < oceanGeo.attributes.position.count; i++) {
    gridOcean.userData.baseY[i] = oceanGeo.attributes.position.getY(i);
  }
  group.add(gridOcean);

  return { group, gridOcean };
}

// ── 6. MANDELBROT SET ──────────────────────────────────────────────────────

export function buildMandelbrot(ctx: BuildContext): MandelbrotResult {
  const group = new THREE.Group();
  const mPts: number[] = [];
  const mCols: number[] = [];
  const mData: any[] = [];
  const maxIter = 40;
  for (let x = -2.1; x < 0.8; x += 0.012) {
    for (let y = -1.2; y < 1.2; y += 0.012) {
      let cx = x, cy = y, zx = 0, zy = 0, iter = 0;
      while (zx * zx + zy * zy < 4 && iter < maxIter) {
        const tmp = zx * zx - zy * zy + cx;
        zy = 2 * zx * zy + cy;
        zx = tmp;
        iter++;
      }
      if (iter < maxIter && iter > 2) {
        const smooth = iter + 1 - Math.log(Math.log(Math.sqrt(zx * zx + zy * zy))) / Math.log(2);
        const height = (smooth / maxIter) * 6.0;
        mPts.push((x + 0.65) * 10, height - 3.0, y * 10);
        mCols.push(1, 1, 1);
        mData.push({ baseX: (x + 0.65) * 10, baseZ: y * 10, baseY: height - 3.0, iterRatio: smooth / maxIter });
      }
    }
  }
  const mGeom = new THREE.BufferGeometry();
  mGeom.setAttribute('position', new THREE.Float32BufferAttribute(mPts, 3));
  mGeom.setAttribute('color', new THREE.Float32BufferAttribute(mCols, 3));
  const mandelbrotSystem = new THREE.Points(mGeom, new THREE.PointsMaterial({
    size: 0.25, map: ctx.glowTexture, vertexColors: true, transparent: true,
    opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  mandelbrotSystem.userData.data = mData;
  group.add(mandelbrotSystem);

  return { group, mandelbrotSystem };
}

// ── 7. ASTROLABE ───────────────────────────────────────────────────────────

export function buildAstrolabe(_ctx: BuildContext): AstrolabeResult {
  const group = new THREE.Group();
  const astrolabeRings: THREE.Group[] = [];

  const astroMatSolid = createMaterial('mesh', 0x00ffff, 0.15, true, false);
  const astroMatDash = createMaterial('line', 0x00ffff, 0.6, true, false, true);
  astroMatDash.userData.isAccent = true;
  for (let i = 1; i <= 8; i++) {
    const ringGroup = new THREE.Group();
    const radius = i * 2.0;
    ringGroup.add(new THREE.Mesh(new THREE.TorusGeometry(radius, 0.05, 8, 80), astroMatSolid.clone()));
    const dashedEdges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.TorusGeometry(radius, 0.2, 4, 40)), astroMatDash.clone());
    dashedEdges.computeLineDistances();
    ringGroup.add(dashedEdges);
    ringGroup.rotation.x = Math.random() * Math.PI;
    ringGroup.rotation.y = Math.random() * Math.PI;
    ringGroup.userData = { rxSpeed: (Math.random() - 0.5) * 0.015, rySpeed: (Math.random() - 0.5) * 0.015 };
    group.add(ringGroup);
    astrolabeRings.push(ringGroup);
  }

  return { group, astrolabeRings };
}

// ── 8. TESSERACT ───────────────────────────────────────────────────────────

export function buildTesseract(ctx: BuildContext): TesseractResult {
  const group = new THREE.Group();

  const tessMat = createMaterial('line', 0x00ffff, 0.8, true, false);
  tessMat.userData.isAccent = true;
  const tessNodesMat = new THREE.PointsMaterial({
    size: 1.0, map: ctx.glowTexture, color: 0x00ffff, transparent: true,
    opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const tPts4D: { x: number; y: number; z: number; w: number }[] = [];
  for (let i = 0; i < 16; i++) {
    tPts4D.push({ x: (i & 1) ? 1 : -1, y: (i & 2) ? 1 : -1, z: (i & 4) ? 1 : -1, w: (i & 8) ? 1 : -1 });
  }
  const tEdges: number[] = [];
  for (let i = 0; i < 16; i++) {
    for (let j = i + 1; j < 16; j++) {
      if (Math.abs(tPts4D[i].x - tPts4D[j].x) + Math.abs(tPts4D[i].y - tPts4D[j].y) +
          Math.abs(tPts4D[i].z - tPts4D[j].z) + Math.abs(tPts4D[i].w - tPts4D[j].w) === 2) {
        tEdges.push(i, j);
      }
    }
  }
  const tessGeo = new THREE.BufferGeometry();
  tessGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(tEdges.length * 3), 3));
  const tesseractLines = new THREE.LineSegments(tessGeo, tessMat);
  tesseractLines.userData = { pts4D: tPts4D, edges: tEdges, angleXW: 0, angleYW: 0 };
  const tessNodesGeo = new THREE.BufferGeometry();
  tessNodesGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(16 * 3), 3));
  const tessNodes = new THREE.Points(tessNodesGeo, tessNodesMat);
  tessNodes.userData.isAccent = true;
  group.add(tesseractLines);
  group.add(tessNodes);
  group.scale.set(3, 3, 3);

  return { group, tesseractLines };
}

// ── 9. NETWORK (Shannon) ──────────────────────────────────────────────────

export function buildNetwork(ctx: BuildContext): NetworkResult {
  const group = new THREE.Group();
  const shannonNodes: { pos: THREE.Vector3; velocity: THREE.Vector3 }[] = [];

  const netNodeMat = new THREE.PointsMaterial({
    size: 0.6, map: ctx.glowTexture, color: 0x00ffff, transparent: true,
    opacity: 0.8, blending: THREE.AdditiveBlending,
  });
  const netLineMat = createMaterial('line', 0x00ffff, 0.2, true, false);
  netLineMat.userData.isAccent = true;
  const netPts: THREE.Vector3[] = [];
  for (let i = 0; i < 120; i++) {
    const p = new THREE.Vector3((Math.random() - 0.5) * 20, (Math.random() - 0.5) * 20, (Math.random() - 0.5) * 20);
    netPts.push(p);
    shannonNodes.push({ pos: p, velocity: new THREE.Vector3((Math.random() - 0.5) * 0.015, (Math.random() - 0.5) * 0.015, (Math.random() - 0.5) * 0.015) });
  }
  group.add(new THREE.Points(new THREE.BufferGeometry().setFromPoints(netPts), netNodeMat));
  const shannonLines = new THREE.LineSegments(new THREE.BufferGeometry(), netLineMat);
  group.add(shannonLines);

  return { group, shannonNodes, shannonLines };
}

// ── 10. MOBIUS ─────────────────────────────────────────────────────────────

export function buildMobius(ctx: BuildContext): MobiusResult {
  const group = new THREE.Group();

  const mobPts: { u: number; v: number }[] = [];
  for (let u = 0; u < Math.PI * 2; u += 0.04) {
    for (let v = -1; v <= 1; v += 0.15) mobPts.push({ u, v });
  }
  const mobGeo = new THREE.BufferGeometry();
  mobGeo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(mobPts.length * 3), 3));
  const mobiusSystem = new THREE.Points(mobGeo, new THREE.PointsMaterial({
    size: 0.25, map: ctx.glowTexture, color: 0x00ffff, transparent: true,
    opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  mobiusSystem.userData = { data: mobPts, isAccent: true };
  group.add(mobiusSystem);
  group.scale.set(4, 4, 4);

  return { group, mobiusSystem };
}

// ── 11. QUANTUM (Massive Rainbow Cloud) ────────────────────────────────────

export function buildQuantum(ctx: BuildContext): QuantumResult {
  const group = new THREE.Group();
  const quantumRings: THREE.LineLoop[] = [];

  group.add(new THREE.Points(
    new THREE.SphereGeometry(3.5, 64, 64),
    new THREE.PointsMaterial({ size: 0.4, map: ctx.glowTexture, color: 0xffffff, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending, depthWrite: false }),
  ));
  const numQRings = 30;
  for (let i = 0; i < numQRings; i++) {
    const qLineMat = createMaterial('line', 0xffffff, 0.5, true, false);
    qLineMat.userData.isAccent = true;
    const qGeo = new THREE.BufferGeometry();
    qGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(300 * 3), 3));
    const qLine = new THREE.LineLoop(qGeo, qLineMat as THREE.LineBasicMaterial);
    qLine.rotation.x = Math.random() * Math.PI;
    qLine.rotation.y = Math.random() * Math.PI;
    qLine.userData = { radius: 6 + i * 0.4, waveSpeed: 2 + Math.random() * 4, colorPhase: i / numQRings };
    group.add(qLine);
    quantumRings.push(qLine);
  }

  return { group, quantumRings };
}

// ── 12. NONE (Transcendence — Matrix rain) ─────────────────────────────────

export function buildNone(_ctx: BuildContext): NoneResult {
  const group = new THREE.Group();
  const matrixLines: THREE.Line[] = [];

  const lineMat = createMaterial('line', 0x00ffff, 0.2, true, false);
  lineMat.userData.isAccent = true;
  for (let i = 0; i < 100; i++) {
    const x = (Math.random() - 0.5) * 40;
    const z = (Math.random() - 0.5) * 40;
    const y = (Math.random() - 0.5) * 40;
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(x, y - 5, z), new THREE.Vector3(x, y + 5, z)]),
      lineMat.clone(),
    );
    line.userData = { speed: 0.01 + Math.random() * 0.02, baseY: y };
    group.add(line);
    matrixLines.push(line);
  }

  return { group, matrixLines };
}

// ── 13. EDEN (Giga Earth / REZ Tribute) ────────────────────────────────────

export function buildEden(_ctx: BuildContext): EdenResult {
  const group = new THREE.Group();
  const edenDebris: THREE.Object3D[] = [];

  // Box tunnel with solid backside
  const tunnelGroup = new THREE.Group();
  const edenTunnelGeo = new THREE.BoxGeometry(40, 40, 200, 4, 4, 20);
  const tunnelWireMat = createMaterial('line', 0xff00ff, 0.3, true, false);
  tunnelWireMat.userData.isAccent = true;
  const tunnelWire = new THREE.LineSegments(
    new THREE.EdgesGeometry(edenTunnelGeo),
    tunnelWireMat,
  );
  const tunnelSolidMat = new THREE.MeshBasicMaterial({ color: 0x050515, side: THREE.BackSide });
  (tunnelSolidMat as any).transparent = true;
  (tunnelSolidMat as any).opacity = 1.0;
  tunnelSolidMat.userData = { baseOpacity: 1.0, isTunnelSolid: true };
  const tunnelSolid = new THREE.Mesh(edenTunnelGeo, tunnelSolidMat);
  tunnelGroup.add(tunnelWire);
  tunnelGroup.add(tunnelSolid);
  group.add(tunnelGroup);

  // Giga Earth sphere (orange, segmented, with pole holes)
  const sphereGeo = new THREE.SphereGeometry(6, 24, 16, 0, Math.PI * 2, 0.2, Math.PI - 0.4);
  const sphereMat = new THREE.MeshBasicMaterial({ color: 0xff6600, transparent: true, opacity: 0.9 });
  sphereMat.userData = { baseOpacity: 0.9, isBossSphere: true };
  const edenLady = new THREE.Mesh(sphereGeo, sphereMat);
  const sphereWire = new THREE.WireframeGeometry(sphereGeo);
  const sphereWireMat = new THREE.LineBasicMaterial({ color: 0x550000, transparent: true, opacity: 0.6 });
  sphereWireMat.userData = { baseOpacity: 0.6, isBossWire: true };
  edenLady.add(new THREE.LineSegments(sphereWire, sphereWireMat));
  group.add(edenLady);

  // Vertical energy spines
  const spineMat = createMaterial('line', 0xff3300, 0.8, true, false);
  for (let i = 0; i < 15; i++) {
    const sx = (Math.random() - 0.5) * 4;
    const sz = (Math.random() - 0.5) * 4;
    group.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(sx, -50, sz), new THREE.Vector3(sx, 50, sz)]),
      spineMat,
    ));
  }

  // Geometric player (cylinder body + sphere head + ring)
  const edenPlayer = new THREE.Group();
  const pBodyMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
  pBodyMat.userData = { baseOpacity: 1.0, isPlayerBody: true };
  edenPlayer.add(new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.3, 1.5, 8), pBodyMat));
  const pHead = new THREE.Mesh(new THREE.SphereGeometry(0.2, 8, 8), pBodyMat);
  pHead.position.y = 0.9;
  edenPlayer.add(pHead);
  const ringPts: THREE.Vector3[] = [];
  for (let ri = 0; ri <= 32; ri++) {
    const th = (ri / 32) * Math.PI * 2;
    ringPts.push(new THREE.Vector3(Math.cos(th) * 1.5, Math.sin(th) * 1.5, 0));
  }
  const pRing = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(ringPts),
    createMaterial('line', 0x00ffff, 0.8, true, false),
  );
  pRing.rotation.x = Math.PI / 2;
  edenPlayer.add(pRing);
  group.add(edenPlayer);

  // Line-based data streaks (debris)
  const edenDebrisMat = createMaterial('mesh', 0x00ffff, 0.4, true, true);
  for (let i = 0; i < 60; i++) {
    const dGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0, 0, -2 - Math.random() * 3),
    ]);
    const dLine = new THREE.Line(dGeo, edenDebrisMat);
    dLine.position.set(
      (Math.random() - 0.5) * 35,
      (Math.random() - 0.5) * 35,
      (Math.random() - 0.5) * 100,
    );
    dLine.userData = { speed: 1.0 + Math.random() * 3.0 };
    group.add(dLine);
    edenDebris.push(dLine as any);
  }

  return { group, edenDebris, edenPlayer, edenLady };
}

// ── Build all 13 structures ────────────────────────────────────────────────

export function buildAllStructures(ctx: BuildContext): AllStructureRefs {
  const structures: Record<string, THREE.Group> = {};

  const cubes = buildCubes(ctx);
  structures['CUBES'] = cubes.group;

  const ico = buildIcosahedron(ctx);
  structures['ICOSAHEDRON'] = ico.group;

  const dome = buildDome(ctx);
  structures['DOME'] = dome.group;

  const cables = buildCables(ctx);
  structures['CABLES'] = cables.group;

  const grid = buildGrid(ctx);
  structures['GRID'] = grid.group;

  const mandelbrot = buildMandelbrot(ctx);
  structures['MANDELBROT'] = mandelbrot.group;

  const astrolabe = buildAstrolabe(ctx);
  structures['ASTROLABE'] = astrolabe.group;

  const tesseract = buildTesseract(ctx);
  structures['TESSERACT'] = tesseract.group;

  const network = buildNetwork(ctx);
  structures['NETWORK'] = network.group;

  const mobius = buildMobius(ctx);
  structures['MOBIUS'] = mobius.group;

  const quantum = buildQuantum(ctx);
  structures['QUANTUM'] = quantum.group;

  const none = buildNone(ctx);
  structures['NONE'] = none.group;

  const eden = buildEden(ctx);
  structures['EDEN'] = eden.group;

  return {
    structures,
    coreCubes: cubes.coreCubes,
    matrixLines: none.matrixLines,
    gridOcean: grid.gridOcean,
    mandelbrotSystem: mandelbrot.mandelbrotSystem,
    tesseractLines: tesseract.tesseractLines,
    astrolabeRings: astrolabe.astrolabeRings,
    shannonNodes: network.shannonNodes,
    shannonLines: network.shannonLines,
    mobiusSystem: mobius.mobiusSystem,
    quantumRings: quantum.quantumRings,
    abyssParticles: dome.abyssParticles,
    cathedralRings: dome.cathedralRings,
    edenDebris: eden.edenDebris,
    edenPlayer: eden.edenPlayer,
    edenLady: eden.edenLady,
  };
}

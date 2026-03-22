/**
 * desktop-viz/animators.ts — Per-structure animation logic extracted from animate().
 */

import * as THREE from 'three';
import type { AllStructureRefs } from './structures';

// ── Shared animation context ────────────────────────────────────────────────

export interface AnimContext {
  elapsed: number;
  delta: number;
  idleFactor: number;
  audioData: { low: number; mid: number; high: number; total: number };
  moodLerp: {
    baseColor: THREE.Color;
    accentColor: THREE.Color;
    rotationSpeed: number;
    bloomStrength: number;
    particleSpeedScale: number;
    grain: number;
  };
  targetStructureId: string;
}

// ── Per-structure animators ─────────────────────────────────────────────────

export function animateCubes(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['CUBES']?.visible || refs.coreCubes.length === 0) return;
  refs.coreCubes.forEach((c) => {
    if (!c?.userData) return;
    c.rotation.x += 0.01 * c.userData.speed * ctx.idleFactor;
    c.rotation.y += 0.012 * c.userData.speed * ctx.idleFactor;
    const organicBreathe = Math.sin(ctx.elapsed * 2.0 * c.userData.speed + c.userData.rx) * 0.15;
    const interactionForce = ctx.audioData.total * 0.5;
    const expansion = organicBreathe + ctx.audioData.low * 1.5 + interactionForce * 1.5;
    c.position.set(
      c.userData.baseX + c.userData.dir.x * expansion,
      c.userData.baseY + c.userData.dir.y * expansion,
      c.userData.baseZ + c.userData.dir.z * expansion,
    );
  });
}

export function animateDome(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['DOME']?.visible) return;
  if (refs.abyssParticles) {
    refs.abyssParticles.rotation.y -= 0.005 * ctx.idleFactor;
    refs.abyssParticles.scale.setScalar(1.0 + ctx.audioData.low * 0.3 * ctx.idleFactor);
  }
  refs.cathedralRings.forEach((c, i) => {
    if (c) {
      c.rotation.y += 0.002 * (i % 2 === 0 ? 1 : -1) * ctx.idleFactor;
      c.scale.setScalar(1.0 + ctx.audioData.mid * 0.1);
    }
  });
}

export function animateGrid(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['GRID']?.visible || !refs.gridOcean?.geometry?.attributes?.position || !refs.gridOcean.userData) return;
  const positions = (refs.gridOcean.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
  const baseYs = refs.gridOcean.userData.baseY as Float32Array;
  for (let i = 0; i < refs.gridOcean.geometry.attributes.position.count; i++) {
    positions[i * 3 + 1] = baseYs[i] +
      Math.sin(positions[i * 3] * 0.1 + ctx.elapsed) * 0.8 +
      Math.cos(positions[i * 3 + 2] * 0.1 - ctx.elapsed * 1.2) * 0.8 +
      Math.sin(positions[i * 3] * 0.3 + positions[i * 3 + 2] * 0.3) * (ctx.audioData.low * 3.0 * ctx.idleFactor);
  }
  refs.gridOcean.geometry.attributes.position.needsUpdate = true;
}

export function animateMandelbrot(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['MANDELBROT']?.visible || !refs.mandelbrotSystem?.geometry?.attributes?.position || !refs.mandelbrotSystem.userData?.data) return;
  refs.mandelbrotSystem.rotation.y = ctx.elapsed * 0.05;
  const positions = (refs.mandelbrotSystem.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
  const mDat = refs.mandelbrotSystem.userData.data as any[];
  const colorsArr = (refs.mandelbrotSystem.geometry.attributes.color as THREE.BufferAttribute).array as Float32Array;
  for (let i = 0; i < mDat.length; i++) {
    const wave = Math.sin(mDat[i].baseX * 0.5 + ctx.elapsed) * Math.cos(mDat[i].baseZ * 0.5 + ctx.elapsed) * (ctx.audioData.low * 3.0);
    positions[i * 3 + 1] = mDat[i].baseY + ctx.audioData.low * 4.0 * mDat[i].iterRatio * ctx.idleFactor + wave;
    const c = new THREE.Color().copy(ctx.moodLerp.baseColor).lerp(ctx.moodLerp.accentColor, mDat[i].iterRatio + ctx.audioData.mid);
    colorsArr[i * 3] = c.r; colorsArr[i * 3 + 1] = c.g; colorsArr[i * 3 + 2] = c.b;
  }
  refs.mandelbrotSystem.geometry.attributes.position.needsUpdate = true;
  refs.mandelbrotSystem.geometry.attributes.color.needsUpdate = true;
}

export function animateAstrolabe(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['ASTROLABE']?.visible || refs.astrolabeRings.length === 0) return;
  refs.astrolabeRings.forEach((ring) => {
    if (ring?.userData) {
      ring.rotation.x += ring.userData.rxSpeed * (1 + ctx.audioData.mid * 3);
      ring.rotation.y += ring.userData.rySpeed * (1 + ctx.audioData.mid * 3);
    }
  });
}

export function animateTesseract(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['TESSERACT']?.visible || !refs.tesseractLines?.userData || !refs.tesseractLines.geometry?.attributes?.position) return;
  const tessGroup = refs.structures['TESSERACT'];
  const tessNodesChild = tessGroup.children[1] as THREE.Points;
  refs.tesseractLines.userData.angleXW += (0.5 + ctx.audioData.low * 3.0) * ctx.delta * ctx.idleFactor;
  refs.tesseractLines.userData.angleYW += (0.3 + ctx.audioData.low * 2.0) * ctx.delta * ctx.idleFactor;
  const cosXW = Math.cos(refs.tesseractLines.userData.angleXW), sinXW = Math.sin(refs.tesseractLines.userData.angleXW);
  const cosYW = Math.cos(refs.tesseractLines.userData.angleYW), sinYW = Math.sin(refs.tesseractLines.userData.angleYW);
  const pts3D: THREE.Vector3[] = [];
  (refs.tesseractLines.userData.pts4D as { x: number; y: number; z: number; w: number }[]).forEach((p) => {
    let x = p.x, y = p.y, _z = p.z, w = p.w;
    const nx = x * cosXW - w * sinXW; const nw = x * sinXW + w * cosXW; x = nx; w = nw;
    const ny = y * cosYW - w * sinYW; const nw2 = y * sinYW + w * cosYW; y = ny; w = nw2;
    const wf = 2 / (4 - w);
    pts3D.push(new THREE.Vector3(x * wf, y * wf, _z * wf));
  });
  const lPositions = (refs.tesseractLines.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
  const edges = refs.tesseractLines.userData.edges as number[];
  for (let i = 0; i < edges.length; i += 2) {
    const idx = (i / 2) * 6;
    const e1 = edges[i], e2 = edges[i + 1];
    if (pts3D[e1] && pts3D[e2]) {
      lPositions[idx] = pts3D[e1].x; lPositions[idx + 1] = pts3D[e1].y; lPositions[idx + 2] = pts3D[e1].z;
      lPositions[idx + 3] = pts3D[e2].x; lPositions[idx + 4] = pts3D[e2].y; lPositions[idx + 5] = pts3D[e2].z;
    }
  }
  refs.tesseractLines.geometry.attributes.position.needsUpdate = true;
  if (tessNodesChild?.geometry?.attributes?.position) {
    const nPositions = (tessNodesChild.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
    pts3D.forEach((p, i) => { nPositions[i * 3] = p.x; nPositions[i * 3 + 1] = p.y; nPositions[i * 3 + 2] = p.z; });
    tessNodesChild.geometry.attributes.position.needsUpdate = true;
  }
}

export function animateNetwork(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['NETWORK']?.visible || !refs.shannonLines?.geometry || refs.shannonNodes.length === 0) return;
  const netGroup = refs.structures['NETWORK'];
  const nodeChild = netGroup.children[0] as THREE.Points;
  if (!nodeChild?.geometry?.attributes?.position) return;
  const nodePos = (nodeChild.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
  const linePts: THREE.Vector3[] = [];
  for (let i = 0; i < 120; i++) {
    if (!refs.shannonNodes[i]) continue;
    const p = refs.shannonNodes[i].pos, v = refs.shannonNodes[i].velocity;
    p.add(v);
    if (p.length() > 20) v.multiplyScalar(-1);
    nodePos[i * 3] = p.x; nodePos[i * 3 + 1] = p.y; nodePos[i * 3 + 2] = p.z;
    let connected = 0;
    for (let j = i + 1; j < 120; j++) {
      if (connected > 3 || !refs.shannonNodes[j]) break;
      if (p.distanceTo(refs.shannonNodes[j].pos) < 6 + ctx.audioData.high * 6) {
        linePts.push(p.clone(), refs.shannonNodes[j].pos.clone());
        connected++;
      }
    }
  }
  nodeChild.geometry.attributes.position.needsUpdate = true;
  if (linePts.length > 0) refs.shannonLines!.geometry.setFromPoints(linePts);
  else refs.shannonLines!.geometry.setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
}

export function animateMobius(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['MOBIUS']?.visible || !refs.mobiusSystem?.geometry?.attributes?.position || !refs.mobiusSystem.userData?.data) return;
  const positions = (refs.mobiusSystem.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
  const mDat = refs.mobiusSystem.userData.data as { u: number; v: number }[];
  const flowOffset = ctx.elapsed * (2 + ctx.audioData.mid * 4);
  for (let i = 0; i < mDat.length; i++) {
    const u = mDat[i].u + flowOffset, v = mDat[i].v;
    const R = 3.0, r = 1.5;
    positions[i * 3] = (R + r * v * Math.cos(u / 2)) * Math.cos(u);
    positions[i * 3 + 1] = (R + r * v * Math.cos(u / 2)) * Math.sin(u);
    positions[i * 3 + 2] = r * v * Math.sin(u / 2);
  }
  refs.mobiusSystem.geometry.attributes.position.needsUpdate = true;
}

export function animateQuantum(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['QUANTUM']?.visible || refs.quantumRings.length === 0) return;
  refs.quantumRings.forEach((qLine) => {
    if (!qLine?.geometry?.attributes?.position || !qLine.userData) return;
    const positions = (qLine.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array;
    for (let i = 0; i < 300; i++) {
      const theta = (i / 300) * Math.PI * 2;
      const wave = Math.sin(theta * 10 + ctx.elapsed * qLine.userData.waveSpeed) * (ctx.audioData.high * 4.0);
      const r = qLine.userData.radius + wave;
      positions[i * 3] = Math.cos(theta) * r;
      positions[i * 3 + 1] = Math.sin(theta) * r;
      positions[i * 3 + 2] = 0;
    }
    qLine.geometry.attributes.position.needsUpdate = true;
    if ((qLine.material as THREE.LineBasicMaterial).color) {
      (qLine.material as THREE.LineBasicMaterial).color.setHSL(
        (qLine.userData.colorPhase + ctx.elapsed * 0.1 + ctx.audioData.mid) % 1.0, 1.0, 0.5,
      );
    }
  });
}

export function animateEden(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['EDEN']?.visible) return;
  if (refs.edenLady) {
    refs.edenLady.rotation.y += ctx.moodLerp.rotationSpeed * 5.0 * ctx.idleFactor;
    refs.edenLady.scale.setScalar(1.0 + ctx.audioData.low * 0.5 * ctx.moodLerp.bloomStrength);
  }
  if (refs.edenPlayer) {
    const r = 8 + ctx.audioData.mid * 5 * ctx.moodLerp.particleSpeedScale;
    refs.edenPlayer.position.x = Math.cos(ctx.elapsed * 2.0) * r;
    refs.edenPlayer.position.z = Math.sin(ctx.elapsed * 2.0) * r;
    refs.edenPlayer.position.y = -3 + Math.sin(ctx.elapsed * 4.0) * 2;
    refs.edenPlayer.rotation.y = -ctx.elapsed * 2.0;
    refs.edenPlayer.rotation.z = Math.sin(ctx.elapsed * 1.5) * 0.1;
    refs.edenPlayer.rotation.x = 0.2;
  }
  refs.edenDebris.forEach((d) => {
    d.position.z += (d.userData.speed * ctx.moodLerp.particleSpeedScale) + (ctx.audioData.mid * 5);
    if (d.position.z > 30) {
      d.position.z = -100;
      d.position.x = (Math.random() - 0.5) * 35;
      d.position.y = (Math.random() - 0.5) * 35;
    }
  });
}

export function animateNone(refs: AllStructureRefs, ctx: AnimContext) {
  if (!refs.structures['NONE']?.visible || refs.matrixLines.length === 0) return;
  refs.matrixLines.forEach((line) => {
    if (line?.userData) {
      line.position.y += line.userData.speed * (1 + ctx.audioData.mid * 3);
      if (line.position.y > 20) line.position.y = -20;
    }
  });
}

// ── Run all structure animators ─────────────────────────────────────────────

export function animateAllStructures(refs: AllStructureRefs, ctx: AnimContext) {
  // Generic rotation for non-special structures
  const activeGroup = refs.structures[ctx.targetStructureId];
  if (activeGroup && !['GRID', 'MANDELBROT', 'TESSERACT', 'NETWORK', 'EDEN'].includes(ctx.targetStructureId)) {
    activeGroup.rotation.y += ctx.moodLerp.rotationSpeed * ctx.idleFactor;
    if (ctx.targetStructureId !== 'DOME') activeGroup.rotation.z += ctx.moodLerp.rotationSpeed * 0.5 * ctx.idleFactor;
  }

  animateCubes(refs, ctx);
  animateDome(refs, ctx);
  animateGrid(refs, ctx);
  animateMandelbrot(refs, ctx);
  animateAstrolabe(refs, ctx);
  animateTesseract(refs, ctx);
  animateNetwork(refs, ctx);
  animateMobius(refs, ctx);
  animateQuantum(refs, ctx);
  animateEden(refs, ctx);
  animateNone(refs, ctx);
}
